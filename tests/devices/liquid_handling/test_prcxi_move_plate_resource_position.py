import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import yaml
import pytest

from unilabos.devices.liquid_handling.prcxi.prcxi import (
    PRCXIError,
    PRCXI9300Backend,
    PRCXI9300Deck,
    PRCXI9300Handler,
)
from unilabos.devices.liquid_handling.prcxi.prcxi_labware import PRCXI_96_DeepWell


REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_deck_and_plate():
    deck = PRCXI9300Deck(name="PRCXI_Deck", size_x=555.0, size_y=397.0, size_z=0.0)
    plate = PRCXI_96_DeepWell("plate")
    deck.assign_child_at_slot(plate, slot=15)
    plate.unilabos_extra = {"update_resource_site": "T2"}
    return deck, plate


def _make_handler(deck, *, track_position: bool):
    backend = PRCXI9300Backend(tablets_info=[], setup=False)
    backend.pick_up_resource = AsyncMock(return_value={"step": "pick"})
    backend.drop_resource = AsyncMock(return_value={"step": "drop"})

    handler = object.__new__(PRCXI9300Handler)
    handler.deck = deck
    handler._unilabos_backend = backend
    handler.track_move_plate_resource_position = track_position
    handler._protocol_resource_slots = {}
    handler.step_mode = False
    handler._simulator = False
    backend._handler = handler
    return handler, backend


def test_slot_lookup_can_ignore_stale_accounting_site() -> None:
    deck, plate = _make_deck_and_plate()
    backend = PRCXI9300Backend(tablets_info=[], setup=False)
    backend._handler = type("Handler", (), {"deck": deck})()

    assert backend._deck_plate_slot_no(plate, deck) == 2
    assert (
        backend._deck_plate_slot_no(
            plate,
            deck,
            prefer_update_resource_site=False,
        )
        == 15
    )


def test_frontend_mode_attaches_detached_resource_by_display_location() -> None:
    deck, plate = _make_deck_and_plate()
    deck.unassign_child_resource(plate)
    plate.location = deck.get_slot_location(15)
    handler = object.__new__(PRCXI9300Handler)
    handler.deck = deck

    handler._attach_resources_to_deck_if_needed(
        [plate],
        prefer_update_resource_site=False,
    )

    assert deck._get_site_resource(14) is plate
    assert deck._get_site_resource(1) is None


def test_move_plate_frontend_position_mode_does_not_reparent_or_reaccount() -> None:
    deck, plate = _make_deck_and_plate()
    handler, backend = _make_handler(deck, track_position=False)

    asyncio.run(handler.move_plate([plate], 4))

    assert backend.pick_up_resource.await_args.kwargs["source_plate_number"] == 15
    assert backend.drop_resource.await_args.kwargs["target_plate_number"] == 4
    assert deck._get_site_resource(14) is plate
    assert deck._get_site_resource(3) is None
    assert "update_resource_site" not in plate.unilabos_extra
    assert handler._remembered_resource_slot(plate) == 4


def test_frontend_mode_snapshots_on_create_then_tracks_consecutive_moves() -> None:
    deck, plate = _make_deck_and_plate()
    handler, backend = _make_handler(deck, track_position=False)
    backend.create_protocol = MagicMock()

    asyncio.run(
        handler.create_protocol(
            protocol_name="move-twice",
            track_move_plate_resource_position=False,
        )
    )
    asyncio.run(handler.move_plate([plate], 4))
    asyncio.run(handler.move_plate([plate], 2))

    assert [
        call.kwargs["source_plate_number"]
        for call in backend.pick_up_resource.await_args_list
    ] == [15, 4]
    assert [
        call.kwargs["target_plate_number"]
        for call in backend.drop_resource.await_args_list
    ] == [4, 2]
    assert handler._remembered_resource_slot(plate) == 2
    assert backend._deck_plate_slot_no(plate, deck) == 2
    # 前端树不跟随 protocol 内搬运，下一次 create_protocol 仍会重新以它为准。
    assert deck._get_site_resource(14) is plate
    assert "update_resource_site" not in plate.unilabos_extra

    asyncio.run(
        handler.create_protocol(
            protocol_name="next-workflow",
            track_move_plate_resource_position=False,
        )
    )
    assert handler._remembered_resource_slot(plate) == 15


def test_create_protocol_pulls_latest_frontend_position_before_snapshot() -> None:
    deck, local_plate = _make_deck_and_plate()
    deck.unilabos_uuid = "deck-uuid"
    handler, backend = _make_handler(deck, track_position=False)
    backend.create_protocol = MagicMock()

    remote_plate = PRCXI_96_DeepWell("plate")
    remote_plate.unilabos_extra = {"update_resource_site": "T2"}
    remote_tree_set = SimpleNamespace(to_plr_resources=lambda: [remote_plate])
    handler._ros_node = SimpleNamespace(
        get_resource=AsyncMock(return_value=remote_tree_set),
    )

    asyncio.run(
        handler.create_protocol(
            protocol_name="pull-frontend",
            track_move_plate_resource_position=False,
        )
    )
    asyncio.run(handler.move_plate([local_plate], 4))

    handler._ros_node.get_resource.assert_awaited_once_with(
        ["deck-uuid"],
        with_children=True,
    )
    assert backend.pick_up_resource.await_args.kwargs["source_plate_number"] == 2
    assert handler._remembered_resource_slot(local_plate) == 4


def test_create_protocol_rejects_stale_local_fallback_when_cloud_pull_fails() -> None:
    deck, _plate = _make_deck_and_plate()
    deck.unilabos_uuid = "deck-uuid"
    handler, backend = _make_handler(deck, track_position=False)
    backend.create_protocol = MagicMock()
    handler._ros_node = SimpleNamespace(
        get_resource=AsyncMock(side_effect=RuntimeError("cloud unavailable")),
    )

    with pytest.raises(PRCXIError, match="同步 PRCXI 物料失败"):
        asyncio.run(
            handler.create_protocol(
                protocol_name="must-not-use-stale-tree",
                track_move_plate_resource_position=False,
            )
        )

    backend.create_protocol.assert_not_called()


def test_move_plate_accounting_mode_updates_tree_and_site_marker() -> None:
    deck, plate = _make_deck_and_plate()
    plate.unilabos_extra["update_resource_site"] = "T15"
    handler, backend = _make_handler(deck, track_position=True)
    backend.create_protocol = MagicMock()

    asyncio.run(
        handler.create_protocol(
            protocol_name="accounting-mode",
            track_move_plate_resource_position=True,
        )
    )
    asyncio.run(handler.move_plate([plate], 4))

    assert backend.pick_up_resource.await_args.kwargs["source_plate_number"] == 15
    assert backend.drop_resource.await_args.kwargs["target_plate_number"] == 4
    assert deck._get_site_resource(14) is None
    assert deck._get_site_resource(3) is plate
    assert plate.unilabos_extra["update_resource_site"] == "T4"


def test_ai4c_json_no_longer_contains_protocol_position_switch() -> None:
    graph_path = REPO_ROOT / "unilabos/devices/workstation/AI4C/AI4C_station.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    prcxi = next(node for node in graph["nodes"] if node["id"] == "PRCXI")

    assert "track_move_plate_resource_position" not in prcxi["config"]


def test_registry_exposes_position_switch_on_create_protocol_action() -> None:
    registry_path = REPO_ROOT / "unilabos/registry/devices/liquid_handler.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    prcxi = registry["liquid_handler.prcxi"]
    init_props = prcxi["init_param_schema"]["config"]["properties"]
    action = prcxi["class"]["action_value_mappings"]["auto-create_protocol"]
    switch = action["schema"]["properties"]["goal"]["properties"][
        "track_move_plate_resource_position"
    ]

    assert "track_move_plate_resource_position" not in init_props
    assert action["goal_default"]["track_move_plate_resource_position"] is False
    assert switch["type"] == "boolean"
    assert switch["default"] is False
