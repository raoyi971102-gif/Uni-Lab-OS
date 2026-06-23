"""生成 SZLab 加液模块流程图 PNG。"""
from __future__ import annotations
from matplotlib.patches import FancyBboxPatch
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")


def _desktop_path() -> Path:
    candidates = [
        Path("/mnt/c/Users/元柏/Desktop"),
        Path.home() / "Desktop",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return Path.home()


def _draw_box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    color: str,
    *,
    fontsize: float = 9,
    bold: bool = False,
    edge: str = "#333333",
    style: str = "round",
) -> None:
    if style == "ellipse":
        patch = mpatches.Ellipse(
            (x + w / 2, y + h / 2),
            w,
            h,
            facecolor=color,
            edgecolor=edge,
            linewidth=1.5,
            zorder=2,
        )
    elif style == "diamond":
        cx, cy = x + w / 2, y + h / 2
        patch = plt.Polygon(
            [(cx, cy + h / 2), (cx + w / 2, cy), (cx, cy - h / 2), (cx - w / 2, cy)],
            facecolor=color,
            edgecolor=edge,
            linewidth=1.5,
            zorder=2,
        )
    else:
        patch = FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.02,rounding_size=0.15",
            facecolor=color,
            edgecolor=edge,
            linewidth=1.5,
            zorder=2,
        )
    ax.add_patch(patch)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontweight="bold" if bold else "normal",
        zorder=3,
        color="#111111",
    )


def _draw_arrow(ax, x1: float, y1: float, x2: float, y2: float, label: str = "", color: str = "#555555") -> None:
    ax.annotate(
        "",
        xy=(x2, y2),
        xytext=(x1, y1),
        arrowprops=dict(arrowstyle="->", color=color, lw=1.5),
    )
    if label:
        ax.text((x1 + x2) / 2 + 0.25, (y1 + y2) / 2, label, fontsize=8, color="#666666")


def main() -> None:
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "WenQuanYi Micro Hei",
        "Noto Sans CJK SC",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    out_path = _desktop_path() / "SZLab_加液模块流程图.png"

    fig, ax = plt.subplots(1, 1, figsize=(14, 22))
    ax.set_xlim(0, 14)
    ax.set_ylim(-2.8, 22)
    ax.axis("off")
    fig.patch.set_facecolor("#FAFAFA")

    c_start = "#4A90D9"
    c_decision = "#F5D547"
    c_process = "#FFFFFF"
    c_state = "#D4B8E8"
    c_param = "#2C5282"
    c_s09 = "#E8F4E8"
    c_placeholder = "#FFE8E8"

    ax.text(
        7,
        21.5,
        "任务类型2：加液体 — SZLab S09 加液模块",
        ha="center",
        fontsize=16,
        fontweight="bold",
        color="#1A365D",
    )
    ax.text(7, 21.0, "（对照图一流程 · 标注当前代码实现）", ha="center", fontsize=10, color="#666666")

    def section(x: float, y: float, text: str) -> None:
        ax.text(
            x,
            y,
            text,
            fontsize=11,
            fontweight="bold",
            color="#2C5282",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#E8EEF4", edgecolor="#2C5282"),
        )

    cx = 5.5
    bw = 4.0
    bh = 0.65

    section(0.3, 20.3, "阶段 A · 进料")
    _draw_box(ax, cx, 19.5, bw, bh, "安全位", c_start, style="ellipse", fontsize=10, bold=True)
    _draw_box(
        ax,
        cx - 0.5,
        18.5,
        bw + 1,
        0.9,
        "确认液体站机臂在安全位？\n(读 S09原点信号_1~4)",
        c_decision,
        style="diamond",
        fontsize=8,
    )
    _draw_arrow(ax, cx + bw / 2, 19.5, cx + bw / 2, 19.35)
    _draw_arrow(ax, cx + bw / 2, 18.5, cx + bw / 2, 18.15, label="是")
    ax.text(10.2, 18.9, "否 → 等待回安全位", fontsize=8, color="#C53030")
    _draw_arrow(ax, 10.0, 18.9, 10.0, 19.5, color="#C53030")
    _draw_arrow(ax, 10.0, 19.5, cx + bw, 19.8, color="#C53030")

    _draw_box(
        ax,
        cx,
        17.3,
        bw,
        0.85,
        "机械臂：固体称量 → 液体站天平\nrobot.place_sample_to_liquid_station  [待实现]",
        c_placeholder,
        fontsize=8,
    )
    _draw_arrow(ax, cx + bw / 2, 17.3, cx + bw / 2, 16.9)

    _draw_box(ax, cx, 16.2, bw, 0.7, "prepare_liquid_station\n申请空闲加液工位 (1~5)", c_process, fontsize=8)
    _draw_arrow(ax, cx + bw / 2, 16.2, cx + bw / 2, 15.8)

    _draw_box(ax, cx, 15.1, bw, 0.7, "bind_sample_to_station\n绑定 sample_id ↔ station", c_process, fontsize=8)
    _draw_arrow(ax, cx + bw / 2, 15.1, cx + bw / 2, 14.8)

    _draw_box(ax, cx + 0.5, 14.2, 3.0, 0.6, "瓶样（固体加样后）", c_state, style="ellipse", fontsize=9)

    section(0.3, 13.5, "阶段 B · 重复 N 次加液体 (N≤5)")
    loop_rect = FancyBboxPatch(
        (0.8, 4.8),
        12.4,
        8.4,
        boxstyle="round,pad=0.05,rounding_size=0.3",
        facecolor="#F7FAFC",
        edgecolor="#2C5282",
        linewidth=2,
        linestyle="--",
        zorder=1,
    )
    ax.add_patch(loop_rect)
    ax.text(
        7,
        13.0,
        "循环体：每次调用 add_liquid / run_liquid_workflow",
        ha="center",
        fontsize=9,
        color="#2C5282",
        style="italic",
    )

    steps = [
        (12.5, "移液枪 → TIP区，联枪头", "tip_box, tip_index"),
        (11.5, "移液枪 → 取液区", ""),
        (10.5, "移液枪清洗", "bottle_row/col · 已开盖液体"),
        (9.5, "移液枪吸液", "volume → S09取液体量"),
        (8.5, "移液枪 → 天平区", ""),
        (7.5, "注入瓶样", "station · 瓶样(加样中)"),
        (6.5, "移液枪 → TIP区", ""),
        (5.5, "脱枪头", "已使用 TIP 头架"),
    ]
    sw = 3.8
    for index, (y, main, param) in enumerate(steps):
        _draw_box(ax, 1.5, y, sw, 0.55, main, c_process, fontsize=8)
        if param:
            _draw_box(ax, 5.6, y + 0.05, 3.2, 0.45, param, c_param, fontsize=7, edge="#2C5282")
        if index < len(steps) - 1:
            _draw_arrow(ax, 1.5 + sw / 2, y, 1.5 + sw / 2, y - 0.45)
        else:
            _draw_arrow(ax, 1.5 + sw / 2, y, 1.5 + sw / 2, y - 0.55)

    _draw_box(
        ax,
        9.2,
        5.0,
        3.8,
        2.8,
        "★ 一次 S09 工艺调用 ★\n\n写 PLC 参数后脉冲执行\nS09试剂盒工位编号\nS09试剂TIP编号 / S09液体瓶编号\nS09取液体量 / S09放液量\nS09工艺选择 → S09工艺执行\n等待完成 → 读 S09天平反馈",
        c_s09,
        fontsize=7.5,
        bold=True,
        edge="#276749",
    )
    _draw_arrow(ax, 5.5, 5.5, 9.2, 6.5)

    _draw_box(ax, cx, 4.2, bw, 0.6, "i < N ？继续下一次加液", c_decision, style="diamond", fontsize=8)
    _draw_arrow(ax, 1.5 + sw / 2, 5.5, cx + bw / 2, 4.8)
    ax.text(10.5, 4.5, "是 → 回到联枪头", fontsize=8, color="#276749")
    _draw_arrow(ax, 11.5, 4.5, 11.5, 12.5, color="#276749")
    _draw_arrow(ax, 11.5, 12.5, 3.4, 12.5, color="#276749")
    _draw_arrow(ax, 3.4, 12.5, 3.4, 12.75, color="#276749")
    _draw_arrow(ax, cx + bw / 2, 4.2, cx + bw / 2, 3.7, label="否")

    section(0.3, 3.3, "阶段 C · 出料")
    _draw_box(ax, cx, 2.7, bw, bh, "在安全位", c_start, style="ellipse", fontsize=10)
    _draw_box(ax, cx - 0.5, 1.7, bw + 1, 0.9, "确认液体站机臂在安全位？", c_decision, style="diamond", fontsize=8)
    _draw_arrow(ax, cx + bw / 2, 2.7, cx + bw / 2, 2.15)
    _draw_box(
        ax,
        cx,
        0.7,
        bw,
        0.85,
        "机械臂：液体站天平 → 溶剂站\nrobot.pick_sample_from_liquid_station  [待实现]",
        c_placeholder,
        fontsize=8,
    )
    _draw_arrow(ax, cx + bw / 2, 1.7, cx + bw / 2, 1.55, label="是")
    _draw_box(ax, cx, -0.1, bw, 0.65, "release_station / run_liquid_workflow\n释放工位绑定", c_process, fontsize=8)
    _draw_arrow(ax, cx + bw / 2, 0.7, cx + bw / 2, 0.55)
    _draw_box(ax, cx + 0.5, -0.85, 3.0, 0.55, "瓶样（完成液体加样）", c_state, style="ellipse", fontsize=9)
    _draw_arrow(ax, cx + bw / 2, -0.1, cx + bw / 2, -0.55)

    legend_y = -1.6
    for index, (color, label) in enumerate(
        [
            (c_start, "起始/安全态"),
            (c_decision, "判断"),
            (c_process, "软件动作"),
            (c_state, "瓶样状态"),
            (c_param, "参数/物料"),
            (c_s09, "S09 PLC工艺"),
            (c_placeholder, "待实现"),
        ]
    ):
        lx = 0.5 + index * 1.9
        ax.add_patch(
            FancyBboxPatch(
                (lx, legend_y),
                0.35,
                0.3,
                boxstyle="round,pad=0.02",
                facecolor=color,
                edgecolor="#333333",
                linewidth=1,
            )
        )
        ax.text(lx + 0.5, legend_y + 0.15, label, fontsize=7, va="center")

    ax.text(
        7,
        -2.3,
        "模块内三区：TIP区(tip_box 1/2) · 取液区(试剂瓶4×5) · 天平区(加液工位1~5)",
        ha="center",
        fontsize=9,
        color="#555555",
        style="italic",
    )

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="#FAFAFA", pad_inches=0.3)
    print(f"Saved: {out_path}")
    print(f"Exists: {out_path.exists()}")


if __name__ == "__main__":
    main()
