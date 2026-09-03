import csv
import importlib
from pathlib import Path

from unilabos.registry.decorators import get_topic_config


xuse_module = importlib.import_module("unilabos.devices.workstation.XUSE.XUSE")
XUSEDevice = xuse_module.XUSEDevice
XUSE_DIR = Path(xuse_module.__file__).resolve().parent

FAULT_VARIABLES = [
    (
        "注粉转盘出现故障",
        "Powder_Injection_Turntable_Fault",
        "powder_injection_turntable_fault",
    ),
    ("加粉X轴出现故障", "Powder_X_Axis_Fault", "powder_x_axis_fault"),
    (
        "防掉粉电机出现故障",
        "Powder_Drop_Prevention_Motor_Fault",
        "powder_drop_prevention_motor_fault",
    ),
    ("加粉Z轴出现故障", "Powder_Z_Axis_Fault", "powder_z_axis_fault"),
    (
        "加粉旋转旋转轴出现故障",
        "Powder_Rotation_Axis_Fault",
        "powder_rotation_axis_fault",
    ),
]


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def test_powder_fault_variables_are_in_real_and_sim_tables():
    real_rows = _read_csv(XUSE_DIR / "xuse_variables.csv")
    sim_rows = _read_csv(XUSE_DIR / "xuse_variables_sim.csv")

    for chinese_name, english_name, _ in FAULT_VARIABLES:
        real_matches = [row for row in real_rows if row["Name"] == chinese_name]
        sim_matches = [row for row in sim_rows if row["Name"] == chinese_name]
        assert len(real_matches) == 1
        assert len(sim_matches) == 1
        assert real_matches[0] == {
            "Name": chinese_name,
            "EnglishName": english_name,
            "NodeType": "VARIABLE",
            "DataType": "BOOLEAN",
            "NodeLanguage": "Chinese",
            "NodeId": f"ns=4;s=uniab|{chinese_name}",
        }
        assert sim_matches[0]["EnglishName"] == english_name
        assert sim_matches[0]["DataType"] == "BOOLEAN"
        assert sim_matches[0]["NodeId"] == f"ns=2;s=XUSE.设备 1.{chinese_name}"


def test_powder_fault_statuses_are_cached_and_published_every_five_seconds():
    device = object.__new__(XUSEDevice)
    device._arm_status_cache = {
        english_name: index % 2 == 0
        for index, (_, english_name, _) in enumerate(FAULT_VARIABLES)
    }

    for index, (_, english_name, method_name) in enumerate(FAULT_VARIABLES):
        assert english_name in device._arm_status_cache
        method = getattr(XUSEDevice, method_name)
        config = get_topic_config(method)
        assert config["period"] == 5.0
        assert config["name"] is None
        assert method(device) is (index % 2 == 0)
