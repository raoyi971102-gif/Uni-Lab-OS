# 电化学工作站 Deck 修改指南

## 概念说明

**Deck** 是工作站的物理台面抽象，定义了所有设备和耗材的空间布局。Deck 由若干 **warehouse**（存放区域）组成，每个 warehouse 可以容纳一个或多个资源（多孔板、试剂瓶等）。

坐标系：
- `x`：左右方向（mm）
- `y`：前后方向（mm）
- `z`：上下方向/高度（mm）
- 原点 `(0, 0, 0)` 位于 Deck 左下角

## 文件位置

```
unilabos/devices/workstation/electrochem/
├── __init__.py
├── deck.py                    ← Deck 布局定义（修改这里）
├── electrochem_workstation.py ← 工作站逻辑
└── DECK_GUIDE.md              ← 本文件

unilabos/registry/resources/electrochem/
└── deck.yaml                  ← Deck 注册表（一般不需修改）
```

## 如何修改 Deck 布局

### 1. 增加/删除多孔架

打开 `deck.py`，在 `setup()` 方法中编辑 `self.warehouses` 和 `self.warehouse_locations`：

```python
def setup(self) -> None:
    self.warehouses = {
        # 现有的多孔架
        "多孔架1": electrochem_rack_slot("多孔架1"),
        "多孔架2": electrochem_rack_slot("多孔架2"),
        "多孔架3": electrochem_rack_slot("多孔架3"),

        # ✅ 添加新的多孔架 - 复制一行即可
        "多孔架4": electrochem_rack_slot("多孔架4"),
        "多孔架5": electrochem_rack_slot("多孔架5"),
        
        "试剂区域": electrochem_reagent_area("试剂区域", num_x=2, num_y=2),
    }

    self.warehouse_locations = {
        "多孔架1": Coordinate(50.0, 850.0, 0.0),
        "多孔架2": Coordinate(300.0, 850.0, 0.0),
        "多孔架3": Coordinate(550.0, 850.0, 0.0),

        # ✅ 为新多孔架指定坐标（x 方向间隔约 250mm）
        "多孔架4": Coordinate(800.0, 850.0, 0.0),
        "多孔架5": Coordinate(1050.0, 850.0, 0.0),

        "试剂区域": Coordinate(950.0, 450.0, 0.0),
    }
```

> **注意**: `self.warehouses` 和 `self.warehouse_locations` 中的 key 必须一一对应。

### 2. 修改 warehouse 尺寸

编辑 `electrochem_rack_slot()` 或 `electrochem_reagent_area()` 中的参数：

```python
def electrochem_rack_slot(name: str):
    return warehouse_factory(
        name=name,
        num_items_x=1,       # x 方向格子数
        num_items_y=1,       # y 方向格子数
        num_items_z=1,       # z 方向层数（堆叠层数）
        dx=5.0,              # 第一个格子的 x 偏移
        dy=5.0,              # 第一个格子的 y 偏移
        item_dx=137.0,       # 格子间 x 间距
        item_dy=96.0,        # 格子间 y 间距
        resource_size_x=127.0,   # 资源宽度
        resource_size_y=86.0,    # 资源深度
        resource_size_z=25.0,    # 资源高度
        category="rack_slot",
    )
```

常用修改场景：
| 需求 | 修改参数 |
|------|----------|
| 放更大的板 | 增大 `resource_size_x/y/z` |
| 一个位置堆叠多块板 | 增大 `num_items_z` |
| 一排多个板 | 增大 `num_items_x` 或 `num_items_y` |

### 3. 自定义 warehouse 类型

如果预定义的工厂函数不满足需求，可以直接调用 `warehouse_factory`：

```python
def my_custom_warehouse(name: str):
    """自定义 2行×3列×2层 的存放区域"""
    return warehouse_factory(
        name=name,
        num_items_x=3,       # 3列
        num_items_y=2,       # 2行
        num_items_z=2,       # 2层
        dx=10.0,
        dy=10.0,
        dz=0.0,
        item_dx=140.0,       # 列间距
        item_dy=100.0,       # 行间距
        item_dz=30.0,        # 层间距
        resource_size_x=127.0,
        resource_size_y=86.0,
        resource_size_z=25.0,
        category="custom_area",
        layout="row-major",  # 命名方式: "col-major"=列优先(A01,B01..), "row-major"=行优先(A01,A02..)
    )
```

### 4. 修改 Deck 整体尺寸

修改 `ElectrochemDeck.__init__` 中的默认参数：

```python
class ElectrochemDeck(Deck):
    def __init__(
        self,
        name: str = "electrochem_deck",
        size_x: float = 2000.0,   # ← 加宽台面
        size_y: float = 1500.0,   # ← 加深台面
        size_z: float = 200.0,
        ...
    ):
```

## 在 Graph 文件中使用 Deck

在设备图 JSON 文件中配置 `deck` 参数以使用注册的 Deck 类型：

```json
{
    "id": "electrochem_ws",
    "type": "electrochem_workstation",
    "config": {
        "deck": "ElectrochemDeck"
    }
}
```

系统会自动通过 `unilabos/registry/resources/electrochem/deck.yaml` 解析到 `ElectrochemDeck` 类。

## 常见资源类型

以下是注册表中可用的多孔板/试剂架类型，可在 graph 文件的 children 中引用：

| 注册名 | 说明 | 来源 |
|--------|------|------|
| `nest_96_wellplate_2ml_deep` | 96 深孔板 2mL | Opentrons |
| `corning_96_wellplate_360ul_flat` | 96 平底板 360μL | Opentrons |
| `corning_24_wellplate_3point4ml_flat` | 24 孔板 3.4mL | Opentrons |
| `corning_6_wellplate_16point8ml_flat` | 6 孔板 16.8mL | Opentrons |
| `PRCXI_96_DeepWell` | 96 深孔板 | PRCXI |
| `PRCXI_48_DeepWell` | 48 深孔板 | PRCXI |
| `opentrons_24_tuberack_nest_2ml_screwcap` | 24位试管架 2mL | Opentrons |
| `opentrons_6_tuberack_falcon_50ml_conical` | 6位离心管架 50mL | Opentrons |

完整列表请查看 `unilabos/registry/resources/` 下的 YAML 文件。
