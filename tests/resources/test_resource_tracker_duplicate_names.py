from pylabrobot.resources import Coordinate, Deck, Resource

from unilabos.devices.liquid_handling.prcxi.prcxi_labware import PRCXI_1000uL_Tips
from unilabos.resources.itemized_carrier import ItemizedCarrier
from unilabos.resources.resource_tracker import (
    EXTRA_FRONTEND_NAME,
    DeviceNodeResourceTracker,
    ResourceTreeSet,
    build_plr_name_to_uuid_map,
)


def _collect_plr_names(resource) -> list[str]:
    names = [resource.name]
    for child in resource.children:
        names.extend(_collect_plr_names(child))
    return names


def test_duplicate_names_under_different_parents_use_runtime_alias_and_roundtrip() -> None:
    deck = Deck(name="test_deck", size_x=500, size_y=500, size_z=100)
    first = PRCXI_1000uL_Tips("tips")
    second = PRCXI_1000uL_Tips("tips1")
    deck.assign_child_resource(first, location=Coordinate(0, 0, 0))
    deck.assign_child_resource(second, location=Coordinate(150, 0, 0))

    tree_set = ResourceTreeSet.from_plr_resources([deck], known_newly_created=True)
    second_node = next(node for node in tree_set.all_nodes if node.res_content.name == "tips1")
    for child in second_node.children:
        # 模拟前端模板实例化：父节点名称不同，但子节点仍保留模板名称。
        child.res_content.name = child.res_content.name.replace("tips1_", "tips_", 1)
        child.res_content.id = child.res_content.name

    duplicate_name = "tips_tipspot_A1"
    original_nodes = [node for node in tree_set.all_nodes if node.res_content.name == duplicate_name]
    original_uuids = {node.res_content.uuid for node in original_nodes}
    assert len(original_nodes) == 2

    plr_deck = tree_set.to_plr_resources(skip_devices=False)[0]
    runtime_names = _collect_plr_names(plr_deck)
    assert len(runtime_names) == len(set(runtime_names))
    assert "tips1_tipspot_A1" in runtime_names

    roundtrip = ResourceTreeSet.from_plr_resources([plr_deck])
    restored_nodes = [node for node in roundtrip.all_nodes if node.res_content.name == duplicate_name]
    assert len(restored_nodes) == 2
    assert {node.res_content.uuid for node in restored_nodes} == original_uuids
    assert all(EXTRA_FRONTEND_NAME not in node.res_content.extra for node in roundtrip.all_nodes)


def test_device_uuid_refill_uses_runtime_alias_for_duplicate_child_names() -> None:
    deck = Deck(name="test_deck", size_x=500, size_y=500, size_z=100)
    first = PRCXI_1000uL_Tips("tips")
    second = PRCXI_1000uL_Tips("tips1")
    deck.assign_child_resource(first, location=Coordinate(0, 0, 0))
    deck.assign_child_resource(second, location=Coordinate(150, 0, 0))

    tree_set = ResourceTreeSet.from_plr_resources([deck], known_newly_created=True)
    second_node = next(node for node in tree_set.all_nodes if node.res_content.name == "tips1")
    for child in second_node.children:
        child.res_content.name = child.res_content.name.replace("tips1_", "tips_", 1)
        child.res_content.id = child.res_content.name

    duplicate_name = "tips_tipspot_A1"
    source_nodes = [node for node in tree_set.all_nodes if node.res_content.name == duplicate_name]
    assert len(source_nodes) == 2
    source_uuids = {node.res_content.uuid for node in source_nodes}

    name_to_uuid = build_plr_name_to_uuid_map(tree_set.root_nodes)
    assert name_to_uuid["tips_tipspot_A1"] != name_to_uuid["tips1_tipspot_A1"]

    plr_deck = tree_set.to_plr_resources(skip_devices=False)[0]
    tracker = DeviceNodeResourceTracker()
    tracker.loop_set_uuid(plr_deck, name_to_uuid)

    runtime_a1_nodes = [
        child
        for rack in plr_deck.children
        for child in rack.children
        if child.name in {"tips_tipspot_A1", "tips1_tipspot_A1"}
    ]
    assert len(runtime_a1_nodes) == 2
    assert {node.unilabos_uuid for node in runtime_a1_nodes} == source_uuids


def test_itemized_carrier_falls_back_to_unique_xy_site() -> None:
    carrier = ItemizedCarrier(
        name="carrier",
        size_x=200,
        size_y=200,
        size_z=50,
        sites=[
            {
                "label": "1",
                "position": {"x": 10, "y": 20, "z": 10},
                "size": {"width": 100, "height": 80, "depth": 20},
                "occupied_by": None,
            }
        ],
    )
    child = Resource(name="child", size_x=10, size_y=10, size_z=10)

    carrier.assign_child_resource(child, location=Coordinate(10, 20, 0))

    assert child.parent is carrier
    assert child.location == Coordinate(10, 20, 10)
