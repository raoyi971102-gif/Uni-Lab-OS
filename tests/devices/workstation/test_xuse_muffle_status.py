import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from unilabos.registry.ast_registry_scanner import scan_directory
from unilabos.registry.decorators import get_topic_config


xuse_module = importlib.import_module("unilabos.devices.workstation.XUSE.XUSE")
XUSEDevice = xuse_module.XUSEDevice
XUSE_FILE = Path(xuse_module.__file__).resolve()
REPO_ROOT = XUSE_FILE.parents[4]


def test_muffle_furnace_status_topics_are_published_per_furnace():
    device = object.__new__(XUSEDevice)
    device._muffle_furnace_status = {
        idx: {
            "started_at": None,
            "elapsed_seconds": float(idx),
            "total_seconds": float(idx * 10),
            "set_temperature": float(idx * 100),
            "profile": [],
        }
        for idx in range(1, 7)
    }
    device._muffle_temperature_cache = [11.1, 22.2, 33.3, 44.4, 55.5, 66.6]

    expected = {
        "current_runtime": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "total_set_time": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "set_temperature": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0],
        "current_temperature": [11.1, 22.2, 33.3, 44.4, 55.5, 66.6],
    }
    for metric, values in expected.items():
        for position, value in enumerate(values, start=1):
            method_name = f"muffle_furnace_{position}_{metric}"
            method = getattr(XUSEDevice, method_name)
            config = get_topic_config(method)
            assert config["period"] == 1.0
            assert method(device) == value
    assert get_topic_config(XUSEDevice.muffle_furnace_current_runtime) == {}


def test_muffle_furnace_status_topics_are_registered_as_six_groups():
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = scan_directory(
            XUSE_FILE.parent,
            python_path=REPO_ROOT,
            executor=executor,
            include_files=[XUSE_FILE],
        )
    status = result["devices"]["XUSE_station"]["status_properties"]
    metrics = ("current_runtime", "total_set_time", "set_temperature", "current_temperature")
    for position in range(1, 7):
        for metric in metrics:
            name = f"muffle_furnace_{position}_{metric}"
            assert name in status
            assert status[name]["return_type"] == "float"
    for name in (
        "muffle_furnace_current_runtime",
        "muffle_furnace_total_set_time",
        "muffle_furnace_set_temperature",
        "muffle_furnace_current_temperature",
    ):
        assert name not in status
