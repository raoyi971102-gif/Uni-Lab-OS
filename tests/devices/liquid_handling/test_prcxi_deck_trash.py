import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from unilabos.devices.liquid_handling.prcxi.prcxi import (
    PRCXI9300Backend,
    PRCXI9300Deck,
    PRCXI9300Handler,
    PRCXI9300Trash,
)
from unilabos.devices.liquid_handling.prcxi.prcxi_labware import PRCXI_300ul_Tips


def test_get_trash_area_accepts_frontend_resource_type_name() -> None:
    deck = PRCXI9300Deck(name="PRCXI_Deck", size_x=555.0, size_y=397.0, size_z=0.0)
    trash = PRCXI9300Trash(
        name="PRCXI_trash",
        size_x=126.59,
        size_y=84.87,
        size_z=89.5,
    )

    deck.assign_child_at_slot(trash, slot=16)

    assert deck.get_trash_area() is trash
    assert trash.location == deck.get_slot_location("T16")


def test_backend_drop_tips_recognizes_frontend_named_trash_at_t16() -> None:
    deck = PRCXI9300Deck(name="PRCXI_Deck", size_x=555.0, size_y=397.0, size_z=0.0)
    trash = PRCXI9300Trash(
        name="PRCXI_trash",
        size_x=126.59,
        size_y=84.87,
        size_z=89.5,
    )
    trash.unilabos_extra = {"update_resource_site": "T16"}
    deck.assign_child_at_slot(trash, slot=16)

    backend = PRCXI9300Backend(tablets_info=[], setup=False)
    backend.api_client = MagicMock()
    backend.api_client.UnLoad.return_value = {"step": "drop-to-trash"}
    backend.steps_todo_list = []

    asyncio.run(
        backend.drop_tips(
            ops=[SimpleNamespace(resource=trash)],
            use_channels=[0],
        )
    )

    assert backend.api_client.UnLoad.call_args.kwargs["plate_no"] == 16
    assert backend.steps_todo_list == [{"step": "drop-to-trash"}]


def test_backend_drop_tips_can_return_tip_to_original_rack_position() -> None:
    deck = PRCXI9300Deck(name="PRCXI_Deck", size_x=555.0, size_y=397.0, size_z=0.0)
    tip_rack = PRCXI_300ul_Tips("tips_to_return")
    deck.assign_child_at_slot(tip_rack, slot=15)
    original_spot = tip_rack.get_item("D5")

    backend = PRCXI9300Backend(
        tablets_info=[],
        setup=False,
        pip_setting={"left": {"vol": 300.0, "channels": 1}},
    )
    backend._active_axis = "Left"
    backend.api_client = MagicMock()
    backend.api_client.UnLoad.return_value = {"step": "return-tip"}
    backend.steps_todo_list = []

    asyncio.run(
        backend.drop_tips(
            ops=[SimpleNamespace(resource=original_spot)],
            use_channels=[0],
        )
    )

    kwargs = backend.api_client.UnLoad.call_args.kwargs
    assert kwargs["plate_no"] == 15
    assert kwargs["hole_col"] == 5
    assert kwargs["hole_row"] == 4
    assert backend.steps_todo_list == [{"step": "return-tip"}]


def test_handler_return_tips_restores_tipspot_tracker_and_origin() -> None:
    deck = PRCXI9300Deck(name="PRCXI_Deck", size_x=555.0, size_y=397.0, size_z=0.0)
    tip_rack = PRCXI_300ul_Tips("tracked_tips")
    deck.assign_child_at_slot(tip_rack, slot=15)
    original_spot = tip_rack.get_item("D5")
    handler = PRCXI9300Handler(
        deck=deck,
        host="127.0.0.1",
        port=9999,
        timeout=1.0,
        setup=False,
        pip_setting={"left": {"vol": 300.0, "channels": 1}},
    )
    handler._unilabos_backend.steps_todo_list = []

    async def _pick_and_return() -> None:
        await handler.setup()
        await handler.pick_up_tips([original_spot], use_channels=[0])
        assert original_spot.has_tip() is False
        await handler.return_tips(use_channels=[0])

    asyncio.run(_pick_and_return())

    assert original_spot.has_tip() is True
    steps = handler._unilabos_backend.steps_todo_list
    assert [step["Function"] for step in steps] == ["Load", "UnLoad"]
    assert steps[-1]["PlateNo"] == 15
    assert steps[-1]["HoleCol"] == 5
    assert steps[-1]["HoleRow"] == 4
