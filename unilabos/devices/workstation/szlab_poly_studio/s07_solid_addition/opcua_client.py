from __future__ import annotations

import logging
import time
from typing import Any

from opcua import Client, ua
from opcua.common.connection import SecureConnection

_TOKEN_TIME_CHECK_PATCHED = False


def _ignore_plc_clock_token_time_check() -> None:
    global _TOKEN_TIME_CHECK_PATCHED
    if _TOKEN_TIME_CHECK_PATCHED:
        return

    def _check_sym_header_without_time_check(self, securityHeader):
        assert isinstance(securityHeader, ua.SymmetricAlgorithmHeader), (
            "Expected SymAlgHeader, got: {0}".format(securityHeader)
        )
        if securityHeader.TokenId != self.security_token.TokenId:
            if securityHeader.TokenId != self.next_security_token.TokenId:
                if self._allow_prev_token and securityHeader.TokenId == self.prev_security_token.TokenId:
                    return
                raise ua.UaError(
                    "Invalid security token id {}, expected {} or {}".format(
                        securityHeader.TokenId,
                        self.security_token.TokenId,
                        self.next_security_token.TokenId,
                    )
                )
            self.revolve_tokens()
            self.security_policy.make_remote_symmetric_key(self.local_nonce, self.remote_nonce)
            self.prev_security_token = ua.ChannelSecurityToken()
        if self.prev_security_token.TokenId != 0:
            self.security_policy.make_remote_symmetric_key(self.local_nonce, self.remote_nonce)
            self.prev_security_token = ua.ChannelSecurityToken()

    SecureConnection._check_sym_header = _check_sym_header_without_time_check
    _TOKEN_TIME_CHECK_PATCHED = True


class S07OpcUaClient:
    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        browse_depth: int = 8,
        browse_limit: int = 5000,
        node_id_map: dict[str, str] | None = None,
        allow_recursive_browse: bool = False,
        timeout: float = 30.0,
        ignore_token_time_check: bool = False,
    ):
        logging.getLogger("opcua").setLevel(logging.WARNING)
        if ignore_token_time_check:
            _ignore_plc_clock_token_time_check()
        self.url = url
        self._browse_depth = int(browse_depth)
        self._browse_limit = int(browse_limit)
        self._node_id_map = node_id_map or {}
        self._allow_recursive_browse = bool(allow_recursive_browse)
        self.client = Client(url, timeout=timeout)
        if username and password:
            self.client.set_user(username)
            self.client.set_password(password)
        self.client.connect()
        self._nodes_by_name = self._browse_device_nodes()

    def _browse_device_nodes(self) -> dict[str, Any]:
        if self._node_id_map:
            return {name: self.client.get_node(node_id) for name, node_id in self._node_id_map.items()}

        objects = self.client.get_objects_node()
        top_children = objects.get_children()
        for child in top_children:
            if child.get_browse_name().Name == "VirtualMixer":
                return {node.get_browse_name().Name: node for node in child.get_children()}

        if not self._allow_recursive_browse:
            top_names = []
            for child in top_children:
                try:
                    top_names.append(f"{child.get_browse_name().Name}({child.nodeid})")
                except Exception:
                    top_names.append(str(child.nodeid))
            raise RuntimeError(
                "OPC UA 中未找到 VirtualMixer 对象。真机节点树较大，已停止自动递归扫描以避免卡住；"
                "请先用 probe_real_opcua.py 找到 S07 变量 NodeId，并写入 s07_debug.json 的 "
                f"device.opcua_node_id_map。顶层对象: {top_names}"
            )

        nodes = self._browse_nodes_recursively(objects)
        if not nodes:
            raise RuntimeError("OPC UA 中未找到 VirtualMixer 对象，也没有递归扫描到可用变量节点")
        return nodes

    def _browse_nodes_recursively(self, root: Any) -> dict[str, Any]:
        nodes_by_name: dict[str, Any] = {}
        visited = 0
        stack: list[tuple[Any, int]] = [(root, 0)]

        while stack and visited < self._browse_limit:
            node, depth = stack.pop()
            visited += 1
            try:
                children = node.get_children()
            except Exception:
                continue
            for child in children:
                try:
                    browse_name = child.get_browse_name().Name
                except Exception:
                    browse_name = ""
                try:
                    display_name = child.get_display_name().Text
                except Exception:
                    display_name = ""
                for name in (browse_name, display_name):
                    if name and name not in nodes_by_name:
                        nodes_by_name[name] = child
                if depth < self._browse_depth:
                    stack.append((child, depth + 1))

        logging.getLogger(__name__).info(
            "未找到 VirtualMixer，已递归扫描 OPC UA 节点: visited=%s indexed=%s",
            visited,
            len(nodes_by_name),
        )
        return nodes_by_name

    def read(self, name: str) -> Any:
        return self._node(name).get_value()

    def write(self, name: str, value: Any) -> None:
        node = self._node(name)
        variant_type = None
        try:
            variant_type = node.get_data_type_as_variant_type()
            self._write_value_only(node, value, variant_type)
        except ua.UaStatusCodeError as exc:
            raise RuntimeError(f"写入 OPC UA 变量失败: {name} ({node.nodeid}) = {value!r}: {exc}") from exc
        except Exception:
            if variant_type is None:
                self._write_value_only(node, value, None)
            else:
                node.set_value(value, variant_type)

    def pulse(self, name: str, value: Any = True, reset_value: Any = False, reset_delay: float = 1.0) -> None:
        self.write(name, reset_value)
        time.sleep(0.1)
        self.write(name, value)
        time.sleep(reset_delay)
        self.write(name, reset_value)

    def disconnect(self) -> None:
        self.client.disconnect()

    def _write_value_only(self, node: Any, value: Any, variant_type: Any | None) -> None:
        variant = ua.Variant(value, variant_type) if variant_type is not None else ua.Variant(value)
        data_value = ua.DataValue(variant)
        data_value.StatusCode = None
        data_value.SourceTimestamp = None
        data_value.ServerTimestamp = None
        node.set_attribute(ua.AttributeIds.Value, data_value)

    def _node(self, name: str):
        try:
            return self._nodes_by_name[name]
        except KeyError:
            node = self.client.get_node(f"ns=4;s=上位机通讯|{name}")
            self._nodes_by_name[name] = node
            return node
