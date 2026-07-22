"""
GN 合成工站根节点驱动

作为物料树 2D 布局容器，子模块（烘箱、移液站等）由各自驱动独立注册。
底图文件名与 @device icon 及 graph 中 icon 字段保持一致。
"""

from unilabos.registry.decorators import device, not_action


@device(
    id="GN_station",
    display_name="GN 合成工站",
    category=["workstation"],
    description="GN 合成工站 2D 布局容器",
    icon="GN_station_bg.png",
)
class GNStationDevice:
    """合成工站根节点，用于云端 2D 底图与子设备布局。"""

    def __init__(self, *args, **kwargs):
        pass

    @not_action
    def post_init(self, ros_node):
        pass
