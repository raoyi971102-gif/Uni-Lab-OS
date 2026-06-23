from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from unilabos.registry.ast_registry_scanner import scan_directory
from unilabos.devices.workstation.szlab_poly_studio.pump.sensors import (
    S06PipelineRoute,
    default_s06_pipeline_routes,
    s06_pump_valve_var,
)
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
