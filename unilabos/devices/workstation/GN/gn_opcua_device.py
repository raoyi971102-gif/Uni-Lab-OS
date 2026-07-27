"""
GN OPC UA 设备基类

- 直连模式：传入 url（单机调试，同 AI4C_station 单设备）
- 代理模式：传入 plc_device_id，经 gn_plc 转发（工站内，同 szlab s07 / AI4C.json）
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

try:
    from rclpy.action import ActionClient
    from unilabos_msgs.action import StrSingleInput
except ModuleNotFoundError:
    ActionClient = None
    StrSingleInput = None

from unilabos.devices.workstation.AI4C.base_opcua_client import (
    BaseOpcUaClient,
    OpcUaClientWithSubscription,
)
from unilabos.registry.decorators import not_action
from unilabos.resources.resource_tracker import JSON_UNILABOS_PARAM, PARAM_SAMPLE_UUIDS


class GnOpcUaDevice(OpcUaClientWithSubscription):
    """GN 模块 OPC UA 基类。"""

    def __init__(
        self,
        url: Optional[str] = None,
        plc_device_id: Optional[str] = None,
        csv_path: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_subscription: bool = False,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        plc_action_timeout: float = 120.0,
        plc_wait_server_timeout: float = 5.0,
        *args,
        **kwargs,
    ):
        self.plc_device_id = plc_device_id
        self.plc_action_timeout = plc_action_timeout
        self.plc_wait_server_timeout = plc_wait_server_timeout
        self._ros_node = None
        self._plc_driver = None
        self._plc_command_client = None

        if plc_device_id:
            # 代理模式经 gn_plc 转发，无需本地注册 CSV 节点
            BaseOpcUaClient.__init__(self)
            self._use_subscription = use_subscription
            self._node_values = {}
            self._cache_timeout = cache_timeout
            return

        if not url:
            raise ValueError("GN OPC UA 设备需要 url 或 plc_device_id")
        super().__init__(
            url=url,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs,
        )
        if csv_path:
            self.load_nodes_from_csv(csv_path)

    @not_action
    def bind_plc_driver(self, plc_driver) -> None:
        """工站内由 GN_station 注入 gn_plc 驱动。"""
        self._plc_driver = plc_driver

    @not_action
    def _has_opcua_node(self, name: str) -> bool:
        """判断 OPC 节点是否已在客户端/PLC 注册（代理模式查 gn_plc）。"""

        def _registered_on(client) -> bool:
            mapping = getattr(client, "_name_mapping", {})
            registry = getattr(client, "_node_registry", {})
            pending = getattr(client, "_variables_to_find", {})
            chinese = mapping.get(name, name)
            return (
                name in mapping
                or name in registry
                or chinese in registry
                or name in pending
                or chinese in pending
            )

        if self.plc_device_id:
            plc = self._plc_driver
            if plc is not None:
                return _registered_on(plc)
            return True
        return _registered_on(self)

    @not_action
    def post_init(self, ros_node) -> None:
        if not self.plc_device_id or self._plc_driver is not None:
            return
        if ActionClient is None or StrSingleInput is None:
            raise RuntimeError("GN OPC UA 代理模式需要 ROS2 rclpy 与 unilabos_msgs")
        self._ros_node = ros_node
        self._plc_command_client = ActionClient(
            ros_node,
            StrSingleInput,
            f"/devices/{self.plc_device_id}/_execute_driver_command",
            callback_group=ros_node.callback_group,
        )

    @not_action
    def _wait_future(self, future, timeout: float, description: str):
        done = threading.Event()
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout):
            raise TimeoutError(f"{description} 超时 ({timeout}s)")
        return future.result()

    @not_action
    def _call_plc_command(self, function_name: str, function_args: dict[str, Any]) -> Any:
        if self._plc_driver is not None:
            method = getattr(self._plc_driver, function_name, None)
            if method is None:
                raise RuntimeError(f"{self.plc_device_id} 不支持命令: {function_name}")
            return method(**function_args)
        if self._plc_command_client is None:
            raise RuntimeError(
                f"{self.plc_device_id} 尚未绑定 PLC 驱动（gn_plc 可能启动失败），无法读写 OPC UA"
            )
        if not self._plc_command_client.wait_for_server(timeout_sec=self.plc_wait_server_timeout):
            raise RuntimeError(
                f"{self.plc_device_id} 命令服务不可用（/devices/{self.plc_device_id}/_execute_driver_command），"
                f"请检查 gn_plc 是否已成功初始化并连接 OPC UA"
            )
        goal = StrSingleInput.Goal()
        goal.string = json.dumps(
            {
                "function_name": function_name,
                "function_args": function_args,
                JSON_UNILABOS_PARAM: {PARAM_SAMPLE_UUIDS: {}},
            },
            ensure_ascii=False,
        )
        goal_handle = self._wait_future(
            self._plc_command_client.send_goal_async(goal),
            self.plc_action_timeout,
            f"发送 PLC 命令 {function_name}",
        )
        if not goal_handle.accepted:
            raise RuntimeError(f"{self.plc_device_id} 拒绝执行命令: {function_name}")
        result_wrapper = self._wait_future(
            goal_handle.get_result_async(),
            self.plc_action_timeout,
            f"等待 PLC 命令 {function_name} 返回",
        )
        result = result_wrapper.result
        result_info = json.loads(result.return_info or "{}")
        if not result.success or not result_info.get("suc", False):
            raise RuntimeError(result_info.get("error") or f"{self.plc_device_id} 命令失败: {function_name}")
        return result_info.get("return_value")

    def get_node_value(self, name, use_cache=True, force_read=False):
        if self.plc_device_id:
            return self._call_plc_command(
                "read_variable",
                {"node_name": name, "use_cache": use_cache and not force_read},
            )
        return super().get_node_value(name, use_cache=use_cache, force_read=force_read)

    def set_node_value(self, name, value):
        if self.plc_device_id:
            return bool(
                self._call_plc_command(
                    "write_variable",
                    {"node_name": name, "value": value},
                )
            )
        return super().set_node_value(name, value)
