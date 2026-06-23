"""根据 flow JSON 监听 OPC UA 变量并写回伪 PLC 响应。"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from opcua import Client

logger = logging.getLogger(__name__)


def _format_flow_summary(flow: dict[str, Any]) -> str:
    lines = [f"flow={flow.get('name', 'unknown')}"]
    for rule in flow.get("rules", []):
        trigger = rule.get("trigger", {})
        lines.append(
            f"rule={rule.get('name')}: trigger {trigger.get('node')} == {trigger.get('value')} ({trigger.get('edge')})"
        )
        for action in rule.get("actions", []):
            if "write" in action:
                node, value = action["write"]
                lines.append(f"  action: write {node} = {value}")
            elif "sleep" in action:
                lines.append(f"  action: sleep {action['sleep']}s")
    return "\n".join(lines)


class OpcUaFlowDaemon:
    def __init__(self, url: str, flow_path: Path, poll_interval: float = 0.05):
        self.url = url
        self.flow_path = flow_path
        self.poll_interval = poll_interval
        self.flow = json.loads(flow_path.read_text(encoding="utf-8"))
        self._client = Client(url)
        self._nodes: dict[str, Any] = {}
        self._last_values: dict[str, Any] = {}
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def summary(self) -> str:
        return _format_flow_summary(self.flow)

    def connect(self) -> None:
        logging.getLogger("opcua").setLevel(logging.WARNING)
        try:
            self._client.connect()
        except ConnectionRefusedError as exc:
            raise ConnectionRefusedError(
                f"无法连接 {self.url}。请先另开终端启动 OPC CSV 服务器，例如:\n"
                "  PYTHONPATH=. python tests/pseudo_devices/common/opcua_csv_server.py \\\n"
                "    --csv unilabos/devices/workstation/szlab_poly_studio/pump/pump_nodes.csv \\\n"
                f"    --endpoint {self.url}"
            ) from exc
        objects = self._client.get_objects_node()
        virtual_mixer = None
        for child in objects.get_children():
            if child.get_browse_name().Name == "VirtualMixer":
                virtual_mixer = child
                break
        if virtual_mixer is None:
            raise RuntimeError("OPC UA 中未找到 VirtualMixer 对象")
        for child in virtual_mixer.get_children():
            self._nodes[child.get_browse_name().Name] = child
        for rule in self.flow.get("rules", []):
            trigger_name = rule.get("trigger", {}).get("node")
            if trigger_name:
                self._last_values[trigger_name] = self.read(trigger_name)

    def disconnect(self) -> None:
        self._client.disconnect()

    def read(self, name: str) -> Any:
        return self._nodes[name].get_value()

    def write(self, name: str, value: Any) -> None:
        self._nodes[name].set_value(value)

    def _is_rising(self, name: str, expected: Any) -> bool:
        previous = self._last_values.get(name)
        current = self.read(name)
        self._last_values[name] = current
        return previous != expected and current == expected

    def _run_rule(self, rule: dict[str, Any]) -> None:
        trigger = rule.get("trigger", {})
        node = trigger.get("node")
        expected = trigger.get("value")
        edge = trigger.get("edge", "rising")
        if edge != "rising" or not self._is_rising(node, expected):
            return

        observed = {name: self.read(name) for name in rule.get("log_nodes", [])}
        logger.info("flow trigger %s observed=%s", rule.get("name"), observed)

        for action in rule.get("actions", []):
            if "write" in action:
                target, value = action["write"]
                self.write(target, value)
                logger.info("flow write %s=%r", target, value)
            elif "sleep" in action:
                time.sleep(float(action["sleep"]))

    def run_once(self) -> None:
        for rule in self.flow.get("rules", []):
            self._run_rule(rule)

    def start(self) -> None:
        if self._running:
            return
        self.connect()
        self._running = True
        logger.info("flow daemon 已启动\n%s", self.summary)

        def _loop() -> None:
            while self._running:
                try:
                    self.run_once()
                except Exception as exc:
                    logger.error("flow daemon 循环异常: %s", exc)
                time.sleep(self.poll_interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self.disconnect()
        logger.info("flow daemon 已停止")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="OPC UA flow daemon")
    parser.add_argument("--url", required=True)
    parser.add_argument("--flow", type=Path, required=True)
    args = parser.parse_args()

    daemon = OpcUaFlowDaemon(url=args.url, flow_path=args.flow)
    daemon.start()
    logger.info("按 Ctrl+C 停止")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    main()
