"""
磁力搅拌器设备驱动
对样品进行磁力搅拌
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_magnetic_stirrer",
    category=["gn_magnetic_stirrer"],
    description="GN 磁力搅拌器，对样品进行加热搅拌得到溶液A",
    icon="magnetic_stirrer.webp",
)
class MagneticStirrerDevice(OpcUaClientWithSubscription):
    """磁力搅拌器设备类"""

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

    @action(auto_prefix=True, description="磁力搅拌：按指定转速、温度和时间搅拌")
    def stir(self, stir_speed_rpm: int, heat_temp_c: float, duration_sec: int) -> dict:
        """
        磁力搅拌
        Args:
            stir_speed_rpm: 搅拌转速 (rpm)
            heat_temp_c: 加热温度 (°C)
            duration_sec: 搅拌时间 (秒)
        """
        logger.info(f"[磁力搅拌器] 等待空闲，参数：{stir_speed_rpm}rpm, {heat_temp_c}°C, {duration_sec}s")
        while not self.get_node_value("stirrer_ready"):
            time.sleep(1.0)

        self.set_node_value("stir_complete", False)
        self.set_node_value("stir_speed", stir_speed_rpm)
        self.set_node_value("stir_temp", heat_temp_c)
        self.set_node_value("stir_duration", duration_sec)
        self.set_node_value("stir_start", True)
        logger.info(f"[磁力搅拌器] 搅拌中...")

        while not self.get_node_value("stir_complete"):
            time.sleep(5.0)

        self.set_node_value("stir_start", False)
        logger.info(f"[磁力搅拌器] 搅拌完成")
        return {
            "stir_speed_rpm": stir_speed_rpm,
            "heat_temp_c": heat_temp_c,
            "duration_sec": duration_sec,
            "message": "磁力搅拌完成，已得到溶液A",
        }

    @action(auto_prefix=True, description="停止搅拌：立即停止当前的搅拌动作")
    def stop_stir(self) -> dict:
        """停止搅拌"""
        logger.info("[磁力搅拌器] 停止搅拌...")
        self.set_node_value("stir_start", False)
        self.set_node_value("stir_stop", True)
        time.sleep(1.0)
        self.set_node_value("stir_stop", False)
        return {"message": "搅拌已停止"}
