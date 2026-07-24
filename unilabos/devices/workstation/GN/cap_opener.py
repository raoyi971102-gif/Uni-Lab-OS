"""
开盖机构设备驱动
负责样品瓶的开盖和关盖动作
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_cap_opener",
    category=["gn_cap_opener"],
    description="GN 开盖机构，对样品瓶进行开盖和关盖操作",
    icon="cap_opener.webp",
)
class CapOpenerDevice(OpcUaClientWithSubscription):
    """开盖机构设备类"""

    def __init__(
        self,
        url: str,
        csv_path: str = None,
        username: str = None,
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
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

    @action(auto_prefix=True, description="开盖：对样品瓶进行开盖操作")
    def open_cap(self) -> dict:
        """开盖动作：等待机构空闲，触发开盖，等待完成"""
        logger.info("[开盖机构] 等待空闲...")
        while not self.get_node_value("opener_ready"):
            time.sleep(1.0)

        self.set_node_value("open_cap_complete", False)
        self.set_node_value("open_cap_trigger", True)
        logger.info("[开盖机构] 开盖中...")

        while not self.get_node_value("open_cap_complete"):
            time.sleep(1.0)

        self.set_node_value("open_cap_trigger", False)
        logger.info("[开盖机构] 开盖完成")
        return {"message": "开盖完成"}

    @action(auto_prefix=True, description="关盖：对样品瓶进行关盖操作")
    def close_cap(self) -> dict:
        """关盖动作：等待机构空闲，触发关盖，等待完成"""
        logger.info("[开盖机构] 等待空闲...")
        while not self.get_node_value("opener_ready"):
            time.sleep(1.0)

        self.set_node_value("close_cap_complete", False)
        self.set_node_value("close_cap_trigger", True)
        logger.info("[开盖机构] 关盖中...")

        while not self.get_node_value("close_cap_complete"):
            time.sleep(1.0)

        self.set_node_value("close_cap_trigger", False)
        logger.info("[开盖机构] 关盖完成")
        return {"message": "关盖完成"}
