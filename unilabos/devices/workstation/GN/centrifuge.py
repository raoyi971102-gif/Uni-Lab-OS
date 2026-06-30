"""
离心机设备驱动
对样品进行离心处理
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_centrifuge",
    category=["gn_centrifuge"],
    description="GN 离心机，对样品进行离心分离",
    icon="centrifuge.webp",
)
class CentrifugeDevice(OpcUaClientWithSubscription):
    """离心机设备类"""

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

    @action(auto_prefix=True, description="离心：按指定转速和时间进行离心")
    def centrifuge(self, speed_rpm: int, duration_sec: int, temperature_c: float = 25.0) -> dict:
        """
        离心
        Args:
            speed_rpm: 离心转速 (rpm)
            duration_sec: 离心时间 (秒)
            temperature_c: 温度 (°C，默认室温)
        """
        logger.info(f"[离心机] 等待空闲，参数：{speed_rpm}rpm, {duration_sec}s, {temperature_c}°C")
        while not self.get_node_value("centrifuge_ready"):
            time.sleep(1.0)

        self.set_node_value("centrifuge_complete", False)
        self.set_node_value("centrifuge_speed", speed_rpm)
        self.set_node_value("centrifuge_duration", duration_sec)
        self.set_node_value("centrifuge_temp", temperature_c)
        self.set_node_value("centrifuge_start", True)
        logger.info("[离心机] 离心中...")

        while not self.get_node_value("centrifuge_complete"):
            time.sleep(5.0)

        self.set_node_value("centrifuge_start", False)
        logger.info("[离心机] 离心完成")
        return {
            "speed_rpm": speed_rpm,
            "duration_sec": duration_sec,
            "temperature_c": temperature_c,
            "message": "离心完成",
        }

    @action(auto_prefix=True, description="打开/关闭离心机舱门")
    def set_door(self, is_open: bool) -> dict:
        """离心机门控制"""
        logger.info(f"[离心机] 门 -> {'打开' if is_open else '关闭'}")
        self.set_node_value("door_action_complete", False)
        self.set_node_value("door_open" if is_open else "door_close", True)
        while not self.get_node_value("door_action_complete"):
            time.sleep(0.5)
        self.set_node_value("door_open" if is_open else "door_close", False)
        return {"message": f"离心机门已{'打开' if is_open else '关闭'}"}
