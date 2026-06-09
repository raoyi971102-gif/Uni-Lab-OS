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

import time
import asyncio
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

from pylabrobot.resources import Deck

from unilabos.devices.workstation.workstation_base import WorkstationBase, WorkflowStatus, WorkflowInfo
from unilabos.devices.workstation.azo.peristaltic_pump import PeristalticPump
from unilabos.devices.workstation.azo.temperature_controller import TemperatureController
from unilabos.devices.workstation.azo.spectrometer import SpectrometerDriver
from unilabos.utils.log import logger


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
        deck: Optional[Deck] = None,
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

    def __del__(self):
        """析构函数，确保设备安全关闭"""
        try:
            self.stop_pumps()
            self.temperature_controller.stop_heating()
            self.spectrometer.disconnect()
        except:
            pass
