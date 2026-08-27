import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from unilabos.devices.liquid_handling.prcxi.prcxi import (
    PRCXI9300Backend,
    PRCXI9300Deck,
    PRCXI9300Trash,
)


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
