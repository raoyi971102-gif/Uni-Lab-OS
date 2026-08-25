"""偶氮工作站 RS485 二进制串口通信设备。

泵和温控器通过 Modbus RTU 共享同一 485 总线，内置 serial 文本节点无法使用。
本设备暴露带锁的 transact()，由工作站硬件代理注入到各子设备。
"""

from __future__ import annotations

from threading import Lock
from typing import Optional

from serial import Serial, SerialException

from unilabos.registry.decorators import HardwareInterface, device, not_action, topic_config
from unilabos.utils.log import logger


@device(
    id="azo_rs485_serial",
    category=["communication_devices"],
    description="偶氮工作站 RS485 二进制串口，供蠕动泵与温控器共享",
    display_name="偶氮 RS485 串口",
    hardware_interface=HardwareInterface(
        name="hardware_interface",
        read="transact",
        write="transact",
    ),
)
class AzoRs485Serial:
    """Modbus RTU 原始二进制串口。实例 id 必须以 serial_ 开头。"""

    def __init__(
        self,
        port: str,
        baudrate: int = 38400,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int | float = 1,
        timeout: Optional[float] = 1.0,
        simulate: bool = False,
        **kwargs,
    ):
        """
        Args:
            port[串口号]: 串口名称，例如 COM10。
            baudrate[波特率]: 串口波特率，默认 38400。
            bytesize[数据位]: 数据位，默认 8。
            parity[校验位]: 校验位，默认 N。
            stopbits[停止位]: 停止位，默认 1。
            timeout[超时秒数]: 读取超时时间。
            simulate[模拟模式]: 为 True 时不打开真实串口。
        """
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.simulate = simulate
        self._lock = Lock()
        self._status = "Offline"
        self.hardware_interface = None

        if self.simulate:
            self._status = "Idle"
            logger.info(f"Azo RS485 串口进入模拟模式: {port}")
            return

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
    def transact(self, command: bytes, expected_length: int = 8) -> bytes:
        """发送一帧并读取响应，整段事务加锁，避免 485 总线冲突。"""
        if self.simulate:
            return bytes(command)[:expected_length] if expected_length > 0 else b""

        if self.hardware_interface is None:
            raise RuntimeError("RS485 串口未打开")

        payload = bytes(command)
        with self._lock:
            self._status = "Running"
            try:
                self.hardware_interface.reset_input_buffer()
                self.hardware_interface.write(payload)
                if expected_length <= 0:
                    return b""
                return self.hardware_interface.read(expected_length)
            finally:
                self._status = "Idle"

    @not_action
    def close(self) -> None:
        serial_port = getattr(self, "hardware_interface", None)
        if serial_port is not None and getattr(serial_port, "is_open", False):
            serial_port.close()
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
