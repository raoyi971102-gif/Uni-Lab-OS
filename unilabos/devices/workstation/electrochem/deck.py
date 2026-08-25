"""
电化学工作站 Deck 定义
Electrochem Workstation Deck

包含：
- 来宇移液站工作区域
- GreenLab 电反应仪 6 通道反应位
- 多孔板/试剂架存放位
"""

from pylabrobot.resources import Deck, Coordinate

from unilabos.resources.warehouse import warehouse_factory


# ============ Warehouse 工厂函数 ============

def electrochem_rack_slot(name: str):
    """单个多孔架存放位 (1行×1列×1层)，用于放置多孔板/深孔板等"""
    return warehouse_factory(
        name=name,
        num_items_x=1,
        num_items_y=1,
        num_items_z=1,
        dx=5.0,
        dy=5.0,
        dz=0.0,
        item_dx=137.0,
        item_dy=96.0,
        item_dz=0.0,
        resource_size_x=127.0,
        resource_size_y=86.0,
        resource_size_z=25.0,
        category="rack_slot",
    )


def electrochem_reagent_area(name: str, num_x: int = 3, num_y: int = 2):
    """试剂瓶/容器存放区域"""
    return warehouse_factory(
        name=name,
        num_items_x=num_x,
        num_items_y=num_y,
        num_items_z=1,
        dx=5.0,
        dy=5.0,
        dz=0.0,
        item_dx=140.0,
        item_dy=100.0,
        item_dz=0.0,
        resource_size_x=127.0,
        resource_size_y=86.0,
        resource_size_z=40.0,
        category="reagent_area",
        layout="row-major",
    )


# ============ Deck 类定义 ============

class ElectrochemDeck(Deck):
    """电化学工作站 Deck

    布局说明（示例，坐标单位 mm）:
    ┌─────────────────────────────────────────────────┐
    │  (0,800)                                        │
    │   ┌──────────┐  ┌──────────┐  ┌──────────┐     │
    │   │ 多孔架1  │  │ 多孔架2  │  │ 多孔架3  │     │
    │   └──────────┘  └──────────┘  └──────────┘     │
    │                                                 │
    │  (0,400)                                        │
    │   ┌─────────────────────────┐  ┌────────────┐  │
    │   │   来宇移液站工作区域     │  │  试剂区域   │  │
    │   └─────────────────────────┘  └────────────┘  │
    │                                                 │
    │  (0,0)                                          │
    │   ┌─────────────────────────────────────────┐  │
    │   │      GreenLab 电反应仪 (6通道)           │  │
    │   └─────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────┘

    修改 Deck 教程见 DECK_GUIDE.md
    """

    def __init__(
        self,
        name: str = "electrochem_deck",
        size_x: float = 1500.0,
        size_y: float = 1200.0,
        size_z: float = 200.0,
        category: str = "deck",
        setup: bool = False,
    ) -> None:
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z)
        if setup:
            self.setup()

    def setup(self) -> None:
        """配置 Deck 布局

        在此方法中定义所有 warehouse（存放区域）及其坐标位置。
        如需修改布局，请参考 DECK_GUIDE.md。
        """

        # ---- warehouses 定义 ----
        self.warehouses = {
            # === 多孔架区域 (上方) ===
            # 可根据实际需要增减多孔架数量，修改 num_items_x/y 以改变容量
            "多孔架1": electrochem_rack_slot("多孔架1"),
            "多孔架2": electrochem_rack_slot("多孔架2"),
            "多孔架3": electrochem_rack_slot("多孔架3"),
            # TODO: 如需更多多孔架，在此处添加，例如:
            # "多孔架4": electrochem_rack_slot("多孔架4"),

            # === 试剂存放区域 (中右) ===
            "试剂区域": electrochem_reagent_area("试剂区域", num_x=2, num_y=2),
        }

        # ---- 各 warehouse 在 Deck 上的坐标 (mm) ----
        # Coordinate(x, y, z) - x 为左右方向，y 为前后方向，z 为高度
        self.warehouse_locations = {
            "多孔架1": Coordinate(50.0, 850.0, 0.0),
            "多孔架2": Coordinate(300.0, 850.0, 0.0),
            "多孔架3": Coordinate(550.0, 850.0, 0.0),
            # "多孔架4": Coordinate(800.0, 850.0, 0.0),

            "试剂区域": Coordinate(950.0, 450.0, 0.0),
        }

        # ---- 将 warehouse 分配到 Deck ----
        for wh_name, wh in self.warehouses.items():
            self.assign_child_resource(wh, location=self.warehouse_locations[wh_name])
