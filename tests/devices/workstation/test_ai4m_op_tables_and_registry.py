import csv
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from unilabos.devices.workstation.AI4M import AI4M as ai4m_module
from unilabos.devices.workstation.AI4M import AI4M002 as ai4m002_module
from unilabos.devices.workstation.AI4M.AI4M import AI4MDevice
from unilabos.devices.workstation.AI4M.AI4M002 import AI4M002Device
from unilabos.devices.workstation.AI4M.base_opcua_client import (
    OpcUaClientWithSubscription,
)
from unilabos.registry.ast_registry_scanner import scan_directory
from unilabos.registry.registry import Registry


REPO_ROOT = Path(__file__).parents[3]
AI4M_ROOT = REPO_ROOT / "unilabos" / "devices" / "workstation" / "AI4M"


def _read_plc_export(path: Path) -> list[dict]:
    with path.open("r", encoding="gb18030", newline="") as file:
        for _ in range(17):
            next(file)
        return list(csv.DictReader(file))


def _read_opcua_table(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


@pytest.mark.parametrize(
    ("source_name", "table_name"),
    [
        ("OP10_UniLab.csv", "opcua_nodes_OP10_UniLab.csv"),
        ("OP20_UniLab.csv", "opcua_nodes_OP20_UniLab.csv"),
    ],
)
def test_generated_opcua_table_matches_plc_export(
    source_name: str,
    table_name: str,
) -> None:
    source = _read_plc_export(AI4M_ROOT / source_name)
    table = _read_opcua_table(AI4M_ROOT / table_name)
    type_mapping = {
        "Bool": "BOOLEAN",
        "Int16": "INT16",
        "Int32": "INT32",
        "UInt16": "UINT16",
        "UInt32": "UINT32",
        "Float": "FLOAT",
        "Double": "DOUBLE",
        "String": "STRING",
        "WString": "STRING",
        "DateTime": "DATETIME",
    }

    exported_rows = table[: len(source)]
    assert [row["名称"] for row in source] == [row["Name"] for row in exported_rows]
    assert [type_mapping[row["数据类型"]] for row in source] == [
        row["DataType"] for row in exported_rows
    ]
    supplemental = table[len(source) :]
    if source_name == "OP20_UniLab.csv":
        assert [row["Name"] for row in supplemental] == [
            "电解池1加工完成",
            "电解池2加工完成",
        ]
        assert [row["EnglishName"] for row in supplemental] == [
            "Electrolytic_Cell_1_Done",
            "Electrolytic_Cell_2_Done",
        ]
    else:
        assert supplemental == []
    assert len({row["EnglishName"] for row in table}) == len(table)
    node_root = Path(source_name).stem
    assert all(row["NodeId"].startswith(f"ns=4;s={node_root}|") for row in table)
    assert all(row["NodeId"].endswith(row["Name"]) for row in table)


def test_ai4m_opcua_base_is_exact_ai4c_copy() -> None:
    ai4c_base = (
        REPO_ROOT
        / "unilabos"
        / "devices"
        / "workstation"
        / "AI4C"
        / "base_opcua_client.py"
    )
    ai4m_base = AI4M_ROOT / "base_opcua_client.py"
    assert ai4m_base.read_text(encoding="utf-8") == ai4c_base.read_text(encoding="utf-8")


def test_decorator_registry_keeps_original_action_sets() -> None:
    with ThreadPoolExecutor() as executor:
        registry = scan_directory(
            AI4M_ROOT,
            python_path=REPO_ROOT,
            executor=executor,
            include_files=[AI4M_ROOT / "AI4M.py", AI4M_ROOT / "AI4M002.py"],
        )

    assert set(registry["devices"]) == {"AI4M_station", "AI4M002_station"}
    assert set(registry["devices"]["AI4M_station"]["actions"]) == {
        "trigger_init",
        "trigger_robot_pick_beaker",
        "trigger_robot_place_beaker",
        "trigger_station_process",
    }
    assert set(registry["devices"]["AI4M002_station"]["actions"]) == {
        "trigger_electrolytic_cell_bts_reaction",
        "trigger_3axis_pick_from_electrolytic_cell_and_place_to_finished",
        "trigger_3axis_pick_from_raw_and_place_to_electrolytic_cell",
        "trigger_3axis_pick_from_raw_and_process_to_finished",
        "trigger_s02_init",
    }
    assert registry["devices"]["AI4M_station"]["auto_methods"] == {}
    assert registry["devices"]["AI4M002_station"]["auto_methods"] == {}


def test_handle_and_position_ids_have_empty_goal_defaults() -> None:
    with ThreadPoolExecutor() as executor:
        scanned = scan_directory(
            AI4M_ROOT,
            python_path=REPO_ROOT,
            executor=executor,
            include_files=[AI4M_ROOT / "AI4M.py", AI4M_ROOT / "AI4M002.py"],
        )

    expected = {
        "AI4M_station": {
            "auto-trigger_robot_pick_beaker": {
                "pick_beaker_id": None,
                "place_station_id": None,
            },
            "auto-trigger_robot_place_beaker": {
                "place_beaker_id": None,
                "pick_station_id": None,
            },
            "auto-trigger_station_process": {"station_id": None},
        },
        "AI4M002_station": {
            "auto-trigger_3axis_pick_from_raw_and_place_to_electrolytic_cell": {
                "pick_code": None,
                "electrolytic_cell_id": None,
            },
            "auto-trigger_3axis_pick_from_electrolytic_cell_and_place_to_finished": {
                "electrolytic_cell_id": None,
                "place_code": None,
            },
            "auto-trigger_3axis_pick_from_raw_and_process_to_finished": {
                "pick_code": None,
                "place_code": None,
            },
            "auto-trigger_electrolytic_cell_bts_reaction": {
                "electrolytic_cell_id": None,
                "simulate_bts": False,
            },
        },
    }

    builder = Registry()
    for device_id, action_defaults in expected.items():
        entry = builder._build_device_entry_from_ast(
            device_id,
            scanned["devices"][device_id],
        )
        actions = entry["class"]["action_value_mappings"]
        for action_name, defaults in action_defaults.items():
            for parameter, expected_default in defaults.items():
                assert actions[action_name]["goal_default"][parameter] is expected_default


def test_graph_assigns_a_different_table_to_each_device() -> None:
    for graph_name in ("AI4M.json", "AI4Msim.json"):
        graph = json.loads((AI4M_ROOT / graph_name).read_text(encoding="utf-8"))
        configs = {
            node["id"]: node["config"]
            for node in graph["nodes"]
            if node["id"] in {"AI4M_station", "AI4M002_station"}
        }
        assert configs["AI4M_station"]["csv_path"] == "opcua_nodes_OP10_UniLab.csv"
        assert configs["AI4M002_station"]["csv_path"] == "opcua_nodes_OP20_UniLab.csv"


def test_devices_create_independent_node_registries(monkeypatch) -> None:
    loaded_tables: list[tuple[object, str]] = []

    monkeypatch.setattr(
        OpcUaClientWithSubscription,
        "__init__",
        lambda self, *args, **kwargs: None,
    )
    monkeypatch.setattr(
        OpcUaClientWithSubscription,
        "load_nodes_from_csv",
        lambda self, table: loaded_tables.append((self, table)),
    )
    monkeypatch.setattr(
        ai4m_module,
        "AI4M_deck",
        lambda setup=True: SimpleNamespace(children=[], warehouses={}),
    )
    monkeypatch.setattr(
        ai4m002_module,
        "AI4M002_deck",
        lambda setup=True: SimpleNamespace(children=[], warehouses={}),
    )

    op10 = AI4MDevice(url="opc.tcp://op10:4840")
    op20 = AI4M002Device(url="opc.tcp://op20:4840")
    op10._node_registry["only_op10"] = object()

    assert op10._node_registry is not op20._node_registry
    assert op10._variables_to_find is not op20._variables_to_find
    assert "only_op10" not in op20._node_registry
    assert [(type(device).__name__, table) for device, table in loaded_tables] == [
        ("AI4MDevice", "opcua_nodes_OP10_UniLab.csv"),
        ("AI4M002Device", "opcua_nodes_OP20_UniLab.csv"),
    ]


@pytest.mark.parametrize("device_type", [AI4MDevice, AI4M002Device])
def test_boolean_read_failure_is_not_treated_as_false(device_type) -> None:
    device = object.__new__(device_type)
    device.get_node_value = lambda _name, **_kwargs: None
    with pytest.raises(RuntimeError, match="读取 OPC UA 节点失败"):
        device._read_bool("missing")


def test_wrong_variable_table_is_rejected_before_connecting() -> None:
    with pytest.raises(ValueError, match="只能使用 OP10"):
        AI4MDevice(url="opc.tcp://unused:4840", csv_path="opcua_nodes_OP20_UniLab.csv")
    with pytest.raises(ValueError, match="只能使用 OP20"):
        AI4M002Device(url="opc.tcp://unused:4840", csv_path="opcua_nodes_OP10_UniLab.csv")


def test_op10_robot_action_uses_new_unified_handshake() -> None:
    device = object.__new__(AI4MDevice)
    writes = []
    waits = []
    device._write_node = lambda name, value: writes.append((name, value))
    device._wait_until = lambda name, expected, description, **kwargs: waits.append(
        (name, expected, description, kwargs)
    )
    device._read_bool = lambda name: False

    device._run_robot_action(
        ai4m_module.RobotAction.PICK,
        ai4m_module.RobotTargetPosition.BEAKER_RACK,
        3,
        "测试取料",
    )

    assert writes == [
        ("robot_action_code", 1),
        ("robot_target_position_code", 1),
        ("robot_target_pick_place_code", 3),
        ("robot_action_trigger", True),
        ("robot_action_trigger", False),
    ]
    assert [wait[:2] for wait in waits] == [
        ("robot_idle", True),
        ("robot_action_complete", True),
        ("robot_action_complete", False),
    ]


def test_op20_axis_action_uses_new_unified_handshake() -> None:
    device = object.__new__(AI4M002Device)
    writes = []
    waits = []
    device._write_node = lambda name, value: writes.append((name, value))
    device._wait_until = lambda name, expected, description, **kwargs: waits.append(
        (name, expected, description, kwargs)
    )
    device._read_bool = lambda name: False

    device._run_axis_action(
        ai4m002_module.AxisAction.PLACE,
        ai4m002_module.AxisTargetPosition.FINISHED_ELECTRODE,
        7,
        "测试放料",
    )

    assert writes == [
        ("axis_action_code", 2),
        ("axis_target_position_code", 6),
        ("axis_target_pick_place_code", 7),
        ("axis_action_trigger", True),
        ("axis_action_trigger", False),
    ]
    assert [wait[:2] for wait in waits] == [
        ("axis_idle", True),
        ("axis_action_complete", True),
        ("axis_action_complete", False),
    ]


def test_bts_start_failure_is_propagated_to_action_layer() -> None:
    device = object.__new__(AI4M002Device)
    writes = []
    device._wait_until = lambda *args, **kwargs: None
    device._write_node = lambda name, value: writes.append((name, value))
    device.bts_start_cp_test = lambda **kwargs: {
        "success": False,
        "message": "模拟BTS启动失败",
    }

    with pytest.raises(RuntimeError, match="模拟BTS启动失败"):
        device.trigger_electrolytic_cell_bts_reaction(1)

    assert writes == [
        ("Electrolytic_Cell_1_Done", False),
        ("stirrer_1_start", True),
        ("stirrer_1_start", False),
    ]


def test_op20_stirrer_index_matches_station_number() -> None:
    assert int(ai4m002_module.AxisTargetPosition.STIRRER_1) == 2
    assert int(ai4m002_module.AxisTargetPosition.STIRRER_2) == 3

    rows = _read_opcua_table(AI4M_ROOT / "opcua_nodes_OP20_UniLab.csv")
    index_0_names = [
        row["EnglishName"] for row in rows if row["Name"].startswith("磁搅控制[0].")
    ]
    index_1_names = [
        row["EnglishName"] for row in rows if row["Name"].startswith("磁搅控制[1].")
    ]
    assert index_0_names and all(name.startswith("stirrer_2_") for name in index_0_names)
    assert index_1_names and all(name.startswith("stirrer_1_") for name in index_1_names)


def test_bts_simulation_skips_api_and_completes_plc_handshake() -> None:
    device = object.__new__(AI4M002Device)
    writes = []
    waits = []
    device._wait_until = lambda name, expected, description, **kwargs: waits.append(
        (name, expected, description, kwargs)
    )
    device._write_node = lambda name, value: writes.append((name, value))
    device.bts_start_cp_test = lambda **_kwargs: pytest.fail("仿真模式不应调用 BTS API")
    device._sample_results = lambda *_args, **_kwargs: []

    result = device.trigger_electrolytic_cell_bts_reaction(1, simulate_bts=True)

    assert result["bts_result"]["simulated"] is True
    assert writes == [
        ("Electrolytic_Cell_1_Done", False),
        ("stirrer_1_start", True),
        ("Electrolytic_Cell_1_Done", True),
        ("stirrer_1_start", False),
    ]
    assert [wait[:2] for wait in waits] == [
        ("stirrer_1_request_process", True),
        ("stirrer_1_occupied", True),
        ("stirrer_1_complete", True),
        ("stirrer_1_complete", False),
    ]
