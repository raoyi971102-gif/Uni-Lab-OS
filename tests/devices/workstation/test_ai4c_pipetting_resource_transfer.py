from types import SimpleNamespace

from unilabos.devices.workstation.AI4C.AI4C import (
    DEFAULT_PIPETTING_DECK_ID,
    DEFAULT_PIPETTING_DEVICE_ID,
    PIPETTING_WAREHOUSE_NAME,
    AI4CDevice,
)


class _FakePrcxiDeck:
    def __init__(self, name: str = DEFAULT_PIPETTING_DECK_ID) -> None:
        self.name = name
        self.children: list = []
        self.slots: dict = {}

    def _get_site_resource(self, idx: int):
        return self.slots.get(idx)

    def assign_child_at_slot(self, resource, slot: int, reassign: bool = True) -> None:
        idx = slot - 1
        old = self.slots.get(idx)
        if old is not None and old is not resource:
            self.unassign_child_resource(old)
        parent = getattr(resource, "parent", None)
        if parent is not None and parent is not self and hasattr(parent, "unassign_child_resource"):
            parent.unassign_child_resource(resource)
        resource.parent = self
        self.slots[idx] = resource
        if resource not in self.children:
            self.children.append(resource)

    def unassign_child_resource(self, resource) -> None:
        self.children = [child for child in self.children if child is not resource]
        self.slots = {idx: item for idx, item in self.slots.items() if item is not resource}
        resource.parent = None


def _bare_device(*, create_placeholder: bool = False) -> AI4CDevice:
    device = object.__new__(AI4CDevice)
    device.create_placeholder_resource_when_missing = create_placeholder
    device.pipetting_device_id = DEFAULT_PIPETTING_DEVICE_ID
    device.pipetting_deck_id = DEFAULT_PIPETTING_DECK_ID
    device._external_warehouses = {
        PIPETTING_WAREHOUSE_NAME: {
            "device_id": DEFAULT_PIPETTING_DEVICE_ID,
            "deck_id": DEFAULT_PIPETTING_DECK_ID,
        }
    }
    device.deck = SimpleNamespace(warehouses={"孔板上料架": object()})
    device._ros_node = None
    device._held_well_plate = None
    device._placeholder_resource_counter = 0
    return device


def test_place_to_pipetting_station_reparents_to_prcxi_deck() -> None:
    device = _bare_device()
    plate = SimpleNamespace(name="plate_1", parent=None, unilabos_extra={})
    device._held_well_plate = plate
    prcxi_deck = _FakePrcxiDeck()
    synced = []

    device._resolve_external_deck_and_node = lambda spec: (prcxi_deck, "prcxi-node")
    device._sync_plr_resources = lambda resources, ros_node=None: synced.append(
        (list(resources), ros_node)
    )
    device._sync_resource_to_frontend = lambda: synced.append(("ai4c", None))
    device._create_placeholder_resource = lambda *_args: (_ for _ in ()).throw(
        AssertionError("已有持有资源时不应创建占位")
    )

    device._place_held_resource_to_warehouse(
        PIPETTING_WAREHOUSE_NAME, "5", "well_plate", "_held_well_plate"
    )

    assert device._held_well_plate is None
    assert prcxi_deck._get_site_resource(4) is plate
    assert plate.parent is prcxi_deck
    assert plate.unilabos_extra["update_resource_site"] == "T5"
    assert any(resources == [prcxi_deck] and node == "prcxi-node" for resources, node in synced)


def test_pick_from_pipetting_station_unassigns_prcxi_slot() -> None:
    device = _bare_device()
    prcxi_deck = _FakePrcxiDeck()
    plate = SimpleNamespace(name="plate_1", parent=None, unilabos_extra={})
    prcxi_deck.assign_child_at_slot(plate, 5)
    synced = []

    device._resolve_external_deck_and_node = lambda spec: (prcxi_deck, "prcxi-node")
    device._sync_plr_resources = lambda resources, ros_node=None: synced.append(
        (list(resources), ros_node)
    )
    device._sync_resource_to_frontend = lambda: synced.append(("ai4c", None))

    device._pick_resource_from_warehouse(
        PIPETTING_WAREHOUSE_NAME, "5", "well_plate", "_held_well_plate"
    )

    assert device._held_well_plate is plate
    assert prcxi_deck._get_site_resource(4) is None
    assert plate.parent is None
    assert any(resources == [prcxi_deck] and node == "prcxi-node" for resources, node in synced)


def test_place_to_pipetting_station_no_longer_skips_when_not_on_ai4c_deck() -> None:
    device = _bare_device()
    device._held_well_plate = SimpleNamespace(name="plate_1", parent=None, unilabos_extra={})
    routed = []

    device._place_held_resource_to_external_deck = (
        lambda spec, site_key, resource_kind, held_attr: routed.append(
            (spec["deck_id"], site_key, held_attr)
        )
    )

    device._place_held_resource_to_warehouse(
        PIPETTING_WAREHOUSE_NAME, "5", "well_plate", "_held_well_plate"
    )

    assert routed == [(DEFAULT_PIPETTING_DECK_ID, "5", "_held_well_plate")]
    assert PIPETTING_WAREHOUSE_NAME not in device.deck.warehouses


def test_unknown_external_warehouse_still_skips_place() -> None:
    device = _bare_device()
    device._held_well_plate = object()

    device._place_held_resource_to_warehouse(
        "未知工站", "1", "well_plate", "_held_well_plate"
    )

    assert device._held_well_plate is None
