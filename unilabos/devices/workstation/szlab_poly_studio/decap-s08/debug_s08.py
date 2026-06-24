"""SZLab S08 开关盖工位单独调试脚本。

目录名包含连字符，推荐按文件路径运行：

    PYTHONPATH=. python unilabos/devices/workstation/szlab_poly_studio/decap-s08/debug_s08.py --mode all

模式：
    serve  - 只启动伪 OPC（csv server + flow daemon），保持运行
    run    - 只执行 action（需伪 OPC 已启动，或连接真机）
    all    - 先启动伪 OPC，再执行 action（虚拟模式一键调试）
    reset  - 清除 S08 PC->PLC 信号
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opcua import Client

_DEVICE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DEVICE_DIR.parents[4]
_DEFAULT_CONFIG = _DEVICE_DIR / "s08_debug.json"
logger = logging.getLogger(__name__)


def resolve_repo_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    for root in (_REPO_ROOT, _DEVICE_DIR):
        resolved = (root / candidate).resolve()
        if resolved.exists():
            return resolved
    return (_REPO_ROOT / candidate).resolve()


def load_s08_debug_config(config_path: Path, *, use_production: bool) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    opcua = config["opcua"]
    config["resolved_opcua_url"] = opcua["production_url" if use_production else "virtual_url"]
    config["use_production"] = use_production
    virtual = config.get("virtual_opcua", {})
    config["virtual_csv_path"] = resolve_repo_path(virtual.get("csv", "s08_nodes.csv"))
    config["virtual_flow_path"] = resolve_repo_path(virtual.get("flow", "s08_flow.json"))
    config["virtual_endpoint"] = virtual.get("endpoint", config["resolved_opcua_url"])
    config["virtual_object_name"] = virtual.get("object_name", "VirtualS08")
    return config


def _load_device_class():
    module_path = _DEVICE_DIR / "s08_cap_station.py"
    spec = importlib.util.spec_from_file_location("szlab_decap_s08_debug_device", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 S08 设备模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SZLabS08CapStationDevice


class S08OpcUaClient:
    def __init__(self, url: str, object_name: str = "VirtualS08") -> None:
        self.url = url
        self.object_name = object_name
        self.client = Client(url)
        self.nodes: dict[str, Any] = {}

    def connect(self) -> None:
        self.client.connect()
        objects = self.client.get_objects_node()
        for child in objects.get_children():
            if child.get_browse_name().Name == self.object_name:
                self.nodes = {node.get_browse_name().Name: node for node in child.get_children()}
                return
        raise RuntimeError(f"OPC UA 中未找到对象: {self.object_name}")

    def disconnect(self) -> None:
        self.client.disconnect()

    def read(self, node_name: str) -> Any:
        return self.nodes[node_name].get_value()

    def write(self, node_name: str, value: Any) -> None:
        self.nodes[node_name].set_value(value)


def start_virtual_opcua_stack(config: dict[str, Any]) -> tuple[Any, threading.Thread, threading.Event]:
    from tests.psuedo_devices.common.opcua_csv_server import CsvOpcUaServer
    from tests.psuedo_devices.common.opcua_flow_daemon import FlowDaemon

    endpoint = config["virtual_endpoint"]
    endpoint_host = urlparse(endpoint).hostname
    if endpoint_host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        raise ValueError(
            "虚拟 OPC endpoint 必须绑定本机地址，例如 opc.tcp://127.0.0.1:50102/；"
            f"当前是 {endpoint}。真机地址请写在 production_url，并使用 run --production。"
        )

    print("启动 S08 伪 OPC UA:")
    print(f"  --csv {config['virtual_csv_path']}")
    print(f"  --endpoint {endpoint}")
    print(f"  --flow {config['virtual_flow_path']}")

    server = CsvOpcUaServer(
        endpoint=endpoint,
        csv_path=config["virtual_csv_path"],
        object_name=config["virtual_object_name"],
        namespace_uri="http://unilabos.com/opcua/test/pseudo-device",
        server_name="UniLabOS S08 Debug OPC UA Server",
        name_column="变量名",
        data_type_column="数据类型",
        initial_value_column="初始值",
        node_id_column="",
        initial_values={
            "S08原点信号": True,
            "S08允许加工": True,
        },
    )
    server.start()

    stop_event = threading.Event()
    daemon = FlowDaemon(
        url=endpoint,
        object_name=config["virtual_object_name"],
        flow_path=config["virtual_flow_path"],
        poll_interval=0.02,
        stop_requested=stop_event.is_set,
    )
    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()
    time.sleep(0.5)
    print("S08 伪 OPC 已启动，按 Ctrl+C 停止")
    return server, daemon_thread, stop_event


def serve_virtual_opcua(config_path: Path = _DEFAULT_CONFIG) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_s08_debug_config(config_path, use_production=False)
    server, daemon_thread, stop_event = start_virtual_opcua_stack(config)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("正在停止 S08 伪 OPC ...")
        stop_event.set()
        daemon_thread.join(timeout=2.0)
        server.stop()


def run_s08_debug(
    *,
    opcua_url: str,
    object_name: str,
    process_timeout: float,
    poll_interval: float,
    require_station_ready: bool,
    action_cfg: dict[str, Any],
) -> dict[str, Any]:
    device_cls = _load_device_class()
    client = S08OpcUaClient(opcua_url, object_name=object_name)
    client.connect()
    try:
        device = device_cls(
            plc_device_id="debug_s08_plc",
            process_timeout=process_timeout,
            poll_interval=poll_interval,
            require_station_ready=require_station_ready,
        )
        device._read_plc_variable = client.read
        device._write_plc_variable = client.write

        action_name = str(action_cfg["name"])
        action = getattr(device, action_name)
        kwargs = dict(action_cfg)
        kwargs.pop("name")
        print(f"OPC UA: {opcua_url}")
        print(f"Action: {action_name}")
        print(f"Raw action config: {action_cfg}")
        result = action(**kwargs)
        print(result)
        return result
    finally:
        client.disconnect()


def _run_from_config(config_path: Path, *, use_production: bool) -> dict[str, Any]:
    config = load_s08_debug_config(config_path, use_production=use_production)
    device_cfg = config["device"]
    action_cfg = config["action"]

    if not use_production:
        print("虚拟 OPC 配置:")
        print(f"  csv: {config['virtual_csv_path']}")
        print(f"  endpoint: {config['virtual_endpoint']}")
        print(f"  flow: {config['virtual_flow_path']}")
    print(f"Config file: {config_path.resolve()}")

    return run_s08_debug(
        opcua_url=config["resolved_opcua_url"],
        object_name=config["virtual_object_name"],
        process_timeout=float(device_cfg.get("process_timeout", 300.0)),
        poll_interval=float(device_cfg.get("poll_interval", 0.2)),
        require_station_ready=bool(device_cfg.get("require_station_ready", True)),
        action_cfg=action_cfg,
    )


def reset_plc_signals(config_path: Path, *, use_production: bool) -> None:
    config = load_s08_debug_config(config_path, use_production=use_production)
    client = S08OpcUaClient(config["resolved_opcua_url"], object_name=config["virtual_object_name"])
    client.connect()
    try:
        for name, value in (
            ("S08工艺选择", 0),
            ("S08参数写入完成", False),
            ("S082瓶盖暂存位", 0),
            ("S08工艺完成", 0),
        ):
            client.write(name, value)
            print(f"已清除 {name} = {value!r}")
    finally:
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="SZLab S08 开关盖工位单独调试")
    parser.add_argument(
        "--mode",
        choices=("serve", "run", "all", "reset"),
        default="run",
        help="serve=只起伪 OPC；run=执行 action；all=虚拟一键执行；reset=清除 S08 信号",
    )
    parser.add_argument("--production", action="store_true", help="连接真机 production_url")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG, help="调试配置 JSON")
    args = parser.parse_args()

    if args.mode == "serve":
        if args.production:
            raise SystemExit("serve 模式仅用于虚拟 OPC，请不要加 --production")
        serve_virtual_opcua(args.config)
        return 0

    if args.mode == "reset":
        reset_plc_signals(args.config, use_production=args.production)
        return 0

    virtual_stack: tuple[Any, threading.Thread, threading.Event] | None = None
    if args.mode == "all" and not args.production:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        config = load_s08_debug_config(args.config, use_production=False)
        virtual_stack = start_virtual_opcua_stack(config)

    try:
        result = _run_from_config(args.config, use_production=args.production)
    finally:
        if virtual_stack is not None:
            server, daemon_thread, stop_event = virtual_stack
            stop_event.set()
            daemon_thread.join(timeout=2.0)
            server.stop()

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
