from typing import Optional

from pylabrobot.resources import Deck, Coordinate, Resource
from unilabos.devices.workstation.XUSE.warehouses import (
    AddPowder_warehouse_1x1x1,
    OpenCan_warehouse_1x1x1,
    AddBead_warehouse_1x1x1,
    ScrapePowder_warehouse_1x1x1,
    MuffleFurnace_1_warehouse_1x1x1,
    MuffleFurnace_2_warehouse_1x1x1,
    MuffleFurnace_3_warehouse_1x1x1,
    MuffleFurnace_4_warehouse_1x1x1,
    MuffleFurnace_5_warehouse_1x1x1,
    MuffleFurnace_6_warehouse_1x1x1,
    LargeCrucibleFeed_warehouse_1x1x1,
    BallMill_warehouse_2x2x1,
    SmallCrucibleDischarge_warehouse_2x2x1,
    LargeCrucibleDischarge_warehouse_1x2x1,
    Sieve_warehouse_1x3x1,
    SmallCrucibleRack_warehouse_4x5,
    FunnelRack_warehouse_4x4,
    BallMillCan_warehouse_4x8,
    SmallCrucibleTransition_warehouse_1x1x1,
    FunnelTransition_warehouse_1x1x1,
)


class XUSE_deck(Deck):
    def __init__(
        self,
        name: str = "XUSE_deck",
        size_x: float = 2400.0,
        size_y: float = 800.0,
        size_z: float = 2670.0,
        origin: Coordinate = Coordinate(0, 35, 0),
        category: str = "deck",
        setup: bool = False,
    ) -> None:
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, origin=origin)
        self.warehouses = {}
        self.warehouse_locations = {}
        if setup:
            self.setup()

    def setup(self) -> None:
        # 添加仓库
        self.warehouses = {
            "加样区": AddPowder_warehouse_1x1x1("加样区"),
            "开盖区": OpenCan_warehouse_1x1x1("开盖区"),
            "加珠区": AddBead_warehouse_1x1x1("加珠区"),
            "刮粉区": ScrapePowder_warehouse_1x1x1("刮粉区"),
            "马弗炉1": MuffleFurnace_1_warehouse_1x1x1("马弗炉1"),
            "马弗炉2": MuffleFurnace_2_warehouse_1x1x1("马弗炉2"),
            "马弗炉3": MuffleFurnace_3_warehouse_1x1x1("马弗炉3"),
            "马弗炉4": MuffleFurnace_4_warehouse_1x1x1("马弗炉4"),
            "马弗炉5": MuffleFurnace_5_warehouse_1x1x1("马弗炉5"),
            "马弗炉6": MuffleFurnace_6_warehouse_1x1x1("马弗炉6"),
            "大坩埚入料": LargeCrucibleFeed_warehouse_1x1x1("大坩埚入料"),
            "球磨区": BallMill_warehouse_2x2x1("球磨区"),
            "小坩埚出料": SmallCrucibleDischarge_warehouse_2x2x1("小坩埚出料"),
            "大坩埚出料": LargeCrucibleDischarge_warehouse_1x2x1("大坩埚出料"),
            "过筛区": Sieve_warehouse_1x3x1("过筛区"),
            # 小坩埚/漏斗 仓库（分组行命名）及各自过渡（暂存）仓库
            # 注意：本 deck 以 resource.name 作为字典键回写，字典键须与仓库名保持一致
            "小坩埚仓库": SmallCrucibleRack_warehouse_4x5("小坩埚仓库"),
            "漏斗仓库": FunnelRack_warehouse_4x4("漏斗仓库"),
            "球磨罐仓库": BallMillCan_warehouse_4x8("球磨罐仓库"),
            "小坩埚暂存": SmallCrucibleTransition_warehouse_1x1x1("小坩埚暂存"),
            "漏斗暂存": FunnelTransition_warehouse_1x1x1("漏斗暂存"),
        }
        # warehouse 的位置（以 加样区=(200,570) 为基准，按图三布局落在 CAD 工站图上；
        # 坐标为按图三估算值，可在前端拖拽微调）
        self.warehouse_locations = {
            # 左侧工艺区
            "开盖区": Coordinate(330.0, 570.0, 0.0),
            "加珠区": Coordinate(200.0, 570.0, 0.0),
            "刮粉区": Coordinate(470.0, 570.0, 0.0),
            "加样区": Coordinate(260.0, 150.0, 0.0),
            # 中部
            "球磨区": Coordinate(670.0, 180.0, 0.0),
            "小坩埚出料": Coordinate(900.0, 220.0, 0.0),
            "大坩埚出料": Coordinate(2350.0, 300.0, 0.0),
            "大坩埚入料": Coordinate(1250.0, 230.0, 0.0),
            # 右侧马弗炉（3 列 × 2 行，对齐灰色炉箱）
            "马弗炉1": Coordinate(1560.0, 545.0, 0.0),
            "马弗炉2": Coordinate(1830.0, 545.0, 0.0),
            "马弗炉3": Coordinate(2100.0, 545.0, 0.0),
            "马弗炉4": Coordinate(1560.0, 150.0, 0.0),
            "马弗炉5": Coordinate(1830.0, 150.0, 0.0),
            "马弗炉6": Coordinate(2100.0, 150.0, 0.0),
            # 最右侧（炉子右侧空位）
            "过筛区": Coordinate(950.0, 470.0, 0.0),
            # 小坩埚/漏斗 仓库及过渡仓库（沿底边铺开，坐标为估算值，可在前端拖拽微调）
            "小坩埚仓库": Coordinate(1050.0, 1000.0, 0.0),
            "漏斗仓库": Coordinate(1050.0, 700.0, 0.0),
            "球磨罐仓库": Coordinate(-700.0, 250.0, 0.0),
            "小坩埚暂存": Coordinate(1250.0, 570.0, 0.0),
            "漏斗暂存": Coordinate(1380, 570.0, 0.0),
        }

        # 用快照遍历：assign_child_resource 会回写 self.warehouses，避免“字典迭代中被修改”
        for warehouse_name, warehouse in list(self.warehouses.items()):
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])

    def assign_child_resource(
        self,
        resource: Resource,
        location: Optional[Coordinate],
        reassign: bool = True,
    ):
        super().assign_child_resource(resource, location, reassign)
        self.warehouses[resource.name] = resource
        self.warehouse_locations[resource.name] = location
