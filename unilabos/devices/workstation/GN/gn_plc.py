"""
GN 工站 PLC OPC UA 通讯设备

全工站仅保留一个 OPC UA Session，子模块通过 plc_device_id 转发读写（同 AI4C_plc / szlab_poly_plc）。
连接失败时不阻塞 ROS 节点创建，由 post_init / 首次读写时再重试。
"""

import logging
import os
import time
from typing import Any, Optional

from opcua import Client
from opcua.ua.uaerrors._auto import BadTooManySessions

from unilabos.devices.workstation.AI4C.base_opcua_client import (
    BaseOpcUaClient,
    OpcUaClientWithSubscription,
)
from unilabos.registry.decorators import device, not_action
from unilabos.utils.log import logger

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")


@device(
    id="gn_plc",
    display_name="GN PLC",
    category=["workstation"],
    description="GN 合成工站 OPC UA PLC，负责全站变量读写",
)
class GnPlcDevice(OpcUaClientWithSubscription):
    """GN 工站唯一 OPC UA 连接端点。"""

    def __init__(
        self,
        url: str,
        csv_path: str = DEFAULT_CSV_PATH,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_subscription: bool = False,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        connect_retries: int = 5,
        connect_retry_interval: float = 3.0,
        *args,
        **kwargs,
    ):
        self._connect_retries = max(1, connect_retries)
        self._connect_retry_interval = connect_retry_interval
        self._opc_connected = False

        logging.getLogger("opcua").setLevel(logging.WARNING)
        BaseOpcUaClient.__init__(self)

        client = Client(url)
        if username and password:
            client.set_user(username)
            client.set_password(password)
        self._set_client(client)

        self._use_subscription = use_subscription
        self._subscription = None
        self._subscription_handles = {}
        self._subscription_interval = subscription_interval
        self._node_values = {}
        self._cache_timeout = cache_timeout
        self._connection_check_interval = 30.0
        self._connection_monitor_running = False
        self._connection_monitor_thread = None

        if csv_path:
            self.load_nodes_from_csv(csv_path)

        if not self._try_connect_with_retry(log_final_error=False):
            logger.error(
                "gn_plc 启动时 OPC UA 连接失败（常见原因：BadTooManySessions 会话已满），"
                "ROS 节点仍将创建；请关闭其他 OPC UA 客户端或重启 PLC 后重试"
            )

    @not_action
    def post_init(self, ros_node) -> None:
        if self._opc_connected:
            return
        if self._try_connect_with_retry():
            logger.info("gn_plc post_init 已成功连接 OPC UA")
        else:
            logger.error("gn_plc post_init 仍无法连接 OPC UA，子模块读写将失败直至会话释放")

    @property
    @not_action
    def is_connected(self) -> bool:
        return self._opc_connected

    @not_action
    def _try_connect_with_retry(self, log_final_error: bool = True) -> bool:
        if self._opc_connected:
            return True
        last_error: Optional[Exception] = None
        for attempt in range(1, self._connect_retries + 1):
            try:
                OpcUaClientWithSubscription._connect(self)
                self._opc_connected = True
                if not self._connection_monitor_running:
                    self._start_connection_monitor()
                return True
            except BadTooManySessions as exc:
                last_error = exc
                if attempt >= self._connect_retries:
                    break
                logger.warning(
                    f"OPC UA 会话已满 (BadTooManySessions)，"
                    f"{self._connect_retry_interval:.0f}s 后重试 ({attempt}/{self._connect_retries})"
                )
                time.sleep(self._connect_retry_interval)
            except Exception as exc:
                last_error = exc
                break
        if log_final_error and last_error is not None:
            logger.error(f"gn_plc OPC UA 连接失败: {last_error}")
        return False

    @not_action
    def _ensure_connected(self) -> None:
        if self._opc_connected:
            return
        if not self._try_connect_with_retry():
            raise RuntimeError(
                "gn_plc 未连接 OPC UA（PLC 会话已满 BadTooManySessions）。"
                "请关闭 standalone 调试脚本、其他 Uni-Lab 实例或 UA 客户端，必要时重启 PLC OPC UA 服务"
            )

    @not_action
    def read_variable(self, node_name: str, use_cache: bool = True) -> Any:
        self._ensure_connected()
        return self.get_node_value(node_name, use_cache=use_cache, force_read=not use_cache)

    @not_action
    def write_variable(self, node_name: str, value: Any) -> bool:
        self._ensure_connected()
        return bool(self.set_node_value(node_name, value))
