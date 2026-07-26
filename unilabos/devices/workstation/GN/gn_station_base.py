"""
GN 工站通用基类

GN 的 PLC 只暴露原始可调参数（位置/速度/CmdType/触发），动作编排在上位机。
本模块把所有工站共用的「上位机↔PLC 握手 / 等待到位」逻辑沉到基类，工站子类
只需声明「发什么指令、写哪些点位、等哪个到位」。

握手时序 (run_command)：
    interlock() → 写 setpoints → CmdTrig=0 → 写 CmdType → CmdTrig=1
    → 等 done_node=1 且 reach_checks 全部到位 → CmdTrig=0 →（可选）等 done_node=0

三条等待原语：
    wait_true     等某布尔节点为真（如 *_Done / *_CompleteFB）
    wait_false    等某布尔节点复位为假
    wait_reached  等某数值反馈落在目标±容差内并连续稳定（位置到位判据）
"""

import time
from typing import Callable, Optional, Sequence, Tuple

from unilabos.utils.log import logger
from unilabos.registry.decorators import not_action
from unilabos.devices.workstation.GN.base_opcua_client import OpcUaClientWithSubscription

# (反馈节点名, 目标值, 容差)
ReachCheck = Tuple[str, float, float]

# 区分 “未传 done_node” 与 “显式 done_node=None（不等布尔完成，只看 reach_checks）”
_UNSET = object()


class GNStationClient(OpcUaClientWithSubscription):
    """GN 工站基类：统一构造 + 等待原语 + run_command 握手。

    子类可用类属性覆盖默认节点名，未设置时按 PREFIX 推导：
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
        username: str = None,
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        super().__init__(
            url=url,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs,
        )
        if csv_path:
            self.load_nodes_from_csv(csv_path)

    # ---------------- 节点名解析 ----------------

    @not_action
    def _cmd_type_node(self) -> str:
        return self.CMD_TYPE_NODE or f"{self.PREFIX}CmdType"

    @not_action
    def _cmd_trig_node(self) -> str:
        return self.CMD_TRIG_NODE or f"{self.PREFIX}CmdTrig"

    @not_action
    def _complete_node(self) -> str:
        return self.COMPLETE_NODE or f"{self.PREFIX}CompleteFB"

    # ---------------- 等待原语 ----------------

    @not_action
    def wait_true(self, node: str, timeout: float = 180.0, interval: float = 0.2, description: str = None) -> bool:
        """等布尔节点为真。"""
        desc = description or node
        logger.info(f"等待 {desc} 为真（轮询 {node}）...")
        start = time.time()
        while time.time() - start < timeout:
            if self.get_node_value(node, force_read=True):
                logger.info(f"✓ {desc}")
                return True
            time.sleep(interval)
        value = self.get_node_value(node, force_read=True)
        logger.error(f"✗ {desc} 超时（{node}={value!r}）")
        return False

    @not_action
    def wait_false(self, node: str, timeout: float = 60.0, interval: float = 0.2, description: str = None) -> bool:
        """等布尔节点复位为假。"""
        desc = description or node
        start = time.time()
        while time.time() - start < timeout:
            if not self.get_node_value(node, force_read=True):
                return True
            time.sleep(interval)
        value = self.get_node_value(node, force_read=True)
        logger.error(f"✗ {desc} 复位超时（{node}={value!r}）")
        return False

    @not_action
    def wait_reached(
        self,
        fb_node: str,
        target: float,
        tolerance: float,
        stable_samples: int = 3,
        timeout: float = 120.0,
        interval: float = 0.2,
        description: str = None,
    ) -> bool:
        """等数值反馈落在目标±容差内并连续稳定 stable_samples 次。"""
        desc = description or f"{fb_node}→{target}±{tolerance}"
        logger.info(f"等待到位 {desc} ...")
        start = time.time()
        stable = 0
        while time.time() - start < timeout:
            v = self.get_node_value(fb_node, force_read=True)
            if v is not None and abs(v - target) <= tolerance:
                stable += 1
                if stable >= stable_samples:
                    logger.info(f"✓ 到位 {desc}（{fb_node}={v}）")
                    return True
            else:
                stable = 0
            time.sleep(interval)
        logger.error(f"✗ 到位超时 {desc}（{fb_node}={self.get_node_value(fb_node, force_read=True)!r}）")
        return False

    # ---------------- 握手 ----------------

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
        """通用「上位机↔PLC 握手」。

        Args:
            cmd_type: 写入 CmdType 的指令号；None 表示本次不写 CmdType（仅靠 setpoints/触发）。
            setpoints: {节点名: 值}，值为 None 的键跳过。
            trig_node/cmd_type_node: 触发/指令节点，缺省按 PREFIX 推导。
            done_node: 完成布尔节点；缺省用 COMPLETE_NODE；显式传 None 表示只看 reach_checks。
            reach_checks: 位置到位判据列表 (反馈节点, 目标, 容差)。
            clear_done: 完成后是否等 done_node 复位为假。
            interlock: 触发前的前置动作（如机械手 ensure_idle）。
            description: 日志描述。
        """
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
                    self.set_node_value(node, val)

        self.set_node_value(trig_node, 0)
        time.sleep(0.05)
        ok_type = True
        if cmd_type is not None:
            ok_type = self.set_node_value(cmd_type_node, int(cmd_type))
        ok_trig = self.set_node_value(trig_node, 1)
        # 回读确认指令是否真正写入 PLC（区分“代码没写进去”与“PLC 未响应”）
        rb_type = self.get_node_value(cmd_type_node, force_read=True) if cmd_type is not None else None
        rb_trig = self.get_node_value(trig_node, force_read=True)
        logger.info(f"[{desc}] 触发: 写入{'成功' if ok_type and ok_trig else '失败'} "
                    f"{cmd_type_node}={rb_type} {trig_node}={rb_trig}")

        # 写入失败（多为连接断开/Broken pipe）时直接中止，避免后续建订阅在死连接上崩溃
        if not (ok_type and ok_trig):
            raise ValueError(f"{desc} 指令写入失败（连接可能已断开，请重试）")

        if not self._wait_done(done_node, reach_checks, timeout, desc):
            self.set_node_value(trig_node, 0)
            raise ValueError(f"{desc} 未完成/未到位")
        self.set_node_value(trig_node, 0)
        if clear_done and done_node:
            self.wait_false(done_node, description=f"{desc} 完成复位")
        logger.info(f"{desc} 完成")
        return {"success": True, "message": f"{desc} 完成", "cmd_type": cmd_type}

    @not_action
    def _subscribe_once(self, nodes: Sequence[str]):
        """为指定反馈节点建立临时订阅：数据变化即推入缓存（source='subscription'），返回 subscription。"""
        handler = OpcUaClientWithSubscription.SubscriptionHandler(self)
        with self._client_lock:
            sub = self.client.create_subscription(self._subscription_interval, handler)
            for name in nodes:
                chinese = self._name_mapping.get(name, name)
                ua_node = self._found_node_objects.get(chinese) or self.client.get_node(self.use_node(chinese).node_id)
                sub.subscribe_data_change(ua_node)
        return sub

    @not_action
    def _wait_done(
        self,
        done_node: Optional[str],
        reach_checks: Optional[Sequence[ReachCheck]],
        timeout: float,
        description: str,
    ) -> bool:
        """发指令后等到位：对 done_node 与 reach_checks 反馈节点建临时订阅，
        靠订阅推送值判断（不再轮询读服务器），满足后删除订阅。"""
        watch = ([done_node] if done_node else []) + [c[0] for c in (reach_checks or [])]
        # 全局订阅已开则直接复用其推送缓存；仅在未开订阅时才临时建一个（结束后删除）。
        sub = self._subscribe_once(watch) if (watch and not self._use_subscription) else None
        logger.info(f"[{description}] 已下发，等待到位（{watch}）...")

        start = time.time()
        need = 3 if reach_checks else 1
        stable = 0
        reached = False
        last_log = 0.0
        while time.time() - start < timeout:
            values = {n: self.get_node_value(n, use_cache=True) for n in watch}
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
            # 等待期间可能发生过重连（订阅 id 失效），删除失败可忽略。
            with self._client_lock:
                try:
                    sub.delete()
                except Exception:
                    pass
            for name in watch:
                self._node_values.pop(self._name_mapping.get(name, name), None)
        return reached

    @not_action
    def read_many(self, nodes: dict) -> dict:
        """批量读点位：入参 {别名: 节点名}，返回 {别名: 值}。"""
        return {alias: self.get_node_value(node, force_read=True) for alias, node in nodes.items()}
