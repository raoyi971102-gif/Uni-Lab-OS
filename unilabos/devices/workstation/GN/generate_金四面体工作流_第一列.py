#!/usr/bin/env python3
"""从金四面体合成工作流提取第一列（种子溶液），生成独立 JSON。"""
from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "generate_金四面体工作流.py"
OUT = HERE / "金四面体合成工作流_第一列.json"

spec = importlib.util.spec_from_file_location("wf_main", MAIN)
wf_main = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(wf_main)

TARGET_LAB = wf_main.TARGET_LAB
OPC = wf_main.OPC
make_node = wf_main.make_node
chain = wf_main.chain
STEP_X0 = wf_main.STEP_X0
STEP_DX = wf_main.STEP_DX
ENTRY_X = wf_main.ENTRY_X

LANE_Y = 200.0
AUX_Y = 40.0

COL1_STEPS: list[tuple[str, str, str]] = [
    ("B1-01", "机械臂取96孔深孔板至9320移液工作站（机械臂）", "b1-to-9320"),
    ("B1-02", "移液工作站对其进行加液（9320）", "b1-prcxi-add"),
    ("B1-03", "9320对其进行吹打混匀（9320）", "b1-prcxi-mix"),
    ("B1-04", "得到混合溶液A（禁用）", "b1-mix-a"),
    ("B1-05", "机械臂将其转移到磁力搅拌器（机械臂）", "b1-to-stir"),
    ("B1-06", "磁力搅拌/混合液A（磁力搅拌器）", "b1-stir"),
    ("B1-07", "静置，得到种子溶液（禁用）", "b1-seed"),
]

AUX_STEPS: list[tuple[str, str, str]] = [
    ("AUX-01", "氯金酸原液瓶开盖", "aux-lid-open"),
    ("AUX-02", "氯金酸原液加冰水稀释", "aux-haucl-dilute"),
    ("AUX-03", "氯金酸原液瓶关盖", "aux-lid-close"),
]


def build() -> dict:
    nodes: dict[str, dict] = {}
    layout: dict[str, tuple[float, float]] = {}

    nodes["E0"] = make_node("人工准备耗材至相应位置", "manual-prepare")
    nodes["E1"] = make_node("机械臂转移物料", "manual-prepare")
    nodes["E1"]["disabled"] = False
    layout["E0"] = (ENTRY_X, LANE_Y - 80)
    layout["E1"] = (ENTRY_X, LANE_Y)

    for step, (nid, footer, okey) in enumerate(AUX_STEPS):
        nodes[nid] = make_node(footer, okey)
        layout[nid] = (STEP_X0 + step * STEP_DX - 80, AUX_Y)

    for step, (nid, footer, okey) in enumerate(COL1_STEPS):
        nodes[nid] = make_node(footer, okey)
        layout[nid] = (STEP_X0 + step * STEP_DX, LANE_Y)

    nodes["DONE"] = make_node("第一列完成：种子溶液", "workflow-done")
    layout["DONE"] = (STEP_X0 + len(COL1_STEPS) * STEP_DX, LANE_Y)

    for nid, (x, y) in layout.items():
        nodes[nid]["pose"]["position"]["x"] = round(x, 2)
        nodes[nid]["pose"]["position"]["y"] = round(y, 2)

    pairs: list[tuple[str, str]] = [
        ("E0", "E1"),
        ("E1", AUX_STEPS[0][0]),
        ("E1", COL1_STEPS[0][0]),
    ]
    pairs += chain([s[0] for s in AUX_STEPS])
    pairs += chain([s[0] for s in COL1_STEPS])
    pairs += [
        (AUX_STEPS[-1][0], COL1_STEPS[1][0]),  # 氯金酸稀释完成 → 9320 加液
        (COL1_STEPS[-1][0], "DONE"),
    ]

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

    wf_uuid = str(uuid.uuid4())
    if OUT.exists():
        try:
            wf_uuid = json.loads(OUT.read_text(encoding="utf-8"))["data"]["workflow_uuid"]
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "target_lab_uuid": TARGET_LAB,
        "name": "金四面体合成工作流_第一列",
        "data": {
            "workflow_uuid": wf_uuid,
            "workflow_name": "金四面体合成工作流_第一列（种子溶液）",
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
