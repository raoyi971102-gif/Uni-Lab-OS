"""SZLab S09 移液站单独调试脚本。

运行::

    PYTHONPATH=. python -m unilabos.devices.workstation.szlab_poly_studio.s09_pipetting_station.debug_pipetting_station
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_DEVICE_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _DEVICE_DIR / "pipetting_station_debug.json"


def run_from_config(config_path: Path = _DEFAULT_CONFIG, *, production: bool = False) -> dict:
    from unilabos.devices.workstation.szlab_poly_studio.s09_pipetting_station.pipetting_station import (
        SzlabMixerPipettingStationDevice,
    )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    device_cfg = dict(config["device"])
    action_cfg = dict(config["action"])
    action_name = action_cfg.pop("name")
    url = config["opcua"]["production_url" if production else "virtual_url"]
    if not production:
        device_cfg["csv_path"] = str((_DEVICE_DIR / "pipetting_station_nodes.csv").resolve())
    device = SzlabMixerPipettingStationDevice(url=url, **device_cfg)
    try:
        result = getattr(device, action_name)(**action_cfg)
        print(result)
        return result
    finally:
        device.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="SZLab S09 移液站本地调试")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()
    run_from_config(args.config, production=args.production)


if __name__ == "__main__":
    main()
