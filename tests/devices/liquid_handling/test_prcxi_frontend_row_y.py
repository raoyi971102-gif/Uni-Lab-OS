"""前端孔位行 Y：上传镜像、内存保持 PLR，设备 HoleRow 仍按井名下发。"""

from __future__ import annotations

from typing import Any, Optional

from unilabos.devices.liquid_handling.prcxi.prcxi import (
    PRCXI9300Plate,
    PRCXI9300TubeRack,
    _prcxi_child_center_y,
    _prcxi_is_plr_row_y,
)
from unilabos.devices.liquid_handling.prcxi.prcxi_labware import (
    PRCXI_8_ReagentRack,
    PRCXI_96_DeepWell,
    PRCXI_300ul_Tips,
    PRCXI_nest_1_troughplate,
)
from unilabos.resources.resource_tracker import ResourceTreeSet


def _serialized_child_center_y(node: dict) -> Optional[float]:
    loc = node.get("location") if isinstance(node, dict) else None
    if not isinstance(loc, dict) or loc.get("y") is None:
        return None
    try:
        return float(loc["y"]) + float(node.get("size_y") or 0) / 2.0
    except (TypeError, ValueError):
        return None


def _first_column_pair(resource) -> tuple[Any, Any]:
    ny = int(getattr(resource, "num_items_y"))
    children = list(resource.children)
    return children[0], children[ny - 1]


def _serialized_child_by_suffix(data: dict, suffix: str) -> dict:
    for node in data.get("children") or []:
        if isinstance(node, dict) and str(node.get("name") or "").endswith(suffix):
            return node
    raise AssertionError(f"serialize 结果里找不到以 {suffix} 结尾的子孔")


def test_96_deepwell_memory_keeps_plr_a_at_high_y() -> None:
    plate = PRCXI_96_DeepWell("PRCXI_96_DeepWell")
    a1, h1 = plate.get_item("A1"), plate.get_item("H1")
    assert _prcxi_child_center_y(a1) > _prcxi_child_center_y(h1)
    assert _prcxi_is_plr_row_y(plate)


def test_96_deepwell_serialize_puts_a_on_frontend_top() -> None:
    plate = PRCXI_96_DeepWell("PRCXI_96_DeepWell")
    a_mem, h_mem = plate.get_item("A1"), plate.get_item("H1")
    a_mem_y = _prcxi_child_center_y(a_mem)
    h_mem_y = _prcxi_child_center_y(h_mem)

    data = plate.serialize()
    a_ser = _serialized_child_by_suffix(data, "A1")
    h_ser = _serialized_child_by_suffix(data, "H1")
    assert _serialized_child_center_y(a_ser) < _serialized_child_center_y(h_ser)
    # 内存不被 serialize 改写
    assert _prcxi_child_center_y(a_mem) == a_mem_y
    assert _prcxi_child_center_y(h_mem) == h_mem_y
    assert _prcxi_is_plr_row_y(plate)


def test_96_deepwell_deserialize_restores_plr_row_y() -> None:
    plate = PRCXI_96_DeepWell("PRCXI_96_DeepWell")
    restored = PRCXI9300Plate.deserialize(plate.serialize())
    a1, h1 = restored.get_item("A1"), restored.get_item("H1")
    assert _prcxi_child_center_y(a1) > _prcxi_child_center_y(h1)
    assert _prcxi_is_plr_row_y(restored)


def test_8_reagent_rack_serialize_flips_first_column() -> None:
    rack = PRCXI_8_ReagentRack("PRCXI_8_ReagentRack")
    first, last = _first_column_pair(rack)
    assert first.name.endswith("A1")
    assert last.name.endswith("B1")
    assert _prcxi_child_center_y(first) > _prcxi_child_center_y(last)

    data = rack.serialize()
    a_ser = _serialized_child_by_suffix(data, "A1")
    b_ser = _serialized_child_by_suffix(data, "B1")
    assert _serialized_child_center_y(a_ser) < _serialized_child_center_y(b_ser)
    assert _prcxi_is_plr_row_y(rack)

    restored = PRCXI9300TubeRack.deserialize(data)
    a1, b1 = restored.get_item("A1"), restored.get_item("B1")
    assert _prcxi_child_center_y(a1) > _prcxi_child_center_y(b1)


def test_single_row_trough_is_not_flipped() -> None:
    trough = PRCXI_nest_1_troughplate("PRCXI_nest_1_troughplate")
    well = trough.children[0]
    mem_y = float(well.location.y)
    data = trough.serialize()
    ser_y = float(data["children"][0]["location"]["y"])
    assert abs(ser_y - mem_y) < 1e-6


def _dumped_root_y(resource) -> float:
    tree = ResourceTreeSet.from_plr_resources([resource], known_newly_created=True)
    dumped = tree.dump()
    assert dumped and dumped[0], "from_plr_resources 应产出一棵树"
    pose = dumped[0][0].get("pose") or dumped[0][0].get("position") or {}
    loc = pose.get("position") if isinstance(pose, dict) else None
    if not isinstance(loc, dict) or loc.get("y") is None:
        raise AssertionError(f"上传节点缺少 position.y: {dumped[0][0]}")
    return float(loc["y"])


def test_single_well_upload_keeps_frontend_row_y() -> None:
    plate = PRCXI_96_DeepWell("PRCXI_96_DeepWell")
    a1 = plate.get_item("A1")
    h1 = plate.get_item("H1")
    expected_a = float(_serialized_child_by_suffix(plate.serialize(), "A1")["location"]["y"])
    expected_h = float(_serialized_child_by_suffix(plate.serialize(), "H1")["location"]["y"])
    a_mem_y = float(a1.location.y)
    h_mem_y = float(h1.location.y)

    assert abs(_dumped_root_y(a1) - expected_a) < 1e-6
    assert abs(_dumped_root_y(h1) - expected_h) < 1e-6
    assert _dumped_root_y(a1) < _dumped_root_y(h1)
    # 增量上传不得改内存 PLR 坐标
    assert abs(float(a1.location.y) - a_mem_y) < 1e-6
    assert abs(float(h1.location.y) - h_mem_y) < 1e-6


def test_single_tip_upload_keeps_frontend_row_y() -> None:
    rack = PRCXI_300ul_Tips("tips")
    a1 = rack.get_item("A1")
    expected_a = float(_serialized_child_by_suffix(rack.serialize(), "A1")["location"]["y"])
    assert abs(_dumped_root_y(a1) - expected_a) < 1e-6


def test_single_tube_upload_keeps_frontend_row_y() -> None:
    rack = PRCXI_8_ReagentRack("PRCXI_8_ReagentRack")
    a1 = rack.get_item("A1")
    b1 = rack.get_item("B1")
    expected_a = float(_serialized_child_by_suffix(rack.serialize(), "A1")["location"]["y"])
    expected_b = float(_serialized_child_by_suffix(rack.serialize(), "B1")["location"]["y"])
    assert abs(_dumped_root_y(a1) - expected_a) < 1e-6
    assert abs(_dumped_root_y(b1) - expected_b) < 1e-6
    assert _dumped_root_y(a1) < _dumped_root_y(b1)


def test_mixed_well_y_serialize_puts_every_row_back_on_grid() -> None:
    plate = PRCXI_96_DeepWell("PRCXI_96_DeepWell")
    good = plate.serialize()
    expected_a = float(_serialized_child_by_suffix(good, "A1")["location"]["y"])
    expected_h = float(_serialized_child_by_suffix(good, "H1")["location"]["y"])
    a1 = plate.get_item("A1")
    # 模拟运行后只回写了 A 行：内存里 A 已被写成前端 Y，其余行仍是 PLR
    a1.location = type(a1.location)(a1.location.x, expected_a, a1.location.z)

    data = plate.serialize()
    assert abs(float(_serialized_child_by_suffix(data, "A1")["location"]["y"]) - expected_a) < 1e-3
    assert abs(float(_serialized_child_by_suffix(data, "H1")["location"]["y"]) - expected_h) < 1e-3
    assert float(_serialized_child_by_suffix(data, "A1")["location"]["y"]) < float(
        _serialized_child_by_suffix(data, "H1")["location"]["y"]
    )


def test_dump_rewrites_mixed_cloud_well_y() -> None:
    plate = PRCXI_96_DeepWell("PRCXI_96_DeepWell")
    a1 = plate.get_item("A1")
    tree = ResourceTreeSet.from_plr_resources([plate], known_newly_created=True)
    root = tree.trees[0].root_node
    # 把 A1 改回 PLR 高 Y，模拟云端残留的跑偏坐标
    for child in root.children:
        if str(child.res_content.name).endswith("A1"):
            child.res_content.pose.position.y = float(a1.location.y)
            break
    dumped = tree.dump()
    nodes = {n["name"]: n for n in dumped[0]}
    a_y = float(nodes["PRCXI_96_DeepWell_well_A1"]["pose"]["position"]["y"])
    h_y = float(nodes["PRCXI_96_DeepWell_well_H1"]["pose"]["position"]["y"])
    assert a_y < h_y


def test_tip_rack_d5_hole_row_formula_unchanged() -> None:
    rack = PRCXI_300ul_Tips("tips")
    d5 = rack.get_item("D5")
    ny = int(rack.num_items_y)
    hole_row = rack.children.index(d5) % ny + 1
    assert hole_row == 4
    a1 = rack.get_item("A1")
    h1 = rack.get_item("H1")
    assert rack.children.index(a1) % ny + 1 == 1
    assert rack.children.index(h1) % ny + 1 == 8
