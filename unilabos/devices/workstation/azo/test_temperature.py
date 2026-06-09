"""
温控器调试脚本
独立测试温控器控制功能

使用方法：
    python test_temperature.py

命令格式：
    <温度>
    例如：25.5    # 设置目标温度为25.5°C
    例如：60      # 设置目标温度为60°C

    输入 'start' 开始控温（使用当前目标温度）
    输入 'read' 读取当前温度
    输入 'stop' 停止加热
    输入 'status' 查看状态
    输入 'q' 或 'quit' 退出
"""

import serial
import time
import sys
import struct


class TemperatureDebugger:
    """温控器调试器"""

    def __init__(self, port: str = "COM3", baudrate: int = 38400):
        """初始化串口连接

        Args:
            port: 串口号
            baudrate: 波特率
        """
        self.port = port
        self.baudrate = baudrate
        self.ser = None

        # 温控器Modbus站号
        self.modbus_address = 1  # 温控器站号
        self.target_temp_register = 4096  # 0x1000 - 目标温度，Modbus地址=40001+4096=44097
        self.actual_temp_register = 4098  # 0x1002 - 实际温度，Modbus地址=40001+4098=44099
        self.output_enable_register = 0x1100  # 输出使能，uint16，0=关闭，1=打开
        self.temperature_scale = 100000
        self.read_response_length = 9
        self.write_response_length = 8

        # 当前状态
        self.target_temperature = 25.0
        self.actual_temperature = None
        self.is_heating = False

    def connect(self) -> bool:
        """连接串口"""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1.0
            )
            print(f"✓ 串口连接成功: {self.port} @ {self.baudrate}")
            return True
        except Exception as e:
            print(f"✗ 串口连接失败: {e}")
            return False

    def disconnect(self):
        """断开串口"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("串口已关闭")

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

    def _temperature_to_raw(self, temp: float) -> int:
        """将温度值转换为寄存器原始值"""
        return int(temp * self.temperature_scale)

    def _raw_to_temperature(self, raw: int) -> float:
        """将寄存器原始值转换为温度值"""
        return raw / float(self.temperature_scale)

    def _build_write_command(self, register: int, value: int) -> bytes:
        """构建Modbus写入命令 (功能码 0x10 - 写多个寄存器)"""
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

    def _build_write_uint16_command(self, register: int, value: int) -> bytes:
        """构建Modbus写单个uint16寄存器命令 (功能码 0x06)"""
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"uint16寄存器值超出范围: {value}")

        cmd = bytearray()
        cmd.append(self.modbus_address)
        cmd.append(0x06)
        cmd.append((register >> 8) & 0xFF)
        cmd.append(register & 0xFF)
        cmd.append((value >> 8) & 0xFF)
        cmd.append(value & 0xFF)

        crc = self._calculate_crc16(cmd)
        cmd.append(crc & 0xFF)
        cmd.append((crc >> 8) & 0xFF)

        return bytes(cmd)

    def _build_read_command(self, register: int, count: int = 2) -> bytes:
        """构建Modbus读取命令 (功能码 0x03 - 读保持寄存器)"""
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

    def _read_exact_response(self, expected_length: int, timeout: float = 1.0) -> bytes:
        """在超时时间内尽量读取指定长度响应。"""
        response = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline and len(response) < expected_length:
            if self.ser.in_waiting > 0:
                response.extend(self.ser.read(self.ser.in_waiting))
                if len(response) >= expected_length:
                    break
            else:
                time.sleep(0.02)
        return bytes(response)

    def _extract_read_frame(self, response: bytes) -> bytes:
        """从串口缓存中提取合法读温响应帧。"""
        if len(response) < self.read_response_length:
            raise ValueError(f"响应长度不足，实际 {len(response)} 字节")

        for start in range(0, len(response) - self.read_response_length + 1):
            frame = response[start:start + self.read_response_length]
            if frame[0] != self.modbus_address or frame[1] != 0x03 or frame[2] != 4:
                continue

            expected_crc = frame[-2] | (frame[-1] << 8)
            actual_crc = self._calculate_crc16(frame[:-2])
            if expected_crc == actual_crc:
                return frame

        raise ValueError(f"未找到合法读温响应帧: {response.hex().upper()}")

    def _parse_read_response(self, response: bytes) -> int:
        """解析Modbus读取响应"""
        frame = self._extract_read_frame(response)

        # 提取int32数据 (大端序)
        data_bytes = frame[3:7]
        value = struct.unpack('>i', data_bytes)[0]

        return value

    def set_temperature(self, temperature: float) -> bool:
        """设置目标温度

        Args:
            temperature: 目标温度 (°C)

        Returns:
            是否设置成功
        """
        if not self.ser or not self.ser.is_open:
            print("✗ 串口未连接")
            return False

        try:
            # 转换温度值
            raw_value = self._temperature_to_raw(temperature)

            # 构建命令
            cmd = self._build_write_command(self.target_temp_register, raw_value)

            # 发送命令
            self.ser.write(cmd)
            print(f"→ 设置目标温度: {temperature}°C | 命令: {cmd.hex().upper()}")

            # 读取写寄存器应答，避免残留应答污染下一次读温
            response = self._read_exact_response(self.write_response_length, timeout=0.3)
            if response:
                print(f"← 响应: {response.hex().upper()}")

            # 更新状态
            self.target_temperature = temperature

            print(f"✓ 目标温度已设置为 {temperature}°C")
            return True

        except Exception as e:
            print(f"✗ 设置失败: {e}")
            return False

    def read_temperature(self) -> float:
        """读取实际温度

        Returns:
            实际温度 (°C)，失败返回None
        """
        if not self.ser or not self.ser.is_open:
            print("✗ 串口未连接")
            return None

        try:
            # 构建读取命令
            cmd = self._build_read_command(self.actual_temp_register, count=2)

            # 发送命令
            self.ser.write(cmd)
            print(f"→ 读取实际温度 | 命令: {cmd.hex().upper()}")

            # 等待并读取响应。2个寄存器的RTU响应固定为9字节。
            response = self._read_exact_response(self.read_response_length, timeout=1.0)
            if response:
                print(f"← 响应: {response.hex().upper()}")

                # 解析响应
                raw_value = self._parse_read_response(response)
                temperature = self._raw_to_temperature(raw_value)
                self.actual_temperature = temperature

                print(f"✓ 实际温度: {temperature:.4f}°C")
                return temperature
            else:
                print("✗ 未收到响应")
                return None

        except Exception as e:
            print(f"✗ 读取失败: {e}")
            return None

    def set_output_enable(self, enabled: bool) -> bool:
        """设置输出使能。"""
        if not self.ser or not self.ser.is_open:
            print("✗ 串口未连接")
            return False

        try:
            value = 1 if enabled else 0
            cmd = self._build_write_uint16_command(self.output_enable_register, value)
            self.ser.write(cmd)
            print(f"→ {'打开' if enabled else '关闭'}输出使能: {value} | 命令: {cmd.hex().upper()}")

            response = self._read_exact_response(self.write_response_length, timeout=0.3)
            if response:
                print(f"← 响应: {response.hex().upper()}")

            return True
        except Exception as e:
            print(f"✗ 设置输出使能失败: {e}")
            return False

    def stop_heating(self) -> bool:
        """停止控温（关闭输出使能，并设置目标温度为室温）"""
        print("\n停止控温...")
        if not self.set_output_enable(False):
            return False

        if not self.ser or not self.ser.is_open:
            print("✗ 串口未连接")
            return False

        try:
            raw_value = self._temperature_to_raw(25.0)
            cmd = self._build_write_command(self.target_temp_register, raw_value)
            self.ser.write(cmd)
            print(f"→ 目标温度降至 25.0°C | 命令: {cmd.hex().upper()}")

            response = self._read_exact_response(self.write_response_length, timeout=0.3)
            if response:
                print(f"← 响应: {response.hex().upper()}")

            self.target_temperature = 25.0
        except Exception as e:
            print(f"⚠ 目标温度降至室温失败，但输出已关闭: {e}")

        self.is_heating = False
        print("✓ 控温已停止，输出已关闭")
        return True

    def start_temperature_control(self) -> bool:
        """开始控温（使用当前目标温度）"""
        print(f"\n开始控温，目标温度: {self.target_temperature}°C")
        if not self.set_temperature(self.target_temperature):
            return False
        if not self.set_output_enable(True):
            return False

        self.is_heating = True
        print("✓ 控温已开始，输出已使能")
        return True

    def show_status(self):
        """显示当前状态"""
        print(f"\n{'='*50}")
        print(f"当前状态:")
        print(f"  目标温度: {self.target_temperature}°C")
        if self.actual_temperature is not None:
            print(f"  实际温度: {self.actual_temperature:.4f}°C")
            temp_diff = self.actual_temperature - self.target_temperature
            print(f"  温度偏差: {temp_diff:+.4f}°C")
        else:
            print(f"  实际温度: 未读取")
        print(f"  加热状态: {'加热中' if self.is_heating else '停止'}")
        print(f"  串口: {self.port} @ {self.baudrate}")
        print(f"{'='*50}")

    def interactive_mode(self):
        """交互模式"""
        print("\n" + "="*50)
        print("温控器调试工具")
        print("="*50)
        print("\n命令格式:")
        print("  <温度>  - 设置目标温度")
        print("    例如: 25.5  # 设置为25.5°C")
        print("    例如: 60    # 设置为60°C")
        print("\n特殊命令:")
        print("  read    - 读取当前温度")
        print("  start   - 开始控温（使用当前目标温度）")
        print("  stop    - 停止加热")
        print("  status  - 查看当前状态")
        print("  q/quit  - 退出程序")
        print("="*50 + "\n")

        while True:
            try:
                # 读取用户输入
                user_input = input(">>> ").strip()

                if not user_input:
                    continue

                # 处理特殊命令
                if user_input.lower() in ['q', 'quit', 'exit']:
                    print("\n退出程序...")
                    self.stop_heating()
                    break

                if user_input.lower() == 'status':
                    self.show_status()
                    continue

                if user_input.lower() == 'stop':
                    self.stop_heating()
                    continue

                if user_input.lower() == 'start':
                    self.start_temperature_control()
                    continue

                if user_input.lower() == 'read':
                    print(f"\n{'='*50}")
                    self.read_temperature()
                    print(f"{'='*50}")
                    continue

                # 解析温度命令
                try:
                    temperature = float(user_input)
                except ValueError:
                    print("✗ 格式错误！请输入有效的温度值")
                    print("  例如: 25.5 或 60")
                    continue

                # 温度范围检查
                if temperature < 0 or temperature > 100:
                    print("✗ 温度超出范围！请输入0-100°C之间的温度")
                    continue

                # 设置温度
                print(f"\n{'='*50}")
                self.set_temperature(temperature)
                print(f"{'='*50}")

            except KeyboardInterrupt:
                print("\n\n检测到 Ctrl+C，退出程序...")
                self.stop_heating()
                break
            except Exception as e:
                print(f"✗ 错误: {e}")


def main():
    """主函数"""
    # 检查命令行参数
    port = "COM6"
    baudrate = 38400

    if len(sys.argv) > 1:
        port = sys.argv[1]
    if len(sys.argv) > 2:
        baudrate = int(sys.argv[2])

    # 创建调试器
    debugger = TemperatureDebugger(port=port, baudrate=baudrate)

    # 连接串口
    if not debugger.connect():
        print("\n请检查:")
        print("  1. 串口号是否正确 (当前: {})".format(port))
        print("  2. 设备是否已连接")
        print("  3. 串口是否被其他程序占用")
        print("\n提示: 可以通过命令行参数指定串口")
        print("  python test_temperature.py COM3 38400")
        return

    try:
        # 进入交互模式
        debugger.interactive_mode()
    finally:
        # 确保断开连接
        debugger.disconnect()


if __name__ == "__main__":
    main()
