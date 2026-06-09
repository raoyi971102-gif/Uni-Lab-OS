"""
偶氮工站 - 光谱仪驱动
Spectrometer Driver for Azo Workstation

基于 Ideaoptics SDK 的光谱仪驱动
支持光谱采集、参数设置和数据导出
"""

import time
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path

from unilabos.utils.log import logger

# 尝试导入.NET库（如果环境支持）
try:
    import clr
    clr.AddReference("System")
    clr.AddReference("System.Collections")
    from System import Double
    from System.Collections.Generic import List as NetList

    # 加载.NET库
    def load_spectrometer_assemblies(dll_path: str):
        """加载光谱仪所需的.NET程序集"""
        try:
            clr.AddReference("System")
            clr.AddReference("System.Collections")
            clr.AddReference(dll_path)
            from Ideaoptics.SDK import Spectrometers
            return Spectrometers
        except Exception as e:
            logger.error(f"加载光谱仪SDK失败: {e}")
            return None

    SPECTROMETER_AVAILABLE = True
except Exception as e:
    logger.warning(f"pythonnet或.NET运行环境不可用，光谱仪功能将使用模拟模式: {e}")
    SPECTROMETER_AVAILABLE = False


class SpectrometerDriver:
    """光谱仪驱动类

    支持功能：
    - 设备连接和断开
    - 积分时间设置
    - 平均次数设置
    - 光谱数据采集
    - 数据保存（CSV/JSON格式）
    """

    def __init__(
        self,
        spectrometer_id: str,
        dll_path: Optional[str] = None,
        device_index: int = 0,
        integration_time: float = 50.0,  # 默认积分时间 50ms
        average_count: int = 3,  # 默认平均3次
        **kwargs
    ):
        """初始化光谱仪驱动

        Args:
            spectrometer_id: 光谱仪的唯一标识符
            dll_path: SDK DLL文件路径（如果为None，使用默认路径）
            device_index: 设备索引（如果有多个光谱仪）
            integration_time: 积分时间 (ms)
            average_count: 平均次数
        """
        self.spectrometer_id = spectrometer_id
        self.device_index = device_index
        self.integration_time = integration_time
        self.average_count = average_count

        # 设备状态
        self.is_connected = False
        self.manager = None
        self.active_device = None
        self.wavelengths = []
        self.serial_number = None
        self.type_name = None
        self.pixel_count = 0
        self.dll_path = dll_path

        # 如果未指定DLL路径，使用默认路径
        if dll_path is None:
            dll_path = str(Path(__file__).parent / "光谱仪" / "Python4CyUSB" / "dlls" / "Ideaoptics.USB.SDK.dll")
            self.dll_path = dll_path

        # 初始化SDK
        if SPECTROMETER_AVAILABLE:
            self._init_sdk(dll_path)
        else:
            logger.warning(f"光谱仪 {spectrometer_id}: 使用模拟模式")

        logger.info(f"光谱仪 {spectrometer_id} 驱动初始化完成")

    def _init_sdk(self, dll_path: str):
        """初始化光谱仪SDK"""
        try:
            Spectrometers = load_spectrometer_assemblies(dll_path)
            if Spectrometers:
                self.manager = Spectrometers()
                logger.info(f"光谱仪 {self.spectrometer_id}: IdeaOptics USB SDK加载成功")
            else:
                logger.error(f"光谱仪 {self.spectrometer_id}: SDK加载失败")
        except Exception as e:
            logger.error(f"光谱仪 {self.spectrometer_id}: SDK初始化失败 - {e}")

    def list_devices(self) -> List[Dict[str, Any]]:
        """列出通过 IdeaOptics USB 驱动枚举到的光谱仪。"""
        devices = []
        if not SPECTROMETER_AVAILABLE or self.manager is None:
            return devices

        spectrometer_list = self.manager.LoadAllSpectrometers()
        for index in range(spectrometer_list.Count):
            device = spectrometer_list[index]
            devices.append(
                {
                    "index": index,
                    "serial_number": device.GetSerialNumber(),
                    "type_name": device.GetTypeName(),
                    "device": device,
                }
            )
        return devices

    def connect(self) -> bool:
        """连接光谱仪

        Returns:
            是否连接成功
        """
        if not SPECTROMETER_AVAILABLE or self.manager is None:
            if SPECTROMETER_AVAILABLE and self.dll_path:
                self._init_sdk(self.dll_path)

        if not SPECTROMETER_AVAILABLE or self.manager is None:
            logger.warning(f"光谱仪 {self.spectrometer_id}: 模拟连接成功")
            self.is_connected = True
            self.wavelengths = list(range(200, 1100))  # 模拟波长范围
            return True

        try:
            # 获取设备列表
            devices = self.list_devices()
            if not devices:
                logger.error(f"光谱仪 {self.spectrometer_id}: 未找到 IdeaOptics USB 设备")
                return False

            if self.device_index >= len(devices):
                logger.error(f"光谱仪 {self.spectrometer_id}: 设备索引超出范围")
                return False

            # 连接指定设备
            self.active_device = devices[self.device_index]["device"]
            if self.active_device.Open():
                self.is_connected = True
                self.wavelengths = list(self.active_device.GetWavelength())
                self.serial_number = self.active_device.GetSerialNumber()
                self.type_name = self.active_device.GetTypeName()
                self.pixel_count = self.active_device.GetPixelNumber()

                # 设置参数
                self.active_device.SetIntegrationTime(self.integration_time)
                if hasattr(self.active_device, 'SetAverage'):
                    self.active_device.SetAverage(self.average_count)

                logger.info(
                    f"光谱仪 {self.spectrometer_id}: 连接成功 "
                    f"(型号={self.type_name}, 序列号={self.serial_number}, "
                    f"像素数={self.pixel_count})"
                )
                return True
            else:
                logger.error(f"光谱仪 {self.spectrometer_id}: 打开设备失败")
                return False

        except Exception as e:
            logger.error(f"光谱仪 {self.spectrometer_id}: 连接失败 - {e}")
            return False

    def disconnect(self):
        """断开光谱仪连接"""
        if self.active_device:
            try:
                self.active_device.Disconnect()
                logger.info(f"光谱仪 {self.spectrometer_id}: 已断开连接")
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
        """设置积分时间

        Args:
            time_ms: 积分时间 (ms)

        Returns:
            是否设置成功
        """
        if not self.is_connected:
            logger.error(f"光谱仪 {self.spectrometer_id}: 未连接")
            return False

        if not SPECTROMETER_AVAILABLE or self.active_device is None:
            self.integration_time = time_ms
            logger.info(f"光谱仪 {self.spectrometer_id}: 模拟设置积分时间 {time_ms} ms")
            return True

        try:
            if self.active_device.SetIntegrationTime(time_ms):
                self.integration_time = time_ms
                logger.info(f"光谱仪 {self.spectrometer_id}: 积分时间设置为 {time_ms} ms")
                return True
            return False
        except Exception as e:
            logger.error(f"光谱仪 {self.spectrometer_id}: 设置积分时间失败 - {e}")
            return False

    def set_average_count(self, count: int) -> bool:
        """设置平均次数

        Args:
            count: 平均次数

        Returns:
            是否设置成功
        """
        if not self.is_connected:
            logger.error(f"光谱仪 {self.spectrometer_id}: 未连接")
            return False

        if not SPECTROMETER_AVAILABLE or self.active_device is None:
            self.average_count = count
            logger.info(f"光谱仪 {self.spectrometer_id}: 模拟设置平均次数 {count}")
            return True

        try:
            if hasattr(self.active_device, 'SetAverage'):
                self.active_device.SetAverage(count)
                self.average_count = count
                logger.info(f"光谱仪 {self.spectrometer_id}: 平均次数设置为 {count}")
                return True
            else:
                logger.warning(f"光谱仪 {self.spectrometer_id}: 设备不支持平均功能")
                return False
        except Exception as e:
            logger.error(f"光谱仪 {self.spectrometer_id}: 设置平均次数失败 - {e}")
            return False

    def acquire_spectrum(self) -> Optional[Dict[str, Any]]:
        """采集光谱数据

        Returns:
            光谱数据字典，包含波长和强度，失败返回None
            格式: {
                "wavelengths": [200.0, 201.0, ...],
                "intensities": [1000, 1050, ...],
                "timestamp": "2025-01-01 12:00:00",
                "integration_time": 50.0,
                "average_count": 3
            }
        """
        if not self.is_connected:
            logger.error(f"光谱仪 {self.spectrometer_id}: 未连接")
            return None

        try:
            # 模拟模式
            if not SPECTROMETER_AVAILABLE or self.active_device is None:
                import random
                intensities = [random.randint(500, 2000) for _ in self.wavelengths]
                logger.info(f"光谱仪 {self.spectrometer_id}: 模拟采集光谱数据")
            else:
                # 实际采集：IdeaOptics USB SDK 需要传入 .NET List[Double] 接收数据
                spectrum = NetList[Double]()
                if not self.active_device.GetSpectrum(spectrum):
                    logger.error(f"光谱仪 {self.spectrometer_id}: SDK返回采集失败")
                    return None
                intensities = [value for value in spectrum]
                logger.info(f"光谱仪 {self.spectrometer_id}: 采集光谱数据成功 ({len(intensities)} 个数据点)")

            # 构建返回数据
            data = {
                "wavelengths": self.wavelengths,
                "intensities": intensities,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "integration_time": self.integration_time,
                "average_count": self.average_count,
                "spectrometer_id": self.spectrometer_id,
            }

            return data

        except Exception as e:
            logger.error(f"光谱仪 {self.spectrometer_id}: 采集光谱失败 - {e}")
            return None

    def save_spectrum_csv(self, spectrum_data: Dict[str, Any], file_path: str) -> bool:
        """保存光谱数据为CSV文件

        Args:
            spectrum_data: 光谱数据字典
            file_path: 保存路径

        Returns:
            是否保存成功
        """
        try:
            import csv

            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # 写入元数据
                writer.writerow(['# Spectrometer ID', spectrum_data['spectrometer_id']])
                writer.writerow(['# Timestamp', spectrum_data['timestamp']])
                writer.writerow(['# Integration Time (ms)', spectrum_data['integration_time']])
                writer.writerow(['# Average Count', spectrum_data['average_count']])
                writer.writerow([])
                # 写入数据
                writer.writerow(['Wavelength (nm)', 'Intensity'])
                for wl, intensity in zip(spectrum_data['wavelengths'], spectrum_data['intensities']):
                    writer.writerow([wl, intensity])

            logger.info(f"光谱仪 {self.spectrometer_id}: 光谱数据已保存到 {file_path}")
            return True

        except Exception as e:
            logger.error(f"光谱仪 {self.spectrometer_id}: 保存CSV失败 - {e}")
            return False

    def save_spectrum_json(self, spectrum_data: Dict[str, Any], file_path: str) -> bool:
        """保存光谱数据为JSON文件

        Args:
            spectrum_data: 光谱数据字典
            file_path: 保存路径

        Returns:
            是否保存成功
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(spectrum_data, f, indent=2, ensure_ascii=False)

            logger.info(f"光谱仪 {self.spectrometer_id}: 光谱数据已保存到 {file_path}")
            return True

        except Exception as e:
            logger.error(f"光谱仪 {self.spectrometer_id}: 保存JSON失败 - {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """获取光谱仪状态

        Returns:
            状态字典
        """
        return {
            "spectrometer_id": self.spectrometer_id,
            "is_connected": self.is_connected,
            "serial_number": self.serial_number,
            "type_name": self.type_name,
            "integration_time": self.integration_time,
            "average_count": self.average_count,
            "pixel_count": self.pixel_count or len(self.wavelengths),
            "connection": "IdeaOptics USB SDK" if self.active_device is not None else "simulated",
            "dll_path": self.dll_path,
        }

    def __del__(self):
        """析构函数，确保断开连接"""
        self.disconnect()
