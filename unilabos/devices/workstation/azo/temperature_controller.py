"""
偶氮工站 - 温控器驱动
Temperature Controller Driver for Azo Workstation

通过 Modbus 协议控制 TEC107/115 温控器
支持温度设定和实时温度读取
"""

import time
import struct
from typing import Dict, Any, Optional
from unilabos.utils.log import logger


class TemperatureController:
    """温控器驱动类

    通过 Modbus 协议控制温控器，实现温度设定和监测

    通信参数：
    - 波特率: 38400
    - 数据位: 8
    - 校验位: N (无校验)
    - 停止位: 1
    - Modbus站号: 1
    - 目标温度寄存器: 4096 (0x1000)，Modbus地址为 40001+4096=44097
    - 实际温度寄存器: 4098 (0x1002)，Modbus地址为 40001+4098=44099
    - 数据格式: int32 (大端序)
    - 单位: /10000 (例如: 250000 表示 25.0000°C)
    """

    def __init__(
        self,
        controller_id: str,
        modbus_address: int = 1,
        serial_write_func=None,
        serial_read_func=None,
        **kwargs
    ):
        """初始化温控器

        Args:
            controller_id: 温控器的唯一标识符
            modbus_address: Modbus从站地址 (默认: 1)
            serial_write_func: 串口写入函数（由workstation注入）
            serial_read_func: 串口读取函数（由workstation注入）
        """
        self.controller_id = controller_id
        self.modbus_address = modbus_address

        # 寄存器地址
        self.target_temp_register = 4096  # 0x1000 - 目标温度
        self.actual_temp_register = 4098  # 0x1002 - 实际温度

        # 串口通信函数（通过代理模式注入）
        self.serial_write = serial_write_func
        self.serial_read = serial_read_func

        # 当前状态
        self.target_temperature = 25.0  # 目标温度 (°C)
        self.actual_temperature = 25.0  # 实际温度 (°C)
        self.is_heating = False

        logger.info(f"温控器 {controller_id} 初始化完成 (Modbus地址={modbus_address})")

    def _temperature_to_raw(self, temp: float) -> int:
        """将温度值转换为寄存器原始值

        Args:
            temp: 温度 (°C)

        Returns:
            原始值 (int32, 单位: /10000)
        """
        return int(temp * 10000)

    def _raw_to_temperature(self, raw: int) -> float:
        """将寄存器原始值转换为温度值

        Args:
            raw: 原始值 (int32)

        Returns:
            温度 (°C)
        """
        return raw / 10000.0

    def _build_modbus_write_command(self, register: int, value: int) -> bytes:
        """构建Modbus写入命令 (功能码 0x10 - 写多个寄存器，用于int32)

        Args:
            register: 起始寄存器地址
            value: 要写入的值 (int32)

        Returns:
            Modbus RTU命令字节串
        """
        # Modbus RTU 格式: [从站地址][功能码][起始地址高][起始地址低][寄存器数量高][寄存器数量低][字节数][数据...][CRC低][CRC高]
        cmd = bytearray()
        cmd.append(self.modbus_address)  # 从站地址
        cmd.append(0x10)  # 功能码: 写多个寄存器
        cmd.append((register >> 8) & 0xFF)  # 起始寄存器地址高字节
        cmd.append(register & 0xFF)  # 起始寄存器地址低字节
        cmd.append(0x00)  # 寄存器数量高字节 (2个寄存器用于int32)
        cmd.append(0x02)  # 寄存器数量低字节
        cmd.append(0x04)  # 字节数 (4字节 = int32)

        # int32数据 (大端序)
        data_bytes = struct.pack('>i', value)  # 大端序int32
        cmd.extend(data_bytes)

        # 计算CRC16
        crc = self._calculate_crc16(cmd)
        cmd.append(crc & 0xFF)  # CRC低字节
        cmd.append((crc >> 8) & 0xFF)  # CRC高字节

        return bytes(cmd)

    def _build_modbus_read_command(self, register: int, count: int = 2) -> bytes:
        """构建Modbus读取命令 (功能码 0x03 - 读保持寄存器)

        Args:
            register: 起始寄存器地址
            count: 读取的寄存器数量 (int32需要2个寄存器)

        Returns:
            Modbus RTU命令字节串
        """
        cmd = bytearray()
        cmd.append(self.modbus_address)  # 从站地址
        cmd.append(0x03)  # 功能码: 读保持寄存器
        cmd.append((register >> 8) & 0xFF)  # 起始寄存器地址高字节
        cmd.append(register & 0xFF)  # 起始寄存器地址低字节
        cmd.append((count >> 8) & 0xFF)  # 寄存器数量高字节
        cmd.append(count & 0xFF)  # 寄存器数量低字节

        # 计算CRC16
        crc = self._calculate_crc16(cmd)
        cmd.append(crc & 0xFF)  # CRC低字节
        cmd.append((crc >> 8) & 0xFF)  # CRC高字节

        return bytes(cmd)

    def _calculate_crc16(self, data: bytearray) -> int:
        """计算Modbus CRC16校验码"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def _parse_read_response(self, response: bytes) -> Optional[int]:
        """解析Modbus读取响应

        Args:
            response: 响应字节串

        Returns:
            解析出的int32值，失败返回None
        """
        if len(response) < 7:
            logger.error(f"温控器 {self.controller_id}: 响应长度不足")
            return None

        # 检查功能码
        if response[1] != 0x03:
            logger.error(f"温控器 {self.controller_id}: 功能码错误")
            return None

        # 提取数据字节数
        byte_count = response[2]
        if byte_count != 4:
            logger.error(f"温控器 {self.controller_id}: 数据字节数错误")
            return None

        # 提取int32数据 (大端序)
        data_bytes = response[3:7]
        value = struct.unpack('>i', data_bytes)[0]

        return value

    def set_target_temperature(self, temperature: float) -> bool:
        """设置目标温度

        Args:
            temperature: 目标温度 (°C)

        Returns:
            是否设置成功
        """
        if self.serial_write is None:
            logger.error(f"温控器 {self.controller_id}: 串口写入函数未设置")
            return False

        try:
            # 转换温度值
            raw_value = self._temperature_to_raw(temperature)

            # 构建Modbus命令
            cmd = self._build_modbus_write_command(self.target_temp_register, raw_value)

            # 发送命令
            logger.debug(f"温控器 {self.controller_id}: 设置目标温度 {temperature}°C, 命令: {cmd.hex()}")
            self.serial_write(cmd)

            # 等待响应
            time.sleep(0.1)

            # 更新状态
            self.target_temperature = temperature
            self.is_heating = True

            logger.info(f"温控器 {self.controller_id}: 目标温度设置为 {temperature}°C")
            return True

        except Exception as e:
            logger.error(f"温控器 {self.controller_id}: 设置目标温度失败 - {e}")
            return False

    def read_actual_temperature(self) -> Optional[float]:
        """读取实际温度

        Returns:
            实际温度 (°C)，失败返回None
        """
        if self.serial_write is None or self.serial_read is None:
            logger.error(f"温控器 {self.controller_id}: 串口通信函数未设置")
            return None

        try:
            # 构建读取命令
            cmd = self._build_modbus_read_command(self.actual_temp_register, count=2)

            # 发送命令
            logger.debug(f"温控器 {self.controller_id}: 读取实际温度, 命令: {cmd.hex()}")
            self.serial_write(cmd)

            # 等待并读取响应
            time.sleep(0.1)
            response = self.serial_read()

            if response:
                # 解析响应
                raw_value = self._parse_read_response(response)
                if raw_value is not None:
                    temperature = self._raw_to_temperature(raw_value)
                    self.actual_temperature = temperature
                    logger.debug(f"温控器 {self.controller_id}: 实际温度 {temperature}°C")
                    return temperature

            logger.warning(f"温控器 {self.controller_id}: 读取实际温度失败")
            return None

        except Exception as e:
            logger.error(f"温控器 {self.controller_id}: 读取实际温度异常 - {e}")
            return None

    def stop_heating(self) -> bool:
        """停止加热（设置目标温度为室温）"""
        logger.info(f"温控器 {self.controller_id}: 停止加热")
        self.is_heating = False
        return self.set_target_temperature(25.0)

    def get_status(self) -> Dict[str, Any]:
        """获取温控器当前状态

        Returns:
            状态字典，包含目标温度、实际温度、加热状态等
        """
        return {
            "controller_id": self.controller_id,
            "is_heating": self.is_heating,
            "target_temperature": self.target_temperature,
            "actual_temperature": self.actual_temperature,
            "modbus_address": self.modbus_address,
        }
