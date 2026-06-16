from unilabos.resources.itemized_carrier import Bottle

# XUSE 工站物料定义
# 尺寸按 XUSE warehouse 单格（约 90 x 55 x 25 mm）等比缩小，直径均 < 55mm 以适配单格


def BallMillJar(
    name: str,
    diameter: float = 48.0,
    height: float = 90.0,
    max_volume: float = 150000.0,  # 150mL
    barcode: str = None,
) -> Bottle:
    """球磨罐"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="BallMillJar",
    )


def LargeCrucible(
    name: str,
    diameter: float = 48.0,
    height: float = 70.0,
    max_volume: float = 100000.0,  # 100mL
    barcode: str = None,
) -> Bottle:
    """大坩埚"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="LargeCrucible",
    )


def SmallCrucible(
    name: str,
    diameter: float = 30.0,
    height: float = 45.0,
    max_volume: float = 30000.0,  # 30mL
    barcode: str = None,
) -> Bottle:
    """小坩埚"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="SmallCrucible",
    )


def Funnel(
    name: str,
    diameter: float = 45.0,
    height: float = 60.0,
    max_volume: float = 50000.0,  # 50mL
    barcode: str = None,
) -> Bottle:
    """漏斗"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="Funnel",
    )
