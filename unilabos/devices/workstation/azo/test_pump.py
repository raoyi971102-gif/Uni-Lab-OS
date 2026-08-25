"""
蠕动泵调试脚本

使用方法:
    python test_pump.py
    python test_pump.py COM10 38400

命令:
    <泵A流速> <泵B流速>   例如 1.0 1.5
    rpm <泵A转速> <泵B转速>  直接设 rpm
    status / stop / q
"""

from __future__ import annotations

import sys

from unilabos.devices.workstation.azo.peristaltic_pump import AzoPeristalticPump
from unilabos.devices.workstation.azo.rs485_serial import AzoRs485Serial


def attach_serial(pump: AzoPeristalticPump, serial_dev: AzoRs485Serial) -> None:
    pump.transact = serial_dev.transact


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM10"
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 38400

    serial_dev = AzoRs485Serial(port=port, baudrate=baudrate)
    pump_a = AzoPeristalticPump(device_id="pump_a", port="serial_485", address=5)
    pump_b = AzoPeristalticPump(device_id="pump_b", port="serial_485", address=6)
    attach_serial(pump_a, serial_dev)
    attach_serial(pump_b, serial_dev)

    print("=" * 50)
    print("蠕动泵调试工具")
    print("=" * 50)
    print("命令: <泵A流速 mL/min> <泵B流速 mL/min>")
    print("      rpm <泵A rpm> <泵B rpm>")
    print("      status / stop / q")
    print("=" * 50)

    try:
        while True:
            user_input = input(">>> ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"q", "quit", "exit"}:
                pump_a.stop()
                pump_b.stop()
                break
            if user_input.lower() == "status":
                print(f"泵A: {pump_a.speed} mL/min ({pump_a.rpm} rpm) {pump_a.status}")
                print(f"泵B: {pump_b.speed} mL/min ({pump_b.rpm} rpm) {pump_b.status}")
                continue
            if user_input.lower() == "stop":
                print(pump_a.stop())
                print(pump_b.stop())
                continue

            parts = user_input.split()
            try:
                if parts[0].lower() == "rpm" and len(parts) == 3:
                    print(pump_a.set_rpm(int(parts[1])))
                    print(pump_b.set_rpm(int(parts[2])))
                elif len(parts) == 2:
                    print(pump_a.set_speed(float(parts[0])))
                    print(pump_b.set_speed(float(parts[1])))
                else:
                    print("格式: <流速A> <流速B> 或 rpm <rpmA> <rpmB>")
            except ValueError:
                print("请输入有效数字")
    finally:
        serial_dev.close()


if __name__ == "__main__":
    main()
