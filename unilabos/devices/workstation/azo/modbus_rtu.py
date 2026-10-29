"""Modbus RTU 帧编解码工具。"""

from __future__ import annotations

import struct
from typing import Optional


def crc16(data: bytes | bytearray) -> int:
    """计算 Modbus CRC16（多项式 0xA001）。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def append_crc(payload: bytes | bytearray) -> bytes:
    """在 Modbus 载荷末尾追加小端 CRC16。"""
    crc = crc16(payload)
    return bytes(payload) + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def verify_crc(frame: bytes) -> bool:
    """校验完整帧的 CRC16。"""
    if len(frame) < 4:
        return False
    expected = frame[-2] | (frame[-1] << 8)
    return expected == crc16(frame[:-2])


def to_uint16(value: int) -> int:
    """将有符号 int16 转为 16 位无符号表示。"""
    if value < 0:
        value = (1 << 16) + value
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"uint16 超出范围: {value}")
    return value


def from_uint16(value: int) -> int:
    """将 16 位无符号值还原为有符号 int16。"""
    value &= 0xFFFF
    return value - (1 << 16) if value >= 0x8000 else value


def build_write_single_register(slave: int, register: int, value: int) -> bytes:
    """功能码 0x06：写单个保持寄存器（int16，支持负数）。"""
    raw = to_uint16(value)
    payload = bytes(
        (
            slave & 0xFF,
            0x06,
            (register >> 8) & 0xFF,
            register & 0xFF,
            (raw >> 8) & 0xFF,
            raw & 0xFF,
        )
    )
    return append_crc(payload)


def build_write_registers_uint16(slave: int, register: int, value: int) -> bytes:
    """功能码 0x10：从指定地址写入 1 个寄存器（uint16）。"""
    raw = to_uint16(value)
    payload = bytes(
        (
            slave & 0xFF,
            0x10,
            (register >> 8) & 0xFF,
            register & 0xFF,
            0x00,
            0x01,
            0x02,
            (raw >> 8) & 0xFF,
            raw & 0xFF,
        )
    )
    return append_crc(payload)


def build_write_registers_int32(slave: int, register: int, value: int) -> bytes:
    """功能码 0x10：从指定地址写入 2 个寄存器（大端 int32）。"""
    data = struct.pack(">i", int(value))
    payload = bytes(
        (
            slave & 0xFF,
            0x10,
            (register >> 8) & 0xFF,
            register & 0xFF,
            0x00,
            0x02,
            0x04,
        )
    ) + data
    return append_crc(payload)


def build_read_holding_registers(slave: int, register: int, count: int = 2) -> bytes:
    """功能码 0x03：读保持寄存器。"""
    payload = bytes(
        (
            slave & 0xFF,
            0x03,
            (register >> 8) & 0xFF,
            register & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        )
    )
    return append_crc(payload)


def normalize_response(response) -> bytes:
    """统一串口返回值为 bytes，兼容 bytes / bytearray / hex 字符串。"""
    if response is None:
        return b""
    if isinstance(response, (bytes, bytearray)):
        return bytes(response)
    if isinstance(response, str):
        text = response.strip().replace(" ", "")
        try:
            return bytes.fromhex(text)
        except ValueError:
            return response.encode("latin1", errors="ignore")
    return bytes(response)


def extract_frame(
    response: bytes,
    slave: int,
    function_code: int,
    min_length: int,
) -> Optional[bytes]:
    """从可能含噪声的响应中提取第一个 CRC 合法的 RTU 帧。"""
    raw = normalize_response(response)
    if len(raw) < min_length:
        return None

    for start in range(0, len(raw) - min_length + 1):
        candidate = raw[start : start + min_length]
        if candidate[0] != slave or candidate[1] != function_code:
            continue
        if verify_crc(candidate):
            return candidate

        # 读响应长度随字节数变化，尝试按声明的字节数截取
        if function_code == 0x03 and len(raw) - start >= 5:
            byte_count = raw[start + 2]
            frame_len = 5 + byte_count
            if start + frame_len <= len(raw):
                frame = raw[start : start + frame_len]
                if frame[0] == slave and frame[1] == function_code and verify_crc(frame):
                    return frame
    return None


def parse_read_int32(frame: bytes) -> Optional[int]:
    """解析 0x03 读响应中的大端 int32（4 字节数据）。"""
    if frame is None or len(frame) < 9 or frame[2] < 4:
        return None
    return struct.unpack(">i", frame[3:7])[0]
