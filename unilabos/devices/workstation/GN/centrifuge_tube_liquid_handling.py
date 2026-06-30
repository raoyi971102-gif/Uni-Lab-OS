"""
离心管液体处理设备驱动
负责向离心管中添加多种溶剂
"""

import time
from typing import List
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_centrifuge_tube_liquid_handling",
    category=["gn_centrifuge_tube_liquid_handling"],
    description="GN 离心管液体处理设备，支持多种溶剂添加",
    icon="liquid_handling.webp",
)
class CentrifugeTubeLiquidHandlingDevice(OpcUaClientWithSubscription):
    """离心管液体处理设备类"""

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

    @action(auto_prefix=True, description="加溶剂：单种溶剂按指定体积加入离心管")
    def add_solvent(self, solvent_id: int, volume_ml: float) -> dict:
        """
        加溶剂动作
        Args:
            solvent_id: 溶剂编号
            volume_ml: 体积（mL）
        """
        logger.info(f"[液体处理] 等待空闲，准备添加溶剂{solvent_id}，体积{volume_ml}mL...")
        while not self.get_node_value("liquid_ready"):
            time.sleep(1.0)

        self.set_node_value("liquid_complete", False)
        self.set_node_value("solvent_id", solvent_id)
        self.set_node_value("solvent_volume", volume_ml)
        self.set_node_value("liquid_start", True)
        logger.info(f"[液体处理] 加溶剂{solvent_id}中...")

        while not self.get_node_value("liquid_complete"):
            time.sleep(1.0)

        self.set_node_value("liquid_start", False)
        logger.info(f"[液体处理] 加溶剂{solvent_id}完成")
        return {
            "solvent_id": solvent_id,
            "volume_ml": volume_ml,
            "message": f"溶剂{solvent_id}添加完成",
        }

    @action(auto_prefix=True, description="单通道10mL转移至储液槽：将溶液转移到储液槽供分液使用")
    def transfer_to_reservoir(self, source_id: int, reservoir_id: int, volume_ml: float = 10.0) -> dict:
        """
        单通道10mL转移至储液槽
        Args:
            source_id: 源容器编号
            reservoir_id: 目标储液槽编号
            volume_ml: 转移体积，默认10mL
        """
        logger.info(f"[液体处理] 单通道转移 {volume_ml}mL：{source_id} -> 储液槽{reservoir_id}")
        while not self.get_node_value("liquid_ready"):
            time.sleep(1.0)

        self.set_node_value("transfer_complete", False)
        self.set_node_value("transfer_source_id", source_id)
        self.set_node_value("transfer_reservoir_id", reservoir_id)
        self.set_node_value("transfer_volume", volume_ml)
        self.set_node_value("transfer_start", True)

        while not self.get_node_value("transfer_complete"):
            time.sleep(1.0)

        self.set_node_value("transfer_start", False)
        logger.info(f"[液体处理] 转移完成")
        return {
            "source_id": source_id,
            "reservoir_id": reservoir_id,
            "volume_ml": volume_ml,
            "message": f"已转移{volume_ml}mL到储液槽{reservoir_id}",
        }

    @action(auto_prefix=True, description="批量加溶剂：依次添加多种溶剂")
    def add_solvents_batch(self, solvent_ids: List[int], volumes_ml: List[float]) -> dict:
        """批量加多种溶剂"""
        if len(solvent_ids) != len(volumes_ml):
            raise ValueError("溶剂编号列表与体积列表长度不一致")

        results = []
        for sid, vol in zip(solvent_ids, volumes_ml):
            results.append(self.add_solvent(sid, vol))

        return {
            "solvent_count": len(solvent_ids),
            "results": results,
            "message": f"批量加溶剂完成，共{len(solvent_ids)}种",
        }
