import importlib

from unilabos.devices.workstation.XUSE.XUSE_CONSTS import (
    LargeCrucibleFeedPosition,
    SmallCrucibleDischargePosition,
)


XUSEDevice = importlib.import_module(
    "unilabos.devices.workstation.XUSE.XUSE"
).XUSEDevice


class SequenceDevice(XUSEDevice):
    def __init__(self, values):
        self.values = iter(values)
        self.reads = []

    def get_node_value(self, node_name, use_cache=True):
        self.reads.append((node_name, use_cache))
        return next(self.values)


class TransferDevice(XUSEDevice):
    def __init__(self):
        self.position_waits = []
        self.writes = []

    def _wait_until_value(self, node_name, expected_value, **kwargs):
        self.position_waits.append((node_name, expected_value, kwargs))
        return True

    def _wait_until_true(self, *args, **kwargs):
        return True

    def _wait_until_false(self, *args, **kwargs):
        return True

    def set_node_value(self, node_name, value):
        self.writes.append((node_name, value))
        return True

    def _place_carrier_to_warehouse_at(self, *args, **kwargs):
        return None

    def _pick_carrier_from_warehouse(self, *args, **kwargs):
        return None


def test_wait_until_value_keeps_polling_until_expected(monkeypatch):
    sleeps = []
    monkeypatch.setattr(
        "unilabos.devices.workstation.XUSE.XUSE.time.sleep", sleeps.append
    )
    device = SequenceDevice([0, 1, 2])

    assert device._wait_until_value("Position", 2) is True
    assert device.reads == [
        ("Position", True),
        ("Position", True),
        ("Position", True),
    ]
    assert sleeps == [0.2, 0.2]


def test_small_crucible_place_waits_for_feeding_position(monkeypatch):
    monkeypatch.setattr(
        "unilabos.devices.workstation.XUSE.XUSE.time.sleep", lambda _: None
    )
    device = TransferDevice()

    result = device.place_small_crucible_to_moving_position(1)

    assert result["success"] is True
    assert device.position_waits == [
        (
            "Small_Crucible_Discharge_Current_Position",
            SmallCrucibleDischargePosition.FEEDING,
            {"description": "小坩埚出料机构到达放料位"},
        )
    ]


def test_large_crucible_pick_waits_for_picking_position(monkeypatch):
    monkeypatch.setattr(
        "unilabos.devices.workstation.XUSE.XUSE.time.sleep", lambda _: None
    )
    device = TransferDevice()

    result = device.pick_large_crucible_from_moving_position()

    assert result["success"] is True
    assert device.position_waits == [
        (
            "Large_Crucible_Feed_Current_Position",
            LargeCrucibleFeedPosition.PICKING,
            {"description": "大坩埚入料机构到达取料位"},
        )
    ]
