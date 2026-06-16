from typing import Callable

from pylabrobot.resources import create_homogeneous_resources, Coordinate, ResourceHolder

from unilabos.resources.itemized_carrier import BottleCarrier
from unilabos.devices.workstation.XUSE.bottles import BallMillJar, LargeCrucible, SmallCrucible, Funnel

# 命名约定：球磨罐-BallMillJar，大坩埚-LargeCrucible，小坩埚-SmallCrucible，漏斗-Funnel
# 载架尺寸适配 XUSE warehouse 单格（约 90 x 55 mm），略小于单格以便放入

_CARRIER_SIZE_X = 88.0
_CARRIER_SIZE_Y = 53.0
_CARRIER_SIZE_Z = 20.0


def _single_carrier(
    name: str,
    content_factory: Callable[[str], "object"],
    item_diameter: float,
    model: str,
) -> BottleCarrier:
    """创建单工位载架（中央放置一个物料），尺寸适配 XUSE warehouse 单格。

    参数:
    - name: 载架名称前缀
    - content_factory: 物料工厂函数（接收 name，返回物料）
    - item_diameter: 物料占位直径（mm），需 < 载架内尺寸
    - model: 载架 model 标识
    """
    # 物料居中放置
    center_x = (_CARRIER_SIZE_X - item_diameter) / 2
    center_y = (_CARRIER_SIZE_Y - item_diameter) / 2
    center_z = 5.0

    carrier = BottleCarrier(
        name=name,
        size_x=_CARRIER_SIZE_X,
        size_y=_CARRIER_SIZE_Y,
        size_z=_CARRIER_SIZE_Z,
        sites=create_homogeneous_resources(
            klass=ResourceHolder,
            locations=[Coordinate(center_x, center_y, center_z)],
            resource_size_x=item_diameter,
            resource_size_y=item_diameter,
            name_prefix=name,
        ),
        model=model,
    )
    carrier.num_items_x = 1
    carrier.num_items_y = 1
    carrier.num_items_z = 1
    carrier[0] = content_factory(f"{name}_1")
    return carrier


def BallMillJar_Carrier(name: str) -> BottleCarrier:
    """球磨罐载架"""
    return _single_carrier(name, BallMillJar, item_diameter=48.0, model="BallMillJar_Carrier")


def LargeCrucible_Carrier(name: str) -> BottleCarrier:
    """大坩埚载架"""
    return _single_carrier(name, LargeCrucible, item_diameter=48.0, model="LargeCrucible_Carrier")


def SmallCrucible_Carrier(name: str) -> BottleCarrier:
    """小坩埚载架"""
    return _single_carrier(name, SmallCrucible, item_diameter=30.0, model="SmallCrucible_Carrier")


def Funnel_Carrier(name: str) -> BottleCarrier:
    """漏斗载架"""
    return _single_carrier(name, Funnel, item_diameter=45.0, model="Funnel_Carrier")
