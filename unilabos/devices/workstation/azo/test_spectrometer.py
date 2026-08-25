"""
光谱仪调试脚本

使用方法:
    python test_spectrometer.py
    python test_spectrometer.py --simulate
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from unilabos.devices.workstation.azo.spectrometer import AzoSpectrometer


def main() -> None:
    simulate = "--simulate" in sys.argv
    spectrometer = AzoSpectrometer(device_id="spectrometer", simulate=simulate)
    result = spectrometer.connect()
    print(result)
    if not result.get("success"):
        print("连接失败。可加 --simulate 进入模拟模式。")
        return

    last_spectrum = None
    print("=" * 50)
    print("光谱仪调试工具")
    print("=" * 50)
    print("命令: acquire / save / set_time <ms> / set_avg <n> / status / q")
    print("=" * 50)

    try:
        while True:
            user_input = input(">>> ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"q", "quit", "exit"}:
                break
            if user_input.lower() == "status":
                print(
                    f"连接={spectrometer.connected} 型号={spectrometer.data['type_name']} "
                    f"积分={spectrometer.integration_time}ms 平均={spectrometer.average_count}"
                )
                continue
            if user_input.lower() in {"acquire", "a"}:
                last_spectrum = spectrometer.acquire_spectrum()
                print(
                    {
                        k: last_spectrum[k]
                        for k in ("success", "message", "timestamp", "point_count", "peak_intensity", "mean_intensity")
                        if k in last_spectrum
                    }
                )
                continue
            if user_input.lower().startswith("save"):
                if not last_spectrum or not last_spectrum.get("success"):
                    print("请先采集光谱")
                    continue
                parts = user_input.split(maxsplit=1)
                filename = parts[1] if len(parts) > 1 else f"spectrum_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                print(spectrometer.save_spectrum(last_spectrum, str(Path.cwd() / filename)))
                continue
            if user_input.lower().startswith("set_time"):
                parts = user_input.split()
                if len(parts) != 2:
                    print("格式: set_time 50")
                    continue
                print(spectrometer.set_integration_time(float(parts[1])))
                continue
            if user_input.lower().startswith("set_avg"):
                parts = user_input.split()
                if len(parts) != 2:
                    print("格式: set_avg 3")
                    continue
                print(spectrometer.set_average_count(int(parts[1])))
                continue
            print("未知命令")
    finally:
        spectrometer.disconnect()


if __name__ == "__main__":
    main()
