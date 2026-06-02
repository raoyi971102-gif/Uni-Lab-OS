# -*- coding: utf-8 -*-
"""
Ideaoptics 光谱仪直接控制接口Demo - CyUSB版本

"""

import clr
import sys
import os
import time
from typing import List, Optional, Tuple, Dict, Union
clr.AddReference("System")
clr.AddReference("System.Collections")
from System import Double
from System.Collections.Generic import List as NetList
import matplotlib.pyplot as plt
from matplotlib.ticker import AutoMinorLocator

# 配置中文显示
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

# 加载.NET库
def load_required_assemblies():
    """加载所有必要的.NET程序集"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    required_dlls = [
        r"dlls\Ideaoptics.USB.SDK.dll",
    ]
    
    for dll in required_dlls:
        try:
            dll_path = os.path.join(base_dir, dll)
            clr.AddReference(dll_path)
        except Exception as e:
            print(f"【错误】加载 {dll} 失败: {str(e)}")
            sys.exit(1)

load_required_assemblies()

from Ideaoptics.SDK import Spectrometers
from Ideaoptics.SDK import ISpectrometer

class SpectrometerController:
    """直接通过原生接口控制光谱仪"""
    
    def __init__(self):
        self.manager = Spectrometers()
        self.active_device = None
        self.wavelengths = []
    
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.dispose()
    
    def dispose(self):
        """释放资源"""
        if self.active_device:
            try:
                self.active_device.Disconnect()
            except:
                pass
        
        if self.manager:
            try:
                self.manager.Dispose()
            except:
                pass
    
    def list_devices(self) -> List[Tuple[int, str, ISpectrometer]]:
        """获取设备列表 (索引, 序列号, 设备对象)"""
        devices = []
        spectrometer_list = self.manager.LoadAllSpectrometers()
        for idx in range(spectrometer_list.Count):
            dev = spectrometer_list[idx]
            devices.append((
                idx,
                dev.GetSerialNumber(),
                dev
            ))
        return devices
    
    def connect(self, device_index: int) -> bool:
        """连接指定设备
    
        Args:
            device_index: 设备索引号
        
        Returns:
            bool: 连接是否成功
        
        Raises:
            IndexError: 当设备索引超出范围时
        """
        devices = self.list_devices()
        if not 0 <= device_index < len(devices):
            raise IndexError("设备索引超出范围")
        self.active_device = devices[device_index][2]
        
        if self.active_device.Open():
            self.wavelengths = list(self.active_device.GetWavelength())
            return True
        return False
    
    def get_wavelengths(self) -> List[float]:
        """获取波长列表
        
        Returns:
            List[float]: 波长列表，单位为nm
        """
        if not self.active_device:
            return []
        self.wavelengths = list(self.active_device.GetWavelength())
        return self.wavelengths

    def get_pixel_number(self) -> Optional[int]:
        """获取光谱仪设备总像素数"""
        if not self.active_device:
            return None
        return self.active_device.GetPixelNumber()

    def get_serial_number(self) -> Optional[str]:
        """获取设备序列号"""
        if not self.active_device:
            return None        
        return self.active_device.GetSerialNumber()

    def get_type_name(self) -> Optional[str]:
        """获取当前光谱仪的型号名"""
        if not self.active_device:
            return None
        
        return self.active_device.GetTypeName()

    def set_integration_time(self, time_ms: float) -> bool:
        """设置积分时间（毫秒）
        
        Args:
            time_ms: 积分时间(ms)，需在GetMin/MaxIntegrationTime()范围内
            
        Returns:
            bool: 是否设置成功
        """
        if not self.active_device:
            return False
        return self.active_device.SetIntegrationTime(time_ms)

    def get_integration_time(self) -> Optional[float]:
        """获取当前积分时间
        
        Returns:
            float: 当前积分时间(ms)，失败返回None
        """
        if not self.active_device:
            return None
        time = self.active_device.GetIntegrationTime()
        return time

    def set_average_count(self, count: int) -> bool:
        """设置平均次数（需设备支持）
        
        Args:
            count: 平均次数 (>=1)
            
        Returns:
            bool: 是否设置成功
        """
        if not self.active_device:
            return False
        if hasattr(self.active_device, 'SetAverage'):
            self.active_device.SetAverage(max(1, count))
            return True
        return False

    def get_average_count(self) -> Optional[int]:
        """获取当前平均次数设置
        
        Returns:
            int: 当前平均次数，失败返回None
        """
        if not self.active_device:
            return None
        if hasattr(self.active_device, 'GetAverage'):
            return self.active_device.GetAverage()
        return None
    
    def is_tec_supported(self) -> bool:
        """检查是否支持TEC温控"""
        if not self.active_device:
            return None
        if hasattr(self.active_device, 'TECControl'):
            return self.active_device.TECControl()
        return None

    def enable_tec(self) -> bool:
        """启用TEC温控"""
        if not self.active_device:
            return None
        if hasattr(self.active_device, 'EnableTEC'):
            return self.active_device.EnableTEC();
        return None
    
    def disable_tec(self) -> bool:
        """禁用TEC温控"""
        if not self.active_device:
            return None
        if hasattr(self.active_device, 'DisableTEC'):
            return self.active_device.DisableTEC();
        return None
    
    def set_tec_temperature(self, temperature: float) -> bool:
        """设置TEC目标温度(°C)"""
        return self.active_device.SetTECPresetTemperature(temperature)
    
    def get_tec_target_temperature(self) -> Optional[float]:
        """获取TEC目标温度(°C)"""
        refParam = Double(0.0)
        success, temp = self.active_device.ReadTECPresetTemperature(refParam)
        return temp if success else None
    
    def get_tec_current_temperature(self) -> Optional[float]:
        """获取TEC当前温度(°C)"""
        refParam = Double(0.0)
        success, temp = self.active_device.ReadTECCurrentTemperature(refParam)
        return temp if success else None

    def read_spectrum(self) -> Optional[List[float]]:
        """单次光谱采集（严格对应C#接口）
        
        Args:
            is_irr_correct: 是否进行辐射校正
            
        Returns:
            List[float]: 成功返回光谱数据，失败返回None
        """
        try:
            if not self.active_device:
                return None

            net_list = NetList[Double]()
            if self.active_device.GetSpectrum(net_list):
                return [x for x in net_list]
            return None

        except Exception as e:
            print(f"采集异常: {str(e)}")
            return None

    def get_device_info(self) -> Dict[str, any]:
        """获取设备信息"""
        if not self.active_device:
            return {}
        
        return {
            'type': self.get_type_name(),
            'serial': self.get_serial_number(),
            'pixels': self.get_pixel_number(),
            'wavelengths': self.get_wavelengths(),
            'integration_time': self.get_integration_time(),
            'average_count': self.get_average_count()
        }
    
    def plot_spectrum(self, spectrum: List[float], title: str = ""):
        """绘制光谱图"""
        if not spectrum:
            raise ValueError("无效的光谱数据")
        
        fig, ax = plt.subplots(figsize=(12, 6))
        x_data = self.wavelengths if self.wavelengths else range(len(spectrum))
        ax.plot(x_data, spectrum, linewidth=1.5, color='#64FF32')
        
        ax.set_title(title or "光谱数据", fontsize=14)
        ax.set_xlabel("波长 (nm)" if self.wavelengths else "像素索引", fontsize=12)
        ax.set_ylabel("强度 (a.u.)", fontsize=12)
        ax.grid(True, alpha=0.6)
        
        plt.tight_layout()
        plt.show()

if __name__ == "__main__":
    print("="*50)
    print("Ideaoptics 光谱仪控制演示")
    print("="*50)
    
    try:
        with SpectrometerController() as ctrl:
            # 设备列表
            devices = ctrl.list_devices()
            print(f"可用设备 ({len(devices)}台):")
            for idx, (_, sn, _) in enumerate(devices):
                print(f"[{idx}] {sn}")
            
            if not devices:
                print("未检测到设备，请检查连接！")
                sys.exit(0)
            
            # 连接设备
            if ctrl.connect(0):
                print("\n连接成功!")
                print("设备信息:")
                info = ctrl.get_device_info()
                for key, value in info.items():
                    if key == 'wavelengths' and value:
                        print(f"  {key}: [{value[0]:.2f}nm to {value[-1]:.2f}nm, {len(value)} points]")
                    else:
                        print(f"  {key}: {value}")
                
                # 温控测试
                if ctrl.is_tec_supported():
                    print("\nTEC温控测试:")
                    print("启用TEC...", "成功" if ctrl.enable_tec() else "失败")

                    target_temp = -10.0  # 目标温度-10°C
                    print(f"设置目标温度: {target_temp}°C...", 
                      "成功" if ctrl.set_tec_temperature(target_temp) else "失败")

                    # 根据需要设置一些delay或者monitor，用于给设备足够的事件去达到预设的温度
                    # time.sleep(1)

                    current_temp = ctrl.get_tec_current_temperature()
                    if current_temp is None:
                        print("温度读取失败")
                    else:
                        print(f"当前温度: {current_temp:.2f}°C")
                    
                    # 关闭温控
                    print("禁用TEC...", "成功" if ctrl.disable_tec() else "失败")
                else:
                    print("\n该设备不支持TEC温控")

                # 配置采集参数
                print("\n配置采集参数:")
                target_int_time = 50.0  # 目标积分时间50ms
                avg_count = 3           # 平均3次
                
                # 设置积分时间
                if ctrl.set_integration_time(target_int_time):
                    current_int_time = ctrl.get_integration_time()
                    print(f"  积分时间设置: {current_int_time} ms (目标: {target_int_time} ms)")
                else:
                    print("  积分时间设置失败!")
                
                # 设置平均次数
                if ctrl.set_average_count(avg_count):
                    current_avg = ctrl.get_average_count()
                    print(f"  平均次数设置: {current_avg}次 (目标: {avg_count}次)")
                else:
                    print("  平均次数设置失败 (可能设备不支持)")

                # 采集数据
                print("\n正在采集光谱...")
                spectrum = ctrl.read_spectrum()
                if spectrum:
                    print(f"获取到 {len(spectrum)} 个数据点")
                    print("前5个数据点:", spectrum[:5])
                
                # 可视化
                    ctrl.plot_spectrum(
                        spectrum,
                        title=f"{info['type']} 光谱数据\n序列号: {info['serial']}\n"
                              f"积分时间: {current_int_time}ms | 平均: {current_avg}次"
                    )
                else:
                    print("采集失败")
                
    except Exception as e:
        print(f"\n错误: {str(e)}")
    finally:
        input("\n按Enter键退出...")