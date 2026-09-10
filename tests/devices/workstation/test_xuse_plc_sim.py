"""用 PLCSIM_ROOT 指定 PLC-Sim 源码，测试回环 OPC UA 通信及动作契约。"""

import csv
import importlib
import importlib.util
import os
from pathlib import Path
import socket
import sys
import threading
import time

import openpyxl
import pytest
from opcua import ua

from unilabos.devices.workstation.XUSE.XUSE import XUSEDevice


XUSE_DIR = Path(importlib.import_module(XUSEDevice.__module__).__file__).parent


@pytest.fixture(scope="module")
def plc():
    root = os.environ.get("PLCSIM_ROOT")
    if not root:
        pytest.skip("需设置 PLCSIM_ROOT，指向 Uni-Lab-Sim/PLC-Sim")
    root = Path(root).resolve()
    spec = importlib.util.spec_from_file_location(
        "xuse_test_plc_sim", root / "__init__.py", submodule_search_locations=[str(root)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    simulator = importlib.import_module(f"{spec.name}.server")
    common = importlib.import_module(f"{spec.name}.common")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"opc.tcp://127.0.0.1:{port}/xuse_test/"
    server = simulator.build_server(endpoint)
    ns = simulator.register_ns_padding(server, 4, "urn:xuse:test")
    definitions = common.load_csv(XUSE_DIR / "xuse_variables.csv")
    nodes = simulator.add_nodes(server, ns, definitions)
    aliases = {item.name_en: item.name_cn for item in definitions}

    def read(name):
        return nodes[aliases.get(name, name)].get_value()

    def write(name, value):
        node = nodes[aliases.get(name, name)]
        node.set_value(ua.Variant(value, node.get_data_type_as_variant_type()))

    parameter_pairs = [("Ball_Mill_Parameter_Send", "Ball_Mill_Parameter_Send_Complete")]
    parameter_pairs += [
        (f"Muffle_Furnace_Parameter_Send_{i}", f"Muffle_Furnace_Parameter_Send_Complete_{i}")
        for i in range(1, 7)
    ]
    processes = ["Ball_Mill", "Add_Sample"] + [f"Muffle_Furnace_{i}" for i in range(1, 7)]

    def process_nodes(prefix):
        if prefix.startswith("Muffle_Furnace_"):
            index = prefix.rsplit("_", 1)[1]
            return tuple(f"Muffle_Furnace_{name}_{index}" for name in ("Request_Process", "Start_Process", "Process_Complete"))
        return tuple(f"{prefix}_{name}" for name in ("Request_Process", "Start_Process", "Process_Complete"))

    for prefix in processes:
        write(process_nodes(prefix)[0], True)
    stop = threading.Event()
    failures = []

    def scan():
        started = {}
        try:
            while not stop.wait(0.01):
                for request, complete in parameter_pairs:
                    if read(complete) != read(request):
                        write(complete, read(request))
                for prefix in processes:
                    _, trigger, complete = process_nodes(prefix)
                    if read(trigger):
                        started.setdefault(prefix, time.monotonic())
                        if time.monotonic() - started[prefix] >= 0.15 and not read(complete):
                            write(complete, True)
                    else:
                        started.pop(prefix, None)
                        if read(complete):
                            write(complete, False)
        except Exception as exc:
            failures.append(exc)

    # 通信基类的类级注册表需要隔离，防止其他测试连接到已关闭的服务。
    class IntegrationDevice(XUSEDevice):
        _node_registry = {}
        _variables_to_find = {}
        _found_node_objects = {}

    server.start()
    worker = threading.Thread(target=scan, daemon=True)
    worker.start()
    device = None
    try:
        device = IntegrationDevice(url=endpoint, csv_path=str(XUSE_DIR / "xuse_variables.csv"), subscription_interval=50, cache_timeout=0.1)
        yield device, nodes, read, write
    finally:
        stop.set()
        worker.join(2)
        if device is not None:
            device._arm_status_poller_stop.set()
            device._arm_status_thread.join(2)
            device.disconnect()
        server.stop()
        for name in list(sys.modules):
            if name == spec.name or name.startswith(spec.name + "."):
                del sys.modules[name]
    assert not failures, failures


@pytest.fixture(autouse=True)
def bounded_reads(plc, monkeypatch):
    device, _, _, _ = plc
    original = device.get_node_value
    deadline = time.monotonic() + 15

    def read(*args, **kwargs):
        if time.monotonic() > deadline:
            raise TimeoutError("仿真测试超过 15 秒，终止等待")
        return original(*args, **kwargs)

    monkeypatch.setattr(device, "get_node_value", read)


def recipe(path, sheets):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        sheet.append(["参数名", "参数值"])
        for row in rows:
            sheet.append(row)
    workbook.save(path)
    workbook.close()
    return str(path)


def test_all_nodes_load(plc):
    device, nodes, _, _ = plc
    with (XUSE_DIR / "xuse_variables.csv").open(encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    assert len(nodes) == len(rows) == len(device._node_registry)
    for row in rows:
        assert device.get_node_value(row["EnglishName"], force_read=True) is not None


@pytest.mark.parametrize("signal", [item[0] for item in XUSEDevice._POWDER_FAULT_SIGNALS])
def test_powder_fault_prevents_start(plc, signal):
    device, nodes, read, write = plc
    node = nodes[device._name_mapping.get(signal, signal)]
    value = True if node.get_data_type_as_variant_type() == ua.VariantType.Boolean else 1
    write(signal, value)
    try:
        with pytest.raises(ValueError, match="故障"):
            device.add_powder(check_can_occupied=False, can_number=1)
        assert read("Add_Sample_Start_Process") is False
    finally:
        write(signal, False if isinstance(value, bool) else 0)


def test_ball_mill_fault_prevents_start(plc):
    device, _, read, write = plc
    write("球磨机故障码通讯", 7)
    try:
        with pytest.raises(ValueError, match="故障码=7"):
            device.ball_mill()
        assert read("Ball_Mill_Start_Process") is False
    finally:
        write("球磨机故障码通讯", 0)


@pytest.mark.parametrize("position", range(1, 7))
def test_muffle_fault_prevents_start(plc, position):
    device, _, read, write = plc
    write(f"马弗炉错误代码[{position - 1}]", position + 3)
    try:
        with pytest.raises(ValueError, match="升温超时"):
            device.muffle_furnace_sintering(position, check_can_occupied=False)
        assert read(f"Muffle_Furnace_Start_Process_{position}") is False
    finally:
        write(f"马弗炉错误代码[{position - 1}]", 0)


def test_ball_mill_parameters_match_plc(plc, tmp_path):
    device, _, read, _ = plc
    path = recipe(tmp_path / "mill.xlsx", {"球磨": [("步骤1_工作时间", 1.9), ("步骤2_工作时间", 2)]})
    result = device.set_ball_mill_params(path, record_dir=str(tmp_path))
    assert result["success"] and not result["error"]
    assert Path(result["data"]["record_file"]).is_file()
    expected = sum(read(f"球磨工艺参数[1].步骤{i}_工作时间") for i in range(1, 7)) * 60
    assert device.ball_mill_total_set_time() == expected


@pytest.mark.parametrize("position", range(1, 7))
def test_muffle_parameters_match_plc(plc, tmp_path, position):
    device, _, read, _ = plc
    path = recipe(tmp_path / "furnace.xlsx", {f"马弗炉{position}": [("起始温度", 25), ("第1段程序时间", 1.29), ("第1段程序温度", 100.19)]})
    result = device.set_muffle_furnace_params(path, record_dir=str(tmp_path))
    assert result["success"] and not result["error"]
    expected = read(f"马弗炉_写[{position}].第1段程序时间") / 10 * 60
    assert device.muffle_furnace_total_set_time()[position - 1] == expected


@pytest.mark.parametrize("position", range(1, 7))
def test_muffle_temperature_and_completion(plc, tmp_path, position):
    device, _, read, write = plc
    write(f"马弗炉温度[{position - 1}]", True)
    write(f"马弗炉温度监控[{position - 1}]", 321)
    try:
        result = device.muffle_furnace_sintering(position, False, str(tmp_path))
        assert result["success"]
        assert result["data"]["current_temperature"] == 321
        assert Path(result["data"]["temperature_curve_file"]).is_file()
        assert read(f"Muffle_Furnace_Start_Process_{position}") is False
        elapsed = device.muffle_furnace_current_runtime()[position - 1]
        time.sleep(0.05)
        assert device.muffle_furnace_current_runtime()[position - 1] == elapsed
    finally:
        write(f"马弗炉温度[{position - 1}]", False)


def test_ball_mill_completion(plc):
    device, _, read, write = plc
    for i in range(1, 5):
        write(f"Ball_Mill_Occupied_{i}", True)
    result = device.ball_mill()
    assert result["success"]
    assert read("Ball_Mill_Start_Process") is False
    assert device._ball_mill_status["started_at"] is None
