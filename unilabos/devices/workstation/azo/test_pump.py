"""
蠕动泵调试脚本
独立测试蠕动泵控制功能

使用方法：
    python test_pump.py

命令格式：
    <泵A转速> <泵B转速>
    例如：100 150  # 泵A转速100rpm，泵B转速150rpm
    例如：0 0      # 停止两个泵
    例如：-50 100  # 泵A反转50rpm，泵B正转100rpm

    输入 'q' 或 'quit' 退出
    输入 'status' 查看状态
"""

import serial
import time
import sys


class PumpDebugger:
    """蠕动泵调试器"""

    def __init__(self, port: str = "COM3", baudrate: int = 38400):
        """初始化串口连接

        Args:
            port: 串口号
            baudrate: 波特率
        """
        self.port = port
        self.baudrate = baudrate
        self.ser = None

        # 泵的Modbus站号
        self.pump_a_address = 5  # 蠕动泵1站号
        self.pump_b_address = 6  # 蠕动泵2站号
        self.register_address = 53  # PA-53寄存器，Modbus地址=40001+53=40054

        # 当前状态
        self.pump_a_rpm = 0
        self.pump_b_rpm = 0

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

    def _build_modbus_command(self, slave_address: int, register: int, value: int) -> bytes:
        """构建Modbus写入命令

        Args:
            slave_address: 从站地址
            register: 寄存器地址
            value: 要写入的值 (int16)

        Returns:
            Modbus RTU命令字节串
        """
        cmd = bytearray()
        cmd.append(slave_address)  # 从站地址
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

    def set_pump_rpm(self, pump_id: str, rpm: int) -> bool:
        """设置泵的转速

        Args:
            pump_id: 'A' 或 'B'
            rpm: 转速 (rpm)，<0反转，=0停止，>0正转

        Returns:
            是否设置成功
        """
        if not self.ser or not self.ser.is_open:
            print("✗ 串口未连接")
            return False

        # 确定从站地址
        if pump_id.upper() == 'A':
            slave_address = self.pump_a_address
        elif pump_id.upper() == 'B':
            slave_address = self.pump_b_address
        else:
            print(f"✗ 无效的泵ID: {pump_id}")
            return False

        try:
            # 构建命令
            cmd = self._build_modbus_command(slave_address, self.register_address, rpm)

            # 发送命令
            self.ser.write(cmd)
            print(f"→ 泵{pump_id}: {rpm} rpm | 命令: {cmd.hex().upper()}")

            # 等待响应
            time.sleep(0.05)
            if self.ser.in_waiting > 0:
                response = self.ser.read(self.ser.in_waiting)
                print(f"← 响应: {response.hex().upper()}")

            # 更新状态
            if pump_id.upper() == 'A':
                self.pump_a_rpm = rpm
            else:
                self.pump_b_rpm = rpm

            return True

        except Exception as e:
            print(f"✗ 设置失败: {e}")
            return False

    def set_both_pumps(self, rpm_a: int, rpm_b: int):
        """同时设置两个泵的转速"""
        print(f"\n{'='*50}")
        print(f"设置泵速: A={rpm_a} rpm, B={rpm_b} rpm")
        print(f"{'='*50}")

        success_a = self.set_pump_rpm('A', rpm_a)
        time.sleep(0.1)  # 短暂延时，避免总线冲突
        success_b = self.set_pump_rpm('B', rpm_b)

        if success_a and success_b:
            print("✓ 设置成功")
        else:
            print("✗ 设置失败")

    def stop_all(self):
        """停止所有泵"""
        print("\n停止所有泵...")
        self.set_both_pumps(0, 0)

    def show_status(self):
        """显示当前状态"""
        print(f"\n{'='*50}")
        print(f"当前状态:")
        print(f"  泵A: {self.pump_a_rpm} rpm {'(停止)' if self.pump_a_rpm == 0 else '(运行)'}")
        print(f"  泵B: {self.pump_b_rpm} rpm {'(停止)' if self.pump_b_rpm == 0 else '(运行)'}")
        print(f"  串口: {self.port} @ {self.baudrate}")
        print(f"{'='*50}")

    def interactive_mode(self):
        """交互模式"""
        print("\n" + "="*50)
        print("蠕动泵调试工具")
        print("="*50)
        print("\n命令格式: <泵A转速> <泵B转速>")
        print("例如: 100 150  # 泵A转速100rpm，泵B转速150rpm")
        print("例如: 0 0      # 停止两个泵")
        print("例如: -50 100  # 泵A反转50rpm，泵B正转100rpm")
        print("\n特殊命令:")
        print("  status  - 查看当前状态")
        print("  stop    - 停止所有泵")
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
                    self.stop_all()
                    break

                if user_input.lower() == 'status':
                    self.show_status()
                    continue

                if user_input.lower() == 'stop':
                    self.stop_all()
                    continue

                # 解析转速命令
                parts = user_input.split()
                if len(parts) != 2:
                    print("✗ 格式错误！请输入两个数字，用空格分隔")
                    print("  例如: 100 150")
                    continue

                try:
                    rpm_a = int(parts[0])
                    rpm_b = int(parts[1])
                except ValueError:
                    print("✗ 格式错误！请输入有效的整数")
                    continue

                # 设置转速
                self.set_both_pumps(rpm_a, rpm_b)

            except KeyboardInterrupt:
                print("\n\n检测到 Ctrl+C，退出程序...")
                self.stop_all()
                break
            except Exception as e:
                print(f"✗ 错误: {e}")


def main():
    """主函数"""
    # 检查命令行参数
    port = "COM3"
    baudrate = 38400

    if len(sys.argv) > 1:
        port = sys.argv[1]
    if len(sys.argv) > 2:
        baudrate = int(sys.argv[2])

    # 创建调试器
    debugger = PumpDebugger(port=port, baudrate=baudrate)

    # 连接串口
    if not debugger.connect():
        print("\n请检查:")
        print("  1. 串口号是否正确 (当前: {})".format(port))
        print("  2. 设备是否已连接")
        print("  3. 串口是否被其他程序占用")
        print("\n提示: 可以通过命令行参数指定串口")
        print("  python test_pump.py COM3 38400")
        return

    try:
        # 进入交互模式
        debugger.interactive_mode()
    finally:
        # 确保断开连接
        debugger.disconnect()


if __name__ == "__main__":
    main()
