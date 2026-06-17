from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.szlab_poly_studio.pseudo_clients.s08_cap_station import SzlabS08CapStationPseudoPlcClient
from unilabos.devices.workstation.szlab_poly_studio.s08_cap_station import (
    CapProcessTask,
    LIQUID_UNIT_ID,
    SOLID_UNIT_ID,
    SZLabS08CapStationDevice,
    UNIT_VARIABLES,
    _cap_cache_element_name,
)
from unilabos.registry.ast_registry_scanner import scan_directory

SAMPLE_A = [101, 102, 103]
SAMPLE_B = [201, 202, 203]


def _bind_pseudo_plc(device: SZLabS08CapStationDevice, client: SzlabS08CapStationPseudoPlcClient) -> None:
    device._read_plc_variable = lambda node_name: client.read(node_name)
    device._write_plc_variable = lambda node_name, value: client.write(node_name, value)


def test_s08_cap_station_is_ast_scannable():
    root = Path("unilabos/devices/workstation/szlab_poly_studio")
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = scan_directory(root, python_path=Path(".").resolve(), executor=executor)

    device = result["devices"]["szlab_s08_cap_station"]
    assert device["class_name"] == "SZLabS08CapStationDevice"
    for action in (
        "open_liquid_cap",
        "close_liquid_cap",
        "open_solid_cap",
        "close_solid_cap",
        "read_cap_storage_registry",
        "read_cap_slot_occupancy",
        "wait_station_ready",
        "wait_liquid_allow_process",
        "wait_solid_allow_process",
        "read_liquid_unit_status",
        "read_solid_unit_status",
    ):
        assert action in device["actions"]


def test_open_liquid_cap_writes_sample_id_to_slot_cache():
    device = SZLabS08CapStationDevice(plc_device_id="szlab_poly_plc")
    client = SzlabS08CapStationPseudoPlcClient()
    _bind_pseudo_plc(device, client)
    nodes = UNIT_VARIABLES[LIQUID_UNIT_ID]

    result = device.open_liquid_cap(sample_id=SAMPLE_A, cap_storage_slot=3, timeout=1.0)

    assert result["success"] is True
    assert result["unit_id"] == LIQUID_UNIT_ID
    assert result["cap_storage_slot"] == 3
    assert result["sample_id"][: len(SAMPLE_A)] == SAMPLE_A
    assert (nodes["process_task"], 1) in client.writes
    assert ("S082瓶盖暂存位", 3) in client.writes
    assert (_cap_cache_element_name(3, 0), 101) in client.writes
    assert (_cap_cache_element_name(3, 1), 102) in client.writes
    assert (nodes["params_written"], True) in client.writes
    assert client.writes[-1] == (nodes["params_written"], False)


def test_open_solid_cap_uses_unit_two():
    device = SZLabS08CapStationDevice(plc_device_id="szlab_poly_plc")
    client = SzlabS08CapStationPseudoPlcClient(allow_liquid=False, allow_solid=True)
    _bind_pseudo_plc(device, client)
    nodes = UNIT_VARIABLES[SOLID_UNIT_ID]

    result = device.open_solid_cap(sample_id=SAMPLE_B, cap_storage_slot=2, timeout=1.0)

    assert result["success"] is True
    assert result["unit_id"] == SOLID_UNIT_ID
    assert (nodes["process_task"], int(CapProcessTask.OPEN)) in client.writes


def test_open_liquid_cap_auto_allocates_first_empty_cache_slot():
    device = SZLabS08CapStationDevice(plc_device_id="szlab_poly_plc")
    client = SzlabS08CapStationPseudoPlcClient()
    client.seed_slot_sample_id(1, SAMPLE_B)
    _bind_pseudo_plc(device, client)

    result = device.open_liquid_cap(sample_id=SAMPLE_A, timeout=1.0)

    assert result["success"] is True
    assert result["cap_storage_slot"] == 2
    assert ("S082瓶盖暂存位", 2) in client.writes
    assert (_cap_cache_element_name(2, 0), SAMPLE_A[0]) in client.writes


def test_open_liquid_cap_requires_sample_id():
    device = SZLabS08CapStationDevice(plc_device_id="szlab_poly_plc")
    result = device.open_liquid_cap(sample_id=[])
    assert result["success"] is False


def test_close_liquid_cap_finds_slot_by_sample_id_and_clears_cache():
    device = SZLabS08CapStationDevice(plc_device_id="szlab_poly_plc")
    client = SzlabS08CapStationPseudoPlcClient()
    client.seed_slot_sample_id(4, SAMPLE_A)
    _bind_pseudo_plc(device, client)
    nodes = UNIT_VARIABLES[LIQUID_UNIT_ID]

    result = device.close_liquid_cap(sample_id=SAMPLE_A, timeout=1.0)

    assert result["success"] is True
    assert result["cap_storage_slot"] == 4
    assert (nodes["process_task"], int(CapProcessTask.CLOSE)) in client.writes
    assert ("S082瓶盖暂存位", 4) in client.writes
    assert (_cap_cache_element_name(4, 0), 0) in client.writes


def test_close_liquid_cap_fails_when_sample_not_found():
    device = SZLabS08CapStationDevice(plc_device_id="szlab_poly_plc")
    client = SzlabS08CapStationPseudoPlcClient()
    _bind_pseudo_plc(device, client)

    result = device.close_liquid_cap(sample_id=SAMPLE_A)

    assert result["success"] is False
    assert "未找到" in result["message"]
