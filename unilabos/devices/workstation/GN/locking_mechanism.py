"""
锁紧机构设备驱动
负责加盖密封（密封板带橡胶垫）和弃掉金属盖
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_locking_mechanism",
    category=["gn_locking_mechanism"],
    description="GN 锁紧机构，进行加盖密封和金属盖弃置",
    icon="locking_mechanism.webp",
)
class LockingMechanismDevice(OpcUaClientWithSubscription):
    """锁紧机构设备类"""

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

    @action(auto_prefix=True, description="加盖密封：使用带橡胶垫的密封板对金属孔板加盖密封")
    def seal_with_cap(self, lock_torque: float = 5.0) -> dict:
        """
        加盖密封
        Args:
            lock_torque: 锁紧扭矩 (N·m)
        """
        logger.info(f"[锁紧机构] 等待空闲，准备加盖密封，扭矩{lock_torque}N·m...")
        while not self.get_node_value("locking_ready"):
            time.sleep(1.0)

        self.set_node_value("seal_complete", False)
        self.set_node_value("seal_torque", lock_torque)
        self.set_node_value("seal_start", True)
        logger.info(f"[锁紧机构] 加盖密封中...")

        while not self.get_node_value("seal_complete"):
            time.sleep(1.0)

        self.set_node_value("seal_start", False)
        logger.info(f"[锁紧机构] 加盖密封完成")
        return {
            "lock_torque": lock_torque,
            "message": "加盖密封完成",
        }

    @action(auto_prefix=True, description="弃掉金属盖：将金属密封盖移除并弃置")
    def remove_metal_cap(self) -> dict:
        """弃掉金属盖，让样品自然降温"""
        logger.info("[锁紧机构] 等待空闲，准备弃置金属盖...")
        while not self.get_node_value("locking_ready"):
            time.sleep(1.0)

        self.set_node_value("remove_complete", False)
        self.set_node_value("remove_start", True)
        logger.info("[锁紧机构] 弃置金属盖中...")

        while not self.get_node_value("remove_complete"):
            time.sleep(1.0)

        self.set_node_value("remove_start", False)
        logger.info("[锁紧机构] 金属盖已弃置")
        return {"message": "金属盖已弃置，开始自然降温"}
