"""AI4C 台面 / 设备布局快速编辑器。

默认编辑同目录下的：
- AI4C_station.json  图文件设备节点的 position / pose.size
- AI4C_layout.json   台面整体尺寸与各工位坐标、大小

用法：
    python edit_layout.py                  # 打开图形界面
    python edit_layout.py list             # 列出全部设备/工位
    python edit_layout.py get PRCXI
    python edit_layout.py set PRCXI --x 0 --y 240 --width 550 --height 400
    python edit_layout.py set 移液站 --x 740 --y 950
    python edit_layout.py preview          # 终端俯视图
    python edit_layout.py --graph AI4C.json gui
"""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
DEFAULT_GRAPH = HERE / "AI4C_station.json"
DEFAULT_LAYOUT = HERE / "AI4C_layout.json"

PLACEHOLDER_SIZE = 100.0
NUMERIC_FIELDS = ("x", "y", "z", "width", "height", "depth")


@dataclass
class LayoutItem:
    source: str  # graph | deck
    id: str
    name: str
    kind: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    width: float = 0.0
    height: float = 0.0
    depth: float = 0.0
    extra: str = ""

    def display_size(self) -> Tuple[float, float]:
        width = self.width if self.width > 0 else PLACEHOLDER_SIZE
        height = self.height if self.height > 0 else PLACEHOLDER_SIZE
        return width, height


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _maybe_int(value: float) -> Any:
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, 4)


def _fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any, backup: bool = True) -> None:
    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")


def _node_position(node: Dict[str, Any]) -> Dict[str, Any]:
    pose = node.get("pose") if isinstance(node.get("pose"), dict) else {}
    pose_pos = pose.get("position") if isinstance(pose.get("position"), dict) else {}
    raw_pos = node.get("position") if isinstance(node.get("position"), dict) else {}
    nested = raw_pos.get("position") if isinstance(raw_pos.get("position"), dict) else {}
    if "x" in pose_pos or "y" in pose_pos or "z" in pose_pos:
        return pose_pos
    if "x" in nested or "y" in nested or "z" in nested:
        return nested
    return raw_pos


def _node_size(node: Dict[str, Any]) -> Dict[str, Any]:
    pose = node.get("pose") if isinstance(node.get("pose"), dict) else {}
    size = pose.get("size") if isinstance(pose.get("size"), dict) else {}
    if size:
        return size
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    if any(k in config for k in ("size_x", "size_y", "size_z")):
        return {
            "width": config.get("size_x", 0),
            "height": config.get("size_y", 0),
            "depth": config.get("size_z", 0),
        }
    return {}


def item_from_graph_node(node: Dict[str, Any]) -> LayoutItem:
    pos = _node_position(node)
    size = _node_size(node)
    node_id = str(node.get("id") or "")
    return LayoutItem(
        source="graph",
        id=node_id,
        name=str(node.get("name") or node_id),
        kind=str(node.get("type") or node.get("class") or "device"),
        x=_as_float(pos.get("x")),
        y=_as_float(pos.get("y")),
        z=_as_float(pos.get("z")),
        width=_as_float(size.get("width")),
        height=_as_float(size.get("height")),
        depth=_as_float(size.get("depth")),
        extra=str(node.get("class") or ""),
    )


def apply_item_to_graph_node(node: Dict[str, Any], item: LayoutItem) -> None:
    node["name"] = item.name
    pos = node.setdefault("position", {})
    if not isinstance(pos, dict):
        pos = {}
        node["position"] = pos
    if isinstance(pos.get("position"), dict) and "x" in pos["position"]:
        pos = pos["position"]
    pos["x"] = _maybe_int(item.x)
    pos["y"] = _maybe_int(item.y)
    pos["z"] = _maybe_int(item.z)

    pose = node.get("pose")
    if not isinstance(pose, dict):
        pose = {}
        node["pose"] = pose
    pose["position"] = {"x": pos["x"], "y": pos["y"], "z": pos["z"]}
    had_size = isinstance(pose.get("size"), dict) or any(
        key in (node.get("config") or {}) for key in ("size_x", "size_y", "size_z")
    )
    if had_size or item.width or item.height or item.depth:
        size = pose.setdefault("size", {})
        if not isinstance(size, dict):
            size = {}
            pose["size"] = size
        size["width"] = _maybe_int(item.width)
        size["height"] = _maybe_int(item.height)
        size["depth"] = _maybe_int(item.depth)
        config = node.get("config")
        if isinstance(config, dict):
            if "size_x" in config:
                config["size_x"] = size["width"]
            if "size_y" in config:
                config["size_y"] = size["height"]
            if "size_z" in config:
                config["size_z"] = size["depth"]


def item_from_warehouse(name: str, data: Dict[str, Any]) -> LayoutItem:
    return LayoutItem(
        source="deck",
        id=name,
        name=name,
        kind="warehouse",
        x=_as_float(data.get("x")),
        y=_as_float(data.get("y")),
        z=_as_float(data.get("z")),
        width=_as_float(data.get("width")),
        height=_as_float(data.get("height")),
        depth=_as_float(data.get("depth")),
        extra=str(data.get("factory") or ""),
    )


def apply_item_to_warehouse(data: Dict[str, Any], item: LayoutItem) -> None:
    data["x"] = _maybe_int(item.x)
    data["y"] = _maybe_int(item.y)
    data["z"] = _maybe_int(item.z)
    data["width"] = _maybe_int(item.width)
    data["height"] = _maybe_int(item.height)
    data["depth"] = _maybe_int(item.depth)


class LayoutStore:
    """同时管理图文件设备与台面工位。"""

    def __init__(self, graph_path: Path, layout_path: Path) -> None:
        self.graph_path = graph_path
        self.layout_path = layout_path
        self.graph_data: Dict[str, Any] = {"nodes": [], "links": []}
        self.layout_data: Dict[str, Any] = {"deck": {}, "warehouses": {}}
        self.reload()

    def reload(self) -> None:
        if self.graph_path.exists():
            loaded = load_json(self.graph_path)
            self.graph_data = loaded if isinstance(loaded, dict) else {"nodes": loaded}
        else:
            self.graph_data = {"nodes": [], "links": []}
        if self.layout_path.exists():
            loaded = load_json(self.layout_path)
            self.layout_data = loaded if isinstance(loaded, dict) else {}
        else:
            self.layout_data = {"deck": {}, "warehouses": {}}
        self.layout_data.setdefault("deck", {})
        self.layout_data.setdefault("warehouses", {})

    @property
    def graph_nodes(self) -> List[Dict[str, Any]]:
        nodes = self.graph_data.get("nodes")
        return nodes if isinstance(nodes, list) else []

    @property
    def warehouses(self) -> Dict[str, Any]:
        warehouses = self.layout_data.get("warehouses")
        return warehouses if isinstance(warehouses, dict) else {}

    def graph_items(self) -> List[LayoutItem]:
        return [item_from_graph_node(node) for node in self.graph_nodes if isinstance(node, dict) and node.get("id")]

    def deck_items(self) -> List[LayoutItem]:
        deck_cfg = self.layout_data.get("deck") if isinstance(self.layout_data.get("deck"), dict) else {}
        size = deck_cfg.get("size") if isinstance(deck_cfg.get("size"), dict) else {}
        origin = deck_cfg.get("origin") if isinstance(deck_cfg.get("origin"), dict) else {}
        items = [
            LayoutItem(
                source="deck",
                id="__deck__",
                name=str(deck_cfg.get("name") or "AI4C_deck"),
                kind="deck",
                x=_as_float(origin.get("x")),
                y=_as_float(origin.get("y")),
                z=_as_float(origin.get("z")),
                width=_as_float(size.get("width"), 1217.0),
                height=_as_float(size.get("height"), 1580.0),
                depth=_as_float(size.get("depth"), 2670.0),
                extra="台面整体",
            )
        ]
        for name, data in self.warehouses.items():
            if isinstance(data, dict):
                items.append(item_from_warehouse(str(name), data))
        return items

    def all_items(self) -> List[LayoutItem]:
        return self.graph_items() + self.deck_items()

    def find(self, item_id: str, source: str = "auto") -> LayoutItem:
        candidates: List[LayoutItem] = []
        if source in ("auto", "graph"):
            candidates.extend(self.graph_items())
        if source in ("auto", "deck"):
            candidates.extend(self.deck_items())
        exact = [item for item in candidates if item.id == item_id]
        if not exact:
            exact = [item for item in candidates if item.name == item_id]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            sources = "、".join(item.source for item in exact)
            raise KeyError(f"id={item_id!r} 在 {sources} 中都存在，请用 --source graph 或 --source deck 指定")
        names = [item.id for item in self.all_items()]
        hint = difflib.get_close_matches(item_id, names, n=3, cutoff=0.3)
        suffix = f"，相近 id：{', '.join(hint)}" if hint else ""
        raise KeyError(f"未找到 {item_id!r}{suffix}")

    def update_item(self, item: LayoutItem) -> None:
        if item.source == "graph":
            for node in self.graph_nodes:
                if isinstance(node, dict) and node.get("id") == item.id:
                    apply_item_to_graph_node(node, item)
                    if item.id in ("AI4C_station", "AI4C_deck"):
                        self._sync_deck_size_from_item(item)
                    return
            raise KeyError(f"图文件中没有节点 {item.id!r}")
        if item.id == "__deck__":
            deck_cfg = self.layout_data.setdefault("deck", {})
            deck_cfg["name"] = item.name
            deck_cfg["origin"] = {
                "x": _maybe_int(item.x),
                "y": _maybe_int(item.y),
                "z": _maybe_int(item.z),
            }
            deck_cfg["size"] = {
                "width": _maybe_int(item.width),
                "height": _maybe_int(item.height),
                "depth": _maybe_int(item.depth),
            }
            self._sync_station_size_from_deck(item)
            return
        warehouse = self.warehouses.get(item.id)
        if not isinstance(warehouse, dict):
            raise KeyError(f"台面布局中没有工位 {item.id!r}")
        apply_item_to_warehouse(warehouse, item)

    def _sync_deck_size_from_item(self, item: LayoutItem) -> None:
        if item.width <= 0 and item.height <= 0:
            return
        deck_cfg = self.layout_data.setdefault("deck", {})
        size = deck_cfg.setdefault("size", {})
        if item.width > 0:
            size["width"] = _maybe_int(item.width)
        if item.height > 0:
            size["height"] = _maybe_int(item.height)
        if item.depth > 0:
            size["depth"] = _maybe_int(item.depth)

    def _sync_station_size_from_deck(self, item: LayoutItem) -> None:
        for node_id in ("AI4C_station", "AI4C_deck"):
            for node in self.graph_nodes:
                if isinstance(node, dict) and node.get("id") == node_id:
                    synced = item_from_graph_node(node)
                    synced.width = item.width
                    synced.height = item.height
                    synced.depth = item.depth
                    apply_item_to_graph_node(node, synced)

    def save(self, backup: bool = True) -> None:
        save_json(self.graph_path, self.graph_data, backup=backup)
        save_json(self.layout_path, self.layout_data, backup=backup)

    def deck_size(self) -> Tuple[float, float]:
        for item in self.deck_items():
            if item.id == "__deck__":
                return max(item.width, 1.0), max(item.height, 1.0)
        return 1217.0, 1580.0


def print_table(items: Sequence[LayoutItem], title: str) -> None:
    if not items:
        print(f"\n[{title}] （空）")
        return
    rows = [
        (
            item.id if item.id != "__deck__" else item.name,
            item.kind,
            _fmt(item.x),
            _fmt(item.y),
            _fmt(item.z),
            _fmt(item.width),
            _fmt(item.height),
            _fmt(item.depth),
            item.extra,
        )
        for item in items
    ]
    headers = ("ID", "类型", "X", "Y", "Z", "宽", "高", "深", "备注")
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    print(f"\n[{title}]")
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def print_item(item: LayoutItem) -> None:
    source_label = "图文件设备" if item.source == "graph" else "台面工位"
    print(f"{source_label}  {item.id}")
    print(f"  name   : {item.name}")
    print(f"  type   : {item.kind}")
    print(f"  pos    : x={_fmt(item.x)}  y={_fmt(item.y)}  z={_fmt(item.z)}")
    print(f"  size   : width={_fmt(item.width)}  height={_fmt(item.height)}  depth={_fmt(item.depth)}")
    if item.extra:
        print(f"  extra  : {item.extra}")


def ascii_preview(store: LayoutStore, cols: int = 64, rows: int = 28) -> str:
    deck_w, deck_h = store.deck_size()
    canvas = [[" " for _ in range(cols)] for _ in range(rows)]
    labels: List[Tuple[int, int, str]] = []

    def to_cell(x: float, y: float) -> Tuple[int, int]:
        col = int(max(0, min(cols - 1, round(x / deck_w * (cols - 1)))))
        row = int(max(0, min(rows - 1, round((1.0 - y / deck_h) * (rows - 1)))))
        return col, row

    for item in store.deck_items():
        if item.id == "__deck__":
            continue
        width, height = item.display_size()
        x0, y0 = to_cell(item.x, item.y)
        x1, y1 = to_cell(item.x + width, item.y + height)
        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        for row in range(top, bottom + 1):
            for col in range(left, right + 1):
                edge = row in (top, bottom) or col in (left, right)
                canvas[row][col] = "#" if edge else "."
        labels.append((left, top, item.id[:6]))

    for col, row, text in labels:
        for i, ch in enumerate(text):
            if 0 <= col + i < cols:
                canvas[row][col + i] = ch

    border = "+" + "-" * cols + "+"
    body = "\n".join("|" + "".join(row) + "|" for row in canvas)
    return f"台面 {_fmt(deck_w)} x {_fmt(deck_h)}\n{border}\n{body}\n{border}"


def cmd_list(store: LayoutStore) -> int:
    print_table(store.graph_items(), f"图文件设备  {store.graph_path.name}")
    print_table(store.deck_items(), f"台面工位  {store.layout_path.name}")
    return 0


def cmd_get(store: LayoutStore, item_id: str, source: str) -> int:
    print_item(store.find(item_id, source=source))
    return 0


def cmd_set(
    store: LayoutStore,
    item_id: str,
    source: str,
    values: Dict[str, Optional[float]],
    name: Optional[str],
    dry_run: bool,
    backup: bool,
) -> int:
    item = store.find(item_id, source=source)
    updated = replace(item)
    for field, value in values.items():
        if value is not None:
            setattr(updated, field, value)
    if name is not None:
        updated.name = name
    store.update_item(updated)
    print("已更新：")
    print_item(updated)
    if dry_run:
        print("dry-run：未写入文件")
        return 0
    store.save(backup=backup)
    print(f"已保存 {store.graph_path.name} / {store.layout_path.name}")
    return 0


def cmd_preview(store: LayoutStore) -> int:
    print(ascii_preview(store))
    return 0


def _pick_font() -> Tuple[str, int]:
    return "Microsoft YaHei", 10


def launch_gui(store: LayoutStore) -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:
        print(f"无法启动图形界面：{exc}\n可用：python {Path(__file__).name} list")
        return 1

    app = LayoutEditorApp(store, tk, ttk, messagebox)
    app.run()
    return 0


class LayoutEditorApp:
    """Tkinter 台面编辑器：左侧列表 + 数值表单 + 俯视画布拖拽。"""

    CANVAS_W = 640
    CANVAS_H = 520
    PAD = 24

    def __init__(self, store: LayoutStore, tk: Any, ttk: Any, messagebox: Any) -> None:
        self.store = store
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("AI4C 台面编辑器")
        self.root.geometry("1180x720")
        self.font = _pick_font()
        try:
            self.root.option_add("*Font", self.font)
        except tk.TclError:
            pass

        self.source_var = tk.StringVar(value="deck")
        self.selected_id: Optional[str] = None
        self.fields: Dict[str, tk.StringVar] = {
            key: tk.StringVar() for key in ("id", "name", "kind", *NUMERIC_FIELDS)
        }
        self.status = tk.StringVar(value="拖拽色块移动位置；改数值后点「应用到选中项」，最后「保存文件」。")
        self._drag: Optional[Tuple[str, float, float]] = None
        self._dirty = False
        self._building = False

        self._build()
        self._refresh_list()
        self._select_first()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self.root.mainloop()

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="数据源").pack(side="left")
        combo = ttk.Combobox(
            top,
            textvariable=self.source_var,
            values=("deck", "graph"),
            state="readonly",
            width=12,
        )
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_list())
        ttk.Label(top, text="deck=台面工位    graph=图文件设备").pack(side="left", padx=8)
        ttk.Button(top, text="重新加载", command=self._reload).pack(side="right", padx=4)
        ttk.Button(top, text="保存文件", command=self._save).pack(side="right", padx=4)

        paned = ttk.Panedwindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        ttk.Label(left, text="设备 / 工位").pack(anchor="w")
        self.listbox = tk.Listbox(left, exportselection=False, height=28)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._on_list_select())

        right = ttk.Frame(paned, padding=4)
        paned.add(right, weight=3)

        form = ttk.LabelFrame(right, text="属性", padding=8)
        form.pack(fill="x")
        labels = [
            ("id", "ID"),
            ("name", "名称"),
            ("kind", "类型"),
            ("x", "X"),
            ("y", "Y"),
            ("z", "Z"),
            ("width", "宽 width"),
            ("height", "高 height"),
            ("depth", "深 depth"),
        ]
        for row, (key, label) in enumerate(labels):
            ttk.Label(form, text=label, width=12).grid(row=row // 3, column=(row % 3) * 2, sticky="e", padx=4, pady=3)
            state = "readonly" if key in ("id", "kind") else "normal"
            entry = ttk.Entry(form, textvariable=self.fields[key], width=18, state=state)
            entry.grid(row=row // 3, column=(row % 3) * 2 + 1, sticky="w", padx=4, pady=3)
        btns = ttk.Frame(form)
        btns.grid(row=3, column=0, columnspan=6, pady=8, sticky="w")
        ttk.Button(btns, text="应用到选中项", command=self._apply_form).pack(side="left", padx=4)
        ttk.Button(btns, text="Nudge +X 10", command=lambda: self._nudge(10, 0)).pack(side="left", padx=2)
        ttk.Button(btns, text="Nudge -X 10", command=lambda: self._nudge(-10, 0)).pack(side="left", padx=2)
        ttk.Button(btns, text="Nudge +Y 10", command=lambda: self._nudge(0, 10)).pack(side="left", padx=2)
        ttk.Button(btns, text="Nudge -Y 10", command=lambda: self._nudge(0, -10)).pack(side="left", padx=2)

        canvas_frame = ttk.LabelFrame(right, text="俯视图（原点在左下，Y 向上；可拖拽）", padding=4)
        canvas_frame.pack(fill="both", expand=True, pady=6)
        self.canvas = tk.Canvas(canvas_frame, width=self.CANVAS_W, height=self.CANVAS_H, bg="#f4f4f4")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Configure>", lambda _e: self._redraw_canvas())

        ttk.Label(self.root, textvariable=self.status, padding=8).pack(fill="x")
        self.root.bind("<Control-s>", lambda _e: self._save())
        self.root.bind("<Left>", lambda _e: self._nudge(-1, 0))
        self.root.bind("<Right>", lambda _e: self._nudge(1, 0))
        self.root.bind("<Up>", lambda _e: self._nudge(0, 1))
        self.root.bind("<Down>", lambda _e: self._nudge(0, -1))
        self.root.bind("<Shift-Left>", lambda _e: self._nudge(-10, 0))
        self.root.bind("<Shift-Right>", lambda _e: self._nudge(10, 0))
        self.root.bind("<Shift-Up>", lambda _e: self._nudge(0, 10))
        self.root.bind("<Shift-Down>", lambda _e: self._nudge(0, -10))

    def _current_items(self) -> List[LayoutItem]:
        if self.source_var.get() == "graph":
            return self.store.graph_items()
        return self.store.deck_items()

    def _canvas_items(self) -> List[LayoutItem]:
        return [item for item in self._current_items() if item.id != "__deck__"]

    def _refresh_list(self, keep_id: Optional[str] = None) -> None:
        keep_id = keep_id or self.selected_id
        self.listbox.delete(0, "end")
        target = 0
        for idx, item in enumerate(self._current_items()):
            self.listbox.insert("end", f"{item.id}    ({_fmt(item.x)}, {_fmt(item.y)})  {_fmt(item.width)}x{_fmt(item.height)}")
            if item.id == keep_id:
                target = idx
        if self.listbox.size():
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(target)
            self.listbox.activate(target)
            self.selected_id = self._current_items()[target].id
            self._load_form()
        self._redraw_canvas()

    def _select_first(self) -> None:
        if self.listbox.size():
            self.listbox.selection_set(0)
            self._on_list_select()

    def _on_list_select(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        items = self._current_items()
        if selection[0] >= len(items):
            return
        self.selected_id = items[selection[0]].id
        self._load_form()
        self._redraw_canvas()

    def _selected_item(self) -> Optional[LayoutItem]:
        if not self.selected_id:
            return None
        source = self.source_var.get()
        try:
            return self.store.find(self.selected_id, source=source)
        except KeyError:
            return None

    def _load_form(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        self._building = True
        self.fields["id"].set(item.id)
        self.fields["name"].set(item.name)
        self.fields["kind"].set(item.kind)
        for key in NUMERIC_FIELDS:
            self.fields[key].set(_fmt(getattr(item, key)))
        self._building = False

    def _form_to_item(self) -> LayoutItem:
        item = self._selected_item()
        if item is None:
            raise KeyError("未选中设备")
        updated = replace(item, name=self.fields["name"].get().strip() or item.name)
        for key in NUMERIC_FIELDS:
            updated_value = _as_float(self.fields[key].get(), getattr(item, key))
            setattr(updated, key, updated_value)
        return updated

    def _apply_form(self) -> None:
        try:
            updated = self._form_to_item()
            self.store.update_item(updated)
        except (KeyError, ValueError) as exc:
            self.messagebox.showerror("应用失败", str(exc))
            return
        self._dirty = True
        self.status.set(f"已应用 {updated.id}，尚未保存文件。")
        self._refresh_list(keep_id=updated.id)

    def _nudge(self, dx: float, dy: float) -> None:
        item = self._selected_item()
        if item is None or item.id == "__deck__":
            return
        updated = replace(item, x=item.x + dx, y=item.y + dy)
        self.store.update_item(updated)
        self._dirty = True
        self.status.set(f"{updated.id} 移动到 ({_fmt(updated.x)}, {_fmt(updated.y)})")
        self._refresh_list(keep_id=updated.id)

    def _view_transform(self) -> Tuple[float, float, float, float]:
        canvas_w = max(int(self.canvas.winfo_width()), 100)
        canvas_h = max(int(self.canvas.winfo_height()), 100)
        deck_w, deck_h = self.store.deck_size()
        scale = min((canvas_w - 2 * self.PAD) / deck_w, (canvas_h - 2 * self.PAD) / deck_h)
        origin_x = (canvas_w - deck_w * scale) / 2
        origin_y = (canvas_h + deck_h * scale) / 2
        return origin_x, origin_y, scale, canvas_h

    def _world_to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        origin_x, origin_y, scale, _ = self._view_transform()
        return origin_x + x * scale, origin_y - y * scale

    def _canvas_to_world(self, cx: float, cy: float) -> Tuple[float, float]:
        origin_x, origin_y, scale, _ = self._view_transform()
        return (cx - origin_x) / scale, (origin_y - cy) / scale

    def _redraw_canvas(self) -> None:
        self.canvas.delete("all")
        deck_w, deck_h = self.store.deck_size()
        x0, y1 = self._world_to_canvas(0, 0)
        x1, y0 = self._world_to_canvas(deck_w, deck_h)
        self.canvas.create_rectangle(x0, y0, x1, y1, fill="#ffffff", outline="#333333", width=2)
        self.canvas.create_text(x0 + 4, y1 - 4, text="(0,0)", anchor="sw", fill="#666666")
        self.canvas.create_text(x1 - 4, y0 + 4, text=f"{_fmt(deck_w)} x {_fmt(deck_h)}", anchor="ne", fill="#666666")

        fill = "#8ecae6" if self.source_var.get() == "graph" else "#95d5b2"
        for item in self._canvas_items():
            width, height = item.display_size()
            ax, ay = self._world_to_canvas(item.x, item.y)
            bx, by = self._world_to_canvas(item.x + width, item.y + height)
            selected = item.id == self.selected_id
            self.canvas.create_rectangle(
                min(ax, bx),
                min(ay, by),
                max(ax, bx),
                max(ay, by),
                fill="#ffb703" if selected else fill,
                outline="#d00000" if selected else "#1d3557",
                width=3 if selected else 1,
                tags=("item", item.id),
            )
            self.canvas.create_text(
                (ax + bx) / 2,
                (ay + by) / 2,
                text=item.id,
                fill="#1d3557",
                width=max(abs(bx - ax) - 4, 20),
            )

    def _hit_test(self, wx: float, wy: float) -> Optional[str]:
        hit = None
        for item in self._canvas_items():
            width, height = item.display_size()
            if item.x <= wx <= item.x + width and item.y <= wy <= item.y + height:
                hit = item.id
        return hit

    def _on_canvas_press(self, event: Any) -> None:
        wx, wy = self._canvas_to_world(event.x, event.y)
        item_id = self._hit_test(wx, wy)
        if not item_id:
            self._drag = None
            return
        self.selected_id = item_id
        items = self._current_items()
        for idx, item in enumerate(items):
            if item.id == item_id:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(idx)
                self.listbox.activate(idx)
                break
        item = self._selected_item()
        if item is None:
            return
        self._drag = (item.id, wx - item.x, wy - item.y)
        self._load_form()
        self._redraw_canvas()

    def _on_canvas_drag(self, event: Any) -> None:
        if not self._drag:
            return
        item_id, ox, oy = self._drag
        wx, wy = self._canvas_to_world(event.x, event.y)
        if item_id == "__deck__":
            return
        item = self.store.find(item_id, source=self.source_var.get())
        updated = replace(item, x=round(wx - ox, 2), y=round(wy - oy, 2))
        self.store.update_item(updated)
        self._dirty = True
        self.selected_id = item_id
        self._load_form()
        self._redraw_canvas()
        self.status.set(f"拖拽 {item_id} → ({_fmt(updated.x)}, {_fmt(updated.y)})")

    def _on_canvas_release(self, _event: Any) -> None:
        self._drag = None
        if self.selected_id:
            self._refresh_list(keep_id=self.selected_id)

    def _reload(self) -> None:
        if self._dirty and not self.messagebox.askyesno("重新加载", "有未保存修改，确定丢弃并重新加载？"):
            return
        self.store.reload()
        self._dirty = False
        self.status.set("已从磁盘重新加载。")
        self._refresh_list()

    def _save(self) -> None:
        try:
            if self.selected_id:
                self.store.update_item(self._form_to_item())
            self.store.save(backup=True)
        except (OSError, KeyError, ValueError) as exc:
            self.messagebox.showerror("保存失败", str(exc))
            return
        self._dirty = False
        self.status.set(f"已保存 {self.store.graph_path.name} 与 {self.store.layout_path.name}（首次保存会写 .bak）")
        self._refresh_list(keep_id=self.selected_id)

    def _on_close(self) -> None:
        if self._dirty and not self.messagebox.askyesno("退出", "有未保存修改，确定退出？"):
            return
        self.root.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="快速编辑 AI4C 台面工位与图文件设备的位置、大小",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不带子命令时打开图形界面。方向键微移 1mm，Shift+方向键 10mm，Ctrl+S 保存。",
    )
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH, help="图文件路径，默认 AI4C_station.json")
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT, help="台面布局 JSON，默认 AI4C_layout.json")
    parser.add_argument("--no-backup", action="store_true", help="保存时不写 .bak")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="打开图形界面")
    sub.add_parser("list", help="列出全部设备/工位")
    sub.add_parser("preview", help="终端打印台面俯视图")

    get_p = sub.add_parser("get", help="查看单个设备/工位")
    get_p.add_argument("id")
    get_p.add_argument("--source", choices=("auto", "graph", "deck"), default="auto")

    set_p = sub.add_parser("set", help="修改位置/大小并写回文件")
    set_p.add_argument("id")
    set_p.add_argument("--source", choices=("auto", "graph", "deck"), default="auto")
    set_p.add_argument("--x", type=float)
    set_p.add_argument("--y", type=float)
    set_p.add_argument("--z", type=float)
    set_p.add_argument("--width", type=float)
    set_p.add_argument("--height", type=float)
    set_p.add_argument("--depth", type=float)
    set_p.add_argument("--name")
    set_p.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    store = LayoutStore(args.graph.resolve(), args.layout.resolve())
    command = args.command or "gui"
    backup = not args.no_backup
    if command == "list":
        return cmd_list(store)
    if command == "preview":
        return cmd_preview(store)
    if command == "get":
        return cmd_get(store, args.id, args.source)
    if command == "set":
        values = {key: getattr(args, key) for key in NUMERIC_FIELDS}
        if all(value is None for value in values.values()) and args.name is None:
            parser.error("set 至少需要一个 --x/--y/--z/--width/--height/--depth/--name")
        return cmd_set(store, args.id, args.source, values, args.name, args.dry_run, backup)
    if command == "gui":
        return launch_gui(store)
    parser.error(f"未知命令 {command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
