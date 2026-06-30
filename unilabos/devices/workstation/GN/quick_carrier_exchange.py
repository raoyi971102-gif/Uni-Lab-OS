"""
快速载具更换模块设备驱动
将玻璃管在塑料孔板和金属孔板之间转移
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_quick_carrier_exchange",
    category=["gn_quick_carrier_exchange"],
    description="GN 快速载具更换模块，将玻璃管在塑料/金属孔板间转移",
    icon="quick_carrier_exchange.webp",
)
class QuickCarrierExchangeDevice(OpcUaClientWithSubscription):
    """快速载具更换模块设备类"""

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

    @action(auto_prefix=True, description="将玻璃管从塑料孔板转移到金属孔板")
    def transfer_plastic_to_metal(self) -> dict:
        """玻璃管：塑料孔板 -> 金属孔板"""
        logger.info("[载具更换] 等待空闲，准备转移玻璃管：塑料孔板 -> 金属孔板...")
        while not self.get_node_value("exchange_ready"):
            time.sleep(1.0)

        self.set_node_value("p2m_complete", False)
        self.set_node_value("p2m_start", True)
        logger.info("[载具更换] 玻璃管转移到金属孔板中...")

        while not self.get_node_value("p2m_complete"):
            time.sleep(1.0)

        self.set_node_value("p2m_start", False)
        logger.info("[载具更换] 玻璃管已转入金属孔板")
        return {"message": "玻璃管已从塑料孔板转移至金属孔板"}

    @action(auto_prefix=True, description="将玻璃管从金属孔板转移回塑料孔板")
    def transfer_metal_to_plastic(self) -> dict:
        """玻璃管：金属孔板 -> 塑料孔板"""
        logger.info("[载具更换] 等待空闲，准备转移玻璃管：金属孔板 -> 塑料孔板...")
        while not self.get_node_value("exchange_ready"):
            time.sleep(1.0)

        self.set_node_value("m2p_complete", False)
        self.set_node_value("m2p_start", True)
        logger.info("[载具更换] 玻璃管转移到塑料孔板中...")

        while not self.get_node_value("m2p_complete"):
            time.sleep(1.0)

        self.set_node_value("m2p_start", False)
        logger.info("[载具更换] 玻璃管已转入塑料孔板")
        return {"message": "玻璃管已从金属孔板转移至塑料孔板"}
