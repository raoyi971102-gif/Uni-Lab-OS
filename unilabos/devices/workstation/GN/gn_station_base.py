"""
GN 工作站设备通用基类（**组合式**）。

变更说明（重要）：
    早期版本 `GNStationClient` 直接继承 `OpcUaClientWithSubscription`，导致
        - 每个子设备都是"通信客户端 + 业务对象"二合一，会话生命周期与业务耦合
        - 通过基类隐式的 `_connection_pool` 共享 client，但没有引用计数
          （一台设备 `disconnect()` 会把整站的共享 client 断掉），且节点表/订阅
          存在多次覆盖与重复订阅问题
    现在改为组合模式：`GNStationClient` 只是一个业务基类，通过 `self.plc`
    引用**按 URL 单例的 `GnPlcClient`**（见 `gn_plc_client.py`），所有 OPC UA
    读写、订阅、重连、CSV 加载统一走 `self.plc`，工站范围内保证 **只有 1 个
    OPC UA 会话**。（`base_opcua_client._connection_pool` 已随之删除，避免双重簿记。）

对子类的影响（向后兼容）：
    - `self.set_node_value / self.get_node_value / self.load_nodes_from_csv /
       self.load_csv / self.wait_true / self.wait_false / self.wait_reached /
       self.read_many / self.run_command / self.client / self._client_lock /
       self._node_registry / self._use_subscription / self._name_mapping / ...`
      全部保留（透传到 `self.plc`），旧子类代码可以少改甚至不改
    - `self._opc_write / self._opc_read` 变为透传 `plc.write_with_retry /
      plc.read_with_retry`，各子类可以删除自己那一份重复实现
    - `self._reconnect_opcua` 保留兼容语义，透传到 `plc._reconnect()`
    - `self.disconnect()` 走引用计数（`plc.release()`），一台设备退出不影响其它

握手时序 (run_command)：
    interlock() → 写 setpoints → CmdTrig=0 → 写 CmdType → CmdTrig=1
    → 等 done_node=1 且 reach_checks 全部到位 → CmdTrig=0 →（可选）等 done_node=0
"""

import time
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from unilabos.utils.log import logger
from unilabos.registry.decorators import not_action
from unilabos.devices.workstation.GN.gn_plc_client import GnPlcClient, ReachCheck

# 区分 "未传 done_node" 与 "显式 done_node=None（不等布尔完成，只看 reach_checks）"
_UNSET = object()


class GNStationClient:
    """GN 工站设备业务基类（**通过 self.plc 使用共享 OPC UA 会话**）。

    子类可用类属性覆盖默认节点名，未设置时按 `PREFIX` 推导：
        CMD_TYPE_NODE  默认 f"{PREFIX}CmdType"
        CMD_TRIG_NODE  默认 f"{PREFIX}CmdTrig"
        COMPLETE_NODE  默认 f"{PREFIX}CompleteFB"
    """

    PREFIX: str = ""
    CMD_TYPE_NODE: Optional[str] = None
    CMD_TRIG_NODE: Optional[str] = None
    COMPLETE_NODE: Optional[str] = None

    def __init__(
        self,
        url: str,
        csv_path: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        # 兼容旧调用传入的自定义/未知 kwargs：吞掉，避免报错
        kwargs.pop("plc_device_id", None)
        # 若 CSV 是相对路径，先按本目录解析成绝对路径，交给 plc
        if csv_path and not __import__("os").path.isabs(csv_path):
            import os as _os
            csv_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), csv_path)

        self.plc: GnPlcClient = GnPlcClient.get_or_create(
            url=url,
            csv_path=csv_path,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
        )
        # 记录 URL，便于 disconnect 时做引用计数
        self._plc_url = url
        # 已绑定标志，防止重复 release
        self._plc_released = False

    # ==================================================================
    # 关闭 / 生命周期
    # ==================================================================

    def disconnect(self) -> None:
        """引用计数式关闭：不会直接断整站会话，仅让 plc refcount -1。"""
        if getattr(self, "_plc_released", False):
            return
        try:
            self.plc.release()
        finally:
            self._plc_released = True

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass

    # ==================================================================
    # 属性代理（兼容旧代码里的 self.<xxx>）
    # ==================================================================

    @property
    def client(self):
        return self.plc.client

    @property
    def _client_lock(self):
        return self.plc._client_lock

    @property
    def _node_registry(self):
        return self.plc._node_registry

    @property
    def _variables_to_find(self):
        return self.plc._variables_to_find

    @property
    def _found_node_objects(self):
        return self.plc._found_node_objects

    @property
    def _name_mapping(self):
        return self.plc._name_mapping

    @property
    def _reverse_mapping(self):
        return self.plc._reverse_mapping

    @property
    def _node_values(self):
        return self.plc._node_values

    @property
    def _use_subscription(self):
        return self.plc._use_subscription

    @_use_subscription.setter
    def _use_subscription(self, value):
        self.plc._use_subscription = value

    @property
    def _subscription(self):
        return self.plc._subscription

    @property
    def _subscription_handles(self):
        return self.plc._subscription_handles

    @property
    def _subscription_interval(self):
        return self.plc._subscription_interval

    # ==================================================================
    # 方法代理（读 / 写 / 加载 / 订阅）
    # ==================================================================

    @not_action
    def get_node_value(self, name: str, use_cache: bool = True, force_read: bool = False):
        return self.plc.get_node_value(name, use_cache=use_cache, force_read=force_read)

    @not_action
    def set_node_value(self, name: str, value) -> bool:
        return self.plc.set_node_value(name, value)

    @not_action
    def use_node(self, name: str):
        return self.plc.use_node(name)

    @not_action
    def read_node(self, node_name: str) -> str:
        return self.plc.read_node(node_name)

    @not_action
    def write_node(self, json_input: str) -> str:
        return self.plc.write_node(json_input)

    @not_action
    def load_nodes_from_csv(self, csv_path: str) -> None:
        """兼容旧调用：转发到 plc 的幂等加载。"""
        self.plc.load_nodes_from_csv_once(csv_path)

    @staticmethod
    def load_csv(file_path: str):
        """保持 staticmethod 签名兼容旧子类中的 `self.load_csv(...)` 调用。"""
        from unilabos.devices.workstation.GN.base_opcua_client import BaseOpcUaClient
        return BaseOpcUaClient.load_csv(file_path)

    @not_action
    def register_node_list(self, node_list):
        return self.plc.register_node_list(node_list)

    @not_action
    def _find_nodes(self):
        return self.plc._find_nodes()

    @not_action
    def _setup_subscriptions(self):
        return self.plc._setup_subscriptions()

    @not_action
    def _register_nodes_as_attributes(self):
        # 兼容旧调用：plc 侧的 load_nodes_from_csv 已经处理过一次；这里 no-op
        return

    # ==================================================================
    # 含自动重连的读写（原各子设备的 _opc_write / _opc_read）
    # ==================================================================

    @not_action
    def _opc_write(self, name: str, value, retries: Optional[int] = None) -> bool:
        return self.plc.write_with_retry(name, value, retries=retries)

    @not_action
    def _opc_read(self, name: str, force_read: bool = False, retries: Optional[int] = None):
        return self.plc.read_with_retry(name, force_read=force_read, retries=retries)

    @not_action
    def _reconnect_opcua(self) -> bool:
        """兼容旧命名：主动重连。"""
        return self.plc._reconnect()

    # ==================================================================
    # 节点名推导
    # ==================================================================

    @not_action
    def _cmd_type_node(self) -> str:
        return self.CMD_TYPE_NODE or f"{self.PREFIX}CmdType"

    @not_action
    def _cmd_trig_node(self) -> str:
        return self.CMD_TRIG_NODE or f"{self.PREFIX}CmdTrig"

    @not_action
    def _complete_node(self) -> str:
        return self.COMPLETE_NODE or f"{self.PREFIX}CompleteFB"

    # ==================================================================
    # 等待原语（透传到 plc）
    # ==================================================================

    @not_action
    def wait_true(self, node: str, timeout: float = 180.0, interval: float = 0.2,
                  description: Optional[str] = None) -> bool:
        return self.plc.wait_true(node, timeout=timeout, interval=interval, description=description)

    @not_action
    def wait_false(self, node: str, timeout: float = 60.0, interval: float = 0.2,
                   description: Optional[str] = None) -> bool:
        return self.plc.wait_false(node, timeout=timeout, interval=interval, description=description)

    @not_action
    def wait_reached(
        self,
        fb_node: str,
        target: float,
        tolerance: float,
        stable_samples: int = 3,
        timeout: float = 120.0,
        interval: float = 0.2,
        description: Optional[str] = None,
    ) -> bool:
        return self.plc.wait_reached(
            fb_node, target, tolerance,
            stable_samples=stable_samples, timeout=timeout,
            interval=interval, description=description,
        )

    @not_action
    def _wait_complete_value(
        self,
        expected: int,
        timeout: float,
        interval: float = 0.05,
        description: str = "",
    ) -> bool:
        return self.plc.wait_complete_value(
            self._complete_node(), expected, timeout,
            interval=interval, description=description,
        )

    @not_action
    def _wait_positions_reached(
        self,
        position_targets: Dict[str, float],
        tolerance: float = 5,
        stable_samples: int = 3,
        interval: float = 0.1,
        sample_timeout: float = 2.0,
    ) -> bool:
        return self.plc.wait_positions_reached(
            position_targets, tolerance=tolerance,
            stable_samples=stable_samples, interval=interval,
            sample_timeout=sample_timeout,
        )

    # ==================================================================
    # 通用握手 run_command（对齐原实现）
    # ==================================================================

    @not_action
    def run_command(
        self,
        cmd_type: Optional[int],
        setpoints: Optional[dict] = None,
        *,
        trig_node: Optional[str] = None,
        cmd_type_node: Optional[str] = None,
        done_node=_UNSET,
        reach_checks: Optional[Sequence[ReachCheck]] = None,
        clear_done: bool = True,
        interlock: Optional[Callable[[], None]] = None,
        description: str = "",
        timeout: float = 120.0,
    ) -> dict:
        """通用「上位机↔PLC 握手」；行为与旧实现一致，OPC 调用全部走 self.plc。"""
        trig_node = trig_node or self._cmd_trig_node()
        cmd_type_node = cmd_type_node or self._cmd_type_node()
        if done_node is _UNSET:
            done_node = self._complete_node()
        desc = description or (f"CmdType={cmd_type}" if cmd_type is not None else "动作")

        if interlock is not None:
            interlock()
        if setpoints:
            for node, val in setpoints.items():
                if val is not None:
                    self.plc.write(node, val)

        self.plc.write(trig_node, 0)
        time.sleep(0.05)
        ok_type = True
        if cmd_type is not None:
            ok_type = self.plc.write(cmd_type_node, int(cmd_type))
        ok_trig = self.plc.write(trig_node, 1)
        rb_type = self.plc.read(cmd_type_node, force_read=True) if cmd_type is not None else None
        rb_trig = self.plc.read(trig_node, force_read=True)
        logger.info(
            f"[{desc}] 触发: 写入{'成功' if ok_type and ok_trig else '失败'} "
            f"{cmd_type_node}={rb_type} {trig_node}={rb_trig}"
        )
        if not (ok_type and ok_trig):
            raise ValueError(f"{desc} 指令写入失败（连接可能已断开，请重试）")

        if not self._wait_done(done_node, reach_checks, timeout, desc):
            self.plc.write(trig_node, 0)
            raise ValueError(f"{desc} 未完成/未到位")
        self.plc.write(trig_node, 0)
        if clear_done and done_node:
            self.wait_false(done_node, description=f"{desc} 完成复位")
        logger.info(f"{desc} 完成")
        return {"success": True, "message": f"{desc} 完成", "cmd_type": cmd_type}

    @not_action
    def _wait_done(
        self,
        done_node: Optional[str],
        reach_checks: Optional[Sequence[ReachCheck]],
        timeout: float,
        description: str,
    ) -> bool:
        """发指令后等到位：对 done_node 与 reach_checks 反馈节点建临时订阅（仅在
        全局订阅未开时），靠订阅推送值判断。"""
        watch = ([done_node] if done_node else []) + [c[0] for c in (reach_checks or [])]
        sub = self.plc.subscribe_once(watch) if (watch and not self.plc._use_subscription) else None
        logger.info(f"[{description}] 已下发，等待到位（{watch}）...")

        start = time.time()
        need = 3 if reach_checks else 1
        stable = 0
        reached = False
        last_log = 0.0
        while time.time() - start < timeout:
            values = {n: self.plc.get_node_value(n, use_cache=True) for n in watch}
            ok = True
            if done_node:
                ok = bool(values.get(done_node))
            if ok and reach_checks:
                for fb, tgt, tol in reach_checks:
                    v = values.get(fb)
                    if v is None or abs(v - tgt) > tol:
                        ok = False
                        break
            if ok:
                stable += 1
                if stable >= need:
                    reached = True
                    break
            else:
                stable = 0
            now = time.time()
            if now - last_log >= 2.0:
                logger.info(f"[{description}] 等待中 {now - start:.0f}s，当前反馈={values}")
                last_log = now
            time.sleep(0.2)

        if sub is not None:
            with self.plc._client_lock:
                try:
                    sub.delete()
                except Exception:
                    pass
            for name in watch:
                self.plc._node_values.pop(self.plc._name_mapping.get(name, name), None)
        return reached

    # ==================================================================
    # 兼容旧订阅接口
    # ==================================================================

    @not_action
    def _subscribe_once(self, nodes: Sequence[str]):
        return self.plc.subscribe_once(nodes)

    @not_action
    def read_many(self, nodes: dict) -> dict:
        """批量读点位：入参 {别名: 节点名}，返回 {别名: 值}。"""
        return {alias: self.plc.get_node_value(node, force_read=True) for alias, node in nodes.items()}


__all__ = ["GNStationClient", "ReachCheck"]
