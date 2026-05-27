"""
偶氮工站 - 蠕动泵驱动
Peristaltic Pump Driver for Azo Workstation

通过步进电机驱动器（Modbus协议）控制蠕动泵的转速
支持流速到转速的换算
"""

import time
from typing import Dict, Any, Optional
from unilabos.utils.log import logger


class PeristalticPump:
    """蠕动泵驱动类

    通过 Modbus 协议控制步进电机驱动器，实现蠕动泵的流速控制

    通信参数：
    - 波特率: 38400
    - 数据位: 8
    - 校验位: N (无校验)
    - 停止位: 1
    - 寄存器地址: 53 (0x35) - PA-53
    - 数据格式: int16
    - 单位: rpm (转/分钟)
    - 控制逻辑: <0反转, =0停止, >0正转
    """

    def __init__(
        self,
        pump_id: str,
        modbus_address: int,
        serial_write_func=None,
        serial_read_func=None,
        flow_to_rpm_ratio: float = 1.0,  # 流速到转速的换算系数，默认1:1
        **kwargs
    ):
        """初始化蠕动泵

        Args:
            pump_id: 泵的唯一标识符
            modbus_address: Modbus从站地址 (例如: 5 或 6)
            serial_write_func: 串口写入函数（由workstation注入）
            serial_read_func: 串口读取函数（由workstation注入）
            flow_to_rpm_ratio: 流速(ml/min)到转速(rpm)的换算系数
                              例如: rpm = flow_rate * flow_to_rpm_ratio
        """
        self.pump_id = pump_id
        self.modbus_address = modbus_address
        self.register_address = 53  # PA-53寄存器

        # 串口通信函数（通过代理模式注入）
        self.serial_write = serial_write_func
        self.serial_read = serial_read_func

        # 流速换算参数
        self.flow_to_rpm_ratio = flow_to_rpm_ratio

        # 当前状态
        self.current_rpm = 0
        self.current_flow_rate = 0.0
        self.is_running = False

        logger.info(f"蠕动泵 {pump_id} 初始化完成 (Modbus地址={modbus_address}, 换算系数={flow_to_rpm_ratio})")

    def flow_rate_to_rpm(self, flow_rate: float) -> int:
        """将流速转换为转速

        Args:
            flow_rate: 流速 (ml/min)

        Returns:
            转速 (rpm)

        Note:
            TODO: 根据实际泵的参数调整换算公式
            当前使用简单的线性关系: rpm = flow_rate * ratio
            实际可能需要考虑：
            - 泵管内径
            - 泵头滚轮数量
            - 非线性修正
        """
        rpm = int(flow_rate * self.flow_to_rpm_ratio)
        return rpm

    def rpm_to_flow_rate(self, rpm: int) -> float:
        """将转速转换为流速

        Args:
            rpm: 转速 (rpm)

        Returns:
            流速 (ml/min)
        """
        if self.flow_to_rpm_ratio == 0:
            return 0.0
        flow_rate = rpm / self.flow_to_rpm_ratio
        return flow_rate

    def _build_modbus_command(self, register: int, value: int) -> bytes:
        """构建Modbus写入命令 (功能码 0x06 - 写单个寄存器)

        Args:
            register: 寄存器地址
            value: 要写入的值 (int16)

        Returns:
            Modbus RTU命令字节串
        """
        # Modbus RTU 格式: [从站地址][功能码][寄存器高字节][寄存器低字节][数据高字节][数据低字节][CRC低][CRC高]
        cmd = bytearray()
        cmd.append(self.modbus_address)  # 从站地址
        cmd.append(0x06)  # 功能码: 写单个寄存器
        cmd.append((register >> 8) & 0xFF)  # 寄存器地址高字节
        cmd.append(register & 0xFF)  # 寄存器地址低字节

        # 处理int16数据（支持负数）
        if value < 0:
            value = (1 << 16) + value  # 转换为无符号表示
        cmd.append((value >> 8) & 0xFF)  # 数据高字节
        cmd.append(value & 0xFF)  # 数据低字节

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

    def set_rpm(self, rpm: int) -> bool:
        """设置泵的转速

        Args:
            rpm: 目标转速 (rpm)
                 <0: 反转
                 =0: 停止
                 >0: 正转

        Returns:
            是否设置成功
        """
        if self.serial_write is None:
            logger.error(f"蠕动泵 {self.pump_id}: 串口写入函数未设置")
            return False

        try:
            # 构建Modbus命令
            cmd = self._build_modbus_command(self.register_address, rpm)

            # 发送命令
            logger.debug(f"蠕动泵 {self.pump_id}: 设置转速 {rpm} rpm, 命令: {cmd.hex()}")
            self.serial_write(cmd)

            # 等待响应（可选）
            time.sleep(0.05)

            # 更新状态
            self.current_rpm = rpm
            self.current_flow_rate = self.rpm_to_flow_rate(abs(rpm))
            self.is_running = (rpm != 0)

            logger.info(f"蠕动泵 {self.pump_id}: 转速设置为 {rpm} rpm")
            return True

        except Exception as e:
            logger.error(f"蠕动泵 {self.pump_id}: 设置转速失败 - {e}")
            return False

    def set_flow_rate(self, flow_rate: float) -> bool:
        """设置泵的流速

        Args:
            flow_rate: 目标流速 (ml/min)

        Returns:
            是否设置成功
        """
        rpm = self.flow_rate_to_rpm(flow_rate)
        logger.info(f"蠕动泵 {self.pump_id}: 设置流速 {flow_rate} ml/min (对应 {rpm} rpm)")
        return self.set_rpm(rpm)

    def start(self, flow_rate: float) -> bool:
        """启动泵并设置流速

        Args:
            flow_rate: 流速 (ml/min)

        Returns:
            是否启动成功
        """
        return self.set_flow_rate(flow_rate)

    def stop(self) -> bool:
        """停止泵"""
        logger.info(f"蠕动泵 {self.pump_id}: 停止")
        return self.set_rpm(0)

    def get_status(self) -> Dict[str, Any]:
        """获取泵的当前状态

        Returns:
            状态字典，包含转速、流速、运行状态等
        """
        return {
            "pump_id": self.pump_id,
            "is_running": self.is_running,
            "current_rpm": self.current_rpm,
            "current_flow_rate": self.current_flow_rate,
            "modbus_address": self.modbus_address,
        }
