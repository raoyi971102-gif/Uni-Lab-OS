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
from unilabos.devices.workstation.szlab_poly_studio.plc import wait_variable_true
from unilabos.devices.workstation.szlab_poly_studio.magnetic_stirring.magnetic_stirring import (
    SzlabMixerMagneticStirrerDevice,
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


def test_szlab_wait_variable_true_reuses_read_variable_and_interval(monkeypatch):
    class FakeReader:
        def __init__(self):
            self.values = [False, False, True]
            self.reads = []

        def read_variable(self, name, use_cache=False):
            self.reads.append((name, use_cache))
            return self.values.pop(0)

    sleeps = []
    reader = FakeReader()
    monkeypatch.setattr(
        "unilabos.devices.workstation.szlab_poly_studio.plc.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    assert wait_variable_true(reader, "S05加工完成", timeout=5.0, interval=1.0) is True
    assert reader.reads == [
        ("S05加工完成", False),
        ("S05加工完成", False),
        ("S05加工完成", False),
    ]
    assert sleeps == [1.0, 1.0]


def test_szlab_photoshotting_device_is_ast_scannable_from_own_package():
    root = Path("unilabos/devices/workstation/szlab_poly_studio/photoshotting")
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = scan_directory(root, python_path=Path(".").resolve(), executor=executor)

    assert set(result["devices"]) == {"szlab_mixer_photoshotting"}
    actions = result["devices"]["szlab_mixer_photoshotting"]["actions"]
    assert list(actions) == ["take_photo"]


def test_szlab_magnetic_stirrer_device_is_ast_scannable_from_own_package():
    root = Path("unilabos/devices/workstation/szlab_poly_studio/magnetic_stirring")
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = scan_directory(root, python_path=Path(".").resolve(), executor=executor)

    assert set(result["devices"]) == {"szlab_mixer_stirrer"}
    actions = result["devices"]["szlab_mixer_stirrer"]["actions"]
    assert list(actions) == ["run_stirring"]


def test_szlab_magnetic_stirrer_run_stirring_writes_s041_parameters():
    class FakePlcGateway:
        def __init__(self):
            self.reads = []
            self.writes = []
            self.done_values = [False, False, True]

        def read_variable(self, name, use_cache=False):
            self.reads.append(name)
            if name == "S041允许加工":
                return True
            if name == "S041加工完成":
                return self.done_values.pop(0)
            raise KeyError(name)

        def write_variable(self, name, value):
            self.writes.append((name, value))
            return True

    gateway = FakePlcGateway()
    device = SzlabMixerMagneticStirrerDevice(
        url="opc.tcp://127.0.0.1:0/",
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.run_stirring(
        position=1,
        mode=3,
        speed=300,
        temperature=60,
        duration=30,
        safe_temperature=80,
    )

    assert result["success"] is True
    assert result["data"]["station"] == "S041"
    assert gateway.reads == ["S041允许加工", "S041加工完成", "S041加工完成", "S041加工完成"]
    assert gateway.writes == [
        ("S041磁搅工艺选择", 0),
        ("磁搅速度设置_上位机[0]", 0),
        ("磁搅温度设置_上位机[0]", 0),
        ("磁搅时间设置_上位机[0]", 30000),
        ("磁搅安全温度设置_上位机[0]", 0),
        ("S041磁搅工艺选择", 3),
        ("磁搅速度设置_上位机[0]", 300),
        ("磁搅温度设置_上位机[0]", 60),
        ("磁搅时间设置_上位机[0]", 30000),
        ("磁搅安全温度设置_上位机[0]", 80),
        ("S041参数写入完成", True),
        ("S041磁搅工艺选择", 0),
        ("磁搅速度设置_上位机[0]", 0),
        ("磁搅温度设置_上位机[0]", 0),
        ("磁搅时间设置_上位机[0]", 30000),
        ("磁搅安全温度设置_上位机[0]", 0),
        ("S041参数写入完成", False),
    ]


def test_szlab_magnetic_stirrer_waits_for_done_timeout(monkeypatch):
    class FakePlcGateway:
        def __init__(self):
            self.reads = []

        def read_variable(self, name, use_cache=False):
            self.reads.append(name)
            values = {
                "S041允许加工": True,
                "S041加工完成": False,
            }
            return values[name]

        def write_variable(self, name, value):
            return True

    sleeps = []
    ticks = iter([0.0, 0.0, 0.0, 0.0, 1.1])
    monkeypatch.setattr("unilabos.devices.workstation.szlab_poly_studio.plc.time.time", lambda: next(ticks))
    monkeypatch.setattr(
        "unilabos.devices.workstation.szlab_poly_studio.plc.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    gateway = FakePlcGateway()
    device = SzlabMixerMagneticStirrerDevice(
        url="opc.tcp://127.0.0.1:0/",
        timeout=1.0,
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.run_stirring(position=1, mode=3)

    assert result["success"] is False
    assert result["message"] == "S041 加工完成等待超时"
    assert "S041加工完成" in gateway.reads


def test_szlab_magnetic_stirrer_uses_plc_wait_helper_when_available():
    class FakePlcGateway:
        def __init__(self):
            self.waits = []
            self.writes = []

        def wait_variable_true(self, name, timeout=300.0, interval=1.0):
            self.waits.append((name, timeout, interval))
            return True

        def read_variable(self, name, use_cache=False):
            raise AssertionError("应优先使用 wait_variable_true")

        def write_variable(self, name, value):
            self.writes.append((name, value))
            return True

    gateway = FakePlcGateway()
    device = SzlabMixerMagneticStirrerDevice(
        url="opc.tcp://127.0.0.1:0/",
        timeout=12.0,
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.run_stirring(position=1, mode=3)

    assert result["success"] is True
    assert gateway.waits == [
        ("S041允许加工", 12.0, 1.0),
        ("S041加工完成", 12.0, 1.0),
    ]


def test_szlab_magnetic_stirrer_does_not_mark_params_written_after_write_failure():
    class FakePlcGateway:
        def __init__(self):
            self.writes = []

        def read_variable(self, name, use_cache=False):
            return True

        def write_variable(self, name, value):
            self.writes.append((name, value))
            if name == "S041磁搅工艺选择":
                raise RuntimeError("写入 PLC 变量失败: S041磁搅工艺选择")
            return True

    gateway = FakePlcGateway()
    device = SzlabMixerMagneticStirrerDevice(
        url="opc.tcp://127.0.0.1:0/",
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.run_stirring(position=1, mode=3)

    assert result["success"] is False
    assert result["message"] == "写入 PLC 变量失败: S041磁搅工艺选择"
    assert ("S041参数写入完成", True) not in gateway.writes


def test_szlab_magnetic_stirrer_reset_restores_pc_to_plc_defaults():
    class FakePlcGateway:
        def __init__(self):
            self.writes = []

        def read_variable(self, name, use_cache=False):
            raise AssertionError("reset 不需要等待允许加工")

        def write_variable(self, name, value):
            self.writes.append((name, value))
            return True

    gateway = FakePlcGateway()
    device = SzlabMixerMagneticStirrerDevice(
        url="opc.tcp://127.0.0.1:0/",
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.run_stirring(position=1, reset=True)

    assert result["success"] is True
    assert result["message"] == "S041 磁搅 PC->PLC 参数已恢复初始值"
    assert gateway.writes == [
        ("S041磁搅工艺选择", 0),
        ("磁搅速度设置_上位机[0]", 0),
        ("磁搅温度设置_上位机[0]", 0),
        ("磁搅时间设置_上位机[0]", 30000),
        ("磁搅安全温度设置_上位机[0]", 0),
        ("S041参数写入完成", False),
    ]


def test_szlab_magnetic_stirrer_rejects_invalid_mode():
    device = SzlabMixerMagneticStirrerDevice(
        url="opc.tcp://127.0.0.1:0/",
        use_plc_gateway=True,
    )

    result = device.run_stirring(position=1, mode=4)

    assert result == {"success": False, "message": "磁搅工艺选择必须是 1(搅拌)、2(加热)、3(搅拌+加热)"}


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


def test_szlab_photoshotting_take_photo_polls_done_every_second_until_complete(monkeypatch):
    class FakePlcGateway:
        def __init__(self):
            self.reads = []
            self.done_values = [False, False, True]

        def read_variable(self, name, use_cache=False):
            self.reads.append(name)
            if name == "S05加工完成":
                return self.done_values.pop(0)
            values = {"S05拍照结果": 1}
            return values[name]

    sleeps = []
    monkeypatch.setattr(
        "unilabos.devices.workstation.szlab_poly_studio.plc.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    gateway = FakePlcGateway()
    device = SzlabMixerPhotoShottingDevice(
        url="opc.tcp://127.0.0.1:0/",
        timeout=5.0,
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.take_photo(sample_id="sample-1", require_material=True)

    assert result["success"] is True
    assert result["data"]["result"] == "OK"
    assert result["data"]["photo_url"] == ""
    assert gateway.reads == ["S05加工完成", "S05加工完成", "S05加工完成", "S05拍照结果"]
    assert sleeps == [1.0, 1.0]


def test_szlab_photoshotting_uses_plc_wait_helper_when_available():
    class FakePlcGateway:
        def __init__(self):
            self.waits = []
            self.reads = []

        def wait_variable_true(self, name, timeout=300.0, interval=1.0):
            self.waits.append((name, timeout, interval))
            return True

        def read_variable(self, name, use_cache=False):
            self.reads.append(name)
            values = {"S05拍照结果": 1}
            return values[name]

    gateway = FakePlcGateway()
    device = SzlabMixerPhotoShottingDevice(
        url="opc.tcp://127.0.0.1:0/",
        timeout=9.0,
        use_plc_gateway=True,
    )
    device.set_plc_gateway(gateway)

    result = device.take_photo(sample_id="sample-1")

    assert result["success"] is True
    assert gateway.waits == [("S05加工完成", 9.0, 1.0)]
    assert gateway.reads == ["S05拍照结果"]


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


def test_szlab_mixer_registry_actions_expose_s04_s05_robot_actions():
    preset = load_preset("szlab_mixer")

    assert preset.actions["run_stirring"].device_id == "szlab_mixer_stirrer"
    assert preset.actions["take_photo"].device_id == "szlab_mixer_photoshotting"
    assert preset.actions["submit_pick_from_magnetic_stirrer"].device_id == "szlab_mixer_robot"

    workflow = build_graph_workflow(
        flow_nodes=[
            {
                "id": "stir",
                "data": {
                    "device_id": "szlab_mixer_stirrer",
                    "method": "run_stirring",
                    "params": {"position": 1, "mode": 3, "speed": 300, "temperature": 60, "duration": 30},
                },
            },
        ],
        flow_edges=[],
        preset=preset,
    )

    assert workflow["nodes"] == [
        {
            "uuid": "stir",
            "name": "auto-run_stirring",
            "device_name": "szlab_mixer_stirrer",
            "param": {
                "position": 1,
                "mode": 3,
                "speed": 300,
                "temperature": 60,
                "duration": 30,
                "safe_temperature": 80,
                "reset": False,
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
        runtime_config=load_runtime_config("tests/szlab_poly_studio/runtime_configs/szlab_mixer_pump_runtime.json"),
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
        runtime_config=load_runtime_config("tests/szlab_poly_studio/runtime_configs/szlab_mixer_pump_runtime.json"),
    )

    assert created["pump"]["robot_addition_position"] == 7
    assert created["pump"]["robot_stirrer_position"] == 2
    assert len(created["pump"]["pipeline_route_specs"]) == 6


def test_szlab_mixer_preset_loads_own_runtime_config():
    runtime_config = _load_preset_runtime_config(load_preset("szlab_mixer"))

    assert runtime_config.device_factory.devices == {
        "szlab_poly_plc": "unilabos.devices.workstation.szlab_poly_studio.plc.SZLabPolyPLCDevice",
        "szlab_mixer_stirrer": "unilabos.devices.workstation.szlab_poly_studio.magnetic_stirring.magnetic_stirring.SzlabMixerMagneticStirrerDevice",
        "szlab_mixer_photoshotting": "unilabos.devices.workstation.szlab_poly_studio.photoshotting.photoshotting.SzlabMixerPhotoShottingDevice",
        "szlab_mixer_robot": "unilabos.devices.workstation.szlab_poly_studio.szlab_mixer.robot.SzlabMixerRobotDevice",
    }
    assert runtime_config.device_factory.plc_device_id == "szlab_poly_plc"


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
    runtime_config = load_runtime_config("tests/szlab_poly_studio/runtime_configs/szlab_mixer_pump_runtime.json")

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
