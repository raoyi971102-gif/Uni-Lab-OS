from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_S08_MODULE_PATH = (
    Path("unilabos/devices/workstation/szlab_poly_studio/decap-s08/s08_cap_station.py").resolve()
)
_S08_MODULE_NAME = "unilabos_devices_szlab_decap_s08_driver"


def load_s08_cap_station_module():
    cached = sys.modules.get(_S08_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_S08_MODULE_NAME, _S08_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 S08 设备模块: {_S08_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_S08_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
