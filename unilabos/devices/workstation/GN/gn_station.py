"""
GN 合成工站根节点驱动

作为 Workstation 初始化子模块 ROS 节点；各 gn_* 驱动保持独立 OPC UA 直连逻辑。
"""

from typing import List, Optional

from unilabos.devices.workstation.GN.gn_opcua_device import GnOpcUaDevice
from unilabos.devices.workstation.workstation_base import WorkstationBase
from unilabos.registry.decorators import device, not_action
from unilabos.utils.log import logger


@device(
    id="GN_station",
    display_name="GN 合成工站",
    category=["workstation"],
    description="GN 合成工站，统一管理子模块与 2D 布局",
    icon="GN_station_bg.png",
)
class GNStationDevice(WorkstationBase):
    """GN 合成工站 Workstation 节点。"""

    def __init__(
        self,
        protocol_type: Optional[List[str]] = None,
        deck=None,
        background_image: str = "GN_station_bg.png",
        **kwargs,
    ):
        super().__init__(deck=deck, **kwargs)
        self.protocol_type = protocol_type or []
        self.background_image = background_image

    @not_action
    def post_init(self, ros_node):
        super().post_init(ros_node)
        plc_entry = ros_node.sub_devices.get("gn_plc")
        if plc_entry is None:
            logger.error("gn_plc 未初始化，GN 工站 OPC UA 子模块不可用")
            return
        plc_driver = plc_entry.driver_instance
        if hasattr(plc_driver, "is_connected") and not plc_driver.is_connected:
            logger.warning("gn_plc ROS 节点已创建，但 OPC UA 尚未连接，子模块将在首次读写时重试连接")
        for device_id, sub in ros_node.sub_devices.items():
            driver = sub.driver_instance
            if isinstance(driver, GnOpcUaDevice) and driver.plc_device_id == "gn_plc":
                driver.bind_plc_driver(plc_driver)
                logger.info(f"子设备 {device_id} 已绑定 gn_plc")
