"""S08 驱动内部实现与可选校验开关的手动单元测试（不进 CI）。

运行示例::

    PYTHONPATH=. pytest tests/szlab_poly_studio/local/s08_cap_station_internal.py -q
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.szlab_poly_studio.pseudo_clients.s08_cap_station import PseudoSzlabS08OpcUaClient
from tests.szlab_poly_studio.s08_test_helpers import (
    NODE_PARAMS_WRITTEN,
    NODE_PROCESS_COMPLETE,
    NODE_PROCESS_SELECT,
    NODE_STATION_STATUS,
    SAMPLE_A,
    SAMPLE_B,
    S08ProcessType,
    SZLabS08CapStationDevice,
    _cap_cache_element_name,
    make_s08_device,
    s08_module,
)


def test_device_init_resets_unilab_written_params_on_connect():
    pseudo = PseudoSzlabS08OpcUaClient(
        initial_values={
            NODE_PROCESS_SELECT: 6,
            NODE_PARAMS_WRITTEN: True,
            "S082瓶盖暂存位": 2,
        }
    )
    _device, client = make_s08_device(pseudo)

    assert client.values[NODE_PROCESS_SELECT] == 0
    assert client.values[NODE_PARAMS_WRITTEN] is False
    assert client.values["S082瓶盖暂存位"] == 0
    assert (NODE_PROCESS_COMPLETE, 0) not in client.writes


def test_build_opcua_node_id_map_for_uplink_comm_includes_handshake_and_cache_nodes():
    node_id_map = s08_module.build_opcua_node_id_map_for_uplink_comm("ns=4;s=上位机通讯")

    assert node_id_map[NODE_PROCESS_SELECT] == "ns=4;s=上位机通讯|S08工艺选择"
    assert node_id_map["传感器状态_上位机[3].NO[14]"] == "ns=4;s=上位机通讯|传感器状态_上位机[3].NO[14]"
    assert node_id_map[_cap_cache_element_name(1, 0)] == "ns=4;s=上位机通讯|S082_1数据缓存[0]"
    assert node_id_map[_cap_cache_element_name(5, 29)] == "ns=4;s=上位机通讯|S082_5数据缓存[29]"


def test_wait_process_complete_waits_until_process_complete_equals_expected():
    pseudo = PseudoSzlabS08OpcUaClient()
    device = SZLabS08CapStationDevice(
        url="opc.tcp://127.0.0.1:50102/",
        timeout=1.0,
        poll_interval=0.05,
        opcua_client=pseudo,
    )
    process_id = int(S08ProcessType.OPEN_LIQUID_VIAL_100ML)

    assert device._wait_process_complete(process_id, timeout=0.2) is False

    pseudo.values[NODE_PROCESS_SELECT] = process_id
    pseudo.values[NODE_PARAMS_WRITTEN] = True
    assert device._wait_process_complete(process_id, timeout=1.0) is True


def test_is_virtual_test_opcua_url():
    assert s08_module._is_virtual_test_opcua_url("opc.tcp://127.0.0.1:50102/") is True
    assert s08_module._is_virtual_test_opcua_url("opc.tcp://192.168.1.10:4840/") is False


def test_real_opcua_url_auto_uses_uplink_prefix():
    with patch.object(s08_module, "SzlabS08OpcUaClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        s08_module.SZLabS08CapStationDevice(
            url="opc.tcp://192.168.1.10:4840/",
            opcua_uplink_comm_prefix=None,
        )
        node_id_map = mock_cls.call_args.kwargs["node_id_map"]
        assert node_id_map[NODE_PROCESS_SELECT] == "ns=4;s=上位机通讯|S08工艺选择"


def test_process_cap_close_fails_when_sample_not_found():
    device, _client = make_s08_device(validate_cap_constraints=True)
    result = device.process_cap(operation="close", vial_type="liquid_100ml", sample_id=SAMPLE_A)

    assert result["success"] is False
    assert "尚未开盖" in result["message"]


def test_process_cap_open_fails_when_sample_already_opened_on_storage_slot():
    device, client = make_s08_device(validate_cap_constraints=True)
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
    device, client = make_s08_device(validate_cap_constraints=True)
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
    device, client = make_s08_device(validate_cap_constraints=True)
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
    device, client = make_s08_device(require_station_status=True)
    client.set_station_status(1)
    result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is False
    assert "工站未就绪" in result["message"]
    assert NODE_STATION_STATUS in result["message"]
    assert "OPC UA" in result["message"]


def test_process_cap_skips_station_status_check_when_disabled():
    device, client = make_s08_device(require_station_status=False)
    client.set_station_status(0)
    client.values["传感器状态_上位机[3].NO[15]"] = True
    result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is True


def test_process_cap_skips_cap_constraints_when_disabled():
    device, client = make_s08_device(validate_cap_constraints=False)
    client.set_cap_station_present(2, False)
    client.seed_slot_sample_id(1, SAMPLE_A)
    client.set_cap_storage_slot_present(1, True)

    open_result = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_B,
        timeout=1.0,
    )
    assert open_result["success"] is True

    duplicate_open = device.process_cap(
        operation="open",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )
    assert duplicate_open["success"] is True
    assert duplicate_open["cap_storage_slot"] == 1


def test_process_cap_close_fails_when_cap_storage_slot_empty():
    device, client = make_s08_device(validate_cap_constraints=True)
    client.seed_slot_sample_id(3, SAMPLE_A)
    result = device.process_cap(
        operation="close",
        vial_type="liquid_100ml",
        sample_id=SAMPLE_A,
        timeout=1.0,
    )

    assert result["success"] is False
    assert "尚未开盖" in result["message"] or "无瓶盖" in result["message"]
