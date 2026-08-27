import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from unilabos.devices.workstation.AI4C.AI4C import AI4CDevice


def _bare_device(*, create_placeholder: bool) -> AI4CDevice:
    """构造不连接 PLC 的轻量实例，只测试前端资源迁移。"""
    device = object.__new__(AI4CDevice)
    device.create_placeholder_resource_when_missing = create_placeholder
    return device


def test_placeholder_creation_switch_defaults_to_true() -> None:
    parameter = inspect.signature(AI4CDevice.__init__).parameters[
        "create_placeholder_resource_when_missing"
    ]
    assert parameter.default is True


def test_ai4c_graph_disables_placeholder_creation() -> None:
    graph_path = (
        Path(__file__).parents[3]
        / "unilabos"
        / "devices"
        / "workstation"
        / "AI4C"
        / "AI4C_station.json"
    )
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    station = next(node for node in graph["nodes"] if node["id"] == "AI4C_station")

    assert station["config"]["create_placeholder_resource_when_missing"] is False


def test_missing_frontend_resource_does_not_create_placeholder_or_raise() -> None:
    device = _bare_device(create_placeholder=False)
    device.deck = SimpleNamespace(warehouses={"孔板上料架": object()})
    device._held_well_plate = object()
    device._get_warehouse_resource = lambda *_args: None
    device._create_placeholder_resource = lambda *_args: (_ for _ in ()).throw(
        AssertionError("关闭开关后不应创建占位资源")
    )
    sync_calls = []
    device._sync_resource_to_frontend = lambda: sync_calls.append(True)

    device._pick_resource_from_warehouse(
        "孔板上料架", "1", "well_plate", "_held_well_plate"
    )

    assert device._held_well_plate is None
    assert sync_calls == []


def test_place_without_held_resource_does_not_generate_frontend_resource() -> None:
    device = _bare_device(create_placeholder=False)
    device.deck = SimpleNamespace(warehouses={"移液站": object()})
    device._held_well_plate = None
    device._create_placeholder_resource = lambda *_args: (_ for _ in ()).throw(
        AssertionError("关闭开关后不应创建占位资源")
    )
    device._get_warehouse_resource = lambda *_args: (_ for _ in ()).throw(
        AssertionError("无持有资源时应直接跳过前端放料")
    )

    device._place_held_resource_to_warehouse(
        "移液站", "1", "well_plate", "_held_well_plate"
    )

    assert device._held_well_plate is None


def test_existing_frontend_resource_is_still_moved_when_switch_is_off() -> None:
    class _Warehouse:
        def __init__(self) -> None:
            self.unassigned = []

        def unassign_child_resource(self, resource) -> None:
            self.unassigned.append(resource)

    device = _bare_device(create_placeholder=False)
    warehouse = _Warehouse()
    resource = SimpleNamespace(name="plate_1")
    device.deck = SimpleNamespace(warehouses={"孔板上料架": warehouse})
    device._get_warehouse_resource = lambda *_args: resource
    sync_calls = []
    device._sync_resource_to_frontend = lambda: sync_calls.append(True)

    device._pick_resource_from_warehouse(
        "孔板上料架", "1", "well_plate", "_held_well_plate"
    )

    assert warehouse.unassigned == [resource]
    assert device._held_well_plate is resource
    assert sync_calls == [True]
