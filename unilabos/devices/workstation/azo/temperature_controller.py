"""偶氮工作站温控器驱动。

TEC107/115，Modbus RTU。目标/实际温度寄存器为 int32，单位 0.00001 °C。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from unilabos.devices.workstation.azo.modbus_rtu import (
    build_read_holding_registers,
    build_write_registers_int32,
    build_write_registers_uint16,
    build_write_single_register,
    extract_frame,
    parse_read_int32,
)
from unilabos.registry.decorators import HardwareInterface, action, device, not_action, topic_config
from unilabos.utils.log import logger

if TYPE_CHECKING:
    from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode


@device(
    id="azo.temperature_controller",
    category=["temperature"],
    description="偶氮微流控温控器 TEC107/115，Modbus RTU",
    display_name="偶氮温控器",
    hardware_interface=HardwareInterface(
        name="hardware_interface",
        read="transact",
        write="transact",
    ),
)
class AzoTemperatureController:
    """温控器。与蠕动泵共享 RS485，站号默认为 1。"""

    _ros_node: "BaseROS2DeviceNode"
    TARGET_TEMP_REGISTER = 4096  # 0x1000
    ACTUAL_TEMP_REGISTER = 4098  # 0x1002
    OUTPUT_ENABLE_REGISTER = 0x1100
    WRITE_RESPONSE_LENGTH = 8
    READ_RESPONSE_LENGTH = 9

    def __init__(
        self,
        device_id: Optional[str] = None,
        port: str = "serial_485",
        address: int = 1,
        temperature_scale: int = 100000,
        simulate: bool = False,
        **kwargs,
    ):
        """
        Args:
            device_id[设备ID]: 温控器实例 ID。
            port[通信设备]: 共享 RS485 通信设备 id。
            address[Modbus站号]: 温控器从站地址，默认 1。
            temperature_scale[温度倍率]: 寄存器原始值 / scale = °C，固定为 /100000。
            simulate[模拟模式]: 为 True 时不发送 Modbus 指令。
        """
        self.device_id = device_id or "temp_controller"
        self.hardware_interface = port
        self.address = int(address)
        self.temperature_scale = int(temperature_scale)
        self.simulate = bool(simulate)
        self.data: Dict[str, Any] = {
            "status": "Idle",
            "temp": 25.0,
            "temp_target": 25.0,
            "output_enabled": False,
        }

    @not_action
    def post_init(self, ros_node: "BaseROS2DeviceNode") -> None:
        self._ros_node = ros_node
        self.device_id = getattr(ros_node, "device_id", self.device_id)

    @not_action
    def transact(self, command: bytes, expected_length: int = 8) -> bytes:
        """占位方法，启动后由 serial_485.transact 替换。"""
        raise RuntimeError(f"温控器 {self.device_id}: RS485 通信代理尚未注入")

    def _temp_to_raw(self, temp: float) -> int:
        return int(round(temp * self.temperature_scale))

    def _raw_to_temp(self, raw: int) -> float:
        return raw / float(self.temperature_scale)

    def _write_target_temperature(self, temperature: float) -> Dict[str, Any]:
        if self.simulate:
            self.data["temp_target"] = temperature
            return {"success": True, "message": f"模拟设置目标温度 {temperature}°C"}

        cmd = build_write_registers_int32(
            self.address, self.TARGET_TEMP_REGISTER, self._temp_to_raw(temperature)
        )
        try:
            response = self.transact(cmd, self.WRITE_RESPONSE_LENGTH)
        except Exception as exc:
            self.data["status"] = "Error"
            logger.error(f"温控器 {self.device_id}: 写目标温度失败 - {exc}")
            return {"success": False, "error": str(exc), "message": "写目标温度失败"}

        if not response:
            self.data["status"] = "Error"
            return {"success": False, "error": "no_response", "message": "写目标温度无响应"}

        frame = extract_frame(response, self.address, 0x10, self.WRITE_RESPONSE_LENGTH)
        if frame is None:
            self.data["status"] = "Error"
            logger.error(
                f"温控器 {self.device_id}: 写目标温度应答无效, 命令={cmd.hex()} 响应={response.hex()}"
            )
            return {"success": False, "error": "invalid_response", "message": "写目标温度应答无效"}

        self.data["temp_target"] = temperature
        return {"success": True, "message": f"目标温度已设置为 {temperature}°C", "temp_target": temperature}

    def _write_output_enable(self, enabled: bool) -> Dict[str, Any]:
        """写输出使能寄存器 0x1100：1=开始控温，0=停止控温。

        优先用功能码 0x10（与目标温度相同），失败再回退 0x06。
        """
        value = 1 if enabled else 0
        if self.simulate:
            self.data["output_enabled"] = enabled
            return {"success": True, "message": f"模拟输出使能={enabled}", "enabled": enabled}

        attempts = (
            (build_write_registers_uint16(self.address, self.OUTPUT_ENABLE_REGISTER, value), 0x10),
            (build_write_single_register(self.address, self.OUTPUT_ENABLE_REGISTER, value), 0x06),
        )
        last_error = "写输出使能失败"
        for cmd, function_code in attempts:
            try:
                response = self.transact(cmd, self.WRITE_RESPONSE_LENGTH)
            except Exception as exc:
                last_error = str(exc)
                logger.error(f"温控器 {self.device_id}: 写输出使能失败(0x{function_code:02X}) - {exc}")
                continue

            if not response:
                last_error = "no_response"
                logger.warning(
                    f"温控器 {self.device_id}: 写输出使能无响应(0x{function_code:02X}), 命令={cmd.hex()}"
                )
                continue

            frame = extract_frame(response, self.address, function_code, self.WRITE_RESPONSE_LENGTH)
            if frame is None:
                last_error = "invalid_response"
                logger.warning(
                    f"温控器 {self.device_id}: 写输出使能应答无效(0x{function_code:02X}), "
                    f"命令={cmd.hex()} 响应={response.hex()}"
                )
                continue

            self.data["output_enabled"] = enabled
            logger.info(
                f"温控器 {self.device_id}: 输出使能={value} (功能码 0x{function_code:02X})"
            )
            return {"success": True, "enabled": enabled, "message": f"输出使能已设置为 {value}"}

        self.data["status"] = "Error"
        return {"success": False, "error": last_error, "message": "写输出使能失败"}

    @action(description="只设置目标温度，不改变输出使能")
    def set_target_temperature(self, temp: float = 25.0) -> Dict[str, Any]:
        """
        Args:
            temp[目标温度]: 目标温度 (°C)。
        """
        return self._write_target_temperature(temp)

    @action(description="打开输出使能，开始控温")
    def enable_output(self) -> Dict[str, Any]:
        result = self._write_output_enable(True)
        if result.get("success"):
            self.data["status"] = "Running"
            logger.info(f"温控器 {self.device_id}: 已打开输出使能")
        return result

    @action(description="关闭输出使能，停止控温")
    def disable_output(self) -> Dict[str, Any]:
        result = self._write_output_enable(False)
        if result.get("success"):
            self.data["status"] = "Idle"
            logger.info(f"温控器 {self.device_id}: 已关闭输出使能")
        return result

    @action(description="设置目标温度并打开输出使能")
    def set_temperature(self, temp: float = 25.0) -> Dict[str, Any]:
        """
        Args:
            temp[目标温度]: 目标温度 (°C)。
        """
        return self.start(temp)

    @action(description="开始控温：写目标温度并打开输出使能")
    def start(self, temp: float = 25.0) -> Dict[str, Any]:
        """
        Args:
            temp[目标温度]: 目标温度 (°C)。
        """
        write_result = self._write_target_temperature(temp)
        if not write_result.get("success"):
            return write_result

        enable_result = self.enable_output()
        if not enable_result.get("success"):
            return enable_result

        logger.info(f"温控器 {self.device_id}: 开始控温 {temp}°C")
        return {
            "success": True,
            "message": f"已开始控温至 {temp}°C",
            "temp_target": temp,
            "output_enabled": True,
        }

    @action(description="停止控温：只关闭输出使能，不改目标温度")
    def stop(self) -> Dict[str, Any]:
        result = self.disable_output()
        if not result.get("success"):
            return result
        logger.info(f"温控器 {self.device_id}: 已停止控温")
        return {
            "success": True,
            "message": "已关闭输出使能，停止控温",
            "output_enabled": False,
            "temp_target": self.data["temp_target"],
        }

    @action(description="读取实际温度")
    def read_value(self) -> Dict[str, Any]:
        if self.simulate:
            self.data["temp"] = self.data["temp_target"]
            return {
                "success": True,
                "temp": self.data["temp"],
                "message": f"模拟温度 {self.data['temp']}°C",
            }

        cmd = build_read_holding_registers(self.address, self.ACTUAL_TEMP_REGISTER, count=2)
        try:
            response = self.transact(cmd, self.READ_RESPONSE_LENGTH)
        except Exception as exc:
            self.data["status"] = "Error"
            logger.error(f"温控器 {self.device_id}: 读温度失败 - {exc}")
            return {"success": False, "error": str(exc), "message": "读温度失败"}

        frame = extract_frame(response, self.address, 0x03, self.READ_RESPONSE_LENGTH)
        raw = parse_read_int32(frame) if frame is not None else None
        if raw is None:
            logger.error(f"温控器 {self.device_id}: 读温度解析失败, 响应={response.hex() if response else ''}")
            return {"success": False, "error": "parse_error", "message": "读温度解析失败"}

        temperature = self._raw_to_temp(raw)
        self.data["temp"] = temperature
        return {
            "success": True,
            "temp": temperature,
            "message": f"实际温度 {temperature}°C",
        }

    @property
    @topic_config()
    def status(self) -> str:
        return self.data["status"]

    @property
    @topic_config(period=2.0)
    def temp(self) -> float:
        return float(self.data["temp"])

    @property
    @topic_config(period=2.0)
    def temp_target(self) -> float:
        return float(self.data["temp_target"])

    @property
    @topic_config()
    def output_enabled(self) -> bool:
        return bool(self.data.get("output_enabled", False))
