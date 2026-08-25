"""运行一次偶氮工作站模拟实验。"""

from __future__ import annotations

import argparse
import json

from azo_simulator import SimulatedAzoWorkstation


def main() -> None:
    parser = argparse.ArgumentParser(description="Azo 工作站模拟实验")
    parser.add_argument("--flow-a", type=float, default=2.0, help="泵A流速，mL/min")
    parser.add_argument("--flow-b", type=float, default=1.5, help="泵B流速，mL/min")
    parser.add_argument("--temperature", type=float, default=60.0, help="目标温度，degC")
    parser.add_argument("--duration", type=float, default=5.0, help="模拟实验时长，秒")
    parser.add_argument("--spectrum-interval", type=float, default=1.0, help="光谱采集间隔，秒")
    parser.add_argument("--data-dir", default="azo_simulator_data", help="模拟数据保存目录")
    args = parser.parse_args()

    workstation = SimulatedAzoWorkstation(data_save_dir=args.data_dir)
    success = workstation.run_azo_reaction(
        flow_rate_a=args.flow_a,
        flow_rate_b=args.flow_b,
        temperature=args.temperature,
        duration=args.duration,
        spectrum_interval=args.spectrum_interval,
    )

    print(json.dumps({"success": success, "status": workstation.get_workstation_status()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
