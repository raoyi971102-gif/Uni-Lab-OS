"""PyLabRobot 孔板/枪头架子资源命名对齐。

pylabrobot 在 ``ItemizedResource`` 构造时把子孔命名为
``{当时的板名}_{kind}_{identifier}``（例如 ``PRCXI_96_DeepWell_well_A1``）。
之后只改板的 ``name``（云端改名、``plate_slot_N`` 等）**不会**改孔名，
导致多块同类板在同一 deck 上撞名：``Resource 'xxx_well_A1' already assigned to deck``。

本模块把子孔名重写为当前 ``{parent.name}_{kind}_{identifier}``，并同步
``_ordering`` 与 deck 的 ``_resources`` 注册表。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

from pylabrobot.resources.deck import Deck
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource


def expected_itemized_child_name(parent: Resource, identifier: str, child: Resource) -> str:
    """与 pylabrobot ``create_ordered_items_2d`` + 前缀规则一致的子孔名。"""
    kind = type(child).__name__.lower()
    return f"{parent.name}_{kind}_{identifier}"


def _find_deck(resource: Resource) -> Optional[Deck]:
    current: Optional[Resource] = resource
    while current is not None:
        if isinstance(current, Deck):
            return current
        current = getattr(current, "parent", None)
    return None


def _rename_resource_in_place(resource: Resource, new_name: str) -> None:
    """绕过 pylabrobot 的 name setter（已挂到 parent 时禁止改名）。"""
    old_name = resource.name
    if old_name == new_name:
        return
    # 子孔挂在板上时 setter 会抛 RuntimeError；已在 deck 树上时还需改注册表。
    resource._name = new_name
    deck = _find_deck(resource)
    registry = getattr(deck, "_resources", None) if deck is not None else None
    if isinstance(registry, dict):
        if registry.get(old_name) is resource:
            del registry[old_name]
        registry[new_name] = resource


def retarget_itemized_child_names(resource: Resource) -> int:
    """把 ``resource`` 子树里所有 ItemizedResource 的子孔名对齐到当前板名。

    Returns:
        实际改名的子节点数量。
    """
    changed = 0
    if isinstance(resource, ItemizedResource):
        ordering = getattr(resource, "_ordering", None)
        if ordering:
            new_ordering: OrderedDict[str, str] = OrderedDict()
            for identifier, old_child_name in list(ordering.items()):
                ident = str(identifier)
                child = None
                try:
                    child = resource.get_item(ident)
                except Exception:
                    child = next(
                        (c for c in resource.children if getattr(c, "name", None) == old_child_name),
                        None,
                    )
                if child is None:
                    new_ordering[ident] = old_child_name
                    continue
                new_name = expected_itemized_child_name(resource, ident, child)
                new_ordering[ident] = new_name
                if child.name != new_name:
                    _rename_resource_in_place(child, new_name)
                    changed += 1
            resource._ordering = new_ordering
    for child in list(getattr(resource, "children", None) or []):
        changed += retarget_itemized_child_names(child)
    return changed
