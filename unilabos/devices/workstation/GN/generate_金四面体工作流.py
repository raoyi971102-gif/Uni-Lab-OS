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

# 图2 横向布局常量（支线内左右串联，支线间上下分层）
ENTRY_X = -150.0
STEP_X0 = 250.0
STEP_DX = 500.0
LANE_Y0 = 80.0
LANE_DY = 400.0

OPC: dict[str, dict] = {
    "2": {"device": "gn_system_control", "action": "auto-execute_command",
          "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "4": {"device": "gn_quick_carrier_exchange", "action": "auto-execute_command",
          "param": {"cmd_type": 11, "x_pos": 0, "top_z_pos": -830, "take_z_pos": 1800,
                    "push_pos": 240, "push_z_pos": 0, "x_speed": 300, "z1_speed": 100,
                    "z2_speed": 100, "push_speed": 50, "z3_speed": 0}, "disabled": False},
    "5": {"device": "gn_quick_carrier_exchange", "action": "auto-execute_command",
          "param": {"cmd_type": 13, "stir_rpm": 1500, "stir_temp": 30, "stir_time_minutes": 1},
          "disabled": False},
    "6": {"device": "gn_system_control", "action": "auto-execute_command",
          "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "7": {"device": "gn_quick_carrier_exchange", "action": "auto-execute_command",
          "param": {"cmd_type": 12, "x_pos": 1810, "top_z_pos": 0, "take_z_pos": 1600,
                    "push_pos": 240, "push_z_pos": 2100, "x_speed": 300, "z1_speed": 100,
                    "z2_speed": 100, "push_speed": 50, "z3_speed": 100}, "disabled": False},
    "8": {"device": "gn_standard_oven", "action": "auto-execute_command",
          "param": {"cmd_type": 1, "temperature": 30, "hours": 4, "minutes": 30, "wait": True},
          "disabled": False},
    "9": {"device": "gn_robotic_arm", "action": "auto-execute_command",
          "param": {"cmd_type": 3, "module_no": 6, "stack": 2, "x_pos": 3274,
                    "x_speed": 300, "pick_place": 1}, "disabled": False},
    "10": {"device": "gn_solid_weighing", "action": "auto-execute_command",
           "param": {"cmd_type": 11, "x_pos": -300, "y_pos": 700, "material_z_pos": 40000,
                     "gripper_z_pos": 0, "door_pos": 3700, "volune_weight": 50,
                     "x_speed": 500, "y_speed": 500, "door_speed": 150, "timeout": 600},
           "disabled": False},
    "11": {"device": "gn_centrifuge_tube_liquid_handling", "action": "auto-execute_command",
           "param": {"cmd_type": 35, "m_pos": 300, "m_speed": 300, "ultrasound_time": 5},
           "disabled": False},
    "12": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "13": {"device": "gn_solid_weighing", "action": "auto-execute_command",
           "param": {"cmd_type": 11, "x_pos": -300, "y_pos": 700, "material_z_pos": 40000,
                     "gripper_z_pos": 0, "door_pos": 3700, "volune_weight": 30,
                     "x_speed": 500, "y_speed": 500, "door_speed": 150, "timeout": 600},
           "disabled": False},
    "14": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "15": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "16": {"device": "gn_quick_carrier_exchange", "action": "auto-execute_command",
           "param": {"cmd_type": 13, "stir_rpm": 300, "stir_temp": 28, "stir_time_minutes": 2},
           "disabled": False},
    "17": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "18": {"device": "gn_quick_carrier_exchange", "action": "auto-execute_command",
           "param": {"cmd_type": 13, "stir_rpm": 300, "stir_temp": 28, "stir_time_minutes": 60},
           "disabled": False},
    "19": {"device": "gn_robotic_arm", "action": "auto-execute_command",
           "param": {"cmd_type": 3, "module_no": 3, "stack": 1, "x_pos": 0,
                     "x_speed": 300, "pick_place": 1}, "disabled": False},
    "20": {"device": "gn_centrifuge", "action": "auto-execute_command",
           "param": {"cmd_type": 5, "y_pos": -1700, "z_pos": 1000, "inner_z_pos": 3450,
                     "rpm": 0, "time_minutes": 0, "y_speed": 300, "z_speed": 300, "plate_no": 2},
           "disabled": False},
    "21": {"device": "gn_centrifuge", "action": "auto-execute_command",
           "param": {"cmd_type": 6, "rpm": 4000, "time_minutes": 10, "plate_no": 2}, "disabled": False},
    "22": {"device": "gn_centrifuge", "action": "auto-execute_command",
           "param": {"cmd_type": 7, "y_pos": -1700, "z_pos": 100, "inner_z_pos": 3450,
                     "y_speed": 300, "z_speed": 300, "plate_no": 2}, "disabled": False},
    "23": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "24": {"device": "gn_centrifuge_tube_liquid_handling", "action": "auto-execute_command",
           "param": {"cmd_type": 34, "mix_counts": 10}, "disabled": False},
    "25": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "26": {"device": "gn_solid_weighing", "action": "auto-execute_command",
           "param": {"cmd_type": 11, "x_pos": -300, "y_pos": 700, "material_z_pos": 40000,
                     "gripper_z_pos": 0, "door_pos": 3700, "volune_weight": 176,
                     "x_speed": 500, "y_speed": 500, "door_speed": 150, "timeout": 900},
           "disabled": False},
    "27": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "28": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "29": {"device": "gn_quick_carrier_exchange", "action": "auto-execute_command",
           "param": {"cmd_type": 13, "stir_rpm": 300, "stir_temp": 30, "stir_time_minutes": 5},
           "disabled": False},
    "30": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "31": {"device": "gn_standard_oven", "action": "auto-execute_command",
           "param": {"cmd_type": 1, "temperature": 29, "hours": 0, "minutes": 10, "wait": True},
           "disabled": False},
    "32": {"device": "gn_centrifuge", "action": "auto-execute_command",
           "param": {"cmd_type": 6, "rpm": 700, "time_minutes": 15, "plate_no": 2}, "disabled": False},
    "33": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": False, "timeout": 10}, "disabled": True},
    "34": {"device": "gn_system_control", "action": "auto-execute_command",
           "param": {"cmd_type": 2, "wait": True}, "disabled": False},
    "take7": {"device": "gn_robotic_arm", "action": "auto-execute_command",
              "param": {"cmd_type": 3, "module_no": 4, "stack": 1, "x_pos": -3926,
                        "x_speed": 300, "pick_place": 1}, "disabled": False},
    "pass": {"device": "gn_system_control", "action": "auto-execute_command",
             "param": {"cmd_type": 2, "wait": False, "timeout": 5}, "disabled": True},
}
for _alias, _base in (
    ("23-1", "23"), ("23-2", "23"),
    ("33-1", "33"), ("33-2", "33"), ("33-3", "33"),
):
    OPC[_alias] = copy.deepcopy(OPC[_base])

# 图2 七列：每列 (node_id, footer, opc_key)；仅保留流程图关键步骤
LANES: list[tuple[int, list[tuple[str, str, str]]]] = [
    # 列1 种子 — 蓝色框：取96孔深孔板
    (0, [
        ("B1-01", "取96孔深孔板", "4"),
        ("B1-02", "转移至移液工作站", "pass"),
        ("B1-03", "加液(800µl氯金酸+800µl CTAB+300µl NaBH4)", "6"),
        ("B1-04", "吹打混匀", "pass"),
        ("B1-05", "转移至磁力搅拌器", "7"),
        ("B1-06", "磁力搅拌1500rpm/30s", "5"),
        ("B1-07", "静置28-32℃/4.5h", "8"),
        ("B1-08", "得到种子溶液", "pass"),
    ]),  # B2→B1-03 NaBH4；B1-08→B4-04 种子28.6µl
    # 列2 NaBH4 — 蓝色框：取储液槽
    (1, [
        ("B2-01", "取储液槽", "9"),
        ("B2-02", "加NaBH4粉末", "10"),
        ("B2-03", "加冰水稀释+超声解离", "11"),
        ("B2-04", "得到NaBH4溶液", "12"),
    ]),
    # 列3 AA低 — 蓝色框：取储液槽
    (2, [
        ("B3-01", "取储液槽", "9"),
        ("B3-02", "加抗坏血酸粉末", "13"),
        ("B3-03", "加水稀释+吹打混匀", "pass"),
        ("B3-04", "得到抗坏血酸溶液", "14"),
    ]),
    # 列4 金球 — 蓝色框：取96孔板（B3→第一次加液429µl AA；B1→第二次加液28.6µl种子）
    (3, [
        ("B4-01", "取96孔板", "pass"),
        ("B4-02", "第一次加液(571µl CTAC+571µl氯金酸+429µl抗坏血酸)", "15"),
        ("B4-03", "磁力搅拌300rpm/2min", "16"),
        ("B4-04", "第二次加液(28.6µl种子溶液)", "17"),
        ("B4-05", "磁力搅拌28℃/1h", "18"),
        ("B4-06", "转移至离心机", "19"),
        ("B4-07", "离心4000rpm", "21"),
        ("B4-08", "第1次吸上清+20mM CTAC清洗", "23-1"),
        ("B4-09", "第2次吸上清+20mM CTAC清洗", "23-2"),
        ("B4-10", "得到金球溶液", "pass"),
    ]),
    # 列5 反应试剂配制 — 蓝色框：取储液槽（列内终点：加2.5mM氯金酸）
    (4, [
        ("B5-01", "取储液槽", "9"),
        ("B5-02", "转移至离心管液体处理设备", "pass"),
        ("B5-03", "加液(CTAC原液+CTAB原液+水)", "24"),
        ("B5-04", "加2.5mM氯金酸", "25"),
    ]),
    # 列6 金四面体 — 蓝色框：取96孔板（B5/B7/B4→第二次加液）
    (5, [
        ("B6-01", "取96孔板", "take7"),
        ("B6-02", "第一次加液", "28"),
        ("B6-03", "磁力搅拌30℃/300rpm", "29"),
        ("B6-04", "第二次加液(混合液0.3~1.5ml/h+390µl高浓AA+39µl金球)", "30"),
        ("B6-05", "静置29℃/10min", "31"),
        ("B6-06", "离心700rpm/15min", "32"),
        ("B6-07", "第1次去上清+CTAC清洗", "33-1"),
        ("B6-08", "第2次去上清+CTAC清洗", "33-2"),
        ("B6-09", "第3次去上清+CTAC清洗", "33-3"),
    ]),
    # 列7 AA高 — 蓝色框：取样品瓶(40ml)
    (6, [
        ("B7-01", "取样品瓶(40ml)", "26"),
        ("B7-02", "加176.12mg抗坏血酸+10ml水", "pass"),
        ("B7-03", "得到高浓AA溶液", "27"),
    ]),
]

# 附支路：图2左侧氯金酸稀释
AUX = ("AUX-01", "氯金酸原液加冰水稀释", "2")

# 汇合后最终节点（类比 Untitled-2 右侧 auto-run_protocol）
FINISH = ("DONE", "得到金四面体", "34")

# 跨列汇合边
CROSS_EDGES = [
    ("AUX-01", "B1-03"),
    ("B2-04", "B1-03"),
    ("B3-04", "B4-02"),   # 429µl 抗坏血酸 → 金球第一次加液
    ("B1-08", "B4-04"),   # 28.6µl 种子 → 金球第二次加液
    ("B5-04", "B6-04"),   # 混合液 0.3~1.5 ml/h → 金四面体第二次加液
    ("B7-03", "B6-04"),   # 390µl 高浓 AA → 金四面体第二次加液
    ("B4-10", "B6-04"),   # 39µl 金球 → 金四面体第二次加液
]

PRCXI_FOOTER = {
    "2": "[PRCXI] 氯金酸原液加冰水稀释",
    "6": "[PRCXI] 8通道加800µl氯金酸+800µl CTAB+300µl NaBH4",
    "12": "[PRCXI] 分装NaBH4(96孔300µl)",
    "14": "[PRCXI] 抗坏血酸溶液分装(供429µl)",
    "15": "[PRCXI] 第一次加液:571µl CTAC+571µl HAuCl4+429µl抗坏血酸",
    "17": "[PRCXI] 第二次加液:28.6µl种子溶液",
    "23-1": "[PRCXI] 第1次吸上清+20mM CTAC清洗",
    "23-2": "[PRCXI] 第2次吸上清+20mM CTAC清洗",
    "24": "[PRCXI] 加CTAC+CTAB+水",
    "25": "[PRCXI] 加2.5mM氯金酸",
    "27": "[PRCXI] 高浓AA溶液(供390µl)",
    "28": "[PRCXI] 第一次加液(列6列内)",
    "30": "[PRCXI] 第二次加液:混合液0.3~1.5ml/h+390µl高浓AA+39µl金球",
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
        "resource_name": device,
    }


def chain(ids: list[str]) -> list[tuple[str, str]]:
    return [(ids[i], ids[i + 1]) for i in range(len(ids) - 1)]


def build() -> dict:
    nodes: dict[str, dict] = {}
    layout: dict[str, tuple[float, float]] = {}

    entry_y = lane_y(3)
    nodes["E0"] = make_node("人工准备耗材至相应位置", "pass")
    nodes["E0"]["param"] = {"cmd_type": 2, "wait": True, "timeout": 10}
    nodes["E0"]["disabled"] = True
    nodes["E1"] = make_node("机械臂转移物料", "pass")
    nodes["E1"]["param"] = {"cmd_type": 2, "wait": True, "timeout": 30}
    nodes["E1"]["disabled"] = False
    layout["E0"] = (ENTRY_X, entry_y - 120)
    layout["E1"] = (ENTRY_X, entry_y)

    # 附支路：氯金酸稀释（列1支线上方，靠近 B1-03）
    aid, afooter, aopc = AUX
    nodes[aid] = make_node(afooter, aopc)
    layout[aid] = (step_x(2) - 80, lane_y(0) - 140)

    # 七条支线：每条自左向右，纵向分层
    for col, steps in LANES:
        y = lane_y(col)
        for step, (nid, footer, okey) in enumerate(steps):
            nodes[nid] = make_node(footer, okey)
            layout[nid] = (step_x(step), y)

    # 最终完成节点（列6 支线末端右侧）
    fid, ffooter, fopc = FINISH
    nodes[fid] = make_node(ffooter, fopc)
    col6_steps = next(st for c, st in LANES if c == 5)
    layout[fid] = (step_x(len(col6_steps)) + 80, lane_y(5))

    for nid, (x, y) in layout.items():
        nodes[nid]["pose"]["position"]["x"] = round(x, 2)
        nodes[nid]["pose"]["position"]["y"] = round(y, 2)

    pairs: list[tuple[str, str]] = [("E0", "E1"), ("E1", AUX[0])]
    for col, steps in LANES:
        pairs.append(("E1", steps[0][0]))
        pairs += chain([s[0] for s in steps])
    pairs += CROSS_EDGES
    pairs.append(("B6-09", FINISH[0]))

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
