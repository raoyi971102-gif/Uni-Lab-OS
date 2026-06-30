"""
固体称量模块设备驱动
负责13-20种固体物料的精准称量加粉
"""

import time
from typing import List
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_solid_weighing",
    category=["gn_solid_weighing"],
    description="GN 固体称量模块，支持13-20种固体物料的精准称量",
    icon="solid_weighing.webp",
)
class SolidWeighingDevice(OpcUaClientWithSubscription):
    """固体称量模块设备类"""

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

    @action(auto_prefix=True, description="加粉：按指定的物料编号和质量加入固体粉末")
    def add_powder(self, powder_id: int, target_mass_mg: float) -> dict:
        """
        加粉动作
        Args:
            powder_id: 粉末物料编号（1-20）
            target_mass_mg: 目标质量（mg）
        """
        if powder_id < 1 or powder_id > 20:
            raise ValueError(f"粉末编号必须在 1-20 范围内，当前值: {powder_id}")

        logger.info(f"[固体称量] 等待空闲，准备添加粉末{powder_id}，目标质量{target_mass_mg}mg...")
        while not self.get_node_value("weighing_ready"):
            time.sleep(1.0)

        self.set_node_value("weighing_complete", False)
        self.set_node_value("powder_id", powder_id)
        self.set_node_value("target_mass", target_mass_mg)
        self.set_node_value("weighing_start", True)
        logger.info(f"[固体称量] 开始加粉{powder_id}...")

        while not self.get_node_value("weighing_complete"):
            logger.info(f"[固体称量] 加粉{powder_id}中...")
            time.sleep(2.0)

        actual_mass = self.get_node_value("actual_mass") or target_mass_mg
        self.set_node_value("weighing_start", False)
        logger.info(f"[固体称量] 加粉{powder_id}完成，实际质量{actual_mass}mg")
        return {
            "powder_id": powder_id,
            "target_mass_mg": target_mass_mg,
            "actual_mass_mg": actual_mass,
            "message": f"粉末{powder_id}称量完成",
        }

    @action(auto_prefix=True, description="批量加粉：依次添加多种粉末")
    def add_powders_batch(self, powder_ids: List[int], target_masses_mg: List[float]) -> dict:
        """
        批量加粉
        Args:
            powder_ids: 粉末编号列表（13-20种）
            target_masses_mg: 对应的目标质量列表
        """
        if len(powder_ids) != len(target_masses_mg):
            raise ValueError("粉末编号列表与质量列表长度不一致")

        results = []
        for pid, mass in zip(powder_ids, target_masses_mg):
            results.append(self.add_powder(pid, mass))

        return {
            "powder_count": len(powder_ids),
            "results": results,
            "message": f"批量加粉完成，共{len(powder_ids)}种粉末",
        }
