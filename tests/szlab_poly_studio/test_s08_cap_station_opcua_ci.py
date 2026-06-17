from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest
from opcua import Client

from unilabos.devices.workstation.szlab_poly_studio.plc import SZLabPolyPLCDevice
from unilabos.devices.workstation.szlab_poly_studio.s08_cap_station import (
    SZLabS08CapStationDevice,
    _cap_cache_element_name,
)


LOGGER = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
S08_CSV_PATH = REPO_ROOT / "tests" / "szlab_poly_studio" / "fixtures" / "s08_cap_station_ci_nodes.csv"
SAMPLE_ID = [101, 102, 103]

S08_VARIABLES = [
    "S08原点信号",
    "S081_1允许加工",
    "S081_1工艺任务",
    "S081_1参数写入完成",
    "S081_1加工完成",
    "S082瓶盖暂存位",
] + [_cap_cache_element_name(2, index) for index in range(3)]


def _ci_log(message: str, *args: Any) -> None:
    if args:
        message = message % args
    print(f"[szlab_s08_cap_station_ci] {message}", flush=True)
    LOGGER.info(message)


def _browse_virtual_s08_nodes(url: str) -> tuple[Client, dict[str, Any]]:
    client = Client(url)
    client.connect()
    objects = client.get_objects_node()
    for child in objects.get_children():
        if child.get_browse_name().Name == "VirtualS08":
            nodes = {node.get_browse_name().Name: node for node in child.get_children()}
            return client, nodes
    client.disconnect()
    raise RuntimeError("OPC UA 中未找到 VirtualS08 对象")


def _wait_for_virtual_s08(url: str, timeout: float = 15.0) -> None:
    started_at = time.time()
    last_error = ""
    while time.time() - started_at < timeout:
        try:
            client, nodes = _browse_virtual_s08_nodes(url)
            try:
                missing = sorted(set(S08_VARIABLES) - set(nodes))
                if missing:
                    raise RuntimeError(f"VirtualS08 缺少变量: {missing}")
                _ci_log("VirtualS08 ready: url=%s variables=%s", url, sorted(nodes))
                return
            finally:
                client.disconnect()
        except Exception as exc:
            last_error = str(exc)
            _ci_log("等待 VirtualS08 ready: url=%s error=%s", url, last_error)
            time.sleep(0.5)
    raise TimeoutError(f"等待 VirtualS08 超时: {last_error}")


def _bind_s08_to_plc(device: SZLabS08CapStationDevice, plc: SZLabPolyPLCDevice) -> None:
    def _call_plc_command(function_name: str, function_args: dict[str, Any]) -> Any:
        if function_name == "read_variable":
            return plc.read_variable(function_args["node_name"])
        if function_name == "write_variable":
            plc.write_variable(function_args["node_name"], function_args["value"])
            return True
        raise RuntimeError(f"不支持的 PLC 命令: {function_name}")

    device._call_plc_command = _call_plc_command  # type: ignore[method-assign]
    device._read_plc_variable = lambda node_name: plc.read_variable(node_name)
    device._write_plc_variable = lambda node_name, value: plc.write_variable(node_name, value)


class TestSzlabS08CapStationOpcUaDevice:
    """CI 中针对 S08 开盖工位的真实 OPC UA 闭环测试。"""

    @staticmethod
    def opcua_url() -> str:
        url = os.environ.get("UNILABOS_TEST_SZLAB_S08_OPCUA_URL")
        if not url:
            pytest.skip("需要设置 UNILABOS_TEST_SZLAB_S08_OPCUA_URL 才运行 S08 OPC UA 集成测试")
        return url

    def test_open_and_close_liquid_cap_against_virtual_opcua(self) -> None:
        url = self.opcua_url()
        _ci_log("开始 S08 open/close_liquid_cap OPC UA 集成测试: url=%s", url)
        _wait_for_virtual_s08(url)

        plc = SZLabPolyPLCDevice(
            url=url,
            csv_path=str(S08_CSV_PATH),
            auto_connect=True,
            opcua_log_level="WARNING",
        )
        device = SZLabS08CapStationDevice(plc_device_id="szlab_poly_plc", process_timeout=8.0)
        _bind_s08_to_plc(device, plc)
        try:
            open_result = device.open_liquid_cap(sample_id=SAMPLE_ID, cap_storage_slot=2, timeout=8.0)
            after_open = plc.get_variables(S08_VARIABLES)
            _ci_log("open_liquid_cap 返回: %s", open_result)
            _ci_log("open_liquid_cap 后 OPC 状态: %s", after_open)

            assert open_result["success"] is True
            assert open_result["cap_storage_slot"] == 2
            assert after_open["S081_1工艺任务"]["value"] == 1
            assert after_open["S082瓶盖暂存位"]["value"] == 2
            assert after_open[_cap_cache_element_name(2, 0)]["value"] == SAMPLE_ID[0]

            plc.write_variable("S081_1加工完成", False)
            close_result = device.close_liquid_cap(sample_id=SAMPLE_ID, timeout=8.0)
            after_close = plc.get_variables(S08_VARIABLES)
            _ci_log("close_liquid_cap 返回: %s", close_result)
            _ci_log("close_liquid_cap 后 OPC 状态: %s", after_close)

            assert close_result["success"] is True
            assert close_result["cap_storage_slot"] == 2
            assert after_close["S081_1工艺任务"]["value"] == 2
            assert after_close[_cap_cache_element_name(2, 0)]["value"] == 0
            assert after_close["S081_1参数写入完成"]["value"] is False
        finally:
            plc.disconnect()
