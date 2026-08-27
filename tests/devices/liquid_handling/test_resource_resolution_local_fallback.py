import asyncio
from types import SimpleNamespace

from unilabos.devices.liquid_handling.liquid_handler_abstract import LiquidHandlerAbstract


class _Tracker:
    def __init__(self, local_well):
        self.local_well = local_well

    def figure_resource(self, query, try_mode=False):
        if query.get("uuid") or query.get("id"):
            return []
        if query.get("name") == self.local_well.name:
            return [self.local_well]
        return []


class _RemoteTree:
    def __init__(self, remote_well):
        self.remote_well = remote_well

    def to_plr_resources(self, requested_uuids=None):
        return [self.remote_well]


def test_remote_detached_well_falls_back_to_local_parented_well() -> None:
    plate = SimpleNamespace(name="PRCXI_96_DeepWell")
    local_well = SimpleNamespace(name="PRCXI_96_DeepWell_well_A1", parent=plate)
    remote_well = SimpleNamespace(name="PRCXI_96_DeepWell_well_A1", parent=None)
    tracker = _Tracker(local_well)

    async def get_resource(_uuids):
        return _RemoteTree(remote_well)

    handler = object.__new__(LiquidHandlerAbstract)
    handler._ros_node = SimpleNamespace(resource_tracker=tracker, get_resource=get_resource)
    resource_dict = {
        "id": "/AI4C_station/AI4C_deck/PRCXI_96_DeepWell/PRCXI_96_DeepWell_well_A1",
        "name": "PRCXI_96_DeepWell_well_A1",
        "uuid": "remote-well-uuid",
    }

    resolved = asyncio.run(handler._resolve_to_plr_resources([resource_dict]))

    assert resolved == [local_well]
    assert resolved[0].parent is plate
