from typing import Dict, Optional, List, Union
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import ResourceHolder, create_homogeneous_resources

from unilabos.resources.itemized_carrier import ItemizedCarrier, ResourcePLR


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def warehouse_factory(
    name: str,
    num_items_x: int = 1,
    num_items_y: int = 4,
    num_items_z: int = 4,
    dx: float = 100.0,
    dy: float = 65.0,
    dz: float = 120.0,
    item_dx: float = 10.0,
    item_dy: float = 10.0,
    item_dz: float = 10.0,
    resource_size_x: float = 90.0,
    resource_size_y: float = 55.0,
    resource_size_z: float = 25.0,
    removed_positions: Optional[List[int]] = None,
    empty: bool = False,
    category: str = "warehouse",
    model: Optional[str] = None,
    col_offset: int = 0,  # 列起始偏移量，用于生成5-8等命名
    layout: str = "col-major",  # 新增：排序方式，"col-major"=列优先，"row-major"=行优先
    custom_keys: Optional[List[Union[str, int]]] = None,  # 自定义编号列表
    naming_mode: str = "letter_number",  # 排号模式："letter_number"=字母+数字(A1,A2,B1...)，"continuous_number"=连续数字(1,2,3...)，"paired_group"=分组列前缀+连续编号(1-1,1-2.../2-1...)
    reverse_col_order: bool = False,  # 列序号是否从右往左：False=从左到右123，True=从右往左123
    group_size: int = 2,  # paired_group 模式下每组包含的行数/列数
    group_prefixes: Optional[List[str]] = None,  # paired_group 模式下每组的前缀，如 ["1","2"] 或 ["C","D"]
    group_axis: str = "row",  # paired_group 模式分组轴："row"=按行分组(前两行/后两行)，"col"=按列分组
):
    # 创建位置坐标
    locations = []

    for layer in range(num_items_z):  # 层
        for row in range(num_items_y):  # 行
            for col in range(num_items_x):  # 列
                # 计算位置
                x = dx + col * item_dx

                # 根据 layout 决定 y 坐标计算
                if layout == "row-major":
                    # 行优先：row=0(第1行) 应该显示在上方，y 值最小
                    y = dy + row * item_dy
                else:
                    # 列优先：保持原逻辑
                    y = dy + (num_items_y - row - 1) * item_dy

                z = dz + (num_items_z - layer - 1) * item_dz
                locations.append(Coordinate(x, y, z))
    
    if removed_positions:
        locations = [loc for i, loc in enumerate(locations) if i not in removed_positions]
    
    _sites = create_homogeneous_resources(
        klass=ResourceHolder,
        locations=locations,
        resource_size_x=resource_size_x,
        resource_size_y=resource_size_y,
        resource_size_z=resource_size_z,
        name_prefix=name,
    )
    
    len_x, len_y = (num_items_x, num_items_y) if num_items_z == 1 else (num_items_y, num_items_z) if num_items_x == 1 else (num_items_x, num_items_z)

    # 🔑 修改：支持两种排号模式
    # 命名顺序必须与坐标生成顺序一致：层 → 行 → 列
    if custom_keys:
        # 使用自定义键名
        keys = [str(k) for k in custom_keys]
        if len(keys) != len(_sites):
            raise ValueError(f"自定义键名数量({len(keys)})与位置数量({len(_sites)})不匹配")
    elif naming_mode == "continuous_number":
        # 模式2：连续数字模式 - 换行不重新排序，连续递增
        # 例如：1, 2, 3, 4, 5, 6, 7, 8, 9, 10...
        keys = []
        counter = 1 + col_offset  # 支持偏移量，如从5开始
        for layer in range(num_items_z):  # 遍历每一层
            for row in range(num_items_y):  # 遍历每一行
                row_keys = []
                for col in range(num_items_x):  # 遍历每一列
                    row_keys.append(str(counter))
                    counter += 1
                if reverse_col_order:
                    row_keys = list(reversed(row_keys))  # 从右往左：右=1, 中=2, 左=3
                keys.extend(row_keys)
    elif naming_mode == "paired_group":
        # 模式3：分组命名 - 每 group_size 行（或列）为一组，组内用 "前缀-编号" 连续编号。
        # 编号顺序沿用工厂的遍历顺序（层 → 行 → 列），组内按行优先递增。
        # 例：group_axis="row", group_size=2, group_prefixes=["1","2"]，4行×5列 →
        #     前两行(第1组): 1-1..1-5(第1行) → 1-6..1-10(第2行)
        #     后两行(第2组): 2-1..2-5(第3行) → 2-6..2-10(第4行)
        if not group_prefixes:
            raise ValueError("paired_group 模式需要提供 group_prefixes（每组前缀列表）")
        axis_len = num_items_y if group_axis == "row" else num_items_x
        keys = []
        group_counters: Dict[int, int] = {}
        for layer in range(num_items_z):  # 遍历每一层
            for row in range(num_items_y):  # 遍历每一行
                for col in range(num_items_x):  # 遍历每一列
                    group_idx = (row if group_axis == "row" else col) // group_size  # 第几组（0,1,...）
                    if group_idx >= len(group_prefixes):
                        raise ValueError(
                            f"{group_axis}数({axis_len})与 group_size({group_size})/group_prefixes({group_prefixes})不匹配"
                        )
                    group_counters[group_idx] = group_counters.get(group_idx, 0) + 1
                    keys.append(f"{group_prefixes[group_idx]}-{group_counters[group_idx]}")
    else:
        # 模式1：字母+数字模式（默认）- 每行一个字母
        # 例如：A1, A2, A3, B1, B2, B3...
        keys = []
        for layer in range(num_items_z):  # 遍历每一层
            for row in range(num_items_y):  # 遍历每一行
                # 每一行对应一个字母：A, B, C, D...
                # row=0(第1行)→A, row=1(第2行)→B, row=2(第3行)→C
                reversed_row = (num_items_y - 1 - row)  # 调整为从上到下：row=0→reversed_row=2, row=1→reversed_row=1
                global_row = layer * num_items_y + reversed_row
                letter = LETTERS[global_row]
                
                for col in range(num_items_x):  # 遍历每一列
                    # 从左到右编号：1, 2, 3, 4, 5...
                    number = col + 1 + col_offset  # 支持列偏移
                    key = f"{letter}{number}"
                    keys.append(key)

    sites = {i: site for i, site in zip(keys, _sites.values())}

    return WareHouse(
        name=name,
        size_x=dx + item_dx * num_items_x,
        size_y=dy + item_dy * num_items_y,
        size_z=dz + item_dz * num_items_z,
        num_items_x = num_items_x,
        num_items_y = num_items_y,
        num_items_z = num_items_z,
        ordering_layout=layout,  # 传递排序方式到 ordering_layout
        sites=sites,
        category=category,
        model=model,
    )


class WareHouse(ItemizedCarrier):
    """堆栈载体类 - 可容纳16个板位的载体（4层x4行x1列）"""
    def __init__(
        self,
        name: str,
        size_x: float,
        size_y: float,
        size_z: float,
        num_items_x: int,
        num_items_y: int,
        num_items_z: int,
        layout: str = "x-y",
        sites: Optional[Dict[Union[int, str], Optional[ResourcePLR]]] = None,
        category: str = "warehouse",
        model: Optional[str] = None,
        ordering_layout: str = "col-major",
        **kwargs
    ):
        super().__init__(
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            # ordered_items=ordered_items,
            # ordering=ordering,
            num_items_x=num_items_x,
            num_items_y=num_items_y,
            num_items_z=num_items_z,
            layout=layout,
            sites=sites,
            category=category,
            model=model,
        )

        # 保存排序方式，供graphio.py的坐标映射使用
        # 使用独立属性避免与父类的layout冲突
        self.ordering_layout = ordering_layout

    def serialize(self) -> dict:
        """序列化时保存 ordering_layout 属性"""
        data = super().serialize()
        data['ordering_layout'] = self.ordering_layout
        return data

    def get_site_by_layer_position(self, row: int, col: int, layer: int) -> ResourceHolder:
        if not (0 <= layer < 4 and 0 <= row < 4 and 0 <= col < 1):
            raise ValueError("无效的位置: layer={}, row={}, col={}".format(layer, row, col))

        site_index = layer * 4 + row * 1 + col
        return self.sites[site_index]

    def add_rack_to_position(self, row: int, col: int, layer: int, rack) -> None:
        site = self.get_site_by_layer_position(row, col, layer)
        site.assign_child_resource(rack)

    def get_rack_at_position(self, row: int, col: int, layer: int):
        site = self.get_site_by_layer_position(row, col, layer)
        return site.resource
