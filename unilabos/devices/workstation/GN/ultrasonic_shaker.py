"""
超声振荡仪设备驱动
负责样品的超声混匀和振荡/超声溶解
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_ultrasonic_shaker",
    category=["gn_ultrasonic_shaker"],
    description="GN 超声振荡仪，用于样品超声混匀和溶解",
    icon="ultrasonic_shaker.webp",
)
class UltrasonicShakerDevice(OpcUaClientWithSubscription):
    """超声振荡仪设备类"""

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

    @action(auto_prefix=True, description="超声混匀：以指定功率和时间进行超声混匀")
    def ultrasonic_mix(self, duration_sec: int, power_percent: int = 50) -> dict:
        """
        超声混匀
        Args:
            duration_sec: 超声时间（秒）
            power_percent: 超声功率百分比（1-100）
        """
        if power_percent < 1 or power_percent > 100:
            raise ValueError(f"功率百分比必须在 1-100，当前值: {power_percent}")

        logger.info(f"[超声振荡仪] 等待空闲，准备超声混匀 {duration_sec}秒...")
        while not self.get_node_value("ultrasonic_ready"):
            time.sleep(1.0)

        self.set_node_value("ultrasonic_complete", False)
        self.set_node_value("ultrasonic_duration", duration_sec)
        self.set_node_value("ultrasonic_power", power_percent)
        self.set_node_value("ultrasonic_start", True)
        logger.info(f"[超声振荡仪] 超声混匀进行中...")

        while not self.get_node_value("ultrasonic_complete"):
            time.sleep(2.0)

        self.set_node_value("ultrasonic_start", False)
        logger.info(f"[超声振荡仪] 超声混匀完成")
        return {
            "duration_sec": duration_sec,
            "power_percent": power_percent,
            "message": "超声混匀完成",
        }

    @action(auto_prefix=True, description="振荡/超声溶解：振荡和超声组合溶解物料")
    def shake_and_dissolve(self, duration_sec: int, shake_freq_hz: int = 30, power_percent: int = 50) -> dict:
        """
        振荡/超声溶解
        Args:
            duration_sec: 溶解时间（秒）
            shake_freq_hz: 振荡频率（Hz）
            power_percent: 超声功率百分比（1-100）
        """
        logger.info(f"[超声振荡仪] 等待空闲，准备振荡/超声溶解 {duration_sec}秒...")
        while not self.get_node_value("ultrasonic_ready"):
            time.sleep(1.0)

        self.set_node_value("dissolve_complete", False)
        self.set_node_value("dissolve_duration", duration_sec)
        self.set_node_value("dissolve_freq", shake_freq_hz)
        self.set_node_value("dissolve_power", power_percent)
        self.set_node_value("dissolve_start", True)
        logger.info(f"[超声振荡仪] 振荡/超声溶解中...")

        while not self.get_node_value("dissolve_complete"):
            time.sleep(2.0)

        self.set_node_value("dissolve_start", False)
        logger.info(f"[超声振荡仪] 振荡/超声溶解完成")
        return {
            "duration_sec": duration_sec,
            "shake_freq_hz": shake_freq_hz,
            "power_percent": power_percent,
            "message": "振荡/超声溶解完成",
        }
