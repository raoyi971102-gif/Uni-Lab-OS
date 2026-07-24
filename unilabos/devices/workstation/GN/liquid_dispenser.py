"""
加液模块设备驱动
负责单通道和8通道分液操作
"""

import time
from typing import List
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_liquid_dispenser",
    category=["gn_liquid_dispenser"],
    description="GN 加液模块，支持单通道10mL转移和8通道分液（5-6种溶剂）",
    icon="liquid_dispenser.webp",
)
class LiquidDispenserDevice(OpcUaClientWithSubscription):
    """加液模块设备类"""

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

    @action(auto_prefix=True, description="单通道加液：从储液槽吸取10mL转移到指定位置")
    def dispense_single_channel(self, reservoir_id: int, target_well: str, volume_ml: float = 10.0) -> dict:
        """
        单通道加液
        Args:
            reservoir_id: 储液槽编号
            target_well: 目标孔位
            volume_ml: 体积（默认10mL）
        """
        logger.info(f"[加液模块] 单通道加液 {volume_ml}mL：储液槽{reservoir_id} -> {target_well}")
        while not self.get_node_value("dispenser_ready"):
            time.sleep(1.0)

        self.set_node_value("single_complete", False)
        self.set_node_value("single_reservoir_id", reservoir_id)
        self.set_node_value("single_target_well", target_well)
        self.set_node_value("single_volume", volume_ml)
        self.set_node_value("single_start", True)

        while not self.get_node_value("single_complete"):
            time.sleep(1.0)

        self.set_node_value("single_start", False)
        logger.info(f"[加液模块] 单通道加液完成")
        return {
            "reservoir_id": reservoir_id,
            "target_well": target_well,
            "volume_ml": volume_ml,
            "message": f"单通道加液完成",
        }

    @action(auto_prefix=True, description="8通道分液：使用8通道一次性分液到一列孔位")
    def dispense_8channel(self, reservoir_id: int, target_column: int, volume_ul: float) -> dict:
        """
        8通道分液
        Args:
            reservoir_id: 储液槽编号
            target_column: 96孔板目标列（1-12）
            volume_ul: 每孔体积（uL）
        """
        if target_column < 1 or target_column > 12:
            raise ValueError(f"目标列必须在 1-12，当前值: {target_column}")

        logger.info(f"[加液模块] 8通道分液：储液槽{reservoir_id} -> 第{target_column}列，{volume_ul}uL/孔")
        while not self.get_node_value("dispenser_ready"):
            time.sleep(1.0)

        self.set_node_value("multi_complete", False)
        self.set_node_value("multi_reservoir_id", reservoir_id)
        self.set_node_value("multi_target_column", target_column)
        self.set_node_value("multi_volume", volume_ul)
        self.set_node_value("multi_start", True)

        while not self.get_node_value("multi_complete"):
            time.sleep(1.0)

        self.set_node_value("multi_start", False)
        logger.info(f"[加液模块] 8通道分液完成")
        return {
            "reservoir_id": reservoir_id,
            "target_column": target_column,
            "volume_ul": volume_ul,
            "message": f"8通道分液到第{target_column}列完成",
        }

    @action(auto_prefix=True, description="错位8通道分液：以错位方式8通道分液（用于96孔吸上清等场景）")
    def dispense_8channel_offset(
        self,
        reservoir_id: int,
        target_column: int,
        volume_ul: float,
        row_offset: int = 0,
    ) -> dict:
        """
        错位8通道分液（用于补加DMF溶液等场景）
        Args:
            reservoir_id: 储液槽编号
            target_column: 目标列
            volume_ul: 每孔体积
            row_offset: 行偏移（用于错位）
        """
        logger.info(f"[加液模块] 错位8通道：储液槽{reservoir_id} -> 第{target_column}列，偏移{row_offset}行")
        while not self.get_node_value("dispenser_ready"):
            time.sleep(1.0)

        self.set_node_value("offset_complete", False)
        self.set_node_value("offset_reservoir_id", reservoir_id)
        self.set_node_value("offset_target_column", target_column)
        self.set_node_value("offset_volume", volume_ul)
        self.set_node_value("offset_row", row_offset)
        self.set_node_value("offset_start", True)

        while not self.get_node_value("offset_complete"):
            time.sleep(1.0)

        self.set_node_value("offset_start", False)
        logger.info(f"[加液模块] 错位8通道分液完成")
        return {
            "reservoir_id": reservoir_id,
            "target_column": target_column,
            "volume_ul": volume_ul,
            "row_offset": row_offset,
            "message": "错位8通道分液完成",
        }
