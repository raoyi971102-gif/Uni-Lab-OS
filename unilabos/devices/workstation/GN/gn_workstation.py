"""
GN 工作站顶层设备（`gn_workstation`）。

作用：把 GN 有机合成工作站建模为**一个顶层设备 + 一组子设备**：
    - 顶层：`gn_workstation`（本类，继承 `WorkstationBase`）
    - 子设备：机械手 / 离心机 / 固体加样 / 锁紧 / 快换 / 堆栈 / 常规烘箱 /
              真空烘箱 / 离心管液体处理 / PRCXI …

关键收益：
    1. 顶层实例化时**预热** `GnPlcClient`（按 URL 单例），子设备后续
       `GnPlcClient.get_or_create` 会命中同一实例 → 整站只有 1 个 OPC UA 会话
       与 1 份 CSV 节点表
    2. `ROS2WorkstationNode` 自动初始化 `children` 列出的子设备（见
       `unilabos/ros/nodes/presets/workstation.py`），因此在图文件里只写一次
       连接参数（`url` / `csv_path`），子设备节点的 config 可以精简
    3. 顶层动作提供整站级维护入口（诊断 / 复位 / 断开）
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from unilabos.devices.workstation.GN.gn_plc_client import GnPlcClient
from unilabos.devices.workstation.workstation_base import WorkstationBase
from unilabos.registry.decorators import action, device, not_action
from unilabos.utils.log import logger

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.6.csv")


@device(
    id="gn_workstation",
    display_name="GN 有机合成工作站",
    category=["workstation"],
    description=(
        "GN 有机合成工作站顶层设备：机械手 + 8 个功能子设备 + PRCXI 液体处理器；"
        "内部通过 GnPlcClient 单例保持与 PLC 的**唯一 OPC UA 会话**。"
    ),
    icon="",
    version="1.0.0",
)
class GNWorkstation(WorkstationBase):
    """GN 有机合成工作站顶层。

    Args:
        url: OPC UA 服务器地址（整站共享）
        csv_path: 节点表 CSV 路径（整站只加载一次）
        username / password: OPC UA 认证
        use_subscription: 是否启用订阅（默认 False，与旧配置一致）
        cache_timeout / subscription_interval: OPC 缓存/订阅参数
        deck: 可选 PLR Deck（供 WorkstationBase 物料系统使用）
        protocol_type: 顶层协议列表（默认空，不注册整站级 protocol 动作，
            所有工作流由前端/云端组合子设备的 @action 完成）
    """

    def __init__(
        self,
        url: str,
        csv_path: str = DEFAULT_CSV_PATH,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_subscription: bool = False,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        deck: Optional[Any] = None,
        protocol_type: Optional[List[str]] = None,
        **kwargs: Any,
    ):
        # protocol_type 是 ROS2WorkstationNode 需要的字段，此处仅承接、不使用
        _ = protocol_type
        super().__init__(deck=deck, **kwargs)
        self._url = url

        # 预热 PLC 单例：子设备再调用 GnPlcClient.get_or_create(url) 会命中此实例
        self.plc: GnPlcClient = GnPlcClient.get_or_create(
            url=url,
            csv_path=csv_path,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
        )
        logger.info(
            f"✓ GN 工作站已连接 PLC: {url}（refcount={self.plc._gn_refcount}，"
            f"已加载节点 {len(self.plc._node_registry)} 个）"
        )

    # ==================================================================
    # 顶层维护 / 诊断动作
    # ==================================================================

    @action(description="读取 PLC 连接与订阅统计信息")
    def get_plc_stats(self) -> dict:
        try:
            stats = self.plc.get_cache_stats()
            return {
                "success": True,
                "url": self._url,
                "refcount": self.plc._gn_refcount,
                "registered_nodes": len(self.plc._node_registry),
                "subscribed_nodes": len(self.plc._subscribed_names),
                "loaded_csvs": sorted(self.plc._loaded_csv_paths),
                "cache": stats,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @action(description="读取 System_IsReady 判断整站是否就绪")
    def get_system_ready(self) -> dict:
        try:
            value = self.plc.read_with_retry("System_IsReady", force_read=True)
            return {"success": True, "ready": bool(value), "raw": value}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @action(description="System_ResetTrig → 等 System_ResetCompleteFB")
    def system_reset(self, timeout: float = 180.0) -> dict:
        try:
            if not self.plc.write_with_retry("System_ResetTrig", 1):
                return {"success": False, "error": "System_ResetTrig 写入失败"}
            ok = self.plc.wait_true(
                "System_ResetCompleteFB", timeout=timeout, description="整站系统复位完成"
            )
            self.plc.write_with_retry("System_ResetTrig", 0)
            if not ok:
                return {"success": False, "error": "System_ResetCompleteFB 未变为 1"}
            return {"success": True, "message": "整站系统复位完成"}
        except Exception as exc:
            self.plc.write_with_retry("System_ResetTrig", 0)
            return {"success": False, "error": str(exc)}

    @action(description="System_StopTrig 紧急停止")
    def system_stop(self) -> dict:
        try:
            self.plc.write_with_retry("System_StopTrig", 1)
            import time
            time.sleep(0.2)
            self.plc.write_with_retry("System_StopTrig", 0)
            return {"success": True, "message": "整站停止命令已下发"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @action(description="主动重连 OPC UA 会话（限流：3s 内不重复触发）")
    def reconnect_plc(self) -> dict:
        try:
            ok = self.plc._reconnect()
            return {"success": ok, "message": "重连成功" if ok else "被限流或重连失败"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @action(description="释放对 PLC 的引用（引用计数 -1，到 0 才真正断开）")
    def release_plc(self) -> dict:
        try:
            self.plc.release()
            return {"success": True, "message": "GN 工作站已释放 PLC 引用"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ==================================================================
    # WorkstationBase 契约（暂未实现整站级工作流；由子设备各自 @action 组合）
    # ==================================================================

    @not_action
    def _execute_workflow_impl(self, workflow_name: str, parameters):
        logger.warning(
            f"GN 工作站未内置工作流 {workflow_name!r}；请通过前端/云端编排各子设备动作"
        )
        return False

    @not_action
    def _stop_workflow_impl(self, emergency: bool):
        logger.warning("GN 工作站未内置工作流停止")
        return False
