"""偶氮工作站蠕动泵驱动。

通过步进电机驱动器 Modbus RTU 控制转速。对外接口使用流速 (mL/min)，
驱动内部按每转体积换算为 rpm。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from unilabos.devices.workstation.azo.modbus_rtu import build_write_single_register
from unilabos.registry.decorators import HardwareInterface, action, device, not_action, topic_config
from unilabos.utils.log import logger

if TYPE_CHECKING:
    from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode


@device(
    id="azo.peristaltic_pump",
    category=["pump_and_valve"],
    description="偶氮微流控蠕动泵，步进电机 Modbus 控制，对外使用 mL/min",
    display_name="偶氮蠕动泵",
    hardware_interface=HardwareInterface(
        name="hardware_interface",
        read="transact",
        write="transact",
    ),
)
class AzoPeristalticPump:
    """蠕动泵。转速寄存器 PA-53，<0 反转，=0 停止，>0 正转。"""

    _ros_node: "BaseROS2DeviceNode"
    REGISTER_ADDRESS = 53
    WRITE_RESPONSE_LENGTH = 8

    def __init__(
        self,
        device_id: Optional[str] = None,
        port: str = "serial_485",
        address: int = 5,
        volume_per_rev: float = 1.0,
        flow_to_rpm_ratio: Optional[float] = None,
        max_rpm: int = 600,
        simulate: bool = False,
        **kwargs,
    ):
        """
        Args:
            device_id[设备ID]: 泵实例 ID。
            port[通信设备]: 共享 RS485 通信设备 id，须与图文件中 serial_ 节点一致。
            address[Modbus站号]: 步进驱动器从站地址，泵A=5，泵B=6。
            volume_per_rev[每转体积]: 每转输送体积 (mL/rev)，用于流速换算。
            flow_to_rpm_ratio[流速转速比]: 可选，rpm = flow_rate * ratio；优先于每转体积。
            max_rpm[最大转速]: 转速限幅 (rpm)。
            simulate[模拟模式]: 为 True 时不发送 Modbus 指令。
        """
        self.device_id = device_id or f"pump_{address}"
        self.hardware_interface = port
        self.address = int(address)
        self.volume_per_rev = float(volume_per_rev)
        self.flow_to_rpm_ratio = None if flow_to_rpm_ratio is None else float(flow_to_rpm_ratio)
        self.max_rpm = int(max_rpm)
        self.simulate = bool(simulate)
        self.data: Dict[str, Any] = {
            "status": "Idle",
            "speed": 0.0,
            "rpm": 0,
        }

    @not_action
    def post_init(self, ros_node: "BaseROS2DeviceNode") -> None:
        self._ros_node = ros_node
        self.device_id = getattr(ros_node, "device_id", self.device_id)

    @not_action
    def transact(self, command: bytes, expected_length: int = 8) -> bytes:
        """占位方法，启动后由 serial_485.transact 替换。"""
        raise RuntimeError(f"蠕动泵 {self.device_id}: RS485 通信代理尚未注入")

    @not_action
    def flow_rate_to_rpm(self, flow_rate: float) -> int:
        """将流速 (mL/min) 换算为转速 (rpm)。

        优先使用 flow_to_rpm_ratio：rpm = flow_rate * ratio。
        否则使用每转体积：rpm = flow_rate / volume_per_rev。
        """
        if self.flow_to_rpm_ratio is not None:
            rpm = flow_rate * self.flow_to_rpm_ratio
        else:
            if self.volume_per_rev == 0:
                raise ValueError("volume_per_rev 不能为 0")
            rpm = flow_rate / self.volume_per_rev
        rpm_int = int(round(rpm))
        return max(-self.max_rpm, min(self.max_rpm, rpm_int))

    @not_action
    def rpm_to_flow_rate(self, rpm: int) -> float:
        """将转速换算回流速 (mL/min)。"""
        if self.flow_to_rpm_ratio is not None:
            if self.flow_to_rpm_ratio == 0:
                return 0.0
            return rpm / self.flow_to_rpm_ratio
        return rpm * self.volume_per_rev

    def _send_rpm(self, rpm: int) -> Dict[str, Any]:
        if self.simulate:
            self.data["rpm"] = rpm
            self.data["speed"] = abs(self.rpm_to_flow_rate(rpm))
            self.data["status"] = "Idle" if rpm == 0 else "Running"
            return {"success": True, "message": f"模拟设置转速 {rpm} rpm", "rpm": rpm}

        cmd = build_write_single_register(self.address, self.REGISTER_ADDRESS, rpm)
        try:
            response = self.transact(cmd, self.WRITE_RESPONSE_LENGTH)
        except Exception as exc:
            self.data["status"] = "Error"
            logger.error(f"蠕动泵 {self.device_id}: 设置转速失败 - {exc}")
            return {"success": False, "error": str(exc), "message": "发送转速指令失败"}

        if not response:
            self.data["status"] = "Error"
            logger.error(f"蠕动泵 {self.device_id}: 设置转速无响应, 命令={cmd.hex()}")
            return {"success": False, "error": "no_response", "message": "设置转速无响应"}

        self.data["rpm"] = rpm
        self.data["speed"] = abs(self.rpm_to_flow_rate(rpm))
        self.data["status"] = "Idle" if rpm == 0 else "Running"
        logger.info(f"蠕动泵 {self.device_id}: 转速={rpm} rpm, 流速={self.data['speed']} mL/min")
        return {
            "success": True,
            "message": f"转速已设置为 {rpm} rpm",
            "rpm": rpm,
            "speed": self.data["speed"],
        }

    @action(description="按流速启动蠕动泵")
    def start(self, flow_rate: float = 1.0) -> Dict[str, Any]:
        """
        Args:
            flow_rate[流速]: 目标流速 (mL/min)，负值反转。
        """
        return self.set_speed(flow_rate)

    @action(description="停止蠕动泵")
    def stop(self) -> Dict[str, Any]:
        return self._send_rpm(0)

    @action(description="按流速设置泵速")
    def set_speed(self, flow_rate: float = 0.0) -> Dict[str, Any]:
        """
        Args:
            flow_rate[流速]: 目标流速 (mL/min)，负值反转。
        """
        try:
            rpm = self.flow_rate_to_rpm(flow_rate)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "message": "流速换算失败"}
        logger.info(f"蠕动泵 {self.device_id}: 流速 {flow_rate} mL/min -> {rpm} rpm")
        result = self._send_rpm(rpm)
        result["flow_rate"] = flow_rate
        return result

    @action(description="直接设置步进电机转速")
    def set_rpm(self, rpm: int = 0) -> Dict[str, Any]:
        """
        Args:
            rpm[转速]: 目标转速 (rpm)，<0 反转，=0 停止，>0 正转。
        """
        rpm = max(-self.max_rpm, min(self.max_rpm, int(rpm)))
        return self._send_rpm(rpm)

    @property
    @topic_config()
    def status(self) -> str:
        return self.data["status"]

    @property
    @topic_config(period=2.0)
    def speed(self) -> float:
        return float(self.data["speed"])

    @property
    @topic_config(period=2.0)
    def rpm(self) -> int:
        return int(self.data["rpm"])
