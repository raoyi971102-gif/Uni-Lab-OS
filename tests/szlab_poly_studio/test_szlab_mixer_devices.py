import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from unilabos.registry.ast_registry_scanner import scan_directory
from unilabos.devices.workstation.szlab_poly_studio.pump.sensors import (
    S06PipelineRoute,
    default_s06_pipeline_routes,
    s06_pump_valve_var,
)
from unilabos.devices.workstation.szlab_poly_studio.photoshotting.photoshotting import SzlabMixerPhotoShottingDevice
from scripts.run_workflow_local import create_local_devices, load_runtime_config
from scripts.run_workflow_local import WorkflowLogger, WorkflowNode, run_nodes
from scripts.workflow_ui import _load_preset_runtime_config, build_graph_workflow, load_preset


def test_szlab_mixer_devices_are_ast_scannable():
    root = Path("unilabos/devices/workstation/szlab_poly_studio/pump")
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = scan_directory(root, python_path=Path(".").resolve(), executor=executor)

    assert set(result["devices"]) == {"szlab_mixer_pump"}
    assert "run_solvent_addition" in result["devices"]["szlab_mixer_pump"]["actions"]
    assert "transfer_liquid" in result["devices"]["szlab_mixer_pump"]["actions"]


def test_szlab_photoshotting_device_is_ast_scannable_from_own_package():
    root = Path("unilabos/devices/workstation/szlab_poly_studio/photoshotting")
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = scan_directory(root, python_path=Path(".").resolve(), executor=executor)

    assert set(result["devices"]) == {"szlab_mixer_photoshotting"}
    actions = result["devices"]["szlab_mixer_photoshotting"]["actions"]
    assert list(actions) == ["take_photo"]


def test_szlab_photoshotting_debug_assets_use_0623_s05_variables():
    device_dir = Path("unilabos/devices/workstation/szlab_poly_studio/photoshotting")
    latest_csv = Path("unilabos/devices/workstation/szlab_poly_studio/szlab_plc_0623.csv")
    nodes_csv = device_dir / "photoshotting_nodes.csv"
    flow_path = device_dir / "photoshotting_flow.json"
    config_path = device_dir / "photoshotting_debug.json"
    expected_names = {
        "S05加工完成",
        "S05拍照结果",
    }

    latest_text = latest_csv.read_text(encoding="utf-16")
    for name in expected_names:
        assert name in latest_text

    node_names = {
        line.split(",", 2)[1]
        for line in nodes_csv.read_text(encoding="utf-8").splitlines()[1:]
        if line.strip()
    }
    assert node_names == expected_names

    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    assert flow["rules"] == []

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["virtual_opcua"]["csv"].endswith("photoshotting/photoshotting_nodes.csv")
    assert config["device"]["csv_path"] == "photoshotting/photoshotting_nodes.csv"
    assert config["device"]["opcua_node_id_map"] == {
        "S05加工完成": "ns=4;s=上位机通讯|S05加工完成",
        "S05拍照结果": "ns=4;s=上位机通讯|S05拍照结果",
    }
    assert config["action"]["name"] == "take_photo"


def test_szlab_photoshotting_take_photo_only_checks_done_and_result():
    class FakePlcGateway:
        def __init__(self):
            self.reads = []

        def read_variable(self, name, use_cache=False):
            self.reads.append(name)
            values = {
                "S05加工完成": True,
                "S05拍照结果": 1,
            }
            return values[name]

    gateway = FakePlcGateway()
    device = SzlabMixerPhotoShottingDevice(
        url="opc.tcp://127.0.0.1:0/",
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.take_photo(sample_id="sample-1", require_material=True)

    assert result["success"] is True
    assert result["data"]["result"] == "OK"
    assert result["data"]["photo_url"] == ""
    assert gateway.reads == ["S05加工完成", "S05拍照结果"]


def test_szlab_photoshotting_take_photo_fails_when_result_is_ng():
    class FakePlcGateway:
        def __init__(self):
            self.reads = []

        def read_variable(self, name, use_cache=False):
            self.reads.append(name)
            values = {
                "S05加工完成": True,
                "S05拍照结果": 2,
            }
            return values[name]

    gateway = FakePlcGateway()
    device = SzlabMixerPhotoShottingDevice(
        url="opc.tcp://127.0.0.1:0/",
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.take_photo(sample_id="sample-1")

    assert result["success"] is False
    assert result["message"] == "S05 拍照检测 NG"
    assert result["data"]["result"] == "NG"
    assert gateway.reads == ["S05加工完成", "S05拍照结果"]


def test_szlab_poly_plc_uses_node_id_map_without_browsing(monkeypatch, tmp_path):
    pytest.importorskip("pylabrobot")
    from unilabos.devices.workstation.szlab_poly_studio.plc import SZLabPolyPLCDevice

    csv_path = tmp_path / "s05.csv"
    csv_path.write_text(
        "序号,变量名,数据类型\n"
        "1,S05加工完成,BOOL\n"
        "2,S05拍照结果,INT\n",
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, url):
            self.url = url
            self.connected = False

        def connect(self):
            self.connected = True

    def fail_find_nodes(self):
        raise AssertionError("已提供 NodeId map 时不应浏览 OPC UA 地址空间")

    monkeypatch.setattr("unilabos.devices.workstation.szlab_poly_studio.plc.Client", FakeClient)
    monkeypatch.setattr(SZLabPolyPLCDevice, "_find_nodes", fail_find_nodes)

    device = SZLabPolyPLCDevice(
        url="opc.tcp://127.0.0.1:4840/",
        csv_path=str(csv_path),
        opcua_node_id_map={
            "S05加工完成": "ns=4;s=上位机通讯|S05加工完成",
            "S05拍照结果": "ns=4;s=上位机通讯|S05拍照结果",
        },
    )

    assert device.client.connected is True
    assert device.use_node("S05加工完成").node_id == "ns=4;s=上位机通讯|S05加工完成"
    assert device.use_node("S05拍照结果").node_id == "ns=4;s=上位机通讯|S05拍照结果"


def test_szlab_mixer_keeps_pipeline_route_helpers():
    route = S06PipelineRoute(control_valve=11, absolute_position=21)
    routes = default_s06_pipeline_routes()

    assert route.control_valve == 11
    assert route.absolute_position == 21
    assert s06_pump_valve_var(1) == "S06注射泵1控制阀"
    assert (1, "aspirate") in routes


def test_szlab_mixer_registry_actions_only_expose_s06_pump():
    preset = load_preset("szlab_mixer")

    assert list(preset.actions) == ["transfer_liquid", "run_solvent_addition"]
    assert preset.actions["transfer_liquid"].device_id == "szlab_mixer_pump"
    assert preset.actions["run_solvent_addition"].device_id == "szlab_mixer_pump"

    workflow = build_graph_workflow(
        flow_nodes=[
            {
                "id": "pump",
                "data": {
                    "device_id": "szlab_mixer_pump",
                    "method": "run_solvent_addition",
                    "params": {"pump": 1, "volume": 1, "skip_robot": True},
                },
            },
        ],
        flow_edges=[],
        preset=preset,
    )

    assert workflow["nodes"] == [
        {
            "uuid": "pump",
            "name": "auto-run_solvent_addition",
            "device_name": "szlab_mixer_pump",
            "param": {
                "pump": 1,
                "volume": 1,
                "volume_pump_1": 0,
                "volume_pump_2": 0,
                "skip_level_check": False,
                "skip_robot": True,
                "beaker_true_means_present": True,
            },
        },
    ]
    assert workflow["edges"] == []


def test_szlab_mixer_device_creation_ignores_csv_path(monkeypatch, tmp_path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(
        """
        {
          "nodes": [
            {
              "id": "szlab_mixer_pump",
              "config": {"url": "opc.tcp://example:50001", "timeout": 30}
            }
          ],
          "links": []
        }
        """,
        encoding="utf-8",
    )
    created = {}

    class FakeDevice:
        def __init__(self, **kwargs):
            created[len(created)] = kwargs

    monkeypatch.setattr("scripts.run_workflow_local._load_class", lambda class_path: FakeDevice)

    devices = create_local_devices(
        graph_file=graph_path,
        csv_path=Path("/tmp/invalid.csv"),
        runtime_config=load_runtime_config("tests/szlab_poly_studio/runtime_configs/szlab_mixer_runtime.json"),
    )

    assert set(devices) == {"szlab_mixer_pump"}
    assert created == {0: {"url": "opc.tcp://example:50001", "timeout": 300.0}}


def test_production_graph_passes_robot_and_pipeline_specs(monkeypatch):
    created = {}

    class FakePump:
        def __init__(self, **kwargs):
            created["pump"] = kwargs

    def fake_load(class_path: str):
        if class_path.endswith(".pump.SzlabMixerPumpDevice"):
            return FakePump
        raise AssertionError(class_path)

    monkeypatch.setattr("scripts.run_workflow_local._load_class", fake_load)

    create_local_devices(
        graph_file=Path("tests/szlab_poly_studio/fixtures/szlab_mixer_pump_production_graph.json"),
        runtime_config=load_runtime_config("tests/szlab_poly_studio/runtime_configs/szlab_mixer_runtime.json"),
    )

    assert created["pump"]["robot_addition_position"] == 7
    assert created["pump"]["robot_stirrer_position"] == 2
    assert len(created["pump"]["pipeline_route_specs"]) == 6


def test_szlab_mixer_preset_loads_own_runtime_config():
    runtime_config = _load_preset_runtime_config(load_preset("szlab_mixer"))

    assert runtime_config.device_factory.devices == {
        "szlab_mixer_pump": "unilabos.devices.workstation.szlab_poly_studio.pump.pump.SzlabMixerPumpDevice",
    }
    assert runtime_config.device_factory.plc_class == ""


def test_szlab_mixer_run_nodes_samples_current_device_variables():
    class FakePump:
        def __init__(self):
            self.sampled = []

        def get_variables(self, variable_names, use_cache=False):
            self.sampled.append(list(variable_names))
            return {name: {"success": True, "value": False} for name in variable_names}

        def get_opc_variable_metadata(self, variable_name):
            return variable_name, f"ns=2;s={variable_name}"

        def run_solvent_addition(self, pump=1, volume=1, skip_robot=True):
            return {"success": True}

    pump = FakePump()
    events = []
    runtime_config = load_runtime_config("tests/szlab_poly_studio/runtime_configs/szlab_mixer_runtime.json")

    run_nodes(
        [
            WorkflowNode(
                uuid="pump",
                name="auto-run_solvent_addition",
                device_name="szlab_mixer_pump",
                param={"pump": 1, "volume": 1, "skip_robot": True},
            )
        ],
        {"szlab_mixer_pump": pump},
        logger=WorkflowLogger(writer=lambda message, **kwargs: events.append((message, kwargs))),
        runtime_config=runtime_config,
    )

    assert pump.sampled[0] == [
        "S06准备信号",
        "S06允许加工",
        "S06工艺选择",
        "S06_1号溶液添加量",
        "S06_2号溶液添加量",
        "S06参数写入完成",
        "S06加工完成",
        "传感器状态_上位机[3].NO[1]",
        "传感器状态_上位机[4].NO[12]",
        "传感器状态_上位机[5].NO[1]",
    ]
    assert pump.sampled[1] == pump.sampled[0]
    assert any(message.startswith("OPC状态采样") for message, _ in events)
