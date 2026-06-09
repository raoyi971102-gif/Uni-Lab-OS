"""
光谱仪调试脚本
独立测试光谱仪采集功能

使用方法：
    python test_spectrometer.py

命令格式：
    输入 'acquire' 或 'a' 采集一次光谱
    输入 'save' 保存最后一次采集的光谱
    输入 'plot' 绘制最后一次采集的光谱
    输入 'set_time <ms>' 设置积分时间
    输入 'set_avg <count>' 设置平均次数
    输入 'status' 查看状态
    输入 'q' 或 'quit' 退出
"""

import time
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# 尝试导入.NET库
try:
    import clr
    import os
    clr.AddReference("System")
    clr.AddReference("System.Collections")
    from System import Double
    from System.Collections.Generic import List as NetList

    PYTHONNET_AVAILABLE = True
except Exception as e:
    print(f"警告: pythonnet或.NET运行环境不可用，将使用模拟模式: {e}")
    print("安装方法: pip install pythonnet")
    PYTHONNET_AVAILABLE = False


class SpectrometerDebugger:
    """光谱仪调试器"""

    def __init__(self, dll_path: Optional[str] = None):
        """初始化光谱仪

        Args:
            dll_path: SDK DLL文件路径
        """
        self.dll_path = dll_path
        self.manager = None
        self.active_device = None
        self.wavelengths = []
        self.last_spectrum = None

        # 参数
        self.integration_time = 50.0  # ms
        self.average_count = 3

        # 设备信息
        self.serial_number = None
        self.type_name = None
        self.is_connected = False

    def _load_sdk(self) -> bool:
        """加载光谱仪SDK"""
        if not PYTHONNET_AVAILABLE:
            print("使用模拟模式")
            return False

        try:
            # 如果未指定DLL路径，使用默认路径
            if self.dll_path is None:
                self.dll_path = str(
                    Path(__file__).parent / "光谱仪" / "Python4CyUSB" / "dlls" / "Ideaoptics.USB.SDK.dll"
                )

            # 加载DLL
            clr.AddReference("System")
            clr.AddReference("System.Collections")
            clr.AddReference(self.dll_path)
            from Ideaoptics.SDK import Spectrometers

            self.manager = Spectrometers()
            print(f"✓ IdeaOptics USB SDK加载成功: {self.dll_path}")
            return True

        except Exception as e:
            print(f"✗ SDK加载失败: {e}")
            print("将使用模拟模式")
            return False

    def connect(self, device_index: int = 0) -> bool:
        """连接光谱仪

        Args:
            device_index: 设备索引（如果有多个光谱仪）

        Returns:
            是否连接成功
        """
        # 加载SDK
        sdk_loaded = self._load_sdk()

        # 模拟模式
        if not sdk_loaded or self.manager is None:
            print("✓ 光谱仪连接成功 (模拟模式)")
            self.is_connected = True
            self.wavelengths = list(range(200, 1100))  # 模拟波长范围 200-1100nm
            self.serial_number = "SIM-12345"
            self.type_name = "Simulated Spectrometer"
            return True

        # 实际连接
        try:
            # 获取设备列表
            spectrometer_list = self.manager.LoadAllSpectrometers()
            if spectrometer_list.Count == 0:
                print("✗ 未找到光谱仪设备")
                return False

            print(f"找到 {spectrometer_list.Count} 个光谱仪设备:")
            for i in range(spectrometer_list.Count):
                dev = spectrometer_list[i]
                print(f"  [{i}] 序列号: {dev.GetSerialNumber()}")

            if device_index >= spectrometer_list.Count:
                print(f"✗ 设备索引超出范围")
                return False

            # 连接指定设备
            self.active_device = spectrometer_list[device_index]
            if self.active_device.Open():
                self.is_connected = True
                self.wavelengths = list(self.active_device.GetWavelength())
                self.serial_number = self.active_device.GetSerialNumber()
                self.type_name = self.active_device.GetTypeName()

                # 设置参数
                self.active_device.SetIntegrationTime(self.integration_time)
                if hasattr(self.active_device, 'SetAverage'):
                    self.active_device.SetAverage(self.average_count)

                print(f"✓ 光谱仪连接成功")
                print(f"  型号: {self.type_name}")
                print(f"  序列号: {self.serial_number}")
                print(f"  像素数: {len(self.wavelengths)}")
                print(f"  波长范围: {self.wavelengths[0]:.2f} - {self.wavelengths[-1]:.2f} nm")
                return True
            else:
                print("✗ 打开设备失败")
                return False

        except Exception as e:
            print(f"✗ 连接失败: {e}")
            return False

    def disconnect(self):
        """断开光谱仪连接"""
        if self.active_device:
            try:
                self.active_device.Disconnect()
                print("光谱仪已断开连接")
            except:
                pass
        if self.manager:
            try:
                self.manager.Dispose()
            except:
                pass
        self.is_connected = False
        self.active_device = None
        self.manager = None

    def set_integration_time(self, time_ms: float) -> bool:
        """设置积分时间"""
        if not self.is_connected:
            print("✗ 光谱仪未连接")
            return False

        # 模拟模式
        if self.active_device is None:
            self.integration_time = time_ms
            print(f"✓ 积分时间设置为 {time_ms} ms (模拟)")
            return True

        # 实际设置
        try:
            if self.active_device.SetIntegrationTime(time_ms):
                self.integration_time = time_ms
                print(f"✓ 积分时间设置为 {time_ms} ms")
                return True
            else:
                print("✗ 设置失败")
                return False
        except Exception as e:
            print(f"✗ 设置失败: {e}")
            return False

    def set_average_count(self, count: int) -> bool:
        """设置平均次数"""
        if not self.is_connected:
            print("✗ 光谱仪未连接")
            return False

        # 模拟模式
        if self.active_device is None:
            self.average_count = count
            print(f"✓ 平均次数设置为 {count} (模拟)")
            return True

        # 实际设置
        try:
            if hasattr(self.active_device, 'SetAverage'):
                self.active_device.SetAverage(count)
                self.average_count = count
                print(f"✓ 平均次数设置为 {count}")
                return True
            else:
                print("✗ 设备不支持平均功能")
                return False
        except Exception as e:
            print(f"✗ 设置失败: {e}")
            return False

    def acquire_spectrum(self) -> Optional[Dict[str, Any]]:
        """采集光谱数据"""
        if not self.is_connected:
            print("✗ 光谱仪未连接")
            return None

        try:
            print("\n正在采集光谱...")

            # 模拟模式
            if self.active_device is None:
                import random
                time.sleep(0.1)  # 模拟采集时间
                intensities = [random.randint(500, 2000) for _ in self.wavelengths]
                print("✓ 光谱采集成功 (模拟)")
            else:
                # 实际采集：IdeaOptics USB SDK 需要传入 .NET List[Double] 接收数据
                spectrum = NetList[Double]()
                if not self.active_device.GetSpectrum(spectrum):
                    print("✗ SDK返回采集失败")
                    return None
                intensities = [value for value in spectrum]
                print(f"✓ 光谱采集成功 ({len(intensities)} 个数据点)")

            # 构建数据
            self.last_spectrum = {
                "wavelengths": self.wavelengths,
                "intensities": intensities,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "integration_time": self.integration_time,
                "average_count": self.average_count,
                "serial_number": self.serial_number,
                "type_name": self.type_name,
            }

            # 显示统计信息
            print(f"  时间戳: {self.last_spectrum['timestamp']}")
            print(f"  积分时间: {self.integration_time} ms")
            print(f"  平均次数: {self.average_count}")
            print(f"  强度范围: {min(intensities)} - {max(intensities)}")
            print(f"  平均强度: {sum(intensities) / len(intensities):.2f}")

            return self.last_spectrum

        except Exception as e:
            print(f"✗ 采集失败: {e}")
            return None

    def save_spectrum(self, file_path: Optional[str] = None, format: str = "csv") -> bool:
        """保存光谱数据"""
        if self.last_spectrum is None:
            print("✗ 没有可保存的光谱数据，请先采集")
            return False

        try:
            # 生成文件名
            if file_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_path = f"spectrum_{timestamp}.{format}"

            # 保存CSV格式
            if format == "csv":
                import csv
                with open(file_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    # 写入元数据
                    writer.writerow(['# Type', self.last_spectrum['type_name']])
                    writer.writerow(['# Serial Number', self.last_spectrum['serial_number']])
                    writer.writerow(['# Timestamp', self.last_spectrum['timestamp']])
                    writer.writerow(['# Integration Time (ms)', self.last_spectrum['integration_time']])
                    writer.writerow(['# Average Count', self.last_spectrum['average_count']])
                    writer.writerow([])
                    # 写入数据
                    writer.writerow(['Wavelength (nm)', 'Intensity'])
                    for wl, intensity in zip(self.last_spectrum['wavelengths'], self.last_spectrum['intensities']):
                        writer.writerow([wl, intensity])

            # 保存JSON格式
            elif format == "json":
                import json
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(self.last_spectrum, f, indent=2, ensure_ascii=False)

            else:
                print(f"✗ 不支持的格式: {format}")
                return False

            print(f"✓ 光谱数据已保存到: {file_path}")
            return True

        except Exception as e:
            print(f"✗ 保存失败: {e}")
            return False

    def plot_spectrum(self):
        """绘制光谱图"""
        if self.last_spectrum is None:
            print("✗ 没有可绘制的光谱数据，请先采集")
            return

        try:
            import matplotlib.pyplot as plt

            plt.figure(figsize=(12, 6))
            plt.plot(self.last_spectrum['wavelengths'], self.last_spectrum['intensities'], linewidth=1)
            plt.xlabel('Wavelength (nm)', fontsize=12)
            plt.ylabel('Intensity', fontsize=12)
            plt.title(
                f"Spectrum - {self.last_spectrum['type_name']}\n"
                f"Time: {self.last_spectrum['timestamp']} | "
                f"Integration: {self.last_spectrum['integration_time']}ms | "
                f"Average: {self.last_spectrum['average_count']}",
                fontsize=10
            )
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.show()

            print("✓ 光谱图已显示")

        except ImportError:
            print("✗ matplotlib未安装，无法绘图")
            print("  安装方法: pip install matplotlib")
        except Exception as e:
            print(f"✗ 绘图失败: {e}")

    def show_status(self):
        """显示当前状态"""
        print(f"\n{'='*50}")
        print(f"光谱仪状态:")
        print(f"  连接状态: {'已连接' if self.is_connected else '未连接'}")
        if self.is_connected:
            print(f"  型号: {self.type_name}")
            print(f"  序列号: {self.serial_number}")
            print(f"  像素数: {len(self.wavelengths)}")
            if self.wavelengths:
                print(f"  波长范围: {self.wavelengths[0]:.2f} - {self.wavelengths[-1]:.2f} nm")
            print(f"  积分时间: {self.integration_time} ms")
            print(f"  平均次数: {self.average_count}")
            print(f"  已采集光谱: {'是' if self.last_spectrum else '否'}")
        print(f"{'='*50}")

    def interactive_mode(self):
        """交互模式"""
        print("\n" + "="*50)
        print("光谱仪调试工具")
        print("="*50)
        print("\n命令列表:")
        print("  acquire / a           - 采集一次光谱")
        print("  save [filename]       - 保存最后一次采集的光谱")
        print("  plot                  - 绘制最后一次采集的光谱")
        print("  set_time <ms>         - 设置积分时间")
        print("  set_avg <count>       - 设置平均次数")
        print("  status                - 查看当前状态")
        print("  q / quit              - 退出程序")
        print("="*50 + "\n")

        while True:
            try:
                # 读取用户输入
                user_input = input(">>> ").strip()

                if not user_input:
                    continue

                # 分割命令和参数
                parts = user_input.split()
                cmd = parts[0].lower()
                args = parts[1:] if len(parts) > 1 else []

                # 处理命令
                if cmd in ['q', 'quit', 'exit']:
                    print("\n退出程序...")
                    break

                elif cmd == 'status':
                    self.show_status()

                elif cmd in ['acquire', 'a']:
                    print(f"\n{'='*50}")
                    self.acquire_spectrum()
                    print(f"{'='*50}")

                elif cmd == 'save':
                    print(f"\n{'='*50}")
                    filename = args[0] if args else None
                    self.save_spectrum(filename)
                    print(f"{'='*50}")

                elif cmd == 'plot':
                    self.plot_spectrum()

                elif cmd == 'set_time':
                    if not args:
                        print("✗ 请指定积分时间")
                        print("  例如: set_time 50")
                        continue
                    try:
                        time_ms = float(args[0])
                        print(f"\n{'='*50}")
                        self.set_integration_time(time_ms)
                        print(f"{'='*50}")
                    except ValueError:
                        print("✗ 无效的积分时间")

                elif cmd == 'set_avg':
                    if not args:
                        print("✗ 请指定平均次数")
                        print("  例如: set_avg 3")
                        continue
                    try:
                        count = int(args[0])
                        print(f"\n{'='*50}")
                        self.set_average_count(count)
                        print(f"{'='*50}")
                    except ValueError:
                        print("✗ 无效的平均次数")

                else:
                    print(f"✗ 未知命令: {cmd}")
                    print("  输入 'status' 查看帮助")

            except KeyboardInterrupt:
                print("\n\n检测到 Ctrl+C，退出程序...")
                break
            except Exception as e:
                print(f"✗ 错误: {e}")


def main():
    """主函数"""
    # 检查命令行参数
    dll_path = None
    device_index = 0

    if len(sys.argv) > 1:
        dll_path = sys.argv[1]
    if len(sys.argv) > 2:
        device_index = int(sys.argv[2])

    # 创建调试器
    debugger = SpectrometerDebugger(dll_path=dll_path)

    # 连接光谱仪
    if not debugger.connect(device_index):
        print("\n请检查:")
        print("  1. 光谱仪是否已连接")
        print("  2. USB驱动是否已安装")
        print("  3. DLL路径是否正确")
        print("\n提示: 可以通过命令行参数指定DLL路径")
        print("  python test_spectrometer.py <dll_path> <device_index>")
        print("\n将继续使用模拟模式...")
        debugger.is_connected = True
        debugger.wavelengths = list(range(200, 1100))
        debugger.serial_number = "SIM-12345"
        debugger.type_name = "Simulated Spectrometer"

    try:
        # 进入交互模式
        debugger.interactive_mode()
    finally:
        # 确保断开连接
        debugger.disconnect()


if __name__ == "__main__":
    main()
