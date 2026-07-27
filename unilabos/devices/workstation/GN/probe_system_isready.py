"""
探测 PLC 上 `System_IsReady` 节点的真实 NodeId。

背景：
    `opcua_gn1.3.6.csv` 里 System_IsReady 的 NodeId 是
        ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA.System_IsReady
    但运行时报 BadNodeIdUnknown（服务端 address space 里找不到），
    说明 PLC 固件升级后该节点被删除 / 改名 / namespace 变了。

用法：
    python unilabos/devices/workstation/GN/probe_system_isready.py
    # 或者临时改 URL：
    python unilabos/devices/workstation/GN/probe_system_isready.py opc.tcp://192.168.6.6:4840

输出：
    [1] 复现原报错     — 读 CSV 里的原始 NodeId
    [2] 对照读        — 读其它已知 System_* 节点（比如 System_ResetTrig）
    [3] Browse 探测   — 扫描 |var|Inovance-X86-Linux.Application.OPC_UA 下所有 System_ 开头节点
    [4] 候选变体名试探 — 试几个常见改名（Ready/IsReady/Init/InitReady/...）
"""

from __future__ import annotations

import sys
from typing import List, Optional, Tuple

from opcua import Client, ua

DEFAULT_URL = "opc.tcp://192.168.6.6:4840"

# CSV 里保存的原 NodeId（BadNodeIdUnknown 的元凶）
BROKEN_NODE_ID = "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA.System_IsReady"

# 对照读取的其它 System_* 节点（这些在 CSV 里也存在；若这几个也 Bad → 整个 System 组都改了）
BASELINE_NODES = [
    "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA.System_ResetTrig",
    "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA.System_ResetCompleteFB",
    "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA.System_StopTrig",
]

# System 组的容器路径（用于 browse）
SYSTEM_CONTAINER_NODE_IDS = [
    # 常见几种 Inovance 命名习惯
    "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA",
    "ns=4;s=|var|Inovance-X86-Linux.Application",
    "ns=4;s=|var|Inovance-X86-Linux",
]

# 常见改名候选（前后缀组合猜想）
CANDIDATES = [
    "System_Ready",
    "SystemReady",
    "System_IsReady",         # 原名（用于 case sanity check）
    "System_isReady",
    "System_ReadyFB",
    "System_ReadyState",
    "System_InitReady",
    "System_InitComplete",
    "System_InitCompleteFB",
    "System_Init",
    "System_PowerOnReady",
    "System_PowerOnCompleteFB",
    "System_State",
    "System_StatusFB",
    "IsReady",
    "Ready",
]


def try_read(client: Client, node_id: str) -> Tuple[bool, Optional[object], str]:
    """尝试读 node_id；返回 (成功?, 值, 简短诊断串)。"""
    try:
        node = client.get_node(node_id)
        value = node.get_value()
        return True, value, "OK"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"{type(exc).__name__}: {exc}"


def try_browse_children(client: Client, root_node_id: str) -> Tuple[bool, List, str]:
    """尝试展开一个节点，返回 (成功?, 子节点列表, 简短诊断串)。"""
    try:
        node = client.get_node(root_node_id)
        children = node.get_children()
        return True, children, "OK"
    except Exception as exc:  # noqa: BLE001
        return False, [], f"{type(exc).__name__}: {exc}"


def format_browse_name(node) -> str:
    try:
        bn = node.get_browse_name()
        return f"{bn.NamespaceIndex}:{bn.Name}"
    except Exception:  # noqa: BLE001
        return "?"


def format_nodeid(node) -> str:
    try:
        return node.nodeid.to_string()
    except Exception:  # noqa: BLE001
        return "?"


def main(url: str) -> None:
    print(f"\n=== 连接 PLC OPC UA 服务器: {url} ===")
    client = Client(url)
    try:
        client.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] 连接失败: {type(exc).__name__}: {exc}")
        sys.exit(1)

    print("[OK] 已连接")

    try:
        # ------------------------------------------------------------------
        # [1] 复现原报错
        # ------------------------------------------------------------------
        print("\n--- [1] 复现：读取 CSV 里的原始 NodeId ---")
        ok, val, diag = try_read(client, BROKEN_NODE_ID)
        print(f"  NodeId : {BROKEN_NODE_ID}")
        print(f"  结果    : {'OK, value=' + repr(val) if ok else 'FAIL, ' + diag}")

        # ------------------------------------------------------------------
        # [2] 对照读其它 System_* 节点
        # ------------------------------------------------------------------
        print("\n--- [2] 对照读其它 System_* 节点（用于判定是全组坏还是仅 IsReady 坏）---")
        for nid in BASELINE_NODES:
            ok, val, diag = try_read(client, nid)
            short = nid.split(".")[-1]
            print(f"  {short:36s} → {'OK, value=' + repr(val) if ok else diag}")

        # ------------------------------------------------------------------
        # [3] Browse 探测：列出容器下所有 System_ 开头节点
        # ------------------------------------------------------------------
        print("\n--- [3] Browse 扫描容器下的所有 System_* 节点 ---")
        found_any_container = False
        for container_id in SYSTEM_CONTAINER_NODE_IDS:
            ok, children, diag = try_browse_children(client, container_id)
            if not ok:
                print(f"  容器 {container_id} 不可展开：{diag}")
                continue
            found_any_container = True
            print(f"  容器 {container_id} 展开成功，共 {len(children)} 个子节点")
            hits = []
            keywords = ("system_", "ready", "init")
            for child in children:
                name = format_browse_name(child)
                low = name.lower()
                if any(k in low for k in keywords):
                    hits.append((name, format_nodeid(child)))
            if not hits:
                print("    (无 System_/Ready/Init 相关子节点)")
            else:
                print(f"    命中 {len(hits)} 个 (System_/Ready/Init 关键字):")
                for name, nid in sorted(hits):
                    print(f"      - {name:40s}  NodeId={nid}")
            break  # 找到能展开的容器就停
        if not found_any_container:
            print("  [WARN] 所有候选容器都无法 browse；可能 PLC 侧限制了 Browse 或路径结构完全改了")

        # ------------------------------------------------------------------
        # [4] 候选变体名试探
        # ------------------------------------------------------------------
        print("\n--- [4] 候选变体名试探（同样走 |var|Inovance-X86-Linux.Application.OPC_UA.<候选>）---")
        base = "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA"
        winners = []
        for name in CANDIDATES:
            nid = f"{base}.{name}"
            ok, val, _ = try_read(client, nid)
            marker = "OK" if ok else "  "
            print(f"  [{marker}] {name:32s} → {repr(val) if ok else '-'}")
            if ok:
                winners.append((name, nid, val))

        # ------------------------------------------------------------------
        # 结论
        # ------------------------------------------------------------------
        print("\n=== 结论 ===")
        if winners:
            print("  ★ 建议把 CSV 里 System_IsReady 的 NodeId 改为下列之一：")
            for name, nid, val in winners:
                print(f"      候选名={name!r:32s} NodeId={nid} value={val!r}")
        else:
            print("  ✗ 候选变体名都读不到。请看 [3] 的 browse 结果，人工确认服务端里 System 组"
                  "还剩哪些节点。若 System 前缀被整体删除，可能已迁到别的路径（换 CSV 版本）。")
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    main(url)
