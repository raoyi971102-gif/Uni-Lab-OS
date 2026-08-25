"""偶氮反应工作站整站模拟器。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .devices import (
    SimulatedPeristalticPump,
    SimulatedSpectrometer,
    SimulatedTemperatureController,
)


class SimulatedAzoWorkstation:
    """整站模拟器，覆盖泵、温控、光谱仪和偶氮反应流程。"""

    def __init__(
        self,
        pump_a_address: int = 5,
        pump_b_address: int = 6,
        pump_a_flow_ratio: float = 1.0,
        pump_b_flow_ratio: float = 1.0,
        temp_controller_address: int = 1,
        spectrometer_integration_time: float = 50.0,
        spectrometer_average_count: int = 3,
        data_save_dir: str | None = None,
        thermal_rate_deg_per_sec: float = 1.5,
        spectrum_noise_std: float = 20.0,
        **kwargs,
    ):
        self.data_save_dir = Path(data_save_dir or Path.cwd() / "azo_simulator_data")
        self.data_save_dir.mkdir(parents=True, exist_ok=True)
        self.pump_a = SimulatedPeristalticPump(
            pump_id="pump_a",
            modbus_address=pump_a_address,
            flow_to_rpm_ratio=pump_a_flow_ratio,
        )
        self.pump_b = SimulatedPeristalticPump(
            pump_id="pump_b",
            modbus_address=pump_b_address,
            flow_to_rpm_ratio=pump_b_flow_ratio,
        )
        self.temperature_controller = SimulatedTemperatureController(
            controller_id="temp_controller",
            modbus_address=temp_controller_address,
            thermal_rate_deg_per_sec=thermal_rate_deg_per_sec,
        )
        self.spectrometer = SimulatedSpectrometer(
            spectrometer_id="spectrometer",
            integration_time=spectrometer_integration_time,
            average_count=spectrometer_average_count,
            noise_std=spectrum_noise_std,
        )
        self.spectrometer.connect()
        self.current_experiment_id: Optional[str] = None
        self.spectrum_data_list: list[Dict[str, Any]] = []
        self.workflow_status = "idle"

    def set_pump_flow_rates(self, flow_rate_a: float, flow_rate_b: float) -> bool:
        return self.pump_a.set_flow_rate(flow_rate_a) and self.pump_b.set_flow_rate(flow_rate_b)

    def stop_pumps(self) -> bool:
        return self.pump_a.stop() and self.pump_b.stop()

    def set_temperature(self, temperature: float) -> bool:
        return self.temperature_controller.set_target_temperature(temperature)

    def start_heating(self, temperature: Optional[float] = None) -> bool:
        return self.temperature_controller.start_heating(temperature)

    def start_temperature_control(self, temperature: Optional[float] = None) -> bool:
        return self.temperature_controller.start_temperature_control(temperature)

    def stop_heating(self, temperature: float = 25.0) -> bool:
        return self.temperature_controller.stop_heating(temperature)

    def read_temperature(self) -> Optional[float]:
        return self.temperature_controller.read_actual_temperature()

    def acquire_spectrum(self) -> Optional[Dict[str, Any]]:
        return self.spectrometer.acquire_spectrum()

    def save_spectrum(self, spectrum_data: Dict[str, Any], format: str = "csv") -> bool:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.current_experiment_id:
            filename = f"{self.current_experiment_id}_{timestamp}.{format}"
        else:
            filename = f"spectrum_{timestamp}.{format}"
        file_path = self.data_save_dir / filename

        if format == "csv":
            return self.spectrometer.save_spectrum_csv(spectrum_data, str(file_path))
        if format == "json":
            return self.spectrometer.save_spectrum_json(spectrum_data, str(file_path))
        return False

    def wait_seconds(self, seconds: float) -> bool:
        if seconds < 0:
            return False
        end_time = time.time() + seconds
        while time.time() < end_time:
            if self.workflow_status == "stopping":
                return False
            time.sleep(min(0.1, max(0.0, end_time - time.time())))
        return True

    def wait_until_temperature_stable(
        self,
        target_temperature: float,
        tolerance: float = 1.0,
        timeout: float = 300.0,
        check_interval: float = 2.0,
    ) -> bool:
        if tolerance < 0 or timeout < 0 or check_interval <= 0:
            return False
        start_time = time.time()
        while time.time() - start_time <= timeout:
            if self.workflow_status == "stopping":
                return False
            actual_temperature = self.read_temperature()
            if actual_temperature is not None and abs(actual_temperature - target_temperature) <= tolerance:
                return True
            time.sleep(check_interval)
        return False

    def run_pumps_for(self, flow_rate_a: float, flow_rate_b: float, duration: float) -> bool:
        if duration < 0:
            return False
        if not self.set_pump_flow_rates(flow_rate_a, flow_rate_b):
            return False
        completed = False
        try:
            completed = self.wait_seconds(duration)
        finally:
            stop_success = self.stop_pumps()
        return completed and stop_success

    def acquire_spectrum_series(self, duration: float, interval: float, save: bool = True) -> Dict[str, Any]:
        if duration < 0 or interval <= 0:
            return {"success": False, "error": "invalid duration or interval", "spectra_collected": 0}
        if self.current_experiment_id is None:
            self.current_experiment_id = f"azo_sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        start_time = time.time()
        next_spectrum_time = start_time
        collected_count = 0
        saved_count = 0
        while time.time() - start_time <= duration:
            if self.workflow_status == "stopping":
                break
            now = time.time()
            if now >= next_spectrum_time:
                spectrum_data = self.acquire_spectrum()
                if spectrum_data:
                    spectrum_data["experiment_id"] = self.current_experiment_id
                    spectrum_data["elapsed_time"] = now - start_time
                    spectrum_data["temperature_actual"] = self.read_temperature()
                    self.spectrum_data_list.append(spectrum_data)
                    collected_count += 1
                    if save and self.save_spectrum(spectrum_data, format="csv"):
                        saved_count += 1
                next_spectrum_time += interval
            time.sleep(0.05)

        return {
            "success": True,
            "experiment_id": self.current_experiment_id,
            "spectra_collected": collected_count,
            "spectra_saved": saved_count,
            "duration": duration,
            "interval": interval,
        }

    def run_azo_reaction(
        self,
        flow_rate_a: float = 1.0,
        flow_rate_b: float = 1.0,
        temperature: float = 25.0,
        duration: float = 60.0,
        spectrum_interval: float = 10.0,
        temperature_wait_timeout: float = 5.0,
    ) -> bool:
        self.workflow_status = "running"
        self.current_experiment_id = f"azo_sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.spectrum_data_list = []
        try:
            if not self.start_temperature_control(temperature):
                self.workflow_status = "error"
                return False
            self.wait_until_temperature_stable(
                temperature,
                tolerance=1.0,
                timeout=temperature_wait_timeout,
                check_interval=0.2,
            )
            if not self.set_pump_flow_rates(flow_rate_a, flow_rate_b):
                self.workflow_status = "error"
                return False
            series_result = self.acquire_spectrum_series(duration, spectrum_interval, save=True)
            self.stop_pumps()
            self.stop_heating()
            self._save_experiment_summary()
            self.workflow_status = "completed" if series_result["success"] else "error"
            return bool(series_result["success"])
        except Exception:
            self.stop_pumps()
            self.stop_heating()
            self.workflow_status = "error"
            return False

    def stop_workflow(self, emergency: bool = False) -> bool:
        self.workflow_status = "stopping"
        self.stop_pumps()
        self.stop_heating()
        if self.spectrum_data_list and not emergency:
            self._save_experiment_summary()
        self.workflow_status = "stopped"
        return True

    def get_workstation_status(self) -> Dict[str, Any]:
        return {
            "workstation_id": "azo_simulated_workstation",
            "workflow_status": self.workflow_status,
            "current_experiment_id": self.current_experiment_id,
            "pump_a": self.pump_a.get_status(),
            "pump_b": self.pump_b.get_status(),
            "temperature_controller": self.temperature_controller.get_status(),
            "spectrometer": self.spectrometer.get_status(),
            "spectra_collected": len(self.spectrum_data_list),
            "data_save_dir": str(self.data_save_dir),
            "simulated": True,
        }

    def _save_experiment_summary(self) -> None:
        if not self.spectrum_data_list or not self.current_experiment_id:
            return
        summary_file = self.data_save_dir / f"{self.current_experiment_id}_summary.json"
        summary = {
            "experiment_id": self.current_experiment_id,
            "start_time": self.spectrum_data_list[0]["timestamp"],
            "end_time": self.spectrum_data_list[-1]["timestamp"],
            "total_spectra": len(self.spectrum_data_list),
            "status": self.get_workstation_status(),
        }
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
