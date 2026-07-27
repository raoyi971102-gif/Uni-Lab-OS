"""验证嵌套 workstation → device → 空 Deck 时，远端耗材会被深合并进本地 Deck。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unilabos.resources.resource_tracker import ResourceTreeSet


def _node(
    node_id: str,
    *,
    typ: str,
    parent: Optional[str] = None,
    uuid: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "uuid": uuid or f"uuid-{node_id}",
        "name": name or node_id,
        "parent": parent,
        "type": typ,
        "class": "",
        "config": {},
        "data": {},
        "children": [],
    }


def test_merge_remote_deep_merges_children_under_existing_nested_deck() -> None:
    """本地空 PRCXI_Deck 应吸收远端 Deck 下的 tip_rack / plate，而不是整节点跳过。"""
    local_nodes: List[Dict[str, Any]] = [
        _node("gn_workstation", typ="device"),
        _node("PRCXI", typ="device", parent="gn_workstation"),
        _node("PRCXI_Deck", typ="deck", parent="PRCXI"),
    ]
    remote_nodes: List[Dict[str, Any]] = [
        _node("gn_workstation", typ="device", uuid="uuid-gn_workstation-remote"),
        _node("PRCXI", typ="device", parent="gn_workstation", uuid="uuid-PRCXI-remote"),
        _node("PRCXI_Deck", typ="deck", parent="PRCXI", uuid="uuid-PRCXI_Deck-remote"),
        _node("TipRack_A", typ="tip_rack", parent="PRCXI_Deck", uuid="uuid-TipRack_A"),
        _node("Plate_B", typ="plate", parent="PRCXI_Deck", uuid="uuid-Plate_B"),
    ]

    local = ResourceTreeSet.from_raw_dict_list(local_nodes)
    remote = ResourceTreeSet.from_raw_dict_list(remote_nodes)
    local.merge_remote_resources(remote)

    deck = next(n for n in local.all_nodes if n.res_content.name == "PRCXI_Deck")
    child_names = {c.res_content.name for c in deck.children}
    assert child_names == {"TipRack_A", "Plate_B"}


def test_merge_remote_skips_already_present_deck_children() -> None:
    """本地 Deck 已有同名耗材时不重复添加。"""
    local_nodes: List[Dict[str, Any]] = [
        _node("gn_workstation", typ="device"),
        _node("PRCXI", typ="device", parent="gn_workstation"),
        _node("PRCXI_Deck", typ="deck", parent="PRCXI"),
        _node("TipRack_A", typ="tip_rack", parent="PRCXI_Deck"),
    ]
    remote_nodes: List[Dict[str, Any]] = [
        _node("gn_workstation", typ="device", uuid="uuid-gn_workstation-remote"),
        _node("PRCXI", typ="device", parent="gn_workstation", uuid="uuid-PRCXI-remote"),
        _node("PRCXI_Deck", typ="deck", parent="PRCXI", uuid="uuid-PRCXI_Deck-remote"),
        _node("TipRack_A", typ="tip_rack", parent="PRCXI_Deck", uuid="uuid-TipRack_A-remote"),
        _node("Plate_B", typ="plate", parent="PRCXI_Deck", uuid="uuid-Plate_B"),
    ]

    local = ResourceTreeSet.from_raw_dict_list(local_nodes)
    remote = ResourceTreeSet.from_raw_dict_list(remote_nodes)
    local.merge_remote_resources(remote)

    deck = next(n for n in local.all_nodes if n.res_content.name == "PRCXI_Deck")
    child_names = [c.res_content.name for c in deck.children]
    assert child_names.count("TipRack_A") == 1
    assert "Plate_B" in child_names
