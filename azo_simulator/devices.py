"""偶氮工作站高层模拟设备。"""

from __future__ import annotations

import csv
import json
import math
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class SimulatedPeristalticPump:
    """蠕动泵模拟器，接口对齐 ``PeristalticPump``。"""

    def __init__(
        self,
        pump_id: str,
        modbus_address: int,
        flow_to_rpm_ratio: float = 1.0,
        max_flow_rate: float = 100.0,
        failure_rate: float = 0.0,
        **kwargs,
    ):
        self.pump_id = pump_id
        self.modbus_address = modbus_address
        self.flow_to_rpm_ratio = flow_to_rpm_ratio
        self.max_flow_rate = max_flow_rate
        self.failure_rate = failure_rate
        self.current_rpm = 0
        self.current_flow_rate = 0.0
        self.is_running = False
        self.total_volume_ml = 0.0
        self._last_update = time.time()

    def flow_rate_to_rpm(self, flow_rate: float) -> int:
        return int(flow_rate * self.flow_to_rpm_ratio)

    def rpm_to_flow_rate(self, rpm: int) -> float:
        if self.flow_to_rpm_ratio == 0:
            return 0.0
        return abs(rpm) / self.flow_to_rpm_ratio

    def set_rpm(self, rpm: int) -> bool:
        if self._should_fail():
            return False
        self._update_volume()
        self.current_rpm = int(rpm)
        self.current_flow_rate = self.rpm_to_flow_rate(self.current_rpm)
        self.is_running = self.current_rpm != 0
        return True

    def set_flow_rate(self, flow_rate: float) -> bool:
        if flow_rate < 0 or flow_rate > self.max_flow_rate:
            return False
        return self.set_rpm(self.flow_rate_to_rpm(flow_rate))

    def start(self, flow_rate: float) -> bool:
        return self.set_flow_rate(flow_rate)

    def stop(self) -> bool:
        return self.set_rpm(0)

    def get_status(self) -> Dict[str, Any]:
        self._update_volume()
        return {
            "pump_id": self.pump_id,
            "is_running": self.is_running,
            "current_rpm": self.current_rpm,
            "current_flow_rate": self.current_flow_rate,
            "modbus_address": self.modbus_address,
            "total_volume_ml": self.total_volume_ml,
            "simulated": True,
        }

    def _update_volume(self) -> None:
        now = time.time()
        dt_min = max(0.0, now - self._last_update) / 60.0
        self._last_update = now
        if self.is_running:
            self.total_volume_ml += self.current_flow_rate * dt_min

    def _should_fail(self) -> bool:
        return self.failure_rate > 0 and random.random() < self.failure_rate


class SimulatedTemperatureController:
    """温控器模拟器，接口对齐 ``TemperatureController``。"""

    def __init__(
        self,
        controller_id: str,
        modbus_address: int = 1,
        ambient_temperature: float = 25.0,
        thermal_rate_deg_per_sec: float = 1.5,
        noise_std: float = 0.05,
        **kwargs,
    ):
        self.controller_id = controller_id
        self.modbus_address = modbus_address
        self.ambient_temperature = ambient_temperature
        self.thermal_rate_deg_per_sec = thermal_rate_deg_per_sec
        self.noise_std = noise_std
        self.target_temperature = ambient_temperature
        self.actual_temperature = ambient_temperature
        self.is_heating = False
        self._last_update = time.time()

    def set_target_temperature(self, temperature: float) -> bool:
        self._update_temperature()
        self.target_temperature = float(temperature)
        return True

    def start_heating(self, temperature: Optional[float] = None) -> bool:
        return self.start_temperature_control(temperature)

    def start_temperature_control(self, temperature: Optional[float] = None) -> bool:
        if temperature is not None and not self.set_target_temperature(temperature):
            return False
        self._update_temperature()
        self.is_heating = True
        return True

    def read_actual_temperature(self) -> Optional[float]:
        self._update_temperature()
        noise = random.gauss(0.0, self.noise_std) if self.noise_std > 0 else 0.0
        return round(self.actual_temperature + noise, 5)

    def stop_heating(self, temperature: float = 25.0) -> bool:
        self._update_temperature()
        self.target_temperature = float(temperature)
        self.is_heating = False
        return True

    def get_status(self) -> Dict[str, Any]:
        self._update_temperature()
        return {
            "controller_id": self.controller_id,
            "is_heating": self.is_heating,
            "target_temperature": self.target_temperature,
            "actual_temperature": self.actual_temperature,
            "modbus_address": self.modbus_address,
            "simulated": True,
        }

    def _update_temperature(self) -> None:
        now = time.time()
        dt = max(0.0, now - self._last_update)
        self._last_update = now
        target = self.target_temperature if self.is_heating else self.ambient_temperature
        delta = target - self.actual_temperature
        max_step = self.thermal_rate_deg_per_sec * dt
        if abs(delta) <= max_step:
            self.actual_temperature = target
        elif delta > 0:
            self.actual_temperature += max_step
        else:
            self.actual_temperature -= max_step


class SimulatedSpectrometer:
    """IdeaOptics 光谱仪模拟器，接口对齐 ``SpectrometerDriver``。"""

    def __init__(
        self,
        spectrometer_id: str,
        integration_time: float = 50.0,
        average_count: int = 3,
        wavelength_start: float = 200.0,
        wavelength_end: float = 1100.0,
        wavelength_step: float = 1.0,
        noise_std: float = 20.0,
        peak_center_nm: float = 450.0,
        peak_width_nm: float = 35.0,
        peak_height: float = 2500.0,
        baseline: float = 300.0,
        **kwargs,
    ):
        self.spectrometer_id = spectrometer_id
        self.integration_time = integration_time
        self.average_count = average_count
        self.noise_std = noise_std
        self.peak_center_nm = peak_center_nm
        self.peak_width_nm = peak_width_nm
        self.peak_height = peak_height
        self.baseline = baseline
        self.is_connected = False
        self.serial_number = f"SIM-{spectrometer_id}"
        self.type_name = "Simulated IdeaOptics"
        self.dll_path = None
        count = int((wavelength_end - wavelength_start) / wavelength_step) + 1
        self.wavelengths = [wavelength_start + i * wavelength_step for i in range(count)]
        self.pixel_count = len(self.wavelengths)

    def list_devices(self) -> list[Dict[str, Any]]:
        return [
            {
                "index": 0,
                "serial_number": self.serial_number,
                "type_name": self.type_name,
                "device": self,
            }
        ]

    def connect(self) -> bool:
        self.is_connected = True
        return True

    def disconnect(self) -> None:
        self.is_connected = False

    def set_integration_time(self, time_ms: float) -> bool:
        self.integration_time = float(time_ms)
        return True

    def set_average_count(self, count: int) -> bool:
        if count <= 0:
            return False
        self.average_count = int(count)
        return True

    def acquire_spectrum(self) -> Optional[Dict[str, Any]]:
        if not self.is_connected:
            return None
        intensities = []
        for wavelength in self.wavelengths:
            gaussian = self.peak_height * math.exp(
                -((wavelength - self.peak_center_nm) ** 2) / (2 * self.peak_width_nm ** 2)
            )
            noise = random.gauss(0.0, self.noise_std) if self.noise_std > 0 else 0.0
            intensities.append(max(0.0, self.baseline + gaussian + noise))
        return {
            "wavelengths": self.wavelengths,
            "intensities": intensities,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "integration_time": self.integration_time,
            "average_count": self.average_count,
            "spectrometer_id": self.spectrometer_id,
        }

    def save_spectrum_csv(self, spectrum_data: Dict[str, Any], file_path: str) -> bool:
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["# Spectrometer ID", spectrum_data["spectrometer_id"]])
                writer.writerow(["# Timestamp", spectrum_data["timestamp"]])
                writer.writerow(["# Integration Time (ms)", spectrum_data["integration_time"]])
                writer.writerow(["# Average Count", spectrum_data["average_count"]])
                writer.writerow([])
                writer.writerow(["Wavelength (nm)", "Intensity"])
                for wl, intensity in zip(spectrum_data["wavelengths"], spectrum_data["intensities"]):
                    writer.writerow([wl, intensity])
            return True
        except Exception:
            return False

    def save_spectrum_json(self, spectrum_data: Dict[str, Any], file_path: str) -> bool:
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spectrum_data, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "spectrometer_id": self.spectrometer_id,
            "is_connected": self.is_connected,
            "serial_number": self.serial_number,
            "type_name": self.type_name,
            "integration_time": self.integration_time,
            "average_count": self.average_count,
            "pixel_count": self.pixel_count,
            "connection": "simulated",
            "dll_path": self.dll_path,
        }
