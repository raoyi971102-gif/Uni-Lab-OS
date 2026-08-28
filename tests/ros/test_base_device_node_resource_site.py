from types import SimpleNamespace

from pylabrobot.resources import Coordinate

from unilabos.ros.nodes.base_device_node import _find_resource_site_index


def test_find_resource_site_index_handles_prcxi_site_without_occupied_by() -> None:
    resource = SimpleNamespace(name="trash", location=Coordinate(417.5, 301.0, 0.0))
    sites = [
        {
            "label": "T15",
            "position": {"x": 280.0, "y": 301.0, "z": 0.0},
        },
        {
            "label": "T16",
            "position": {"x": 417.5, "y": 301.0, "z": 0.0},
        },
    ]

    assert _find_resource_site_index(resource, sites) == 1


def test_find_resource_site_index_matches_frontend_occupied_resource_name() -> None:
    resource = SimpleNamespace(name="trash", location=Coordinate(0.0, 0.0, 0.0))
    sites = [
        {
            "label": "T16",
            "occupied_by": SimpleNamespace(name="trash"),
            "position": {"x": 417.5, "y": 301.0, "z": 0.0},
        }
    ]

    assert _find_resource_site_index(resource, sites) == 0


def test_find_resource_site_index_returns_none_for_unknown_resource() -> None:
    resource = SimpleNamespace(name="trash", location=Coordinate(1.0, 2.0, 3.0))
    sites = [{"label": "T16", "position": {"x": 417.5, "y": 301.0, "z": 0.0}}]

    assert _find_resource_site_index(resource, sites) is None
