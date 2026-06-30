"""
真空烘干机设备驱动
对样品进行真空烘干
"""

import time
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_vacuum_oven",
    category=["gn_vacuum_oven"],
    description="GN 真空烘干机，对样品进行真空烘干",
    icon="vacuum_oven.webp",
)
class VacuumOvenDevice(OpcUaClientWithSubscription):
    """真空烘干机设备类"""

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

    @action(auto_prefix=True, description="真空烘干：按指定温度、真空度和时间进行真空烘干")
    def vacuum_dry(
        self,
        temperature_c: float,
        duration_sec: int,
        vacuum_pressure_kpa: float,
    ) -> dict:
        """
        真空烘干
        Args:
            temperature_c: 烘干温度 (°C)
            duration_sec: 烘干时间 (秒)
            vacuum_pressure_kpa: 真空度（kPa，负压绝对值）
        """
        logger.info(
            f"[真空烘干机] 等待空闲，参数：温度{temperature_c}°C, "
            f"时间{duration_sec}s, 真空度{vacuum_pressure_kpa}kPa"
        )
        while not self.get_node_value("vacuum_oven_ready"):
            time.sleep(1.0)

        self.set_node_value("vacuum_dry_complete", False)
        self.set_node_value("vacuum_dry_temp", temperature_c)
        self.set_node_value("vacuum_dry_duration", duration_sec)
        self.set_node_value("vacuum_dry_pressure", vacuum_pressure_kpa)
        self.set_node_value("vacuum_dry_start", True)
        logger.info("[真空烘干机] 真空烘干中...")

        while not self.get_node_value("vacuum_dry_complete"):
            time.sleep(10.0)

        self.set_node_value("vacuum_dry_start", False)
        logger.info("[真空烘干机] 真空烘干完成")
        return {
            "temperature_c": temperature_c,
            "duration_sec": duration_sec,
            "vacuum_pressure_kpa": vacuum_pressure_kpa,
            "message": "真空烘干完成",
        }

    @action(auto_prefix=True, description="打开/关闭真空烘干机舱门")
    def set_door(self, is_open: bool) -> dict:
        """控制真空烘干机门"""
        logger.info(f"[真空烘干机] 门 -> {'打开' if is_open else '关闭'}")
        self.set_node_value("door_action_complete", False)
        self.set_node_value("door_open" if is_open else "door_close", True)
        while not self.get_node_value("door_action_complete"):
            time.sleep(0.5)
        self.set_node_value("door_open" if is_open else "door_close", False)
        return {"message": f"真空烘干机门已{'打开' if is_open else '关闭'}"}

    @action(auto_prefix=True, description="放置于成品位：将干燥后样品放置到成品位置")
    def place_to_finished_position(self) -> dict:
        """放置成品到最终位置"""
        logger.info("[真空烘干机] 放置样品到成品位...")
        self.set_node_value("place_finished_complete", False)
        self.set_node_value("place_finished_start", True)
        while not self.get_node_value("place_finished_complete"):
            time.sleep(1.0)
        self.set_node_value("place_finished_start", False)
        logger.info("[真空烘干机] 样品已放置于成品位")
        return {"message": "样品已放置于成品位"}
