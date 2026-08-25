"""RS485/Modbus RTU 总线模拟。

该模块用于不接真实串口时模拟偶氮工作站的两台泵和温控器。
接口刻意保持为 pyserial 风格的 ``write`` / ``read``，便于直接注入现有驱动。
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


def crc16_modbus(data: bytes | bytearray) -> int:
    """计算 Modbus RTU CRC16。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def append_crc(frame: bytes | bytearray) -> bytes:
    crc = crc16_modbus(frame)
    return bytes(frame) + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _int16_from_register(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


@dataclass
class SimulatedRS485Bus:
    """偶氮工作站 RS485 总线模拟器。

    支持当前驱动用到的最小 Modbus 功能：
    - 泵：功能码 0x06，站号 5/6，寄存器 0x0035，写 int16 rpm。
    - 温控：功能码 0x10，站号 1，寄存器 0x1000，写 int32 目标温度。
    - 温控：功能码 0x10，站号 1，寄存器 0x1100，写 uint16 输出使能；兼容 0x06。
    - 温控：功能码 0x03，站号 1，寄存器 0x1002，读 int32 实际温度。
    """

    pump_addresses: tuple[int, int] = (5, 6)
    temperature_address: int = 1
    ambient_temperature: float = 25.0
    temperature_scale: int = 100000
    thermal_rate_deg_per_sec: float = 1.5
    response_delay: float = 0.0
    strict_crc: bool = True
    pump_echo_response: bool = False
    pump_rpm: Dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.pump_rpm = {address: 0 for address in self.pump_addresses}
        self.target_temperature = float(self.ambient_temperature)
        self.actual_temperature = float(self.ambient_temperature)
        self.output_enabled = False
        self.last_update = time.time()
        self._read_buffer = bytearray()
        self.history: list[bytes] = []

    def write(self, data: bytes | bytearray) -> int:
        """接收一帧 Modbus RTU 请求并生成响应。"""
        frame = bytes(data)
        self.history.append(frame)
        response = self._handle_frame(frame)
        if self.response_delay > 0:
            time.sleep(self.response_delay)
        if response:
            self._read_buffer.extend(response)
        return len(frame)

    def read(self, size: Optional[int] = 1) -> bytes:
        """读取模拟响应缓冲区。"""
        if size is None or size <= 0:
            size = len(self._read_buffer)
        if not self._read_buffer:
            return b""
        chunk = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return chunk

    def read_until(self, terminator: bytes = b"\n") -> bytes:
        """兼容 pyserial 的 read_until。Modbus 响应无换行时返回当前完整缓冲。"""
        if not self._read_buffer:
            return b""
        idx = self._read_buffer.find(terminator)
        if idx >= 0:
            size = idx + len(terminator)
        else:
            size = len(self._read_buffer)
        return self.read(size)

    def flush(self) -> None:
        self._read_buffer.clear()

    def close(self) -> None:
        self.flush()

    def snapshot(self) -> dict:
        self._update_temperature()
        return {
            "pump_rpm": dict(self.pump_rpm),
            "target_temperature": self.target_temperature,
            "actual_temperature": self.actual_temperature,
            "output_enabled": self.output_enabled,
            "pending_response_bytes": len(self._read_buffer),
        }

    def _handle_frame(self, frame: bytes) -> bytes:
        if len(frame) < 4:
            return b""
        if self.strict_crc and not self._valid_crc(frame):
            return b""

        address = frame[0]
        function = frame[1]
        if address in self.pump_rpm:
            return self._handle_pump_frame(address, function, frame)
        if address == self.temperature_address:
            return self._handle_temperature_frame(function, frame)
        return self._exception(address, function, 0x02)

    def _handle_pump_frame(self, address: int, function: int, frame: bytes) -> bytes:
        if function != 0x06 or len(frame) < 8:
            return self._exception(address, function, 0x01)
        register = (frame[2] << 8) | frame[3]
        if register != 0x0035:
            return self._exception(address, function, 0x02)
        raw_value = (frame[4] << 8) | frame[5]
        self.pump_rpm[address] = _int16_from_register(raw_value)
        # 当前真实 PeristalticPump 驱动不会读取写寄存器应答；
        # 默认不缓存泵应答，避免污染后续温控器读写。
        return frame[:8] if self.pump_echo_response else b""

    def _handle_temperature_frame(self, function: int, frame: bytes) -> bytes:
        if function == 0x10:
            register = (frame[2] << 8) | frame[3] if len(frame) >= 4 else -1
            if register == 0x1100:
                return self._write_temperature_output_enable_fc10(frame)
            return self._write_temperature_target(frame)
        if function == 0x06:
            return self._write_temperature_output_enable(frame)
        if function == 0x03:
            return self._read_temperature(frame)
        return self._exception(self.temperature_address, function, 0x01)

    def _write_temperature_target(self, frame: bytes) -> bytes:
        if len(frame) < 13:
            return self._exception(self.temperature_address, 0x10, 0x03)
        register = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        byte_count = frame[6]
        if register != 0x1000 or count != 2 or byte_count != 4:
            return self._exception(self.temperature_address, 0x10, 0x02)
        raw_value = struct.unpack(">i", frame[7:11])[0]
        self._update_temperature()
        self.target_temperature = raw_value / float(self.temperature_scale)
        return append_crc(frame[:6])

    def _write_temperature_output_enable(self, frame: bytes) -> bytes:
        if len(frame) < 8:
            return self._exception(self.temperature_address, 0x06, 0x03)
        register = (frame[2] << 8) | frame[3]
        value = (frame[4] << 8) | frame[5]
        if register != 0x1100:
            return self._exception(self.temperature_address, 0x06, 0x02)
        self._update_temperature()
        self.output_enabled = bool(value)
        return frame[:8]

    def _write_temperature_output_enable_fc10(self, frame: bytes) -> bytes:
        if len(frame) < 11:
            return self._exception(self.temperature_address, 0x10, 0x03)
        count = (frame[4] << 8) | frame[5]
        byte_count = frame[6]
        if count != 1 or byte_count != 2:
            return self._exception(self.temperature_address, 0x10, 0x03)
        value = (frame[7] << 8) | frame[8]
        self._update_temperature()
        self.output_enabled = bool(value)
        return append_crc(frame[:6])

    def _read_temperature(self, frame: bytes) -> bytes:
        if len(frame) < 8:
            return self._exception(self.temperature_address, 0x03, 0x03)
        register = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        if register != 0x1002 or count != 2:
            return self._exception(self.temperature_address, 0x03, 0x02)
        self._update_temperature()
        raw = int(self.actual_temperature * self.temperature_scale)
        payload = bytes([self.temperature_address, 0x03, 0x04]) + struct.pack(">i", raw)
        return append_crc(payload)

    def _update_temperature(self) -> None:
        now = time.time()
        dt = max(0.0, now - self.last_update)
        self.last_update = now
        target = self.target_temperature if self.output_enabled else self.ambient_temperature
        delta = target - self.actual_temperature
        max_step = self.thermal_rate_deg_per_sec * dt
        if abs(delta) <= max_step:
            self.actual_temperature = target
        elif delta > 0:
            self.actual_temperature += max_step
        else:
            self.actual_temperature -= max_step

    @staticmethod
    def _valid_crc(frame: bytes) -> bool:
        if len(frame) < 4:
            return False
        expected = frame[-2] | (frame[-1] << 8)
        return crc16_modbus(frame[:-2]) == expected

    @staticmethod
    def _exception(address: int, function: int, code: int) -> bytes:
        return append_crc(bytes([address, function | 0x80, code]))
