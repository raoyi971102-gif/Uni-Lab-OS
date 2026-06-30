"""
常规烘箱设备驱动
对样品进行常规烘干
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_standard_oven",
    category=["gn_standard_oven"],
    description="GN 常规烘箱，对样品进行加热烘干",
    icon="oven.webp",
)
class StandardOvenDevice(OpcUaClientWithSubscription):
    """常规烘箱设备类"""

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

    @action(auto_prefix=True, description="烘干：按指定温度和时间烘干样品")
    def dry(self, temperature_c: float, duration_sec: int) -> dict:
        """
        烘干
        Args:
            temperature_c: 烘干温度 (°C)
            duration_sec: 烘干时间 (秒)
        """
        logger.info(f"[常规烘箱] 等待空闲，准备烘干：温度{temperature_c}°C, 时间{duration_sec}s")
        while not self.get_node_value("oven_ready"):
            time.sleep(1.0)

        self.set_node_value("dry_complete", False)
        self.set_node_value("dry_temp", temperature_c)
        self.set_node_value("dry_duration", duration_sec)
        self.set_node_value("dry_start", True)
        logger.info("[常规烘箱] 烘干中...")

        while not self.get_node_value("dry_complete"):
            time.sleep(10.0)

        self.set_node_value("dry_start", False)
        logger.info(f"[常规烘箱] 烘干完成")
        return {
            "temperature_c": temperature_c,
            "duration_sec": duration_sec,
            "message": "烘干完成",
        }

    @action(auto_prefix=True, description="打开/关闭烘箱舱门")
    def set_door(self, is_open: bool) -> dict:
        """控制烘箱门"""
        logger.info(f"[常规烘箱] 门 -> {'打开' if is_open else '关闭'}")
        self.set_node_value("door_action_complete", False)
        self.set_node_value("door_open" if is_open else "door_close", True)
        while not self.get_node_value("door_action_complete"):
            time.sleep(0.5)
        self.set_node_value("door_open" if is_open else "door_close", False)
        return {"message": f"烘箱门已{'打开' if is_open else '关闭'}"}
