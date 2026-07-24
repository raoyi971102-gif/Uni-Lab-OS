"""
GN 合成工站根节点驱动

作为物料树 2D 布局容器，子模块由各自驱动独立注册。
"""

from unilabos.devices.workstation.workstation_base import WorkstationBase
from unilabos.registry.decorators import device, not_action


@device(
    id="GN_station",
    display_name="GN 合成工站",
    category=["workstation"],
    description="GN 合成工站 2D 布局容器",
    icon="GN_station_bg.png",
)
class GNStationDevice(WorkstationBase):
    """合成工站根节点，用于云端 2D 底图与子设备布局。"""

    def __init__(self, *args, deck=None, **kwargs):
        super().__init__(deck=deck, *args, **kwargs)

    @not_action
    def post_init(self, ros_node):
        super().post_init(ros_node)
