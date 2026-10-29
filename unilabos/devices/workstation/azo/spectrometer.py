"""偶氮工作站光谱仪驱动。

IdeaOptics USB Device + Ideaoptics.USB.SDK.dll，独享 USB，不走 RS485。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.utils.log import logger

if TYPE_CHECKING:
    from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode

DEFAULT_DLL_PATH = Path(__file__).parent / "光谱仪" / "Python4CyUSB" / "dlls" / "Ideaoptics.USB.SDK.dll"


def _load_sdk(dll_path: str):
    """延迟加载 pythonnet 与 IdeaOptics SDK。"""
    import clr

    clr.AddReference("System")
    clr.AddReference("System.Collections")
    clr.AddReference(dll_path)
    from Ideaoptics.SDK import Spectrometers

    return Spectrometers


@device(
    id="azo.spectrometer",
    category=["sensor"],
    description="IdeaOptics USB 光谱仪，用于微流控在线表征",
    display_name="偶氮光谱仪",
)
class AzoSpectrometer:
    """基于 Ideaoptics CyUSB SDK 的光谱仪。"""

    _ros_node: "BaseROS2DeviceNode"

    def __init__(
        self,
        device_id: Optional[str] = None,
        dll_path: Optional[str] = None,
        device_index: int = 0,
        integration_time: float = 50.0,
        average_count: int = 3,
        simulate: bool = False,
        **kwargs,
    ):
        """
        Args:
            device_id[设备ID]: 光谱仪实例 ID。
            dll_path[SDK路径]: Ideaoptics.USB.SDK.dll 路径，默认使用包内 SDK。
            device_index[设备索引]: 多台光谱仪时的索引。
            integration_time[积分时间]: 积分时间 (ms)。
            average_count[平均次数]: 采集平均次数。
            simulate[模拟模式]: 为 True 时不连接真实 USB 设备。
        """
        self.device_id = device_id or "spectrometer"
        self.dll_path = dll_path or str(DEFAULT_DLL_PATH)
        self.device_index = int(device_index)
        self.simulate = bool(simulate)
        self.manager = None
        self.active_device = None
        self.wavelengths: List[float] = []
        self.data: Dict[str, Any] = {
            "status": "Idle",
            "connected": False,
            "integration_time": float(integration_time),
            "average_count": int(average_count),
            "serial_number": "",
            "type_name": "",
            "pixel_count": 0,
        }
        self._stop_requested = False

    @not_action
    def post_init(self, ros_node: "BaseROS2DeviceNode") -> None:
        self._ros_node = ros_node
        self.device_id = getattr(ros_node, "device_id", self.device_id)
        result = self.connect()
        if not result.get("success"):
            logger.warning(f"光谱仪 {self.device_id}: post_init 连接失败 - {result.get('message')}")

    @action(description="连接 IdeaOptics USB 光谱仪")
    def connect(self) -> Dict[str, Any]:
        if self.data["connected"] and (self.simulate or self.active_device is not None):
            return {"success": True, "message": "光谱仪已连接"}

        if self.simulate:
            self.wavelengths = [float(x) for x in range(200, 1100)]
            self.data.update(
                {
                    "status": "Idle",
                    "connected": True,
                    "serial_number": "SIM",
                    "type_name": "SimulatedSpectrometer",
                    "pixel_count": len(self.wavelengths),
                }
            )
            return {"success": True, "message": "光谱仪模拟连接成功", "pixel_count": len(self.wavelengths)}

        try:
            Spectrometers = _load_sdk(self.dll_path)
            self.manager = Spectrometers()
            spectrometer_list = self.manager.LoadAllSpectrometers()
            if spectrometer_list.Count == 0:
                self.data["status"] = "Error"
                return {"success": False, "error": "not_found", "message": "未找到 IdeaOptics USB 设备"}
            if self.device_index >= spectrometer_list.Count:
                self.data["status"] = "Error"
                return {"success": False, "error": "index_out_of_range", "message": "设备索引超出范围"}

            self.active_device = spectrometer_list[self.device_index]
            if not self.active_device.Open():
                self.data["status"] = "Error"
                return {"success": False, "error": "open_failed", "message": "打开光谱仪失败"}

            self.wavelengths = list(self.active_device.GetWavelength())
            self.active_device.SetIntegrationTime(self.data["integration_time"])
            if hasattr(self.active_device, "SetAverage"):
                self.active_device.SetAverage(self.data["average_count"])

            self.data.update(
                {
                    "status": "Idle",
                    "connected": True,
                    "serial_number": str(self.active_device.GetSerialNumber()),
                    "type_name": str(self.active_device.GetTypeName()),
                    "pixel_count": int(self.active_device.GetPixelNumber()),
                }
            )
            logger.info(
                f"光谱仪 {self.device_id}: 连接成功 "
                f"(型号={self.data['type_name']}, 序列号={self.data['serial_number']})"
            )
            return {
                "success": True,
                "message": "光谱仪连接成功",
                "serial_number": self.data["serial_number"],
                "type_name": self.data["type_name"],
                "pixel_count": self.data["pixel_count"],
            }
        except Exception as exc:
            self.data["status"] = "Error"
            logger.error(f"光谱仪 {self.device_id}: 连接失败 - {exc}")
            return {"success": False, "error": str(exc), "message": "光谱仪连接失败，请检查 pythonnet 与 USB 驱动"}

    @action(description="断开光谱仪")
    def disconnect(self) -> Dict[str, Any]:
        if self.active_device is not None:
            try:
                self.active_device.Disconnect()
            except Exception:
                pass
        if self.manager is not None:
            try:
                self.manager.Dispose()
            except Exception:
                pass
        self.active_device = None
        self.manager = None
        self.data["connected"] = False
        self.data["status"] = "Idle"
        return {"success": True, "message": "光谱仪已断开"}

    @not_action
    def clear_stop(self) -> None:
        self._stop_requested = False
        if self.data.get("connected") and self.data.get("status") != "Error":
            self.data["status"] = "Idle"

    @action(description="停止光谱采集")
    def stop(self) -> Dict[str, Any]:
        self._stop_requested = True
        if self.data.get("status") == "Acquiring":
            logger.info(f"光谱仪 {self.device_id}: 收到停止请求，当前采集结束后不再继续")
        self.data["status"] = "Idle"
        return {"success": True, "message": "光谱仪采集已停止"}

    @action(description="设置积分时间")
    def set_integration_time(self, time_ms: float = 50.0) -> Dict[str, Any]:
        """
        Args:
            time_ms[积分时间]: 积分时间 (ms)。
        """
        if not self.data["connected"]:
            return {"success": False, "error": "not_connected", "message": "光谱仪未连接"}
        if self.simulate or self.active_device is None:
            self.data["integration_time"] = time_ms
            return {"success": True, "message": f"模拟设置积分时间 {time_ms} ms", "integration_time": time_ms}
        try:
            if not self.active_device.SetIntegrationTime(time_ms):
                return {"success": False, "error": "set_failed", "message": "设置积分时间失败"}
            self.data["integration_time"] = time_ms
            return {"success": True, "message": f"积分时间已设置为 {time_ms} ms", "integration_time": time_ms}
        except Exception as exc:
            return {"success": False, "error": str(exc), "message": "设置积分时间异常"}

    @action(description="设置平均次数")
    def set_average_count(self, count: int = 3) -> Dict[str, Any]:
        """
        Args:
            count[平均次数]: 采集平均次数，至少为 1。
        """
        count = max(1, int(count))
        if not self.data["connected"]:
            return {"success": False, "error": "not_connected", "message": "光谱仪未连接"}
        if self.simulate or self.active_device is None:
            self.data["average_count"] = count
            return {"success": True, "message": f"模拟设置平均次数 {count}", "average_count": count}
        try:
            if not hasattr(self.active_device, "SetAverage"):
                return {"success": False, "error": "unsupported", "message": "设备不支持平均次数设置"}
            self.active_device.SetAverage(count)
            self.data["average_count"] = count
            return {"success": True, "message": f"平均次数已设置为 {count}", "average_count": count}
        except Exception as exc:
            return {"success": False, "error": str(exc), "message": "设置平均次数异常"}

    @action(description="采集一张光谱")
    def acquire_spectrum(self) -> Dict[str, Any]:
        if self._stop_requested:
            return {"success": False, "error": "stopped", "message": "光谱仪已停止，跳过采集"}
        if not self.data["connected"]:
            return {"success": False, "error": "not_connected", "message": "光谱仪未连接"}

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.data["status"] = "Acquiring"
        try:
            if self.simulate or self.active_device is None:
                intensities = [500.0 + (i % 50) * 10.0 for i in range(len(self.wavelengths))]
            else:
                from System import Double
                from System.Collections.Generic import List as NetList

                spectrum = NetList[Double]()
                if not self.active_device.GetSpectrum(spectrum):
                    self.data["status"] = "Idle"
                    return {"success": False, "error": "acquire_failed", "message": "SDK 返回采集失败"}
                intensities = [float(value) for value in spectrum]
        except Exception as exc:
            self.data["status"] = "Error"
            logger.error(f"光谱仪 {self.device_id}: 采集失败 - {exc}")
            return {"success": False, "error": str(exc), "message": "采集光谱失败"}

        self.data["status"] = "Idle"
        if self._stop_requested:
            return {"success": False, "error": "stopped", "message": "采集过程中收到停止请求"}

        peak_intensity = max(intensities) if intensities else 0.0
        mean_intensity = sum(intensities) / len(intensities) if intensities else 0.0
        return {
            "success": True,
            "message": f"采集成功，{len(intensities)} 个数据点",
            "timestamp": timestamp,
            "integration_time": self.data["integration_time"],
            "average_count": self.data["average_count"],
            "point_count": len(intensities),
            "peak_intensity": peak_intensity,
            "mean_intensity": mean_intensity,
            "wavelengths": self.wavelengths,
            "intensities": intensities,
        }

    @not_action
    def save_spectrum(self, spectrum_data: Dict[str, Any], file_path: str, format: str = "csv") -> Dict[str, Any]:
        """
        Args:
            spectrum_data[光谱数据]: acquire_spectrum 返回的数据字典。
            file_path[保存路径]: 输出文件路径。
            format[格式]: csv 或 json。
        """
        if not spectrum_data or not spectrum_data.get("wavelengths"):
            return {"success": False, "error": "empty_data", "message": "没有可保存的光谱数据"}

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if format == "json":
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(spectrum_data, f, indent=2, ensure_ascii=False)
            else:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["# Spectrometer ID", self.device_id])
                    writer.writerow(["# Timestamp", spectrum_data.get("timestamp", "")])
                    writer.writerow(["# Integration Time (ms)", spectrum_data.get("integration_time", "")])
                    writer.writerow(["# Average Count", spectrum_data.get("average_count", "")])
                    writer.writerow([])
                    writer.writerow(["Wavelength (nm)", "Intensity"])
                    for wavelength, intensity in zip(spectrum_data["wavelengths"], spectrum_data["intensities"]):
                        writer.writerow([wavelength, intensity])
            return {"success": True, "message": f"光谱已保存到 {path}", "file_path": str(path)}
        except Exception as exc:
            return {"success": False, "error": str(exc), "message": "保存光谱失败"}

    @property
    @topic_config()
    def status(self) -> str:
        return self.data["status"]

    @property
    @topic_config()
    def connected(self) -> bool:
        return bool(self.data["connected"])

    @property
    @topic_config()
    def integration_time(self) -> float:
        return float(self.data["integration_time"])

    @property
    @topic_config()
    def average_count(self) -> int:
        return int(self.data["average_count"])

    @property
    @topic_config()
    def serial_number(self) -> str:
        return str(self.data["serial_number"])

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass
