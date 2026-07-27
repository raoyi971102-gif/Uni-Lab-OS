#!/usr/bin/env python3
"""按图2流程 + Untitled-2 布局风格生成 leap-lab 并行工作流 JSON。

布局参照图2 / Untitled-2.json（横向展开）：
  - 入口在左侧 (x≈-150)，向各支线分叉
  - 7 条支线纵向等距排列 (Δy≈400)，彼此不重叠
  - 支线内节点自左向右串联 (Δx≈500)
  - 汇合节点在对应支线末端略靠右
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

TARGET_LAB = "342e97d4-f412-40e4-972a-d8fa6bd102bc"
OUT = Path(__file__).with_name("金四面体合成工作流.json")

# GN 9320 移液站：实例 id（device_name）与注册表类型（resource_name）分离
# 见 workflow/common.py、GN_station.json class=liquid_handler.prcxi id=PRCXI
PRCXI_INSTANCE = "PRCXI"
PRCXI_RESOURCE = "liquid_handler.prcxi"

# 机械手工站 ModuleNo / XPos（与 robotic_arm.py ROBOT_DESTINATIONS 一致）
ROBOT_MOD_STACK = 6          # 堆栈
ROBOT_MOD_SOLID = 7          # SolidFeed @ 6318
ROBOT_MOD_TUBE = 5           # CentrifugeTubeLiquid @ -1726
ROBOT_MOD_N9320 = 4          # N9320 @ -3926
ROBOT_MOD_QUICK = 2          # 快换/磁力搅拌 @ -10478
ROBOT_MOD_CENTRIFUGE = 3     # 离心机 @ -8582
ROBOT_MOD_OVEN = 8           # 常规烘箱 @ -13278
ROBOT_X_STACK = 3274         # 堆栈孔板/储液槽
ROBOT_X_STACK_BOTTLE = 2473  # 堆栈小瓶/大瓶
ROBOT_X_SOLID = 6318         # 固体加样
ROBOT_X_TUBE = -1726         # 离心管液体处理
ROBOT_X_N9320 = -3926        # 9320移液站
ROBOT_X_QUICK = -10478       # 快换/磁力搅拌
ROBOT_X_CENTRIFUGE = -8582   # 离心机
ROBOT_X_OVEN = -13278        # 常规烘箱

# 离心管动作点位已写死在 centrifuge_tube_liquid_handling.py；@action(auto_prefix=True) → auto-{method}
TUBE_ACTION_PREFIX = "auto-"


def _robot_transfer(
    destination: str | None = None,
    *,
    module_no: int | None = None,
    x_pos: int | None = None,
    stack: int | None = None,
    pick_place: int = 1,
    x_speed: int = 300,
    timeout: float = 180.0,
    disabled: bool = False,
) -> dict:
    """与 robotic_arm.transfer_carrier 参数对齐。"""
    param: dict = {"x_speed": x_speed, "pick_place": pick_place, "timeout": timeout}
    if destination:
        param["destination"] = destination
    if module_no is not None:
        param["module_no"] = module_no
    if x_pos is not None:
        param["x_pos"] = x_pos
    if stack is not None:
        param["stack"] = stack
    return {
        "device": "gn_robotic_arm", "action": "transfer_carrier",
        "param": param, "disabled": disabled,
    }


def _prcxi_add(vols: list, disabled: bool = False) -> dict:
    n = len(vols)
    return {
        "device": PRCXI_INSTANCE, "resource": PRCXI_RESOURCE, "action": "add_liquid",
        "param": {"asp_vols": vols, "dis_vols": vols, "reagent_sources": [], "targets": [],
                  "use_channels": [], "flow_rates": [], "offsets": [], "liquid_height": [],
                  "blow_out_air_volume": [], "spread": "wide", "is_96_well": n > 1,
                  "mix_time": 0, "mix_vol": 0, "mix_rate": 0, "mix_liquid_height": 0.0,
                  "none_keys": []},
        "disabled": disabled,
    }


def _prcxi_remove(disabled: bool = False) -> dict:
    return {
        "device": PRCXI_INSTANCE, "resource": PRCXI_RESOURCE, "action": "remove_liquid",
        "param": {"asp_vols": [], "targets": [], "use_channels": [], "flow_rates": [],
                  "offsets": [], "liquid_height": [], "blow_out_air_volume": [],
                  "spread": "wide", "is_96_well": True, "none_keys": []},
        "disabled": disabled,
    }


def _magnetic_stir(rpm: int, temp_c: int, minutes: int, disabled: bool = False) -> dict:
    return {
        "device": "gn_quick_carrier_exchange", "action": "magnetic_stir",
        "param": {"rpm": rpm, "temp_c": temp_c, "minutes": minutes},
        "disabled": disabled,
    }


def _rest_on_stirrer(temp_c: int, minutes: int, disabled: bool = False) -> dict:
    return {
        "device": "gn_quick_carrier_exchange", "action": "rest_on_stirrer",
        "param": {"temp_c": temp_c, "minutes": minutes},
        "disabled": disabled,
    }


def _centrifuge_run(rpm: int, minutes: int, disabled: bool = False) -> dict:
    return {
        "device": "gn_centrifuge", "action": "run",
        "param": {"rpm": rpm, "minutes": minutes, "plate_no": 2},
        "disabled": disabled,
    }


def _solid_dispense(weight_mg: int, disabled: bool = False) -> dict:
    return {
        "device": "gn_solid_weighing", "action": "dispense_powder",
        "param": {"weight_mg": weight_mg},
        "disabled": disabled,
    }


def _tube_action(method: str, disabled: bool = False) -> dict:
    return {
        "device": "gn_centrifuge_tube_liquid_handling",
        "action": f"{TUBE_ACTION_PREFIX}{method}",
        "param": {},
        "disabled": disabled,
    }


def _tube_lid_open() -> dict:
    return _tube_action("small_gripper_open_lid")


def _tube_lid_close() -> dict:
    return _tube_action("small_gripper_close_lid")


def _tube_ch8_aspirate(disabled: bool = False) -> dict:
    return _tube_action("ch8_aspirate", disabled=disabled)


def _tube_ch8_dispense(disabled: bool = False) -> dict:
    return _tube_action("ch8_dispense", disabled=disabled)


def _tube_ch8_mix(disabled: bool = False) -> dict:
    return _tube_action("ch8_mix", disabled=disabled)


def _tube_ultrasound(disabled: bool = False) -> dict:
    return _tube_action("ultrasound_mix", disabled=disabled)


def _tube_ultrasound_stop(disabled: bool = True) -> dict:
    return _tube_action("ultrasound_stop", disabled=disabled)


def _prcxi_remove_vol(vol: float, disabled: bool = False) -> dict:
    tpl = _prcxi_remove(disabled)
    tpl["param"]["asp_vols"] = [vol]
    return tpl


def _prcxi_add_vol(vol: float, disabled: bool = False) -> dict:
    return _prcxi_add([vol], disabled=disabled)


def _prcxi_mix(disabled: bool = False) -> dict:
    return {
        "device": PRCXI_INSTANCE, "resource": PRCXI_RESOURCE, "action": "mix",
        "param": {"mix_time": 10, "mix_vol": 100, "mix_rate": 150.0,
                  "height_to_bottom": 0.5, "targets": [], "offsets": [],
                  "none_keys": []},
        "disabled": disabled,
    }


def _prcxi_marker(disabled: bool = True) -> dict:
    return _prcxi_mix(disabled=disabled)


# 各列离心管开/关盖（点位写死在驱动；LID_* 仅作流程注释）
LID_AUX = ("large", 1)      # 氯金酸原液瓶（附支路稀释）
LID_COL2 = ("large", 2)   # NaBH4
LID_COL3 = ("large", 3)   # 抗坏血酸(低)
LID_COL5 = ("large", 4)   # 列5氯金酸反应液
LID_COL7 = ("small", 1)   # 高浓抗坏血酸样品瓶

# 图2 横向布局常量（支线内左右串联，支线间上下分层）
ENTRY_X = -150.0
STEP_X0 = 250.0
STEP_DX = 500.0
LANE_Y0 = 80.0
LANE_DY = 400.0

OPC: dict[str, dict] = {
    "manual-prepare": {"device": "gn_system_control", "action": "manual_prepare",
                       "param": {"timeout": 10}, "disabled": True},
    "workflow-done": {"device": "gn_system_control", "action": "workflow_complete",
                      "param": {"timeout": 10}, "disabled": False},
    "b1-to-9320": _robot_transfer("stack_plate"),
    "b1-prcxi-add": {"device": PRCXI_INSTANCE, "resource": PRCXI_RESOURCE,
                     "action": "add_liquid",
                     "param": {"asp_vols": [800, 800, 300], "dis_vols": [800, 800, 300],
                               "reagent_sources": [], "targets": [], "use_channels": [],
                               "flow_rates": [], "offsets": [], "liquid_height": [],
                               "blow_out_air_volume": [], "spread": "wide", "is_96_well": True,
                               "mix_time": 0, "mix_vol": 0, "mix_rate": 0,
                               "mix_liquid_height": 0.0, "none_keys": []},
                     "disabled": False},
    "b1-prcxi-mix": {"device": PRCXI_INSTANCE, "resource": PRCXI_RESOURCE,
                     "action": "mix",
                     "param": {"mix_time": 10, "mix_vol": 100, "mix_rate": 150.0,
                               "height_to_bottom": 0.5, "targets": [], "offsets": [],
                               "none_keys": []}, "disabled": False},
    "b1-mix-a": {"device": PRCXI_INSTANCE, "resource": PRCXI_RESOURCE,
                 "action": "mix",
                 "param": {"mix_time": 0, "mix_vol": 0, "mix_rate": 0.0,
                           "height_to_bottom": 0.0, "targets": [], "offsets": [],
                           "none_keys": []}, "disabled": True},
    "b1-to-stir": _robot_transfer("magnetic_stirrer"),
    "b1-stir": _magnetic_stir(1500, 30, 1),
    "b1-seed": _rest_on_stirrer(30, 270, disabled=True),
    "b2-to-solid": _robot_transfer("stack_reagent"),
    "b2-nabh4-feed": _solid_dispense(50),
    "b2-to-tube": _robot_transfer("solid_feed"),
    "b2-lid-open": _tube_lid_open(),
    "b2-dilute": _tube_ch8_aspirate(),
    "b2-ultrasound": _tube_ultrasound(),
    "b2-nabh4-done": _tube_ultrasound_stop(),
    "b2-lid-close": _tube_lid_close(),
    "b2-to-9320": _robot_transfer("tube_handler"),
    "b3-to-solid": _robot_transfer("stack_reagent"),
    "b3-aa-feed": _solid_dispense(30),
    "b3-to-tube": _robot_transfer("solid_feed"),
    "b3-lid-open": _tube_lid_open(),
    "b3-water": _tube_ch8_dispense(),
    "b3-mix": _tube_ch8_mix(),
    "b3-aa-done": _tube_ultrasound_stop(),
    "b3-lid-close": _tube_lid_close(),
    "b4-plate-9320": _robot_transfer("stack_plate"),
    "b4-aa-to-add": _robot_transfer("tube_handler"),
    "b4-prcxi-add1": _prcxi_add([571, 571, 429]),
    "b4-to-stir1": _robot_transfer("magnetic_stirrer"),
    "b4-stir1": _magnetic_stir(300, 28, 2),
    "b4-to-9320-a": _robot_transfer("magnetic_stirrer"),
    "b4-seed-to-add": _robot_transfer("magnetic_stirrer"),
    "b4-prcxi-seed-add": _prcxi_add_vol(28.6),
    "b4-to-stir2": _robot_transfer("magnetic_stirrer"),
    "b4-stir2": _magnetic_stir(300, 28, 60),
    "b4-to-cent1": _robot_transfer("magnetic_stirrer"),
    "b4-cent1": _centrifuge_run(4000, 10),
    "b4-to-9320-b": _robot_transfer("centrifuge"),
    "b4-aspirate1": _prcxi_remove_vol(1400),
    "b4-add-ctac1": _prcxi_add_vol(500),
    "b4-mix1": _prcxi_mix(),
    "b4-to-cent2": _robot_transfer("prcxi"),
    "b4-cent2": _centrifuge_run(4000, 10),
    "b4-to-9320-c": _robot_transfer("centrifuge"),
    "b4-aspirate2": _prcxi_remove_vol(1400),
    "b4-add-ctac2": _prcxi_add_vol(500),
    "b4-mix2": _prcxi_mix(),
    "b4-gold-done": _prcxi_marker(True),
    "b5-to-tube": _robot_transfer("stack_reagent"),
    "b5-lid-open": _tube_lid_open(),
    "b5-add-liquid": _tube_ch8_dispense(),
    "b5-lid-close": _tube_lid_close(),
    "b6-plate-9320": _robot_transfer("stack_plate"),
    "b6-add1": _prcxi_add([300, 300, 300]),
    "b6-to-stir": _robot_transfer("magnetic_stirrer"),
    "b6-stir": _magnetic_stir(300, 30, 5),
    "b6-gold-add": _robot_transfer("prcxi"),
    "b6-haucl-add": _robot_transfer("tube_handler"),
    "b6-rest-stir": _rest_on_stirrer(29, 10),
    "b6-to-cent1": _robot_transfer("magnetic_stirrer"),
    "b6-cent1": _centrifuge_run(700, 15),
    "b6-to-9320-a": _robot_transfer("centrifuge"),
    "b6-remove1": _prcxi_remove_vol(1400),
    "b6-add-ctac1": _prcxi_add_vol(500),
    "b6-mix1": _prcxi_mix(),
    "b6-to-cent2": _robot_transfer("prcxi"),
    "b6-cent2": _centrifuge_run(700, 15),
    "b6-to-9320-b": _robot_transfer("centrifuge"),
    "b6-remove2": _prcxi_remove_vol(500),
    "b6-add-ctac2": _prcxi_add_vol(1000),
    "b6-mix2": _prcxi_mix(),
    "b6-transfer-plate": _prcxi_add_vol(500),
    "b6-to-cent3": _robot_transfer("prcxi"),
    "b6-cent3": _centrifuge_run(700, 15),
    "b6-to-9320-c": _robot_transfer("centrifuge"),
    "b6-remove3": _prcxi_remove_vol(1000),
    "b6-add-ctac100": _prcxi_add_vol(500),
    "b6-mix3": _prcxi_mix(),
    "b6-tetra-done": _prcxi_marker(True),
    "b7-bottle-solid": _robot_transfer("stack_bottle"),
    "b7-prcxi-add": _prcxi_add([10000]),
    "b7-to-tube": _robot_transfer("solid_feed"),
    "b7-lid-open": _tube_lid_open(),
    "b7-dissolve": _tube_ch8_dispense(),
    "b7-lid-close": _tube_lid_close(),
    "b7-to-b6-add1": _robot_transfer("tube_handler"),
    "aux-lid-open": _tube_lid_open(),
    "aux-haucl-dilute": _tube_ch8_aspirate(),
    "aux-lid-close": _tube_lid_close(),
}
for _alias, _base in (
    ("23-1", "b4-aspirate1"), ("23-2", "b4-aspirate2"),
    ("33-1", "b6-remove1"), ("33-2", "b6-remove2"), ("33-3", "b6-remove3"),
):
    OPC[_alias] = copy.deepcopy(OPC[_base])

LANES: list[tuple[int, list[tuple[str, str, str]]]] = [
    (0, [
        ("B1-01", "机械臂取96孔深孔板至9320移液工作站（机械臂）", "b1-to-9320"),
        ("B1-02", "移液工作站对其进行加液（9320）", "b1-prcxi-add"),
        ("B1-03", "9320对其进行吹打混匀（9320）", "b1-prcxi-mix"),
        ("B1-04", "得到混合溶液A（禁用）", "b1-mix-a"),
        ("B1-05", "机械臂将其转移到磁力搅拌器（机械臂）", "b1-to-stir"),
        ("B1-06", "磁力搅拌/混合液A（磁力搅拌器）", "b1-stir"),
        ("B1-07", "静置，得到种子溶液（禁用）", "b1-seed"),
    ]),
    (1, [
        ("B2-01", "机械臂取储液槽至固体粉末加样仪（机械臂）", "b2-to-solid"),
        ("B2-02", "固体粉末加样仪加NaBH4粉末（固体粉末加样仪）", "b2-nabh4-feed"),
        ("B2-03", "机械臂取该样品至离心管液体处理设备（机械臂）", "b2-to-tube"),
        ("B2-04", "试剂瓶开盖（离心管液体处理）", "b2-lid-open"),
        ("B2-05", "加冰水稀释（离心管液体处理）", "b2-dilute"),
        ("B2-06", "超声震荡（离心管液体处理）", "b2-ultrasound"),
        ("B2-07", "得到NaBH4溶液（禁用）", "b2-nabh4-done"),
        ("B2-08", "试剂瓶关盖（离心管液体处理）", "b2-lid-close"),
        ("B2-09", "机械臂转移溶液至移液工作站（机械臂）", "b2-to-9320"),
    ]),
    (2, [
        ("B3-01", "机械臂取储液槽至粉末加样仪（机械臂）", "b3-to-solid"),
        ("B3-02", "加抗坏血酸粉末（粉末加样仪）", "b3-aa-feed"),
        ("B3-03", "机械臂转移粉末样品至离心管液体处理器（机械臂）", "b3-to-tube"),
        ("B3-04", "试剂瓶开盖（离心管液体处理）", "b3-lid-open"),
        ("B3-05", "加水稀释（离心管液体处理）", "b3-water"),
        ("B3-06", "吹打混匀（离心管液体处理）", "b3-mix"),
        ("B3-07", "得到抗坏血酸溶液（离心管液体处理，禁用）", "b3-aa-done"),
        ("B3-08", "试剂瓶关盖（离心管液体处理）", "b3-lid-close"),
    ]),
    (3, [
        ("B4-01", "机械臂取96孔板至9320（机械臂）", "b4-plate-9320"),
        ("B4-02", "机械臂取抗坏血酸溶液至第四列第一次加液（机械臂）", "b4-aa-to-add"),
        ("B4-03", "9320取CTAC和氯金酸稀释后加入（9320）", "b4-prcxi-add1"),
        ("B4-04", "机械臂取样品至磁力搅拌器（机械臂）", "b4-to-stir1"),
        ("B4-05", "磁力搅拌（磁力搅拌器）", "b4-stir1"),
        ("B4-06", "机械臂转移至9320（机械臂）", "b4-to-9320-a"),
        ("B4-07", "机械臂取种子溶液到第二次加液（机械臂）", "b4-seed-to-add"),
        ("B4-08", "PRCXI种子加液（9320）", "b4-prcxi-seed-add"),
        ("B4-09", "机械臂转移加液后溶液至磁力搅拌器（机械臂）", "b4-to-stir2"),
        ("B4-10", "磁力搅拌（磁力搅拌器）", "b4-stir2"),
        ("B4-11", "机械臂取溶液至离心机（机械臂）", "b4-to-cent1"),
        ("B4-12", "离心（离心机）", "b4-cent1"),
        ("B4-13", "机械臂取溶液至9320（机械臂）", "b4-to-9320-b"),
        ("B4-14", "取上清液（1400µl）（9320）", "b4-aspirate1"),
        ("B4-15", "加入20mM CTAC（500µl）（9320）", "b4-add-ctac1"),
        ("B4-16", "混匀（9320）", "b4-mix1"),
        ("B4-17", "机械臂取溶液至离心机（机械臂）", "b4-to-cent2"),
        ("B4-18", "离心（离心机）", "b4-cent2"),
        ("B4-19", "机械臂取溶液至9320（机械臂）", "b4-to-9320-c"),
        ("B4-20", "取上清液（1400µl）（9320）", "b4-aspirate2"),
        ("B4-21", "加入20mM CTAC（500µl）（9320）", "b4-add-ctac2"),
        ("B4-22", "混匀（9320）", "b4-mix2"),
        ("B4-23", "得到金球溶液（9320，禁用）", "b4-gold-done"),
    ]),
    (4, [
        ("B5-01", "机械臂取储液槽至离心管液体处理设备（机械臂）", "b5-to-tube"),
        ("B5-02", "试剂瓶开盖（离心管液体处理）", "b5-lid-open"),
        ("B5-03", "加液（离心管液体处理设备）", "b5-add-liquid"),
        ("B5-04", "试剂瓶关盖（离心管液体处理）", "b5-lid-close"),
    ]),
    (5, [
        ("B6-01", "机械臂取96孔板至9320（机械臂）", "b6-plate-9320"),
        ("B6-02", "加液（9320）", "b6-add1"),
        ("B6-03", "机械臂取溶液至磁力搅拌器（机械臂）", "b6-to-stir"),
        ("B6-04", "磁力搅拌（磁力搅拌器）", "b6-stir"),
        ("B6-05", "机械臂取金球溶液加入到溶液中（机械臂）", "b6-gold-add"),
        ("B6-06", "机械臂取氯金酸加入到溶液中（机械臂）", "b6-haucl-add"),
        ("B6-07", "静置（磁力搅拌器）", "b6-rest-stir"),
        ("B6-08", "机械臂取溶液至离心机（机械臂）", "b6-to-cent1"),
        ("B6-09", "离心（离心机）", "b6-cent1"),
        ("B6-10", "机械臂取溶液至9320（机械臂）", "b6-to-9320-a"),
        ("B6-11", "取上清液（1400µl）（9320）", "b6-remove1"),
        ("B6-12", "加入20mM CTAC（500µl）（9320）", "b6-add-ctac1"),
        ("B6-13", "混匀（9320）", "b6-mix1"),
        ("B6-14", "机械臂取溶液至离心机（机械臂）", "b6-to-cent2"),
        ("B6-15", "离心（离心机）", "b6-cent2"),
        ("B6-16", "机械臂取溶液至9320（机械臂）", "b6-to-9320-b"),
        ("B6-17", "取上清液（500µl）（9320）", "b6-remove2"),
        ("B6-18", "加入20mM CTAC（1000µl）（9320）", "b6-add-ctac2"),
        ("B6-19", "混匀（9320）", "b6-mix2"),
        ("B6-20", "移取500µl到酶标板（9320）", "b6-transfer-plate"),
        ("B6-21", "机械臂取溶液至离心机（机械臂）", "b6-to-cent3"),
        ("B6-22", "离心（离心机）", "b6-cent3"),
        ("B6-23", "机械臂取溶液至9320（机械臂）", "b6-to-9320-c"),
        ("B6-24", "上清液（1000µl）（9320）", "b6-remove3"),
        ("B6-25", "加100mM CTAC（9320）", "b6-add-ctac100"),
        ("B6-26", "混匀（9320）", "b6-mix3"),
        ("B6-27", "得到金四面体溶液（9320，禁用）", "b6-tetra-done"),
    ]),
    (6, [
        ("B7-01", "机械臂取样品瓶至粉末加样仪（机械臂）", "b7-bottle-solid"),
        ("B7-02", "加样（9320）", "b7-prcxi-add"),
        ("B7-03", "机械臂转移至离心管液体处理设备（机械臂）", "b7-to-tube"),
        ("B7-04", "试剂瓶开盖（离心管液体处理）", "b7-lid-open"),
        ("B7-05", "加水溶解（离心管液体处理设备）", "b7-dissolve"),
        ("B7-06", "试剂瓶关盖（离心管液体处理）", "b7-lid-close"),
        ("B7-07", "机械臂取溶液至第六列第一次加液（机械臂）", "b7-to-b6-add1"),
    ]),
]

AUX_LANE: list[tuple[str, str, str]] = [
    ("AUX-01", "氯金酸原液瓶开盖", "aux-lid-open"),
    ("AUX-02", "氯金酸原液加冰水稀释", "aux-haucl-dilute"),
    ("AUX-03", "氯金酸原液瓶关盖", "aux-lid-close"),
]

FINISH = ("DONE", "得到金四面体", "workflow-done")

CROSS_EDGES = [
    ("AUX-03", "B1-02"),
    ("B2-07", "B1-02"),
    ("B3-07", "B4-02"),
    ("B1-07", "B4-07"),
    ("B5-03", "B6-06"),
    ("B4-23", "B6-05"),
    ("B7-07", "B6-02"),
]

PRCXI_FOOTER = {
    "b4-prcxi-seed-add": "[PRCXI] 第二次加液:28.6µl种子溶液",
    "23-1": "[PRCXI] 第1次吸上清+20mM CTAC清洗",
    "23-2": "[PRCXI] 第2次吸上清+20mM CTAC清洗",
    "33-1": "[PRCXI] 第1次去上清+CTAC清洗",
    "33-2": "[PRCXI] 第2次去上清+CTAC清洗",
    "33-3": "[PRCXI] 第3次去上清+CTAC清洗",
}


def lane_y(col: int) -> float:
    return LANE_Y0 + col * LANE_DY


def step_x(step: int) -> float:
    return STEP_X0 + step * STEP_DX


def make_node(footer: str, opc_key: str) -> dict:
    tpl = copy.deepcopy(OPC[opc_key])
    if opc_key in PRCXI_FOOTER:
        footer = PRCXI_FOOTER[opc_key]
    action = tpl.pop("action")
    device = tpl.pop("device")
    resource = tpl.pop("resource", device)
    disabled = tpl.pop("disabled", False)
    return {
        "uuid": str(uuid.uuid4()),
        "parent_uuid": "",
        "name": action,
        "type": "ILab",
        "icon": "",
        "pose": {
            "layout": "x-y",
            "position": {"x": 0.0, "y": 0.0, "z": 0},
            "position_3d": {"x": 0, "y": 0, "z": 0},
            "size": {"width": 0, "height": 0, "depth": 0},
            "scale": {"x": 1, "y": 1, "z": 1},
            "rotation": {"x": 0, "y": 0, "z": 0},
            "extra": None,
            "cross_section_type": "rectangle",
        },
        "param": tpl["param"],
        "footer": footer,
        "device_name": device,
        "disabled": disabled,
        "minimized": False,
        "lab_node_type": "Device",
        "template_uuid": "",
        "template_name": action,
        "resource_name": resource,
    }


def chain(ids: list[str]) -> list[tuple[str, str]]:
    return [(ids[i], ids[i + 1]) for i in range(len(ids) - 1)]


def build() -> dict:
    nodes: dict[str, dict] = {}
    layout: dict[str, tuple[float, float]] = {}

    entry_y = lane_y(3)
    nodes["E0"] = make_node("人工准备耗材至相应位置", "manual-prepare")
    nodes["E1"] = make_node("机械臂转移物料", "manual-prepare")
    nodes["E1"]["disabled"] = False
    layout["E0"] = (ENTRY_X, entry_y - 120)
    layout["E1"] = (ENTRY_X, entry_y)

    aux_y = lane_y(0) - 140
    for step, (nid, footer, okey) in enumerate(AUX_LANE):
        nodes[nid] = make_node(footer, okey)
        layout[nid] = (step_x(step) - 80, aux_y)

    for col, steps in LANES:
        y = lane_y(col)
        for step, (nid, footer, okey) in enumerate(steps):
            nodes[nid] = make_node(footer, okey)
            layout[nid] = (step_x(step), y)

    fid, ffooter, fopc = FINISH
    nodes[fid] = make_node(ffooter, fopc)
    col6_steps = next(st for c, st in LANES if c == 5)
    layout[fid] = (step_x(len(col6_steps)) + 80, lane_y(5))

    for nid, (x, y) in layout.items():
        nodes[nid]["pose"]["position"]["x"] = round(x, 2)
        nodes[nid]["pose"]["position"]["y"] = round(y, 2)

    pairs: list[tuple[str, str]] = [("E0", "E1"), ("E1", AUX_LANE[0][0])]
    pairs += chain([s[0] for s in AUX_LANE])
    for col, steps in LANES:
        pairs.append(("E1", steps[0][0]))
        pairs += chain([s[0] for s in steps])
    pairs += CROSS_EDGES
    pairs.append(("B6-27", FINISH[0]))

    seen: set[tuple[str, str]] = set()
    pairs = [p for p in pairs if p not in seen and not seen.add(p)]

    edges = [
        {
            "source_node_uuid": nodes[s]["uuid"],
            "target_node_uuid": nodes[t]["uuid"],
            "source_handle_key": "ready",
            "source_handle_io": "source",
            "target_handle_key": "ready",
            "target_handle_io": "target",
        }
        for s, t in pairs
    ]

    wf_uuid = "03a7393c-fdfc-4573-ad9b-580938960482"
    if OUT.exists():
        try:
            wf_uuid = json.loads(OUT.read_text(encoding="utf-8"))["data"]["workflow_uuid"]
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "target_lab_uuid": TARGET_LAB,
        "name": "金四面体合成工作流",
        "data": {
            "workflow_uuid": wf_uuid,
            "workflow_name": "金四面体合成工作流",
            "nodes": list(nodes.values()),
            "edges": edges,
        },
    }


def main() -> None:
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n, e = len(data["data"]["nodes"]), len(data["data"]["edges"])
    print(f"Wrote {OUT.name}: {n} nodes, {e} edges")


if __name__ == "__main__":
    main()
