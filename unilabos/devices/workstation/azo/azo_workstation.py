"""偶氮反应微流控工作站。

两个蠕动泵经 Y 形接头汇入同一管路，流经温控反应后再由光谱仪表征。
泵与温控共享 RS485，光谱仪独享 USB。工作站只编排子设备，不直接占有串口。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

from unilabos.devices.workstation.workstation_base import WorkstationBase, WorkflowInfo, WorkflowStatus
from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.utils.log import logger

try:
    from unilabos.ros.nodes.presets.workstation import ROS2WorkstationNode
except ImportError:
    ROS2WorkstationNode = None


@device(
    id="azo_workstation",
    category=["workstation"],
    description="偶氮微流控工作站：双蠕动泵混合、温控反应、光谱在线表征",
    display_name="偶氮反应工作站",
    icon="reaction_station.webp",
)
class AzoWorkstation(WorkstationBase):
    """硬件控制型微流控工作站。"""

    _ros_node: "ROS2WorkstationNode"

    def __init__(
        self,
        deck=None,
        protocol_type=None,
        pump_a_id: str = "pump_a",
        pump_b_id: str = "pump_b",
        temp_controller_id: str = "temp_controller",
        spectrometer_id: str = "spectrometer",
        data_save_dir: Optional[str] = None,
        **kwargs,
    ):
        """
        Args:
            pump_a_id[泵A设备ID]: 图文件中液体 A 蠕动泵节点 id。
            pump_b_id[泵B设备ID]: 图文件中液体 B 蠕动泵节点 id。
            temp_controller_id[温控设备ID]: 图文件中温控器节点 id。
            spectrometer_id[光谱仪设备ID]: 图文件中光谱仪节点 id。
            data_save_dir[数据目录]: 光谱与实验总结保存目录。
        """
        super().__init__(deck=deck, **kwargs)
        self.protocol_type = protocol_type or []
        self.pump_a_id = pump_a_id
        self.pump_b_id = pump_b_id
        self.temp_controller_id = temp_controller_id
        self.spectrometer_id = spectrometer_id
        self.data_save_dir = Path(data_save_dir or (Path.cwd() / "azo_experiment_data"))
        self.data_save_dir.mkdir(parents=True, exist_ok=True)
        self.current_experiment_id: Optional[str] = None
        self.spectrum_data_list: list[Dict[str, Any]] = []
        self._stop_requested = False
        self._hardware_stop_lock = RLock()
        self._reaction_task = None
        self.data: Dict[str, Any] = {
            "status": "Idle",
            "workflow_status": WorkflowStatus.IDLE.value,
        }
        self.supported_workflows = {
            "azo_reaction": WorkflowInfo(
                name="azo_reaction",
                description="偶氮反应：双泵进样、温控反应、周期性光谱采集",
                estimated_duration=3600.0,
                required_materials=["液体A", "液体B"],
                output_product="偶氮反应产物",
                parameters_schema={
                    "flow_rate_a": {"type": "number", "description": "泵A流速 (mL/min)", "minimum": 0.0, "default": 1.0},
                    "flow_rate_b": {"type": "number", "description": "泵B流速 (mL/min)", "minimum": 0.0, "default": 1.0},
                    "temperature": {"type": "number", "description": "反应温度 (°C)", "minimum": 0.0, "maximum": 100.0, "default": 25.0},
                    "duration": {"type": "number", "description": "反应时长 (秒)", "minimum": 0.0, "default": 3600.0},
                    "spectrum_interval": {"type": "number", "description": "光谱采集间隔 (秒)", "minimum": 1.0, "default": 10.0},
                    "temp_tolerance": {"type": "number", "description": "温度稳定容差 (°C)", "default": 1.0},
                    "temp_timeout": {"type": "number", "description": "等待温度稳定超时 (秒)", "default": 300.0},
                },
            ),
        }

    @not_action
    def post_init(self, ros_node: "ROS2WorkstationNode") -> None:
        super().post_init(ros_node)
        self._ros_node = ros_node
        missing = [
            device_id
            for device_id in (self.pump_a_id, self.pump_b_id, self.temp_controller_id, self.spectrometer_id)
            if device_id not in ros_node.sub_devices
        ]
        if missing:
            logger.warning(f"偶氮工作站缺少子设备: {missing}")
        else:
            logger.info(
                f"偶氮工作站就绪: {self.pump_a_id}, {self.pump_b_id}, "
                f"{self.temp_controller_id}, {self.spectrometer_id}"
            )

    def _driver(self, device_id: str):
        sub = self._ros_node.sub_devices.get(device_id)
        if sub is None:
            raise RuntimeError(f"未找到子设备: {device_id}")
        return sub.driver_instance

    def _should_stop(self) -> bool:
        return self._stop_requested or self.current_workflow_status in (
            WorkflowStatus.STOPPING,
            WorkflowStatus.STOPPED,
        )

    @not_action
    def on_action_cancel(self, action_name: str = "") -> None:
        """云端取消 ROS 动作时由设备节点立刻回调，用于马上停硬件。"""
        logger.info(f"偶氮工作站收到动作取消: {action_name or '(unknown)'}")
        self.request_stop()

    @not_action
    def request_stop(self) -> bool:
        """置位停止标志并立刻停泵、停加热、停光谱仪。"""
        self._stop_requested = True
        if self.current_workflow_status not in (WorkflowStatus.STOPPED, WorkflowStatus.IDLE):
            self.current_workflow_status = WorkflowStatus.STOPPING
            self.data["workflow_status"] = WorkflowStatus.STOPPING.value
        self._stop_hardware()
        return True

    def _stop_hardware(self) -> None:
        with self._hardware_stop_lock:
            errors = []
            for name, func in (
                ("停泵", self.stop_pumps),
                ("停加热", self.stop_heating),
                ("停光谱仪", self.stop_spectrometer),
            ):
                try:
                    result = func()
                    if isinstance(result, dict) and not result.get("success", True):
                        errors.append(f"{name}失败: {result.get('message') or result.get('error')}")
                except Exception as exc:
                    errors.append(f"{name}异常: {exc}")
                    logger.error(f"偶氮工作站{name}失败: {exc}")
            if errors:
                logger.warning(f"偶氮工作站硬件停止部分失败: {errors}")
            else:
                logger.info("偶氮工作站已停止泵、加热和光谱仪")

    def _clear_stop_flags(self) -> None:
        self._stop_requested = False
        try:
            spectrometer = self._driver(self.spectrometer_id)
            if hasattr(spectrometer, "clear_stop"):
                spectrometer.clear_stop()
        except Exception:
            pass

    def _finalize_reaction(self, final_status: WorkflowStatus) -> None:
        self._stop_hardware()
        try:
            if self.spectrum_data_list:
                self._save_experiment_summary()
        except Exception as exc:
            logger.error(f"保存实验总结失败: {exc}")
        self.current_workflow_status = final_status
        self.data["status"] = "Idle"
        self.data["workflow_status"] = final_status.value
        self._reaction_task = None

    def stop_workflow(self, emergency: bool = False) -> bool:
        """停止工作流。即使状态已是 Idle 也下发停硬件，避免泵/加热继续跑。"""
        logger.info(f"停止偶氮反应工作流 (紧急停止: {emergency})")
        return self.request_stop()

    @not_action
    async def _sleep(self, seconds: float) -> bool:
        remaining = seconds
        while remaining > 0:
            if self._should_stop():
                return False
            step = min(0.2, remaining)
            await self._ros_node.sleep(step)
            remaining -= step
        return not self._should_stop()

    @action(description="同时设置两个蠕动泵流速")
    def set_pump_flow_rates(self, flow_rate_a: float = 1.0, flow_rate_b: float = 1.0) -> Dict[str, Any]:
        """
        Args:
            flow_rate_a[泵A流速]: 液体 A 流速 (mL/min)。
            flow_rate_b[泵B流速]: 液体 B 流速 (mL/min)。
        """
        result_a = self._driver(self.pump_a_id).set_speed(flow_rate_a)
        result_b = self._driver(self.pump_b_id).set_speed(flow_rate_b)
        success = bool(result_a.get("success") and result_b.get("success"))
        return {
            "success": success,
            "message": "双泵流速已设置" if success else "设置泵流速失败",
            "pump_a": result_a,
            "pump_b": result_b,
        }

    @action(description="停止两个蠕动泵")
    def stop_pumps(self) -> Dict[str, Any]:
        result_a = self._driver(self.pump_a_id).stop()
        result_b = self._driver(self.pump_b_id).stop()
        success = bool(result_a.get("success") and result_b.get("success"))
        return {"success": success, "message": "泵已停止" if success else "停泵失败"}

    @action(description="设置反应温度并打开输出使能")
    def set_temperature(self, temp: float = 25.0) -> Dict[str, Any]:
        """
        Args:
            temp[目标温度]: 反应温度 (°C)。
        """
        return self._driver(self.temp_controller_id).start(temp)

    @action(description="停止控温，关闭输出使能")
    def stop_heating(self, **kwargs) -> Dict[str, Any]:
        return self._driver(self.temp_controller_id).stop()

    @action(description="停止光谱仪采集")
    def stop_spectrometer(self) -> Dict[str, Any]:
        spectrometer = self._driver(self.spectrometer_id)
        if hasattr(spectrometer, "stop"):
            return spectrometer.stop()
        return {"success": True, "message": "光谱仪无需额外停止"}

    @action(description="读取当前反应温度")
    def read_temperature(self) -> Dict[str, Any]:
        return self._driver(self.temp_controller_id).read_value()

    @action(description="采集一张光谱")
    def acquire_spectrum(self) -> Dict[str, Any]:
        if self._should_stop():
            return {"success": False, "error": "stopped", "message": "已收到停止请求，跳过光谱采集"}
        return self._driver(self.spectrometer_id).acquire_spectrum()

    @action(description="等待温度进入目标范围")
    async def wait_until_temperature_stable(
        self,
        target_temperature: float = 25.0,
        tolerance: float = 1.0,
        timeout: float = 300.0,
        check_interval: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Args:
            target_temperature[目标温度]: 目标温度 (°C)。
            tolerance[容差]: 允许偏差 (°C)。
            timeout[超时]: 最大等待时间 (秒)。
            check_interval[检查间隔]: 轮询间隔 (秒)。
        """
        elapsed = 0.0
        last_temp = None
        while elapsed <= timeout:
            if self._should_stop():
                return {"success": False, "error": "stopped", "message": "等待温度稳定时收到停止请求"}
            result = self.read_temperature()
            if result.get("success"):
                last_temp = float(result["temp"])
                delta = abs(last_temp - target_temperature)
                logger.info(f"当前温度 {last_temp}°C, 目标 {target_temperature}°C, 偏差 {delta}°C")
                if delta <= tolerance:
                    return {
                        "success": True,
                        "message": "温度已进入目标范围",
                        "temp": last_temp,
                        "elapsed": elapsed,
                    }
            slept = await self._sleep(check_interval)
            if not slept:
                return {"success": False, "error": "stopped", "message": "等待温度稳定时收到停止请求", "temp": last_temp}
            elapsed += check_interval
        return {
            "success": False,
            "error": "timeout",
            "message": f"等待温度稳定超时 {timeout} 秒",
            "temp": last_temp,
        }

    def _save_spectrum_file(self, spectrum_data: Dict[str, Any]) -> Optional[str]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        experiment_id = self.current_experiment_id or "spectrum"
        file_path = self.data_save_dir / f"{experiment_id}_{timestamp}.csv"
        result = self._driver(self.spectrometer_id).save_spectrum(spectrum_data, str(file_path), format="csv")
        if result.get("success"):
            return str(file_path)
        return None

    def _save_experiment_summary(self) -> None:
        if not self.spectrum_data_list or not self.current_experiment_id:
            return
        summary_file = self.data_save_dir / f"{self.current_experiment_id}_summary.json"
        summary = {
            "experiment_id": self.current_experiment_id,
            "start_time": self.spectrum_data_list[0].get("timestamp"),
            "end_time": self.spectrum_data_list[-1].get("timestamp"),
            "total_spectra": len(self.spectrum_data_list),
            "parameters": {
                "flow_rate_a": self.spectrum_data_list[0].get("flow_rate_a"),
                "flow_rate_b": self.spectrum_data_list[0].get("flow_rate_b"),
                "temperature_setpoint": self.spectrum_data_list[0].get("temperature_setpoint"),
            },
        }
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        logger.info(f"实验总结已保存到 {summary_file}")

    @action(description="运行偶氮微流控反应并周期性采集光谱")
    async def run_azo_reaction(
        self,
        flow_rate_a: float = 1.0,
        flow_rate_b: float = 1.0,
        temperature: float = 25.0,
        duration: float = 3600.0,
        spectrum_interval: float = 10.0,
        temp_tolerance: float = 1.0,
        temp_timeout: float = 300.0,
    ) -> Dict[str, Any]:
        """
        Args:
            flow_rate_a[泵A流速]: 液体 A 流速 (mL/min)。
            flow_rate_b[泵B流速]: 液体 B 流速 (mL/min)。
            temperature[反应温度]: 反应温度 (°C)。
            duration[反应时长]: 反应时长 (秒)。
            spectrum_interval[采集间隔]: 光谱采集间隔 (秒)。
            temp_tolerance[温度容差]: 温度稳定判据 (°C)。
            temp_timeout[升温超时]: 等待温度稳定的最长时间 (秒)。
        """
        self._clear_stop_flags()
        self.current_experiment_id = f"azo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.spectrum_data_list = []
        self.current_workflow_status = WorkflowStatus.RUNNING
        self.data["status"] = "Running"
        self.data["workflow_status"] = WorkflowStatus.RUNNING.value

        logger.info(
            f"开始偶氮反应 {self.current_experiment_id}: "
            f"A={flow_rate_a} mL/min, B={flow_rate_b} mL/min, T={temperature}°C, "
            f"时长={duration}s, 间隔={spectrum_interval}s"
        )

        final_status = WorkflowStatus.ERROR
        try:
            if self._should_stop():
                final_status = WorkflowStatus.STOPPED
                return {"success": False, "error": "stopped", "message": "启动前已收到停止请求"}

            with self._hardware_stop_lock:
                if self._should_stop():
                    final_status = WorkflowStatus.STOPPED
                    return {"success": False, "error": "stopped", "message": "控温前已收到停止请求"}
                heat_result = self.set_temperature(temperature)
            if not heat_result.get("success"):
                return {"success": False, "error": "heat_failed", "message": "开始控温失败", "detail": heat_result}
            if self._should_stop():
                final_status = WorkflowStatus.STOPPED
                return {"success": False, "error": "stopped", "message": "控温后收到停止请求"}

            stable = await self.wait_until_temperature_stable(temperature, temp_tolerance, temp_timeout)
            if self._should_stop() or stable.get("error") == "stopped":
                final_status = WorkflowStatus.STOPPED
                return {"success": False, "error": "stopped", "message": stable.get("message") or "升温等待时已停止"}
            if not stable.get("success"):
                return {"success": False, "error": "temp_unstable", "message": stable.get("message"), "detail": stable}

            if self._should_stop():
                final_status = WorkflowStatus.STOPPED
                return {"success": False, "error": "stopped", "message": "启动泵前已收到停止请求"}

            with self._hardware_stop_lock:
                if self._should_stop():
                    final_status = WorkflowStatus.STOPPED
                    return {"success": False, "error": "stopped", "message": "启动泵前已收到停止请求"}
                pump_result = self.set_pump_flow_rates(flow_rate_a, flow_rate_b)
            if not pump_result.get("success"):
                return {"success": False, "error": "pump_failed", "message": "启动泵失败", "detail": pump_result}
            if self._should_stop():
                final_status = WorkflowStatus.STOPPED
                return {"success": False, "error": "stopped", "message": "启动泵后收到停止请求"}

            elapsed = 0.0
            next_acquire = 0.0
            while elapsed <= duration:
                if self._should_stop():
                    logger.info("偶氮反应收到停止请求")
                    break
                if elapsed >= next_acquire:
                    spectrum = self.acquire_spectrum()
                    if self._should_stop():
                        logger.info("偶氮反应收到停止请求")
                        break
                    if spectrum.get("success"):
                        spectrum["experiment_id"] = self.current_experiment_id
                        spectrum["elapsed_time"] = elapsed
                        spectrum["flow_rate_a"] = flow_rate_a
                        spectrum["flow_rate_b"] = flow_rate_b
                        spectrum["temperature_setpoint"] = temperature
                        temp_result = self.read_temperature()
                        if temp_result.get("success"):
                            spectrum["temperature_actual"] = temp_result["temp"]
                        self.spectrum_data_list.append(spectrum)
                        self._save_spectrum_file(spectrum)
                        logger.info(f"采集光谱 #{len(self.spectrum_data_list)} (t={elapsed:.1f}s)")
                    next_acquire += spectrum_interval
                remaining = duration - elapsed
                if remaining <= 0:
                    break
                step = min(0.2, remaining)
                slept = await self._sleep(step)
                if not slept:
                    break
                elapsed += step

            stopped = self._should_stop()
            final_status = WorkflowStatus.STOPPED if stopped else WorkflowStatus.COMPLETED
            return {
                "success": True,
                "stopped": stopped,
                "message": (
                    f"偶氮反应已停止，共采集 {len(self.spectrum_data_list)} 张光谱"
                    if stopped
                    else f"偶氮反应完成，共采集 {len(self.spectrum_data_list)} 张光谱"
                ),
                "experiment_id": self.current_experiment_id,
                "spectra_collected": len(self.spectrum_data_list),
            }
        except Exception as exc:
            logger.error(f"偶氮反应执行失败: {exc}")
            final_status = WorkflowStatus.STOPPED if self._should_stop() else WorkflowStatus.ERROR
            return {"success": False, "error": str(exc), "message": "偶氮反应执行失败"}
        finally:
            self._finalize_reaction(final_status)

    def execute_workflow(self, workflow_name: str, parameters: Dict[str, Any] = None, **kwargs) -> bool:
        """执行偶氮反应工作流，阻塞直到结束或被云端取消。"""
        parameters = parameters or {}
        try:
            self.workflow_parameters = parameters
            self.workflow_start_time = datetime.now().timestamp()
            return self._execute_workflow_impl(workflow_name, parameters)
        except Exception as exc:
            logger.error(f"偶氮工作站执行工作流失败: {exc}")
            self.current_workflow_status = WorkflowStatus.ERROR
            self.data["status"] = "Error"
            self.data["workflow_status"] = WorkflowStatus.ERROR.value
            return False

    def _execute_workflow_impl(self, workflow_name: str, parameters: Dict[str, Any]) -> bool:
        if workflow_name != "azo_reaction":
            logger.error(f"不支持的工作流: {workflow_name}")
            return False
        from unilabos.ros.nodes.base_device_node import ROS2DeviceNode

        self._reaction_task = ROS2DeviceNode.run_async_func(self.run_azo_reaction, True, **parameters)
        try:
            result = self._reaction_task.result()
            if isinstance(result, BaseException):
                logger.error(f"偶氮反应任务异常: {result}")
                return False
            if isinstance(result, dict):
                if result.get("stopped"):
                    return True
                return bool(result.get("success", False))
            return True
        except Exception as exc:
            logger.error(f"等待偶氮反应结束失败: {exc}")
            self.request_stop()
            return False

    def _stop_workflow_impl(self, emergency: bool = False) -> bool:
        logger.info(f"停止偶氮反应工作流 (紧急停止: {emergency})")
        return self.request_stop()

    @property
    @topic_config()
    def status(self) -> str:
        return self.data["status"]

    @property
    @topic_config()
    def workflow_status(self) -> str:
        return self.current_workflow_status.value

    @property
    @topic_config()
    def current_experiment(self) -> str:
        return self.current_experiment_id or ""

    @property
    @topic_config()
    def spectra_collected(self) -> int:
        return len(self.spectrum_data_list)
