from collections import OrderedDict

from pylabrobot.resources import Coordinate, Plate, Well, create_ordered_items_2d
from pylabrobot.resources.deck import Deck

from unilabos.resources.plr_naming import retarget_itemized_child_names


def _make_plate(name: str) -> Plate:
    return Plate(
        name=name,
        size_x=127.0,
        size_y=85.0,
        size_z=14.0,
        ordered_items=create_ordered_items_2d(
            Well,
            num_items_x=2,
            num_items_y=2,
            dx=0,
            dy=0,
            dz=0,
            item_dx=9,
            item_dy=9,
            size_x=9,
            size_y=9,
            size_z=10,
        ),
    )


def test_retarget_renames_wells_after_plate_rename():
    plate = _make_plate("PRCXI_96_DeepWell")
    assert plate.get_item("A1").name == "PRCXI_96_DeepWell_well_A1"

    plate.name = "plate_slot_3"
    assert plate.get_item("A1").name == "PRCXI_96_DeepWell_well_A1"

    changed = retarget_itemized_child_names(plate)
    assert changed == 4
    assert plate.get_item("A1").name == "plate_slot_3_well_A1"
    assert plate.get_item("B2").name == "plate_slot_3_well_B2"
    assert plate._ordering["A1"] == "plate_slot_3_well_A1"


def test_retarget_on_deck_updates_resource_registry():
    deck = Deck(size_x=400, size_y=300, size_z=100, name="deck")
    plate_a = _make_plate("PRCXI_96_DeepWell")
    plate_a.name = "plate_slot_3"
    plate_b = _make_plate("PRCXI_96_DeepWell")
    plate_b.name = "plate_slot_7"

    deck.assign_child_resource(plate_a, location=Coordinate(0, 0, 0))
    # 第二块板孔名与第一块相同，直接挂会撞名；先对齐再挂。
    retarget_itemized_child_names(plate_a)
    retarget_itemized_child_names(plate_b)
    deck.assign_child_resource(plate_b, location=Coordinate(130, 0, 0))

    assert deck.has_resource("plate_slot_3_well_A1")
    assert deck.has_resource("plate_slot_7_well_A1")
    assert not deck.has_resource("PRCXI_96_DeepWell_well_A1")
