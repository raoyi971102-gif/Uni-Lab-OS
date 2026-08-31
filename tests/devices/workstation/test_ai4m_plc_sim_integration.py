"""使用 Uni-Lab-Sim/PLC-Sim 对 AI4M OP10/OP20 驱动做真实 OPC UA 联调。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

from unilabos.devices.workstation.AI4M.AI4M import AI4MDevice
from unilabos.devices.workstation.AI4M.AI4M002 import AI4M002Device
from unilabos.devices.workstation.AI4M.bottle_carriers import (
    Electrode_Holder,
    Hydrogel_Powder_Containing_1BottleCarrier,
)


REPO_ROOT = Path(__file__).parents[3]
AI4M_ROOT = REPO_ROOT / "unilabos" / "devices" / "workstation" / "AI4M"


def _plc_sim_root() -> Path:
    configured = os.environ.get("UNILAB_PLC_SIM_ROOT")
    if not configured:
        pytest.skip("未设置 UNILAB_PLC_SIM_ROOT，跳过外部 PLC-Sim 集成测试")
    root = Path(configured).resolve()
    if not (root / "server.py").is_file() or not (root / "common.py").is_file():
        pytest.skip(f"UNILAB_PLC_SIM_ROOT 不是 PLC-Sim 源码目录: {root}")
    return root


def _load_plc_sim_modules(root: Path):
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    module_name = "unilab_ai4m_plc_sim_server"
    spec = importlib.util.spec_from_file_location(module_name, root / "server.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 PLC-Sim server.py: {root}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _AI4MPlcScan:
    """只模拟本次驱动联调所需的 PLC 输出扫描逻辑。"""

    def __init__(self, nodes: dict) -> None:
        self.nodes = nodes
        self.forced_true: set[str] = set()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def set(self, name: str, value) -> None:
        self.nodes[name].set_value(value)

    def get(self, name: str):
        return self.nodes[name].get_value()

    def force_true(self, name: str) -> None:
        self.forced_true.add(name)
        self.set(name, True)

    def start(self) -> None:
        for name in ("机械臂空闲", "三轴空闲"):
            self.set(name, True)
        for station_id in (1, 2, 3):
            self.set(f"移液搅拌控制[{station_id - 1}].移液搅拌请求加工", True)
        for station_id in (1, 2):
            self.set(f"磁搅控制[{station_id - 1}].磁搅请求加工", True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2.0)

    def _scan_initialization(self) -> None:
        self.set("机械臂初始化完成", bool(self.get("机械臂初始化")))
        self.set("三轴初始化完成", bool(self.get("三轴初始化")))
        for index in range(3):
            self.set(
                f"移液搅拌控制[{index}].移液搅拌初始化完成",
                bool(self.get(f"移液搅拌控制[{index}].移液搅拌初始化")),
            )
        for index in range(2):
            self.set(
                f"磁搅控制[{index}].磁搅初始化完成",
                bool(self.get(f"磁搅控制[{index}].磁搅初始化")),
            )

    def _scan_robot(self) -> None:
        triggered = bool(self.get("机械臂动作触发"))
        self.set("机械臂动作完成", triggered)
        self.set("机械臂空闲", not triggered)
        if not triggered:
            return
        action = int(self.get("机械臂动作代码"))
        position = int(self.get("机械臂目标位置代码"))
        station_id = int(self.get("机械臂目标取放料代码"))
        if position == 2 and station_id in (1, 2, 3):
            self.set(
                f"移液搅拌控制[{station_id - 1}].移液搅拌占位",
                action == 2,
            )

    def _scan_axis(self) -> None:
        triggered = bool(self.get("三轴动作触发"))
        self.set("三轴动作完成", triggered)
        self.set("三轴空闲", not triggered)
        if not triggered:
            return
        action = int(self.get("三轴动作代码"))
        position = int(self.get("三轴目标位置代码"))
        cell_id = {3: 1, 2: 2}.get(position)
        if cell_id is not None:
            self.set(f"磁搅控制[{cell_id - 1}].磁搅占位", action == 2)

    def _scan_processes(self) -> None:
        for index in range(3):
            downloaded = bool(
                self.get(f"移液搅拌控制[{index}].移液搅拌参数已下发")
            )
            started = bool(self.get(f"移液搅拌控制[{index}].移液搅拌开始加工"))
            self.set(
                f"移液搅拌控制[{index}].移液搅拌参数已执行",
                downloaded,
            )
            self.set(
                f"移液搅拌控制[{index}].移液搅拌加工完成",
                started,
            )
        for index in range(2):
            downloaded = bool(self.get(f"磁搅控制[{index}].磁搅参数已下发"))
            started = bool(self.get(f"磁搅控制[{index}].磁搅开始加工"))
            self.set(f"磁搅控制[{index}].磁搅参数已执行", downloaded)
            complete_name = f"磁搅控制[{index}].磁搅加工完成"
            self.set(
                complete_name,
                started or complete_name in self.forced_true,
            )

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self._scan_initialization()
            self._scan_robot()
            self._scan_axis()
            self._scan_processes()
            self.stop_event.wait(0.01)


def test_ai4m_and_ai4m002_against_unilab_plc_sim() -> None:
    plc_sim = _load_plc_sim_modules(_plc_sim_root())
    node_defs = plc_sim.load_csvs(
        [
            AI4M_ROOT / "opcua_nodes_OP10_UniLab.csv",
            AI4M_ROOT / "opcua_nodes_OP20_UniLab.csv",
        ]
    )
    port = _free_tcp_port()
    endpoint = f"opc.tcp://127.0.0.1:{port}/xuse_sim/"
    server = plc_sim.build_server(endpoint)
    namespace = plc_sim.register_ns_padding(server, 4, "urn:ai4m:integration")
    server.start()
    nodes = plc_sim.add_nodes(server, namespace, node_defs)
    scan = _AI4MPlcScan(nodes)
    scan.start()

    op10 = None
    op20 = None
    try:
        op10 = AI4MDevice(url=endpoint, use_subscription=True)
        op20 = AI4M002Device(
            url=endpoint,
            use_subscription=True,
            bts_base_url="http://127.0.0.1:1",
            bts_request_timeout=0.2,
        )
        op10.deck.warehouses["水凝胶烧杯堆栈"]["A1"] = (
            Hydrogel_Powder_Containing_1BottleCarrier("仿真烧杯1")
        )
        op20.deck.warehouses["原始电极堆栈"]["1"] = Electrode_Holder(
            "仿真电极1"
        )
        op20.deck.warehouses["原始电极堆栈"]["2"] = Electrode_Holder(
            "仿真电极2"
        )

        assert len(op10.get_node_registry()) == 46
        assert len(op20.get_node_registry()) == 36
        assert op10.get_node_registry() is not op20.get_node_registry()
        assert "机械臂空闲" in op10.get_node_registry()
        assert "机械臂空闲" not in op20.get_node_registry()
        assert "三轴空闲" in op20.get_node_registry()
        assert "三轴空闲" not in op10.get_node_registry()

        assert op10.trigger_init()["message"].endswith("初始化完成")
        picked = op10.trigger_robot_pick_beaker(1, 1)
        assert picked["place_station_id"] == 1
        processed = op10.trigger_station_process(1, 100, 30, 2, 20)
        assert processed["station_id"] == 1
        placed = op10.trigger_robot_place_beaker(1, 1)
        assert placed["place_beaker_id"] == 1
        assert op10.deck.warehouses["水凝胶烧杯堆栈"]["C1"].name == "仿真烧杯1"
        assert op10.download_auto_params(100, 30, 2, 20, 5)["message"].endswith(
            "下发完成"
        )
        with pytest.raises(RuntimeError, match="未提供自动模式"):
            op10.start_auto_mode()

        assert op20.trigger_s02_init()["message"].endswith("初始化完成")
        moved = op20.trigger_3axis_pick_from_raw_and_place_to_electrolytic_cell(1, 1)
        assert moved["electrolytic_cell_id"] == 1
        assert op20.set_stirrer_params(1, 100, 30, 2)["station_id"] == 1
        with pytest.raises(RuntimeError, match="BTS 启动失败"):
            op20.trigger_electrolytic_cell_bts_reaction(1, duration_sec=1)

        scan.force_true("磁搅控制[0].磁搅加工完成")
        finished = op20.trigger_3axis_pick_from_electrolytic_cell_and_place_to_finished(
            1,
            cleaning_time=1,
            nitrogen_time=1,
            place_code=1,
        )
        assert finished["place_code"] == 1
        direct = op20.trigger_3axis_pick_from_raw_and_process_to_finished(
            pick_code=2,
            pickling_time=1,
            cleaning_time=1,
            nitrogen_time=1,
            place_code=2,
        )
        assert direct["place_code"] == 2
        finished_stack = op20.deck.warehouses["完成电极堆栈"]
        assert finished_stack["1"].name == "仿真电极1"
        assert finished_stack["2"].name == "仿真电极2"
    finally:
        if op10 is not None:
            op10.disconnect()
        if op20 is not None:
            op20.disconnect()
        scan.stop()
        server.stop()
