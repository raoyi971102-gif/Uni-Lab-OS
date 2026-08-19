import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pylabrobot.resources import Coordinate, Deck, Resource

from unilabos.devices.workstation.AI4C.warehouses import (
    AI4C_hplc_station_1x1x1,
    AI4C_loading_rack_1x8x1,
    AI4C_magnetic_stirrer_1x1x1,
    AI4C_pipetting_station_4x4x1,
    AI4C_powder_stack_5x5x1,
    AI4C_solid_weighing_1x1x1,
    AI4C_solid_weighing_powder_1x1x1,
    AI4C_unloading_rack_1x8x1,
)

LAYOUT_PATH = Path(__file__).resolve().parent / "AI4C_layout.json"

# JSON 缺失或字段不完整时的兜底值，与历史硬编码保持一致。
_DEFAULT_DECK_SIZE = (1217.0, 1580.0, 2670.0)
_DEFAULT_ORIGIN = (0.0, 35.0, 0.0)
_DEFAULT_WAREHOUSE_LOCATIONS: Dict[str, Tuple[float, float, float]] = {
    "孔板上料架": (40.0, 1130.0, 0.0),
    "孔板下料架": (40.0, 180.0, 0.0),
    "固态称量粉桶堆栈": (245.0, 900.0, 0.0),
    "固态称量": (520.0, 1030.0, 0.0),
    "固态称量粉桶位": (620.0, 1030.0, 0.0),
    "移液站": (740.0, 950.0, 0.0),
    "磁搅": (520.0, 610.0, 0.0),
    "HPLC工站": (820.0, 610.0, 0.0),
}


def load_ai4c_layout(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 AI4C 台面布局 JSON；文件不存在或损坏时返回空 dict。"""
    layout_path = path or LAYOUT_PATH
    if not layout_path.exists():
        return {}
    try:
        with layout_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_deck_geom(
    size_x: Optional[float],
    size_y: Optional[float],
    size_z: Optional[float],
    origin: Optional[Coordinate],
    layout: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, float, Coordinate]:
    layout = layout if layout is not None else load_ai4c_layout()
    deck_cfg = layout.get("deck") if isinstance(layout.get("deck"), dict) else {}
    size_cfg = deck_cfg.get("size") if isinstance(deck_cfg.get("size"), dict) else {}
    origin_cfg = deck_cfg.get("origin") if isinstance(deck_cfg.get("origin"), dict) else {}

    resolved_x = _as_float(size_x, _as_float(size_cfg.get("width"), _DEFAULT_DECK_SIZE[0]))
    resolved_y = _as_float(size_y, _as_float(size_cfg.get("height"), _DEFAULT_DECK_SIZE[1]))
    resolved_z = _as_float(size_z, _as_float(size_cfg.get("depth"), _DEFAULT_DECK_SIZE[2]))
    if origin is None:
        origin = Coordinate(
            _as_float(origin_cfg.get("x"), _DEFAULT_ORIGIN[0]),
            _as_float(origin_cfg.get("y"), _DEFAULT_ORIGIN[1]),
            _as_float(origin_cfg.get("z"), _DEFAULT_ORIGIN[2]),
        )
    return resolved_x, resolved_y, resolved_z, origin


def _warehouse_coordinate(name: str, layout: Dict[str, Any]) -> Coordinate:
    defaults = _DEFAULT_WAREHOUSE_LOCATIONS.get(name, (0.0, 0.0, 0.0))
    warehouses = layout.get("warehouses")
    item = warehouses.get(name) if isinstance(warehouses, dict) else None
    if not isinstance(item, dict):
        return Coordinate(*defaults)
    return Coordinate(
        _as_float(item.get("x"), defaults[0]),
        _as_float(item.get("y"), defaults[1]),
        _as_float(item.get("z"), defaults[2]),
    )


class AI4C_deck(Deck):
    """AI4C 工作站物料台面。"""

    def __init__(
        self,
        name: str = "AI4C_deck",
        size_x: Optional[float] = None,
        size_y: Optional[float] = None,
        size_z: Optional[float] = None,
        origin: Optional[Coordinate] = None,
        category: str = "deck",
        setup: bool = False,
        **kwargs,
    ) -> None:
        size_x, size_y, size_z, origin = _resolve_deck_geom(size_x, size_y, size_z, origin)
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, origin=origin)
        self.warehouses = {}
        self.warehouse_locations = {}
        if setup:
            self.setup()

    def setup(self) -> None:
        # 仓库名称与 AI4C 动作语义保持一致，便于从流程节点追踪真实工位。
        self.warehouses = {
            "孔板上料架": AI4C_loading_rack_1x8x1("孔板上料架"),
            "孔板下料架": AI4C_unloading_rack_1x8x1("孔板下料架"),
            "固态称量粉桶堆栈": AI4C_powder_stack_5x5x1("固态称量粉桶堆栈"),
            "固态称量": AI4C_solid_weighing_1x1x1("固态称量"),
            "固态称量粉桶位": AI4C_solid_weighing_powder_1x1x1("固态称量粉桶位"),
            "移液站": AI4C_pipetting_station_4x4x1("移液站"),
            "磁搅": AI4C_magnetic_stirrer_1x1x1("磁搅"),
            "HPLC工站": AI4C_hplc_station_1x1x1("HPLC工站"),
        }

        # 坐标来自 AI4C_layout.json，可用同目录 edit_layout.py 快速调整。
        layout = load_ai4c_layout()
        self.warehouse_locations = {
            warehouse_name: _warehouse_coordinate(warehouse_name, layout)
            for warehouse_name in self.warehouses
        }

        for warehouse_name, warehouse in self.warehouses.items():
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
