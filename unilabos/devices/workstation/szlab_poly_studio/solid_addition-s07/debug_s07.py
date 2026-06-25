"""SZLab S07 固体加料工位单独调试脚本。

运行：
    PYTHONPATH=. python unilabos/devices/workstation/szlab_poly_studio/solid_addition-s07/debug_s07.py --mode all
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opcua import Client

_DEVICE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DEVICE_DIR.parents[4]
_DEFAULT_CONFIG = _DEVICE_DIR / "s07_debug.json"


def resolve_repo_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    for root in (_REPO_ROOT, _DEVICE_DIR):
        resolved = (root / candidate).resolve()
        if resolved.exists():
            return resolved
    return (_REPO_ROOT / candidate).resolve()


def load_s07_debug_config(config_path: Path, *, use_production: bool) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    opcua = config["opcua"]
    config["resolved_opcua_url"] = opcua["production_url" if use_production else "virtual_url"]
    virtual = config.get("virtual_opcua", {})
    config["virtual_csv_path"] = resolve_repo_path(virtual.get("csv", "s07_nodes.csv"))
    config["virtual_flow_path"] = resolve_repo_path(virtual.get("flow", "s07_flow.json"))
    config["virtual_endpoint"] = virtual.get("endpoint", config["resolved_opcua_url"])
    return config


def start_virtual_opcua_stack(config: dict[str, Any]) -> tuple[Any, Any]:
    from tests.pseudo_devices.common.opcua_csv_server import OpcUaCsvServer
    from tests.pseudo_devices.common.opcua_flow_daemon import OpcUaFlowDaemon

    endpoint = config["virtual_endpoint"]
    endpoint_host = urlparse(endpoint).hostname
    if endpoint_host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        raise ValueError(f"虚拟 OPC endpoint 必须绑定本机地址，当前是 {endpoint}")
    print("启动 S07 伪 OPC UA:")
    print(f"  --csv {config['virtual_csv_path']}")
    print(f"  --endpoint {endpoint}")
    print(f"  --flow {config['virtual_flow_path']}")
    server = OpcUaCsvServer(endpoint=endpoint, csv_path=config["virtual_csv_path"])
    server.start()
    server.write("S07原点信号", True)
    server.write("S07允许加工", True)
    daemon = OpcUaFlowDaemon(url=endpoint, flow_path=config["virtual_flow_path"])
    daemon.start()
    print("S07 伪 OPC 已启动，按 Ctrl+C 停止")
    return server, daemon


def serve_virtual_opcua(config_path: Path = _DEFAULT_CONFIG) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_s07_debug_config(config_path, use_production=False)
    server, daemon = start_virtual_opcua_stack(config)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("正在停止 S07 伪 OPC ...")
        daemon.stop()
        server.stop()


class S07OpcUaClient:
    def __init__(self, url: str) -> None:
        self.client = Client(url)
        self.nodes: dict[str, Any] = {}

    def connect(self) -> None:
        self.client.connect()
        objects = self.client.get_objects_node()
        for child in objects.get_children():
            if child.get_browse_name().Name == "VirtualMixer":
                self.nodes = {node.get_browse_name().Name: node for node in child.get_children()}
                return
        raise RuntimeError("OPC UA 中未找到 VirtualMixer 对象")

    def disconnect(self) -> None:
        self.client.disconnect()

    def read(self, node_name: str) -> Any:
        return self.nodes[node_name].get_value()

    def write(self, node_name: str, value: Any) -> None:
        self.nodes[node_name].set_value(value)


def run_s07_debug(config_path: Path, *, use_production: bool) -> dict[str, Any]:
    config = load_s07_debug_config(config_path, use_production=use_production)
    device_cfg = config["device"]
    action_cfg = dict(config["action"])
    action_name = action_cfg.pop("name")
    module = importlib.import_module("unilabos.devices.workstation.szlab_poly_studio.solid_addition-s07.s07")
    device = module.SZLabS07SolidAdditionDevice(
        plc_device_id="debug_s07_plc",
        process_timeout=float(device_cfg.get("process_timeout", 300.0)),
        poll_interval=float(device_cfg.get("poll_interval", 0.2)),
        require_station_ready=bool(device_cfg.get("require_station_ready", True)),
    )
    client = S07OpcUaClient(config["resolved_opcua_url"])
    client.connect()
    try:
        device._read_plc_variable = client.read
        device._write_plc_variable = client.write
        print(f"OPC UA: {config['resolved_opcua_url']}")
        print(f"Action: {action_name}")
        print(f"Raw action config: {action_cfg}")
        result = getattr(device, action_name)(**action_cfg)
        print("S07 debug result:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"S07 debug success: {bool(result.get('success'))}")
        return result
    finally:
        client.disconnect()


def reset_plc_signals(config_path: Path, *, use_production: bool) -> None:
    sensors = importlib.import_module(
        "unilabos.devices.workstation.szlab_poly_studio.solid_addition-s07.sensors"
    )

    config = load_s07_debug_config(config_path, use_production=use_production)
    client = S07OpcUaClient(config["resolved_opcua_url"])
    client.connect()
    try:
        for name, value in (
            (sensors.NODE_PROCESS_SELECT, 0),
            (sensors.NODE_PARAMS_WRITTEN, False),
            (sensors.NODE_LOAD_POSITION, 0),
            (sensors.NODE_PROCESS_COMPLETE, 0),
        ):
            client.write(name, value)
            print(f"已清除 {name} = {value!r}")
    finally:
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="SZLab S07 固体加料工位单独调试")
    parser.add_argument("--mode", choices=("serve", "run", "all", "reset"), default="run")
    parser.add_argument("--production", action="store_true", help="连接真机 production_url")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG, help="调试配置 JSON")
    args = parser.parse_args()
    if args.mode == "serve":
        serve_virtual_opcua(args.config)
        return 0
    if args.mode == "reset":
        reset_plc_signals(args.config, use_production=args.production)
        return 0
    virtual_stack = None
    if args.mode == "all" and not args.production:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        virtual_stack = start_virtual_opcua_stack(load_s07_debug_config(args.config, use_production=False))
        time.sleep(0.5)
    try:
        result = run_s07_debug(args.config, use_production=args.production)
    finally:
        if virtual_stack is not None:
            server, daemon = virtual_stack
            daemon.stop()
            server.stop()
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
