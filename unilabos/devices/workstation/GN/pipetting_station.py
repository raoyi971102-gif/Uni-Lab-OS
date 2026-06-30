"""
移液工作站设备驱动
吸上清并转移至新的96孔板
"""

import time
from typing import List
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_pipetting_station",
    category=["gn_pipetting_station"],
    description="GN 移液工作站，吸取上清并转移到新96孔板",
    icon="pipetting_station.webp",
)
class PipettingStationDevice(OpcUaClientWithSubscription):
    """移液工作站设备类"""

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

    @action(auto_prefix=True, description="吸上清：从源孔板吸取上清液转移至新96孔板")
    def aspirate_supernatant(
        self,
        source_well: str,
        destination_well: str,
        volume_ul: float,
        aspirate_depth_mm: float = 2.0,
    ) -> dict:
        """
        吸上清液
        Args:
            source_well: 源孔位
            destination_well: 目标孔位
            volume_ul: 吸取体积（uL）
            aspirate_depth_mm: 吸液深度（mm，相对液面）
        """
        logger.info(f"[移液工作站] 吸上清：{source_well} -> {destination_well}, {volume_ul}uL")
        while not self.get_node_value("pipette_ready"):
            time.sleep(1.0)

        self.set_node_value("aspirate_complete", False)
        self.set_node_value("aspirate_source", source_well)
        self.set_node_value("aspirate_destination", destination_well)
        self.set_node_value("aspirate_volume", volume_ul)
        self.set_node_value("aspirate_depth", aspirate_depth_mm)
        self.set_node_value("aspirate_start", True)
        logger.info("[移液工作站] 吸上清中...")

        while not self.get_node_value("aspirate_complete"):
            time.sleep(1.0)

        self.set_node_value("aspirate_start", False)
        logger.info("[移液工作站] 吸上清完成")
        return {
            "source_well": source_well,
            "destination_well": destination_well,
            "volume_ul": volume_ul,
            "message": f"上清液已从{source_well}转移至{destination_well}",
        }

    @action(auto_prefix=True, description="批量吸上清：按列批量吸取上清并转移到新板")
    def aspirate_supernatant_batch(
        self,
        source_wells: List[str],
        destination_wells: List[str],
        volume_ul: float,
        aspirate_depth_mm: float = 2.0,
    ) -> dict:
        """批量吸上清"""
        if len(source_wells) != len(destination_wells):
            raise ValueError("源孔位与目标孔位列表长度不一致")

        results = []
        for src, dst in zip(source_wells, destination_wells):
            results.append(self.aspirate_supernatant(src, dst, volume_ul, aspirate_depth_mm))

        return {
            "count": len(source_wells),
            "results": results,
            "message": f"批量吸上清完成，共{len(source_wells)}次",
        }
