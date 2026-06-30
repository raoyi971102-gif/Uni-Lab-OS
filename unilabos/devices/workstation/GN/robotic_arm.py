"""
机械臂设备驱动
负责不同工位之间的物料转移
"""

import time
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import (
    device,
    action,
    ActionInputHandle,
    ActionOutputHandle,
    DataSource,
)
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_robotic_arm",
    category=["gn_robotic_arm"],
    description="GN 机械臂，负责不同工位间的物料转移",
    icon="robotic_arm.webp",
)
class RoboticArmDevice(OpcUaClientWithSubscription):
    """机械臂设备类"""

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

    @action(
        auto_prefix=True,
        description="机械臂转移物料：从源工位转移到目标工位",
        handles=[
            ActionInputHandle(
                key="from_station",
                data_type="gn_station",
                label="源工位编号",
                data_key="from_station_id",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="to_station",
                data_type="gn_station",
                label="目标工位编号",
                data_key="to_station_id",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def transfer_material(self, from_station_id: int, to_station_id: int) -> dict:
        """
        机械臂从源工位转移物料到目标工位
        - 等待机械臂空闲
        - 下发源工位和目标工位编号
        - 等待转移完成
        """
        logger.info(f"[机械臂] 等待空闲，准备从工位{from_station_id}转移到工位{to_station_id}...")
        while not self.get_node_value("arm_ready"):
            time.sleep(1.0)

        self.set_node_value("arm_transfer_complete", False)
        self.set_node_value("arm_from_station", from_station_id)
        self.set_node_value("arm_to_station", to_station_id)
        self.set_node_value("arm_transfer_start", True)
        logger.info(f"[机械臂] 已下发转移指令：{from_station_id} -> {to_station_id}")

        while not self.get_node_value("arm_transfer_complete"):
            logger.info("[机械臂] 转移中...")
            time.sleep(1.0)

        self.set_node_value("arm_transfer_start", False)
        logger.info(f"[机械臂] 转移完成：{from_station_id} -> {to_station_id}")

        return {
            "from_station_id": from_station_id,
            "to_station_id": to_station_id,
            "message": f"机械臂完成从工位{from_station_id}到工位{to_station_id}的物料转移",
        }

    @action(auto_prefix=True, description="机械臂回到原点")
    def home(self) -> dict:
        """机械臂回到原点位置"""
        logger.info("[机械臂] 回原点中...")
        self.set_node_value("arm_home_complete", False)
        self.set_node_value("arm_home_trigger", True)
        while not self.get_node_value("arm_home_complete"):
            time.sleep(1.0)
        self.set_node_value("arm_home_trigger", False)
        logger.info("[机械臂] 已回到原点")
        return {"message": "机械臂回原点完成"}
