from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.szlab_poly_studio.pseudo_clients.s08_cap_station import PseudoSzlabS08OpcUaClient
from tests.szlab_poly_studio.s08_driver_loader import load_s08_cap_station_module
from unilabos.registry.ast_registry_scanner import scan_directory
from scripts.workflow_ui import load_preset

_s08_module = load_s08_cap_station_module()
NODE_PARAMS_WRITTEN = _s08_module.NODE_PARAMS_WRITTEN
NODE_PROCESS_SELECT = _s08_module.NODE_PROCESS_SELECT
S08ProcessType = _s08_module.S08ProcessType
SZLabS08CapStationDevice = _s08_module.SZLabS08CapStationDevice
_cap_cache_element_name = _s08_module._cap_cache_element_name

SAMPLE_A = [101, 102, 103]
SAMPLE_B = [201, 202, 203]


def make_s08_device(
    client: PseudoSzlabS08OpcUaClient | None = None,
    *,
    timeout: float = 1.0,
) -> tuple[SZLabS08CapStationDevice, PseudoSzlabS08OpcUaClient]:
    pseudo = client or PseudoSzlabS08OpcUaClient()
    device = SZLabS08CapStationDevice(
        url="opc.tcp://127.0.0.1:0/unused",
        timeout=timeout,
        opcua_client=pseudo,
    )
    return device, pseudo


def test_s08_cap_station_is_ast_scannable():
    root = Path("unilabos/devices/workstation/szlab_poly_studio")
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = scan_directory(root, python_path=Path(".").resolve(), executor=executor)

    device = result["devices"]["szlab_s08_cap_station"]
    assert device["class_name"] == "SZLabS08CapStationDevice"
    assert set(device["actions"]) == {"process_cap"}


def test_s08_registry_actions_only_expose_process_cap():
    preset = load_preset("s08_cap_station")

    assert list(preset.actions) == ["process_cap"]
    action = preset.actions["process_cap"]
    assert action.device_id == "szlab_s08_cap_station"
    assert [param["name"] for param in action.params] == [
        "operation",
        "vial_type",
        "sample_id",
        "timeout",
    ]


def test_process_cap_open_liquid_vial_writes_sample_id_to_slot_cache():
    device, client = make_s08_device()
    client.seed_slot_sample_id(1, SAMPLE_B)
    client.seed_slot_sample_id(2, SAMPLE_B)

    result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is True
    assert result["process_type"] == int(S08ProcessType.OPEN_LIQUID_VIAL_100ML)
    assert result["cap_storage_slot"] == 3
    assert result["sample_id"][: len(SAMPLE_A)] == SAMPLE_A
    assert (NODE_PROCESS_SELECT, int(S08ProcessType.OPEN_LIQUID_VIAL_100ML)) in client.writes
    assert ("S082瓶盖暂存位", 3) in client.writes
    assert (_cap_cache_element_name(3, 0), 101) in client.writes
    assert (NODE_PARAMS_WRITTEN, True) in client.writes
    assert client.writes[-1] == ("S082瓶盖暂存位", 0)


def test_process_cap_open_sample_500ml_uses_process_one():
    device, client = make_s08_device()
    client.seed_slot_sample_id(1, SAMPLE_A)
    result = device.process_cap(
        operation="open",
        vial_type="sample_500ml",
        sample_id=SAMPLE_B,
        timeout=1.0,
    )

    assert result["success"] is True
    assert result["process_type"] == int(S08ProcessType.OPEN_SAMPLE_VIAL_500ML)
    assert result["cap_storage_slot"] == 2
    assert (NODE_PROCESS_SELECT, 1) in client.writes


def test_process_cap_sample_250ml_dispatches_open_and_close():
    device, client = make_s08_device()
    client.seed_slot_sample_id(1, SAMPLE_B)
    open_result = device.process_cap(
        operation="open",
        vial_type="sample_250ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )
    client.set_cap_storage_slot_present(open_result["cap_storage_slot"], True)
    close_result = device.process_cap(
        operation="close",
        vial_type="sample_250ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert open_result["success"] is True
    assert open_result["process_type"] == int(S08ProcessType.OPEN_SAMPLE_VIAL_250ML)
    assert open_result["cap_storage_slot"] == 2
    assert close_result["success"] is True
    assert close_result["process_type"] == int(S08ProcessType.CLOSE_SAMPLE_VIAL_250ML)
    assert (NODE_PROCESS_SELECT, int(S08ProcessType.OPEN_SAMPLE_VIAL_250ML)) in client.writes
    assert (NODE_PROCESS_SELECT, int(S08ProcessType.CLOSE_SAMPLE_VIAL_250ML)) in client.writes
    assert (_cap_cache_element_name(2, 0), 0) in client.writes


def test_process_cap_rejects_unknown_operation():
    device, _client = make_s08_device()

    result = device.process_cap(operation="seal", vial_type="liquid_100ml", sample_id=SAMPLE_A)

    assert result["success"] is False
    assert "operation" in result["message"]


def test_process_cap_rejects_unknown_vial_type():
    device, _client = make_s08_device()

    result = device.process_cap(operation="open", vial_type="2L_flask", sample_id=SAMPLE_A)

    assert result["success"] is False
    assert "vial_type" in result["message"]


def test_process_cap_open_auto_allocates_first_empty_cache_slot():
    device, client = make_s08_device()
    client.seed_slot_sample_id(1, SAMPLE_B)
    result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is True
    assert result["cap_storage_slot"] == 2
    assert ("S082瓶盖暂存位", 2) in client.writes
    assert (_cap_cache_element_name(2, 0), SAMPLE_A[0]) in client.writes


def test_process_cap_open_requires_sample_id():
    device, client = make_s08_device()
    result = device.process_cap(operation="open", vial_type="liquid_100ml", sample_id=[])

    assert result["success"] is False
    assert "sample_id" in result["message"]


def test_process_cap_close_requires_sample_id():
    device, client = make_s08_device()
    result = device.process_cap(operation="close", vial_type="liquid_100ml", sample_id=[0, 0, 0])
    assert result["success"] is False
    assert "sample_id" in result["message"]


def test_process_cap_close_finds_slot_by_sample_id_and_clears_cache():
    device, client = make_s08_device()
    client.seed_slot_sample_id(4, SAMPLE_A)
    client.set_cap_storage_slot_present(4, True)
    result = device.process_cap(
        operation="close",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is True
    assert result["cap_storage_slot"] == 4
    assert (NODE_PROCESS_SELECT, int(S08ProcessType.CLOSE_LIQUID_VIAL_100ML)) in client.writes
    assert ("S082瓶盖暂存位", 4) in client.writes
    assert (_cap_cache_element_name(4, 0), 0) in client.writes


def test_process_cap_close_fails_when_sample_not_found():
    device, client = make_s08_device()
    result = device.process_cap(operation="close", vial_type="liquid_100ml", sample_id=SAMPLE_A)

    assert result["success"] is False
    assert "尚未开盖" in result["message"]


def test_process_cap_open_fails_when_sample_already_opened_on_storage_slot():
    device, client = make_s08_device()
    client.seed_slot_sample_id(1, SAMPLE_A)
    client.set_cap_storage_slot_present(1, True)

    result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is False
    assert "已开盖" in result["message"]
    assert "不能对同一瓶重复开盖" in result["message"]
    assert "暂存位1" in result["message"]


def test_process_cap_open_fails_when_cap_station_has_no_bottle():
    device, client = make_s08_device()
    client.set_cap_station_present(1, False)
    result = device.process_cap(
        operation="open",
        vial_type="sample_500ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is False
    assert "开盖工位1" in result["message"]
    assert "NO[14]" in result["message"]


def test_process_cap_open_liquid_vial_requires_station_two_sensor():
    device, client = make_s08_device()
    client.set_cap_station_present(2, False)
    result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is False
    assert "开盖工位2" in result["message"]
    assert "NO[15]" in result["message"]


def test_process_cap_fails_when_station_status_not_ready():
    device, client = make_s08_device()
    client.set_station_status(1)
    result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is False
    assert "工站未就绪" in result["message"]


def test_process_cap_close_fails_when_cap_storage_slot_empty():
    device, client = make_s08_device()
    client.seed_slot_sample_id(3, SAMPLE_A)
    result = device.process_cap(
        operation="close",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is False
    assert "尚未开盖" in result["message"] or "无瓶盖" in result["message"]
