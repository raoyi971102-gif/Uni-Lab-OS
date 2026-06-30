"""
粉末加样仪设备驱动
负责向96孔玻璃管深孔板注入固体物料（3种以内）
"""

import time
from typing import List
from unilabos.utils.log import logger
from unilabos.registry.decorators import device, action
from unilabos.devices.workstation.AI4M.base_opcua_client import OpcUaClientWithSubscription


@device(
    id="gn_powder_dispenser",
    category=["gn_powder_dispenser"],
    description="GN 粉末加样仪，向96孔玻璃管深孔板注入固体物料（3种以内）",
    icon="powder_dispenser.webp",
)
class PowderDispenserDevice(OpcUaClientWithSubscription):
    """粉末加样仪设备类"""

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

    @action(auto_prefix=True, description="注入固体物料：向指定孔位注入指定质量的固体粉末")
    def dispense_powder(self, powder_id: int, well_position: str, target_mass_mg: float) -> dict:
        """
        粉末加样
        Args:
            powder_id: 粉末编号（1-3）
            well_position: 96孔板位置，如A1、B2
            target_mass_mg: 目标质量（mg）
        """
        if powder_id < 1 or powder_id > 3:
            raise ValueError(f"粉末编号必须在 1-3 范围内，当前值: {powder_id}")

        logger.info(f"[粉末加样仪] 等待空闲，准备向{well_position}注入粉末{powder_id}，质量{target_mass_mg}mg...")
        while not self.get_node_value("dispenser_ready"):
            time.sleep(1.0)

        self.set_node_value("dispense_complete", False)
        self.set_node_value("dispense_powder_id", powder_id)
        self.set_node_value("dispense_well_position", well_position)
        self.set_node_value("dispense_target_mass", target_mass_mg)
        self.set_node_value("dispense_start", True)
        logger.info(f"[粉末加样仪] 粉末注入中...")

        while not self.get_node_value("dispense_complete"):
            time.sleep(2.0)

        actual_mass = self.get_node_value("dispense_actual_mass") or target_mass_mg
        self.set_node_value("dispense_start", False)
        logger.info(f"[粉末加样仪] 注入完成，实际质量{actual_mass}mg")
        return {
            "powder_id": powder_id,
            "well_position": well_position,
            "target_mass_mg": target_mass_mg,
            "actual_mass_mg": actual_mass,
            "message": f"粉末{powder_id}注入到{well_position}完成",
        }

    @action(auto_prefix=True, description="批量注入固体物料：按计划向多个孔位注入粉末")
    def dispense_powders_plate(
        self,
        powder_ids: List[int],
        well_positions: List[str],
        target_masses_mg: List[float],
    ) -> dict:
        """
        按板位批量加粉
        Args:
            powder_ids: 粉末编号列表
            well_positions: 对应孔位列表
            target_masses_mg: 对应质量列表
        """
        n = len(powder_ids)
        if not (n == len(well_positions) == len(target_masses_mg)):
            raise ValueError("三个列表长度必须一致")

        results = []
        for pid, pos, mass in zip(powder_ids, well_positions, target_masses_mg):
            results.append(self.dispense_powder(pid, pos, mass))

        return {
            "count": n,
            "results": results,
            "message": f"批量加粉完成，共{n}次",
        }
