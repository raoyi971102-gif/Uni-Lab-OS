"""从 CSV fixture 启动 VirtualMixer OPC UA 伪服务器。"""

from __future__ import annotations

import argparse
import csv
import logging
import threading
import time
from pathlib import Path
from typing import Any

from opcua import Server, ua

logger = logging.getLogger(__name__)

VIRTUAL_MIXER_OBJECT = "VirtualMixer"


def _parse_initial_value(data_type: str, raw: str) -> tuple[Any, ua.VariantType]:
    dtype = (data_type or "").upper()
    text = (raw or "").strip()
    if dtype == "BOOL":
        return text.upper() in {"ON", "TRUE", "1"}, ua.VariantType.Boolean
    if dtype in {"INT", "DINT"}:
        return int(text or "0"), ua.VariantType.Int32
    if dtype == "REAL":
        return float(text or "0"), ua.VariantType.Float
    return text, ua.VariantType.String


def load_csv_variables(csv_path: Path) -> dict[str, tuple[Any, ua.VariantType]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "gb18030", "gbk", "utf-8"):
        try:
            with csv_path.open(encoding=encoding, newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                variables: dict[str, tuple[Any, ua.VariantType]] = {}
                for row in reader:
                    name = (row.get("变量名") or "").strip()
                    if not name:
                        continue
                    variables[name] = _parse_initial_value(row.get("数据类型", ""), row.get("初始值", ""))
                return variables
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return {}


class OpcUaCsvServer:
    def __init__(self, endpoint: str, csv_path: Path):
        self.endpoint = endpoint
        self.csv_path = csv_path
        self.server = Server()
        self.server.set_endpoint(endpoint)
        self.server.set_server_name("Uni-Lab Pseudo VirtualMixer Server")
        self.idx = self.server.register_namespace("http://unilabos.com/opcua/pseudo")
        self.objects = self.server.get_objects_node()
        self.virtual_mixer = self.objects.add_object(self.idx, VIRTUAL_MIXER_OBJECT)
        self.nodes: dict[str, Any] = {}
        self._running = False
        self._thread: threading.Thread | None = None

    def load_from_csv(self) -> None:
        for name, (value, variant_type) in load_csv_variables(self.csv_path).items():
            node = self.virtual_mixer.add_variable(self.idx, name, value, variant_type)
            node.set_writable()
            self.nodes[name] = node
            logger.info("注册变量 %s = %r", name, value)

    def start(self) -> None:
        if self._running:
            return
        self.load_from_csv()
        self.server.start()
        self._running = True
        logger.info("OPC UA CSV 服务器已启动: %s", self.endpoint)

    def stop(self) -> None:
        if not self._running:
            return
        self.server.stop()
        self._running = False
        logger.info("OPC UA CSV 服务器已停止")

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _run() -> None:
            self.start()
            while self._running:
                time.sleep(0.2)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        time.sleep(0.5)

    def read(self, name: str) -> Any:
        return self.nodes[name].get_value()

    def write(self, name: str, value: Any) -> None:
        self.nodes[name].set_value(value)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="从 CSV 启动 VirtualMixer OPC UA 伪服务器")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--endpoint", default="opc.tcp://127.0.0.1:48506/")
    args = parser.parse_args()

    server = OpcUaCsvServer(endpoint=args.endpoint, csv_path=args.csv)
    server.start()
    logger.info("按 Ctrl+C 停止")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
