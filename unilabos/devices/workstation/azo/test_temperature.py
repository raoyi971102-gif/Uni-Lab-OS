"""
温控器调试脚本

使用方法:
    python test_temperature.py
    python test_temperature.py COM10 38400

命令:
    <温度>     设置目标温度并打开输出使能
    start <温度>  同上
    enable     只打开输出使能
    disable / stop  只关闭输出使能（不改目标温度）
    read       读取实际温度
    status / q
"""

from __future__ import annotations

import sys

from unilabos.devices.workstation.azo.rs485_serial import AzoRs485Serial
from unilabos.devices.workstation.azo.temperature_controller import AzoTemperatureController


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM10"
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 38400

    serial_dev = AzoRs485Serial(port=port, baudrate=baudrate)
    controller = AzoTemperatureController(device_id="temp_controller", port="serial_485", address=1)
    controller.transact = serial_dev.transact

    print("=" * 50)
    print("温控器调试工具")
    print("=" * 50)
    print("命令: <温度> / start <温度> / enable / disable / stop / read / status / q")
    print("=" * 50)

    try:
        while True:
            user_input = input(">>> ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"q", "quit", "exit"}:
                print(controller.stop())
                break
            if user_input.lower() == "status":
                print(
                    f"目标: {controller.temp_target}°C  实际: {controller.temp}°C  "
                    f"使能: {controller.output_enabled}  状态: {controller.status}"
                )
                continue
            if user_input.lower() == "read":
                print(controller.read_value())
                continue
            if user_input.lower() in {"stop", "disable"}:
                print(controller.stop())
                continue
            if user_input.lower() == "enable":
                print(controller.enable_output())
                continue
            parts = user_input.split()
            if parts[0].lower() == "start" and len(parts) == 2:
                print(controller.start(float(parts[1])))
                continue
            try:
                print(controller.start(float(user_input)))
            except ValueError:
                print("请输入温度数字，或 start <温度> / enable / disable / stop / read / status / q")
    finally:
        serial_dev.close()


if __name__ == "__main__":
    main()
