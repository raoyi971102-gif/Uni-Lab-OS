from pylabrobot.resources import Resource

from unilabos.devices.workstation.AI4C.AI4C_warehouse import set_resource_class
from unilabos.registry.decorators import resource
from unilabos.resources.itemized_carrier import Bottle


@resource(id="AI4C_Powder_Cylinder", category=["bottles"], description="AI4C 粉桶")
def AI4C_Powder_Cylinder(
    name: str,
    diameter: float = 50.0,
    height: float = 90.0,
    max_volume: float = 100000.0,
    barcode: str = None,
) -> Bottle:
    """创建 AI4C 粉桶。"""
    bottle = Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="AI4C_Powder_Cylinder",
    )
    set_resource_class(bottle, "AI4C_Powder_Cylinder")
    return bottle


@resource(id="AI4C_Well_Plate", category=["plates"], description="AI4C 孔板占位")
def AI4C_Well_Plate(
    name: str,
    size_x: float = 127.8,
    size_y: float = 85.5,
    size_z: float = 14.5,
) -> Resource:
    """创建 AI4C 流程中机械臂搬运的孔板占位资源。"""
    plate = Resource(
        name=name,
        size_x=size_x,
        size_y=size_y,
        size_z=size_z,
        category="plate",
        model="AI4C_Well_Plate",
    )
    set_resource_class(plate, "AI4C_Well_Plate")
    return plate

