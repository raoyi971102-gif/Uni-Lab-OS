"""
偶氮反应工站驱动
Azo Reaction Workstation Driver

微流控系统工作站，集成：
- 2个蠕动泵（通过步进电机控制流速）
- 1个温控器（Modbus通信）
- 1个光谱仪（USB通信）

工作流程：
1. 两个蠕动泵将液体A和液体B泵入Y形连接头
2. 混合液体流经温控装置进行反应
3. 反应液流经光谱仪进行在线表征
4. 实时记录光谱数据
"""

import json
import time
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime

from unilabos.devices.workstation.workstation_base import WorkstationBase, WorkflowStatus, WorkflowInfo
from unilabos.devices.workstation.azo.peristaltic_pump import PeristalticPump
from unilabos.devices.workstation.azo.temperature_controller import TemperatureController
from unilabos.devices.workstation.azo.spectrometer import SpectrometerDriver
from unilabos.registry.decorators import device, not_action, topic_config
from unilabos.utils.log import logger


@device(
    id="azo_workstation",
    category=["workstation"],
    description="偶氮反应微流控工作站，集成双蠕动泵、温控器和 IdeaOptics 光谱仪",
    display_name="偶氮反应工作站",
    icon="reaction_station.webp",
)
class AzoWorkstation(WorkstationBase):
    """偶氮反应工站

    设备组成：
    - pump_a: 蠕动泵A（液体A）
    - pump_b: 蠕动泵B（液体B）
    - temperature_controller: 温控器
    - spectrometer: 光谱仪

    通信架构：
    - 485串口（共享）: pump_a, pump_b, temperature_controller
    - IdeaOptics USB SDK: spectrometer
    """

    def __init__(
        self,
        deck: Optional[Any] = None,
        # 蠕动泵参数
        pump_a_address: int = 5,
        pump_b_address: int = 6,
        pump_a_flow_ratio: float = 1.0,  # TODO: 根据实际泵参数调整
        pump_b_flow_ratio: float = 1.0,  # TODO: 根据实际泵参数调整
        # 温控器参数
        temp_controller_address: int = 1,
        # 光谱仪参数
        spectrometer_dll_path: Optional[str] = None,
        spectrometer_integration_time: float = 50.0,
        spectrometer_average_count: int = 3,
        # 数据保存路径
        data_save_dir: Optional[str] = None,
        *args,
        **kwargs,
    ):
        super().__init__(deck=deck, *args, **kwargs)

        # 数据保存目录
        if data_save_dir is None:
            data_save_dir = str(Path.cwd() / "azo_data")
        self.data_save_dir = Path(data_save_dir)
        self.data_save_dir.mkdir(parents=True, exist_ok=True)

        # 初始化设备实例（串口通信函数将在post_init中注入）
        self.pump_a = PeristalticPump(
            pump_id="pump_a",
            modbus_address=pump_a_address,
            flow_to_rpm_ratio=pump_a_flow_ratio,
        )

        self.pump_b = PeristalticPump(
            pump_id="pump_b",
            modbus_address=pump_b_address,
            flow_to_rpm_ratio=pump_b_flow_ratio,
        )

        self.temperature_controller = TemperatureController(
            controller_id="temp_controller",
            modbus_address=temp_controller_address,
        )

        self.spectrometer = SpectrometerDriver(
            spectrometer_id="spectrometer",
            dll_path=spectrometer_dll_path,
            integration_time=spectrometer_integration_time,
            average_count=spectrometer_average_count,
        )

        # 工作流状态
        self.current_experiment_id = None
        self.spectrum_data_list = []  # 存储采集的光谱数据
        self._serial_485_ready = False

        # 定义支持的工作流
        self.supported_workflows = {
            "azo_reaction": WorkflowInfo(
                name="azo_reaction",
                description="偶氮反应工作流：设置流速和温度，持续采集光谱数据",
                estimated_duration=3600.0,
                required_materials=["液体A", "液体B"],
                output_product="偶氮反应产物",
                parameters_schema={
                    "flow_rate_a": {
                        "type": "number",
                        "description": "泵A流速 (ml/min)",
                        "minimum": 0.0,
                        "default": 1.0,
                    },
                    "flow_rate_b": {
                        "type": "number",
                        "description": "泵B流速 (ml/min)",
                        "minimum": 0.0,
                        "default": 1.0,
                    },
                    "temperature": {
                        "type": "number",
                        "description": "反应温度 (°C)",
                        "minimum": 0.0,
                        "maximum": 100.0,
                        "default": 25.0,
                    },
                    "duration": {
                        "type": "number",
                        "description": "反应时长 (秒)",
                        "minimum": 0.0,
                        "default": 3600.0,
                    },
                    "spectrum_interval": {
                        "type": "number",
                        "description": "光谱采集间隔 (秒)",
                        "minimum": 1.0,
                        "default": 10.0,
                    },
                },
            ),
        }

        logger.info("偶氮反应工站初始化完成")

    @not_action
    def post_init(self, ros_node) -> None:
        """ROS节点初始化后的回调

        在这里注入串口通信函数（通过代理模式）
        """
        super().post_init(ros_node)

        # 光谱仪不是COM串口设备，由 SpectrometerDriver 通过 IdeaOptics USB SDK 直接枚举连接。
        serial_485 = self._get_communication_device("serial_485")
        if serial_485 is None:
            logger.warning("未找到 serial_485 子设备，泵和温控器将无法进行RS485通信")
        else:
            serial_write, serial_read = self._get_binary_serial_io(serial_485)
            if serial_write is None or serial_read is None:
                logger.warning("serial_485 子设备缺少可用的二进制 write/read 接口")
            else:
                self.pump_a.serial_write = serial_write
                self.pump_a.serial_read = serial_read
                self.pump_b.serial_write = serial_write
                self.pump_b.serial_read = serial_read
                self.temperature_controller.serial_write = serial_write
                self.temperature_controller.serial_read = serial_read
                self._serial_485_ready = True
                logger.info("已将 serial_485 二进制串口接口注入到泵和温控器")

        # 连接光谱仪
        if not self.spectrometer.connect():
            logger.warning("光谱仪连接失败，将使用模拟模式")

        logger.info("偶氮反应工站 post_init 完成")

    def _get_communication_device(self, device_id: str):
        """从工作站节点中获取通信子设备实例。"""
        if hasattr(self._ros_node, "communication_node_id_to_instance"):
            device = self._ros_node.communication_node_id_to_instance.get(device_id)
            if device is not None:
                return device

        if hasattr(self._ros_node, "sub_devices"):
            device = self._ros_node.sub_devices.get(device_id)
            if device is not None:
                return device

        return None

    def _get_binary_serial_io(self, serial_device):
        """获取适合 Modbus RTU 的原始二进制串口读写接口。"""
        candidates = [
            getattr(serial_device, "driver_instance", None),
            getattr(serial_device, "ros_node_instance", None),
            serial_device,
        ]

        for candidate in candidates:
            if candidate is None:
                continue

            hardware_interface = getattr(candidate, "hardware_interface", None)
            if hardware_interface is not None and hasattr(hardware_interface, "write") and hasattr(hardware_interface, "read"):
                return hardware_interface.write, hardware_interface.read

            if hasattr(candidate, "write") and hasattr(candidate, "read"):
                return candidate.write, candidate.read

        return None, None

    # ============ 设备控制方法 ============

    def set_pump_flow_rates(self, flow_rate_a: float, flow_rate_b: float) -> bool:
        """设置两个泵的流速

        Args:
            flow_rate_a: 泵A流速 (ml/min)
            flow_rate_b: 泵B流速 (ml/min)

        Returns:
            是否设置成功
        """
        logger.info(f"设置泵流速: A={flow_rate_a} ml/min, B={flow_rate_b} ml/min")

        success_a = self.pump_a.set_flow_rate(flow_rate_a)
        success_b = self.pump_b.set_flow_rate(flow_rate_b)

        return success_a and success_b

    def stop_pumps(self) -> bool:
        """停止所有泵"""
        logger.info("停止所有泵")
        success_a = self.pump_a.stop()
        success_b = self.pump_b.stop()
        return success_a and success_b

    def set_temperature(self, temperature: float) -> bool:
        """设置反应温度

        Args:
            temperature: 目标温度 (°C)

        Returns:
            是否设置成功
        """
        logger.info(f"设置反应温度: {temperature}°C")
        return self.temperature_controller.set_target_temperature(temperature)

    def start_heating(self, temperature: Optional[float] = None) -> bool:
        """开始控温的兼容别名，可选地同时设置目标温度。"""
        if temperature is None:
            logger.info("启动温控加热")
        else:
            logger.info(f"启动温控加热，目标温度: {temperature}°C")
        return self.temperature_controller.start_heating(temperature)

    def start_temperature_control(self, temperature: Optional[float] = None) -> bool:
        """开始控温，可选地同时设置目标温度。"""
        if temperature is None:
            logger.info("开始温控")
        else:
            logger.info(f"开始温控，目标温度: {temperature}°C")
        return self.temperature_controller.start_temperature_control(temperature)

    def stop_heating(self, temperature: float = 25.0) -> bool:
        """停止加热，并将目标温度降到安全温度。"""
        logger.info(f"停止温控加热，安全目标温度: {temperature}°C")
        return self.temperature_controller.stop_heating(temperature)

    def read_temperature(self) -> Optional[float]:
        """读取当前温度

        Returns:
            当前温度 (°C)，失败返回None
        """
        return self.temperature_controller.read_actual_temperature()

    def acquire_spectrum(self) -> Optional[Dict[str, Any]]:
        """采集光谱数据

        Returns:
            光谱数据字典，失败返回None
        """
        return self.spectrometer.acquire_spectrum()

    def wait_seconds(self, seconds: float) -> bool:
        """固定等待一段时间。

        Args:
            seconds: 等待时长（秒）

        Returns:
            是否等待完成
        """
        if seconds < 0:
            logger.error(f"等待时长不能为负数: {seconds}")
            return False

        logger.info(f"等待 {seconds} 秒")
        end_time = time.time() + seconds
        while time.time() < end_time:
            if self.current_workflow_status == WorkflowStatus.STOPPING:
                logger.info("等待过程中收到工作流停止请求")
                return False
            time.sleep(min(0.1, max(0.0, end_time - time.time())))
        return True

    def wait_until_temperature_stable(
        self,
        target_temperature: float,
        tolerance: float = 1.0,
        timeout: float = 300.0,
        check_interval: float = 2.0,
    ) -> bool:
        """等待温度进入目标范围。

        Args:
            target_temperature: 目标温度 (°C)
            tolerance: 允许偏差 (°C)
            timeout: 最大等待时间（秒）
            check_interval: 检查间隔（秒）

        Returns:
            是否在超时前达到目标范围
        """
        if tolerance < 0:
            logger.error(f"温度容差不能为负数: {tolerance}")
            return False
        if timeout < 0:
            logger.error(f"温度等待超时时间不能为负数: {timeout}")
            return False
        if check_interval <= 0:
            logger.error(f"温度检查间隔必须大于 0: {check_interval}")
            return False

        logger.info(
            f"等待温度稳定: 目标={target_temperature}°C, "
            f"容差=±{tolerance}°C, 超时={timeout}秒"
        )
        start_time = time.time()
        while time.time() - start_time <= timeout:
            if self.current_workflow_status == WorkflowStatus.STOPPING:
                logger.info("等待温度稳定过程中收到工作流停止请求")
                return False

            actual_temperature = self.read_temperature()
            if actual_temperature is None:
                logger.warning("读取当前温度失败，继续等待")
            else:
                delta = abs(actual_temperature - target_temperature)
                logger.info(
                    f"当前温度: {actual_temperature}°C, "
                    f"目标温度: {target_temperature}°C, 偏差: {delta}°C"
                )
                if delta <= tolerance:
                    logger.info("温度已进入目标范围")
                    return True

            time.sleep(check_interval)

        logger.warning(f"等待温度稳定超时: {timeout}秒")
        return False

    def run_pumps_for(self, flow_rate_a: float, flow_rate_b: float, duration: float) -> bool:
        """启动两个泵并运行指定时间后停止。

        Args:
            flow_rate_a: 泵A流速 (ml/min)
            flow_rate_b: 泵B流速 (ml/min)
            duration: 运行时长（秒）

        Returns:
            是否完成指定时长运行并成功停止
        """
        if duration < 0:
            logger.error(f"泵运行时长不能为负数: {duration}")
            return False

        logger.info(f"运行泵: A={flow_rate_a} ml/min, B={flow_rate_b} ml/min, 时长={duration}秒")
        if not self.set_pump_flow_rates(flow_rate_a, flow_rate_b):
            logger.error("启动泵失败")
            return False

        completed = False
        try:
            completed = self.wait_seconds(duration)
        finally:
            stop_success = self.stop_pumps()
            if not stop_success:
                logger.error("停止泵失败")
            if not completed:
                logger.warning("泵未完成指定运行时长")
        return completed and stop_success

    def acquire_spectrum_series(self, duration: float, interval: float, save: bool = True) -> Dict[str, Any]:
        """在指定时间内周期性采集光谱。

        Args:
            duration: 采集总时长（秒）
            interval: 采集间隔（秒）
            save: 是否保存每次采集到的光谱数据

        Returns:
            采集结果摘要
        """
        if duration < 0:
            logger.error(f"光谱采集时长不能为负数: {duration}")
            return {"success": False, "error": "duration must be non-negative", "spectra_collected": 0}
        if interval <= 0:
            logger.error(f"光谱采集间隔必须大于 0: {interval}")
            return {"success": False, "error": "interval must be positive", "spectra_collected": 0}

        if self.current_experiment_id is None:
            self.current_experiment_id = f"azo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.info(f"开始周期性采集光谱: 时长={duration}秒, 间隔={interval}秒, 保存={save}")
        start_time = time.time()
        next_spectrum_time = start_time
        collected_count = 0
        saved_count = 0

        while time.time() - start_time <= duration:
            if self.current_workflow_status == WorkflowStatus.STOPPING:
                logger.info("光谱序列采集过程中收到工作流停止请求")
                break

            current_time = time.time()
            if current_time >= next_spectrum_time:
                spectrum_data = self.acquire_spectrum()
                if spectrum_data:
                    spectrum_data["experiment_id"] = self.current_experiment_id
                    spectrum_data["elapsed_time"] = current_time - start_time

                    actual_temp = self.read_temperature()
                    if actual_temp is not None:
                        spectrum_data["temperature_actual"] = actual_temp

                    self.spectrum_data_list.append(spectrum_data)
                    collected_count += 1

                    if save and self.save_spectrum(spectrum_data, format="csv"):
                        saved_count += 1

                    logger.info(f"周期性采集光谱 #{collected_count}")

                next_spectrum_time += interval

            time.sleep(0.1)

        return {
            "success": True,
            "experiment_id": self.current_experiment_id,
            "spectra_collected": collected_count,
            "spectra_saved": saved_count,
            "duration": duration,
            "interval": interval,
        }

    def save_spectrum(self, spectrum_data: Dict[str, Any], format: str = "csv") -> bool:
        """保存光谱数据

        Args:
            spectrum_data: 光谱数据字典
            format: 保存格式 ("csv" 或 "json")

        Returns:
            是否保存成功
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.current_experiment_id:
            filename = f"{self.current_experiment_id}_{timestamp}.{format}"
        else:
            filename = f"spectrum_{timestamp}.{format}"

        file_path = self.data_save_dir / filename

        if format == "csv":
            return self.spectrometer.save_spectrum_csv(spectrum_data, str(file_path))
        elif format == "json":
            return self.spectrometer.save_spectrum_json(spectrum_data, str(file_path))
        else:
            logger.error(f"不支持的保存格式: {format}")
            return False

    # ============ 工作流实现 ============

    def run_azo_reaction(
        self,
        flow_rate_a: float = 1.0,
        flow_rate_b: float = 1.0,
        temperature: float = 25.0,
        duration: float = 3600.0,
        spectrum_interval: float = 10.0,
    ) -> bool:
        """公开的偶氮反应动作。

        Args:
            flow_rate_a: 泵A流速 (ml/min)
            flow_rate_b: 泵B流速 (ml/min)
            temperature: 反应温度 (°C)
            duration: 反应时长（秒）
            spectrum_interval: 光谱采集间隔（秒）

        Returns:
            是否执行成功
        """
        return self._run_azo_reaction(
            {
                "flow_rate_a": flow_rate_a,
                "flow_rate_b": flow_rate_b,
                "temperature": temperature,
                "duration": duration,
                "spectrum_interval": spectrum_interval,
            }
        )

    def _execute_workflow_impl(self, workflow_name: str, parameters: Dict[str, Any]) -> bool:
        """执行工作流的具体实现"""
        if workflow_name == "azo_reaction":
            return self._run_azo_reaction(parameters)
        else:
            logger.error(f"不支持的工作流: {workflow_name}")
            return False

    def _stop_workflow_impl(self, emergency: bool = False) -> bool:
        """停止工作流的具体实现"""
        logger.info(f"停止偶氮反应工作流 (紧急停止: {emergency})")

        try:
            # 停止泵
            self.stop_pumps()

            # 停止加热
            self.temperature_controller.stop_heating()

            # 保存已采集的数据
            if self.spectrum_data_list and not emergency:
                self._save_experiment_summary()

            return True

        except Exception as e:
            logger.error(f"停止工作流失败: {e}")
            return False

    def _run_azo_reaction(self, params: Dict[str, Any]) -> bool:
        """运行偶氮反应工作流

        Args:
            params: 工作流参数
                - flow_rate_a: 泵A流速 (ml/min)
                - flow_rate_b: 泵B流速 (ml/min)
                - temperature: 反应温度 (°C)
                - duration: 反应时长 (秒)
                - spectrum_interval: 光谱采集间隔 (秒)

        Returns:
            是否执行成功
        """
        # 提取参数
        flow_rate_a = params.get("flow_rate_a", 1.0)
        flow_rate_b = params.get("flow_rate_b", 1.0)
        temperature = params.get("temperature", 25.0)
        duration = params.get("duration", 3600.0)
        spectrum_interval = params.get("spectrum_interval", 10.0)

        # 生成实验ID
        self.current_experiment_id = f"azo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.spectrum_data_list = []

        logger.info(
            f"开始偶氮反应实验 {self.current_experiment_id}:\n"
            f"  流速A: {flow_rate_a} ml/min\n"
            f"  流速B: {flow_rate_b} ml/min\n"
            f"  温度: {temperature}°C\n"
            f"  时长: {duration}秒\n"
            f"  光谱采集间隔: {spectrum_interval}秒"
        )

        try:
            # 1. 设置温度并打开输出使能
            if not self.start_temperature_control(temperature):
                logger.error("开始控温失败")
                return False

            # 等待温度稳定（简化版，实际可能需要PID控制）
            logger.info("等待温度稳定...")
            time.sleep(5.0)

            # 2. 启动泵
            if not self.set_pump_flow_rates(flow_rate_a, flow_rate_b):
                logger.error("启动泵失败")
                return False

            # 3. 循环采集光谱数据
            start_time = time.time()
            next_spectrum_time = start_time

            while time.time() - start_time < duration:
                # 检查是否需要停止
                if self.current_workflow_status == WorkflowStatus.STOPPING:
                    logger.info("工作流被请求停止")
                    break

                # 检查是否到达采集时间
                current_time = time.time()
                if current_time >= next_spectrum_time:
                    # 采集光谱
                    spectrum_data = self.acquire_spectrum()
                    if spectrum_data:
                        # 添加实验信息
                        spectrum_data["experiment_id"] = self.current_experiment_id
                        spectrum_data["elapsed_time"] = current_time - start_time
                        spectrum_data["flow_rate_a"] = flow_rate_a
                        spectrum_data["flow_rate_b"] = flow_rate_b
                        spectrum_data["temperature_setpoint"] = temperature

                        # 读取实际温度
                        actual_temp = self.read_temperature()
                        if actual_temp is not None:
                            spectrum_data["temperature_actual"] = actual_temp

                        # 保存数据
                        self.spectrum_data_list.append(spectrum_data)
                        self.save_spectrum(spectrum_data, format="csv")

                        logger.info(
                            f"采集光谱 #{len(self.spectrum_data_list)} "
                            f"(时间: {spectrum_data['elapsed_time']:.1f}s)"
                        )

                    # 计算下次采集时间
                    next_spectrum_time += spectrum_interval

                # 短暂休眠，避免CPU占用过高
                time.sleep(0.1)

            # 4. 停止设备
            self.stop_pumps()
            self.temperature_controller.stop_heating()

            # 5. 保存实验总结
            self._save_experiment_summary()

            logger.info(f"偶氮反应实验 {self.current_experiment_id} 完成，共采集 {len(self.spectrum_data_list)} 个光谱")
            return True

        except Exception as e:
            logger.error(f"偶氮反应工作流执行失败: {e}")
            # 紧急停止
            self.stop_pumps()
            self.temperature_controller.stop_heating()
            return False

    def _save_experiment_summary(self):
        """保存实验总结"""
        if not self.spectrum_data_list:
            return

        try:
            import json

            summary_file = self.data_save_dir / f"{self.current_experiment_id}_summary.json"

            summary = {
                "experiment_id": self.current_experiment_id,
                "start_time": self.spectrum_data_list[0]["timestamp"],
                "end_time": self.spectrum_data_list[-1]["timestamp"],
                "total_spectra": len(self.spectrum_data_list),
                "parameters": {
                    "flow_rate_a": self.spectrum_data_list[0].get("flow_rate_a"),
                    "flow_rate_b": self.spectrum_data_list[0].get("flow_rate_b"),
                    "temperature_setpoint": self.spectrum_data_list[0].get("temperature_setpoint"),
                },
                "data_files": [
                    f"{self.current_experiment_id}_{data['timestamp'].replace(' ', '_').replace(':', '')}.csv"
                    for data in self.spectrum_data_list
                ],
            }

            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)

            logger.info(f"实验总结已保存到 {summary_file}")

        except Exception as e:
            logger.error(f"保存实验总结失败: {e}")

    # ============ 状态查询 ============

    @not_action
    def get_workstation_status(self) -> Dict[str, Any]:
        """获取工作站整体状态

        Returns:
            状态字典
        """
        return {
            "workstation_id": self._ros_node.device_id if hasattr(self, '_ros_node') else "azo_workstation",
            "workflow_status": self.current_workflow_status.value,
            "current_experiment_id": self.current_experiment_id,
            "pump_a": self.pump_a.get_status(),
            "pump_b": self.pump_b.get_status(),
            "temperature_controller": self.temperature_controller.get_status(),
            "spectrometer": self.spectrometer.get_status(),
            "spectra_collected": len(self.spectrum_data_list),
        }

    @property
    @topic_config()
    def workstation_status(self) -> str:
        return json.dumps(self.get_workstation_status(), ensure_ascii=False)

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
    def pump_a_flow_rate(self) -> float:
        return self.pump_a.current_flow_rate

    @property
    @topic_config()
    def pump_b_flow_rate(self) -> float:
        return self.pump_b.current_flow_rate

    @property
    @topic_config()
    def temperature_setpoint(self) -> float:
        return self.temperature_controller.target_temperature

    @property
    @topic_config()
    def temperature_actual(self) -> float:
        return self.temperature_controller.actual_temperature

    @property
    @topic_config()
    def spectra_collected(self) -> int:
        return len(self.spectrum_data_list)

    @property
    @topic_config()
    def serial_485_ready(self) -> bool:
        return self._serial_485_ready

    def __del__(self):
        """析构函数，确保设备安全关闭"""
        try:
            self.stop_pumps()
            self.temperature_controller.stop_heating()
            self.spectrometer.disconnect()
        except:
            pass
