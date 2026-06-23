from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from tests.pseudo_devices.common.opcua_csv_server import OpcUaCsvServer
from tests.pseudo_devices.common.opcua_flow_daemon import OpcUaFlowDaemon
from unilabos.devices.workstation.szlab_mixer.pump import SzlabMixerPumpDevice
from unilabos.devices.workstation.szlab_mixer.sensors import S06PipelineRoute

SZLAB_DIR = Path(__file__).resolve().parent
REPO_ROOT = SZLAB_DIR.parents[1]
PUMP_CSV = REPO_ROOT / "unilabos/devices/workstation/szlab_mixer/pump_nodes.csv"
PUMP_FLOW = REPO_ROOT / "unilabos/devices/workstation/szlab_mixer/pump_flow.json"
DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:48506/"


@pytest.fixture(scope="module")
def opcua_pseudo_stack():
    url = os.environ.get("UNILABOS_TEST_SZLAB_MIXER_OPCUA_URL")
    external = bool(url)
    server = None
    daemon = None

    if not external:
        url = DEFAULT_ENDPOINT
        server = OpcUaCsvServer(endpoint=url, csv_path=PUMP_CSV)
        server.start()
        daemon = OpcUaFlowDaemon(url=url, flow_path=PUMP_FLOW)
        daemon.start()

    logging.info("OPC UA endpoint=%s", url)
    logging.info("CSV=%s", PUMP_CSV)
    logging.info("flow=%s", PUMP_FLOW)
    if daemon:
        logging.info("flow summary:\n%s", daemon.summary)

    yield url, server, daemon

    if daemon:
        daemon.stop()
    if server:
        server.stop()


class TestSzlabMixerPumpOpcUaDevice:
    def test_transfer_liquid_against_virtual_opcua(self, opcua_pseudo_stack):
        url, server, daemon = opcua_pseudo_stack
        before = {}
        if server:
            before = {name: server.read(name) for name in ("S06允许加工", "S06加工完成", "S06注射泵1抽液")}

        device = SzlabMixerPumpDevice(
            url=url,
            timeout=8.0,
            pipeline_routes={
                (1, "aspirate"): S06PipelineRoute(control_valve=11, absolute_position=21),
            },
        )
        try:
            result = device.transfer_liquid(pump=1, volume=10, direction="aspirate", pipeline="aspirate")
            assert result["success"] is True
        finally:
            device.disconnect()

        if server:
            after = {name: server.read(name) for name in ("S06加工完成", "S06注射泵选择", "S06注射泵1抽液")}
            logging.info("OPC before=%s", before)
            logging.info("OPC after=%s", after)
            assert after["S06注射泵1抽液"] == 10
            assert after["S06注射泵选择"] == 1
            assert after["S06加工完成"] is True

    def test_run_solvent_addition_against_virtual_opcua(self, opcua_pseudo_stack):
        url, server, _daemon = opcua_pseudo_stack
        device = SzlabMixerPumpDevice(
            url=url,
            timeout=8.0,
            pipeline_routes={
                (1, kind): S06PipelineRoute(control_valve=1, absolute_position=1)
                for kind in ("aspirate", "dispense", "air")
            },
            robot_addition_position=7,
            robot_stirrer_position=2,
        )
        try:
            result = device.run_solvent_addition(
                pump=1,
                aspirate_volume=10,
                dispense_volume=8,
                air_volume=3,
                skip_robot=False,
            )
            assert result["success"] is True
        finally:
            device.disconnect()

        if server:
            assert server.read("S06注射泵1抽液") == 3
            assert server.read("S06注射泵1排液") == 8
            assert server.read("S03_1取料编号") == 7
            assert server.read("S03_1放料编号") == 2
