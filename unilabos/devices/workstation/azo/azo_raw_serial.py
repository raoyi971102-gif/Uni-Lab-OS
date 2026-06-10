"""
偶氮工作站专用二进制串口设备。

该设备用于 RS485 Modbus RTU 场景，直接暴露 pyserial 的 raw
write/read 接口，避免经过内置 serial 文本行协议节点。
"""

from typing import Optional

from serial import Serial, SerialException

from unilabos.registry.decorators import device, not_action, topic_config
from unilabos.utils.log import logger


@device(
    id="azo_raw_serial",
    category=["communication_devices"],
    description="偶氮工作站专用 RS485 二进制串口通信设备",
    display_name="Azo RS485 Raw Serial",
)
class AzoRawSerial:
    """适配偶氮工作站 Modbus RTU 的原始串口设备。"""

    def __init__(
        self,
        port: str,
        baudrate: int = 38400,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int | float = 1,
        timeout: Optional[float] = 1.0,
        **kwargs,
    ):
        """
        初始化串口。

        Args:
            port[串口号]: 串口名称，例如 COM6。
            baudrate[波特率]: 串口波特率。
            bytesize[数据位]: 数据位。
            parity[校验位]: 校验位。
            stopbits[停止位]: 停止位。
            timeout[超时秒数]: 读取超时时间。
        """
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self._status = "Offline"

        try:
            self.hardware_interface = Serial(
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=timeout,
            )
            self._status = "Idle"
            logger.info(f"Azo RS485 串口已打开: {port}, baudrate={baudrate}")
        except (OSError, SerialException) as exc:
            self._status = "Error"
            raise RuntimeError(f"Azo RS485 串口打开失败: {port}, baudrate={baudrate}") from exc

    @not_action
    def write(self, data: bytes | bytearray) -> int:
        """写入原始二进制数据。"""
        self._status = "Running"
        try:
            return self.hardware_interface.write(bytes(data))
        finally:
            self._status = "Idle"

    @not_action
    def read(self, size: int = 1) -> bytes:
        """读取指定长度的原始二进制数据。"""
        return self.hardware_interface.read(size)

    @not_action
    def read_until(self, terminator: bytes = b"\n") -> bytes:
        """读取到指定终止符。"""
        return self.hardware_interface.read_until(terminator)

    @not_action
    def close(self) -> None:
        if getattr(self, "hardware_interface", None) is not None:
            self.hardware_interface.close()
            self._status = "Offline"

    @property
    @topic_config()
    def status(self) -> str:
        return self._status

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
