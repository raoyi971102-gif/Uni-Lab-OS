from unilabos.devices.workstation.XUSE.XUSE_warehouse import WareHouse, warehouse_factory


# =================== XUSE 工站堆栈 ===================
# 统一的位间距（位置可在 decks.py 中调整）。
# 多位堆栈统一采用连续数字编号（1, 2, 3 ...），与 XUSE 动作中的位置编号一致。

_DX = 10.0
_DY = 10.0
_DZ = 10.0
_ITEM_DX = 100.0
_ITEM_DY = 65.0
_ITEM_DZ = 120.0


def _xuse_warehouse(
    name: str,
    num_items_x: int,
    num_items_y: int,
    custom_keys=None,
    naming_mode: str = "continuous_number",
    group_size: int = 2,
    group_prefixes=None,
    group_axis: str = "row",
) -> WareHouse:
    """创建 XUSE 工站堆栈（z 单层）。

    naming_mode：
    - "continuous_number"：连续数字 1,2,3...（默认）
    - "paired_group"：分组前缀+连续编号（如 1-1,1-2.../2-1...），需配合 group_prefixes；
      group_axis="row" 表示按行分组（前两行/后两行），"col" 表示按列分组
    """
    return warehouse_factory(
        name=name,
        num_items_x=num_items_x,
        num_items_y=num_items_y,
        num_items_z=1,
        dx=_DX,
        dy=_DY,
        dz=_DZ,
        item_dx=_ITEM_DX,
        item_dy=_ITEM_DY,
        item_dz=_ITEM_DZ,
        category="warehouse",
        naming_mode=naming_mode,
        custom_keys=custom_keys,
        group_size=group_size,
        group_prefixes=group_prefixes,
        group_axis=group_axis,
    )


# =================== 1x1 工站 ===================

def AddPowder_warehouse_1x1x1(name: str) -> WareHouse:
    """加样区 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def OpenCan_warehouse_1x1x1(name: str) -> WareHouse:
    """开盖区 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def AddBead_warehouse_1x1x1(name: str) -> WareHouse:
    """加珠区 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def ScrapePowder_warehouse_1x1x1(name: str) -> WareHouse:
    """刮粉区 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def MuffleFurnace_1_warehouse_1x1x1(name: str) -> WareHouse:
    """马弗炉1 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def MuffleFurnace_2_warehouse_1x1x1(name: str) -> WareHouse:
    """马弗炉2 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def MuffleFurnace_3_warehouse_1x1x1(name: str) -> WareHouse:
    """马弗炉3 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def MuffleFurnace_4_warehouse_1x1x1(name: str) -> WareHouse:
    """马弗炉4 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def MuffleFurnace_5_warehouse_1x1x1(name: str) -> WareHouse:
    """马弗炉5 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def MuffleFurnace_6_warehouse_1x1x1(name: str) -> WareHouse:
    """马弗炉6 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def LargeCrucibleFeed_warehouse_1x1x1(name: str) -> WareHouse:
    """大坩埚入料 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


# =================== 多位工站 ===================

def BallMill_warehouse_2x2x1(name: str) -> WareHouse:
    """球磨区 2x2x1 堆栈"""
    return _xuse_warehouse(name, 2, 2)


def SmallCrucibleDischarge_warehouse_2x2x1(name: str) -> WareHouse:
    """小坩埚出料 2x2x1 堆栈"""
    return _xuse_warehouse(name, 2, 2)


def LargeCrucibleDischarge_warehouse_1x2x1(name: str) -> WareHouse:
    """大坩埚出料 1x2x1 堆栈"""
    return _xuse_warehouse(name, 1, 2)


def Sieve_warehouse_1x3x1(name: str) -> WareHouse:
    """过筛区 1x3x1 堆栈"""
    return _xuse_warehouse(name, 1, 3)


# =================== 小坩埚/漏斗 仓库（分组列命名） ===================

def SmallCrucibleRack_warehouse_4x5(name: str) -> WareHouse:
    """小坩埚仓库 4行×5列（共20位）。

    分组行命名：前两行 1-1~1-10，后两行 2-1~2-10。
    """
    return _xuse_warehouse(
        name,
        num_items_x=5,
        num_items_y=4,
        naming_mode="paired_group",
        group_size=2,
        group_prefixes=["1", "2"],
        group_axis="row",
    )


def FunnelRack_warehouse_4x4(name: str) -> WareHouse:
    """漏斗仓库 4行×4列（共16位）。

    分组行命名：前两行 C-1~C-8，后两行 D-1~D-8。
    """
    return _xuse_warehouse(
        name,
        num_items_x=4,
        num_items_y=4,
        naming_mode="paired_group",
        group_size=2,
        group_prefixes=["C", "D"],
        group_axis="row",
    )


def BallMillCan_warehouse_4x8(name: str) -> WareHouse:
    """球磨罐仓库 4行×8列（共32位）。

    分组行命名（每行一组）：第1行 1-1~1-8，第2行 2-1~2-8，第3行 3-1~3-8，第4行 4-1~4-8。
    """
    return _xuse_warehouse(
        name,
        num_items_x=8,
        num_items_y=4,
        naming_mode="paired_group",
        group_size=1,
        group_prefixes=["1", "2", "3", "4"],
        group_axis="row",
    )


def SmallCrucibleTransition_warehouse_1x1x1(name: str) -> WareHouse:
    """小坩埚过渡仓库 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])


def FunnelTransition_warehouse_1x1x1(name: str) -> WareHouse:
    """漏斗过渡仓库 1x1x1 堆栈"""
    return _xuse_warehouse(name, 1, 1, custom_keys=[name])
