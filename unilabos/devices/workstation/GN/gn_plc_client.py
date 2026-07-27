"""
GN 工作站统一 OPC UA 通信驱动。

目标：整个 GN 工作站（同一台 PLC）**只与 PLC 保持 1 个 OPC UA 会话**，
所有子设备（机械手/离心机/固体加样/锁紧/快换/堆栈/烘箱/真空烘箱/离心管…）
通过组合方式引用 `GnPlcClient` 实例，共享同一份会话、节点表、订阅与缓存。

用法（子设备侧）：
    self.plc = GnPlcClient.get_or_create(url, csv_path=DEFAULT_CSV_PATH)
    self.plc.write("Solid_CmdType", 11)
    value = self.plc.read("Solid_WeightFB", force_read=True)
    self.plc.wait_true("Solid_CompleteFB", timeout=30)

设计要点：
1. **按 URL 单例**：`get_or_create(url)` 保证同一 IP 只有 1 个 client 实例。
2. **一次性加载 CSV**：首次调用 `load_nodes_from_csv` 后记住已加载路径，
   同 URL 后续再次调用直接跳过（避免 9 个设备重复解析同一份 CSV）。
3. **订阅去重**：`_setup_subscriptions` 只在真正新增节点后触发一次，
   避免各子设备各自建订阅导致同一节点被订阅 N 次。
4. **统一重连**：`write_with_retry` / `read_with_retry` 内含限流重连,
   替代原先各子设备自写的 `_reconnect_opcua/_opc_write/_opc_read`。
5. **引用计数式 disconnect**：`release()` 到 0 才真正断开，避免一个设备的
   `disconnect()` 拖垮整站。
6. **等待原语汇总**：`wait_true/wait_false/wait_reached/wait_complete_value/
   wait_positions_reached` 从原基类与各设备中集中到这里。
7. **统一 keepalive**：由 `start_keepalive()` 维护 1 个后台线程，定期读取
   `System_IsReady`（缺失则回退标准 OPC UA 节点 `Server_ServerStatus_CurrentTime`
   `ns=0;i=2258`），避免 PLC 因连接长时间空闲而主动断开。所有 GN 子设备共用，
   不再各自开线程。
"""

import os
import threading
import time
from typing import Any, Dict, Optional, Sequence, Tuple

from unilabos.devices.workstation.GN.base_opcua_client import OpcUaClientWithSubscription
from unilabos.utils.log import logger

# (反馈节点名, 目标值, 容差) — 与 gn_station_base.ReachCheck 兼容
ReachCheck = Tuple[str, float, float]


class GnPlcClient(OpcUaClientWithSubscription):
    """GN PLC 通信驱动（按 URL 单例、引用计数）。

    通过 `GnPlcClient.get_or_create(url, csv_path=...)` 获取实例，
    禁止直接 `GnPlcClient(url)` 构造（会绕过单例、破坏共享语义）。
    """

    _singletons: Dict[str, "GnPlcClient"] = {}
    _singletons_lock = threading.RLock()

    # 默认写入重试次数（含首次共 N+1 次尝试）
    DEFAULT_RETRIES: int = 2
    # 保活默认候选节点（按顺序尝试，第一个在 CSV 注册表里能找到的即被采用）
    _KEEPALIVE_CANDIDATES: Tuple[str, ...] = (
        "System_IsReady",
        "System_ResetCompleteFB",
    )
    # OPC UA 标准 CurrentTime 节点（保活兜底：即使 CSV 一个都没加载也能保活）
    _KEEPALIVE_FALLBACK_NODE_ID: str = "ns=0;i=2258"

    def __init__(
        self,
        url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        keepalive_enabled: bool = True,
        keepalive_interval: float = 5.0,
        keepalive_node: Optional[str] = None,
        *args,
        **kwargs,
    ):
        """内部使用，外部请调用 `get_or_create`。"""
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
        self._gn_url = url
        self._gn_refcount = 0
        self._loaded_csv_paths: set = set()
        # 已下发过 _setup_subscriptions 的节点集合（避免重复订阅同一节点）
        self._subscribed_names: set = set(self._subscription_handles.keys())
        # 全局命令锁：workstation 里如需强制"同一时刻只有一条 PLC 命令"可用
        self.command_lock: threading.RLock = threading.RLock()

        # ---- Keepalive 状态（真正的启动/停止在 start_keepalive/stop_keepalive）----
        self._keepalive_enabled: bool = bool(keepalive_enabled)
        self._keepalive_interval: float = float(keepalive_interval)
        self._keepalive_preferred_node: Optional[str] = keepalive_node
        self._keepalive_target: Optional[str] = None  # 实际每轮读取的节点名/id
        self._keepalive_stop: threading.Event = threading.Event()
        self._keepalive_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 单例工厂 / 引用计数
    # ------------------------------------------------------------------

    @classmethod
    def get_or_create(
        cls,
        url: str,
        csv_path: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        keepalive_enabled: bool = True,
        keepalive_interval: float = 5.0,
        keepalive_node: Optional[str] = None,
    ) -> "GnPlcClient":
        """按 URL 返回单例；`csv_path` 只在**首次**加载时生效，同 URL 再次调用会跳过。

        引用计数 +1，配套 `release()` 使用。
        Keepalive 参数仅在**首次**创建时生效；后续复用者若想修改，请显式调用
        `start_keepalive(...)`。
        """
        with cls._singletons_lock:
            inst = cls._singletons.get(url)
            if inst is None:
                inst = cls(
                    url=url,
                    username=username,
                    password=password,
                    use_subscription=use_subscription,
                    cache_timeout=cache_timeout,
                    subscription_interval=subscription_interval,
                    keepalive_enabled=keepalive_enabled,
                    keepalive_interval=keepalive_interval,
                    keepalive_node=keepalive_node,
                )
                cls._singletons[url] = inst
                logger.info(f"✓ GnPlcClient 新建单例: {url}")
            else:
                logger.info(
                    f"✓ GnPlcClient 复用单例: {url} (refcount 即将 {inst._gn_refcount}→{inst._gn_refcount + 1})"
                )
            inst._gn_refcount += 1
            if csv_path:
                inst.load_nodes_from_csv_once(csv_path)
            # CSV 加载完再启动保活，这样能优先命中 CSV 中的节点（幂等，第二次进来是 no-op）
            if inst._keepalive_enabled:
                inst.start_keepalive()
            return inst

    def release(self) -> None:
        """引用计数 -1；归零后停止保活、真正断开 OPC UA 会话并从单例表移除。"""
        with self._singletons_lock:
            self._gn_refcount = max(0, self._gn_refcount - 1)
            logger.info(
                f"GnPlcClient release: {self._gn_url} (refcount={self._gn_refcount})"
            )
            if self._gn_refcount == 0:
                # 先停保活线程再断开，避免线程正在读的时候会话被关掉打印一堆异常
                try:
                    self.stop_keepalive()
                except Exception as exc:
                    logger.warning(f"GnPlcClient stop_keepalive 异常: {exc}")
                try:
                    super().disconnect()
                except Exception as exc:
                    logger.warning(f"GnPlcClient disconnect 异常: {exc}")
                self._singletons.pop(self._gn_url, None)
                logger.info(f"GnPlcClient 已释放并断开: {self._gn_url}")

    @classmethod
    def peek(cls, url: str) -> Optional["GnPlcClient"]:
        """不 +1 引用计数地取出已存在的单例（若不存在返回 None）。"""
        with cls._singletons_lock:
            return cls._singletons.get(url)

    # ------------------------------------------------------------------
    # 节点表加载 / 订阅（去重）
    # ------------------------------------------------------------------

    def load_nodes_from_csv_once(self, csv_path: str) -> None:
        """幂等版 `load_nodes_from_csv`：同一份 CSV 只加载一次。"""
        if not os.path.isabs(csv_path):
            csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), csv_path)
        norm = os.path.normcase(os.path.normpath(csv_path))
        if norm in self._loaded_csv_paths:
            logger.debug(f"GnPlcClient CSV 已加载，跳过：{csv_path}")
            return
        before = len(self._node_registry)
        self.load_nodes_from_csv(csv_path)
        after = len(self._node_registry)
        self._loaded_csv_paths.add(norm)
        logger.info(f"✓ GnPlcClient 加载 CSV：{csv_path}（新增 {after - before} 个节点，共 {after} 个）")

    def _setup_subscriptions(self):
        """仅为**尚未订阅**的节点建立订阅，避免同一节点被订阅多次。"""
        if not self.client or not self._use_subscription:
            return
        with self._client_lock:
            try:
                if self._subscription is None:
                    logger.info(
                        f"GnPlcClient 首次建立订阅（发布间隔 {self._subscription_interval}ms）..."
                    )
                    handler = OpcUaClientWithSubscription.SubscriptionHandler(self)
                    self._subscription = self.client.create_subscription(
                        self._subscription_interval, handler
                    )

                added, skipped = 0, 0
                for node_name, node in self._node_registry.items():
                    if node_name in self._subscribed_names:
                        continue
                    if not (hasattr(node, "type") and node.node_id):
                        skipped += 1
                        continue
                    if node.type.name != "VARIABLE":
                        skipped += 1
                        continue
                    try:
                        ua_node = self._found_node_objects.get(node_name)
                        if ua_node is None:
                            ua_node = self.client.get_node(node.node_id)
                        handle = self._subscription.subscribe_data_change(ua_node)
                        self._subscription_handles[node_name] = handle
                        self._subscribed_names.add(node_name)
                        added += 1
                    except Exception as exc:
                        skipped += 1
                        logger.warning(f"✗ 订阅节点 {node_name} 失败: {exc}")
                logger.info(
                    f"GnPlcClient 订阅追加完成: 新增 {added} 个, 跳过 {skipped} 个, 总计 {len(self._subscribed_names)}"
                )
            except Exception as exc:
                logger.error(f"GnPlcClient 建立订阅失败: {exc}")
                self._use_subscription = False

    # ------------------------------------------------------------------
    # 便捷读写（对外统一命名）
    # ------------------------------------------------------------------

    def read(self, name: str, use_cache: bool = True, force_read: bool = False) -> Any:
        """读节点值（默认走缓存；`force_read=True` 强制服务器读取）。"""
        return self.get_node_value(name, use_cache=use_cache, force_read=force_read)

    def write(self, name: str, value) -> bool:
        """写节点值，返回是否成功。"""
        return self.set_node_value(name, value)

    # ------------------------------------------------------------------
    # 含自动重连的读写（原各设备自写的 _opc_write/_opc_read）
    # ------------------------------------------------------------------

    def write_with_retry(self, name: str, value, retries: Optional[int] = None) -> bool:
        """写入失败（如 Broken pipe）时自动重连并重试。"""
        attempts = (self.DEFAULT_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            if self.set_node_value(name, value):
                return True
            if attempt + 1 < attempts:
                logger.warning(
                    f"写入 {name}={value} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect()
                time.sleep(0.3)
        return False

    def read_with_retry(
        self, name: str, force_read: bool = False, retries: Optional[int] = None
    ):
        """读取返回 None 时自动重连并重试。"""
        attempts = (self.DEFAULT_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            value = self.get_node_value(name, force_read=force_read)
            if value is not None:
                return value
            if attempt + 1 < attempts:
                logger.warning(
                    f"读取 {name} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect()
                time.sleep(0.3)
        return None

    # ------------------------------------------------------------------
    # Keepalive（整站共用；替代原快换模块自建的保活线程）
    # ------------------------------------------------------------------

    def start_keepalive(
        self,
        interval: Optional[float] = None,
        node: Optional[str] = None,
    ) -> None:
        """启动保活线程（幂等）。

        目标节点解析优先级：
            1. 参数 `node`
            2. `__init__` 传入的 `keepalive_node`
            3. `_KEEPALIVE_CANDIDATES` 中第一个已在节点表里注册的
            4. 回退到 OPC UA 标准节点 `Server_ServerStatus_CurrentTime` (`ns=0;i=2258`)

        Args:
            interval: 覆写读取间隔（秒）；不传则沿用 `__init__` 的值。
            node: 覆写读取节点名。
        """
        if interval is not None:
            self._keepalive_interval = float(interval)
        if node is not None:
            self._keepalive_preferred_node = node
        self._keepalive_enabled = True
        # 每次调用都重新解析目标（可能 CSV 刚加载完，之前只能用兜底）
        self._keepalive_target = self._resolve_keepalive_target()

        if self._keepalive_thread and self._keepalive_thread.is_alive():
            logger.debug(
                f"GnPlcClient keepalive 已在运行（target={self._keepalive_target}，"
                f"interval={self._keepalive_interval}s）"
            )
            return

        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_worker,
            daemon=True,
            name=f"GnPlcKeepalive[{self._gn_url}]",
        )
        self._keepalive_thread.start()
        logger.info(
            f"✓ GnPlcClient keepalive 启动: url={self._gn_url}, "
            f"target={self._keepalive_target}, interval={self._keepalive_interval}s"
        )

    def stop_keepalive(self) -> None:
        """停止保活线程（幂等；`release()` 归零时自动调用）。"""
        self._keepalive_stop.set()
        thread = self._keepalive_thread
        if (
            thread
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=2.0)
        self._keepalive_thread = None

    def _resolve_keepalive_target(self) -> str:
        """按优先级选出下一次保活读取的节点名/id。"""
        preferred = self._keepalive_preferred_node
        if preferred and preferred in self._node_registry:
            return preferred
        for candidate in self._KEEPALIVE_CANDIDATES:
            if candidate in self._node_registry:
                return candidate
        return self._KEEPALIVE_FALLBACK_NODE_ID

    # F1: 触发主动 reconnect 前必须连续失败的最小次数（避免单次抖动即 disconnect）
    KEEPALIVE_FAILS_BEFORE_RECONNECT: int = 3

    def _keepalive_worker(self) -> None:
        """后台保活循环：轻量直读；**连续多次失败**才触发一次主动重连。

        与业务读写解耦，绕开 `_read_raw` / `uniopcua.read()`，因此：
        - 不会打印 "读取变量 X 失败" 这类噪音
        - 不会触发基类被动重连（避免与业务路径同时抢重连）

        重连策略（F1 修复）：
        - 单次 probe 失败**不再立即** `_reconnect()`；等下一个周期再探
        - 连续失败 >= `KEEPALIVE_FAILS_BEFORE_RECONNECT` 才做一次主动重连
        - 主动重连**只做一次**（不做重连后立即再探针），把「新 session 的稳定」时间留给
          随后的业务读写或下个 keepalive 周期
        - 这样避免了 "reconnect → 立即读 → 新 session 里 handle 还没准备好 → 又 fail →
          又 reconnect" 的雪崩循环
        """
        consecutive_failures = 0
        while not self._keepalive_stop.wait(self._keepalive_interval):
            target = self._keepalive_target or self._resolve_keepalive_target()
            if self._keepalive_probe(target):
                if consecutive_failures:
                    logger.info(
                        f"GnPlcClient keepalive 恢复（{target}），累计失败次数 {consecutive_failures}"
                    )
                consecutive_failures = 0
                continue

            consecutive_failures += 1
            # 达到阈值：触发一次主动重连（受 _reconnect_min_interval=3s 二次限流）
            if consecutive_failures >= self.KEEPALIVE_FAILS_BEFORE_RECONNECT:
                logger.warning(
                    f"GnPlcClient keepalive 连续 {consecutive_failures} 次探针失败"
                    f"（target={target}），触发主动重连"
                )
                try:
                    if self._reconnect():
                        logger.info("GnPlcClient keepalive 主动重连已完成，下轮周期再验证")
                        # 重连后不立即 probe：给新 session activate 留出时间。
                        # 计数保持不清零，若下轮 probe 成功会打印 "keepalive 恢复"。
                    else:
                        logger.warning("GnPlcClient keepalive 主动重连被限流或失败")
                except Exception as exc:
                    logger.warning(f"GnPlcClient keepalive 主动重连异常: {exc}")
            else:
                # 未到阈值只做静默计数，不打日志避免误报噪音
                logger.debug(
                    f"GnPlcClient keepalive 探针失败 {consecutive_failures}/"
                    f"{self.KEEPALIVE_FAILS_BEFORE_RECONNECT}（target={target}），先观察"
                )

    def _keepalive_probe(self, target: str) -> bool:
        """一次轻量存活探针：直接按 node_id 读一次值，不经过节点缓存/重试路径。

        返回 True 表示会话可用，False 表示读取失败（worker 会累计 N 次后再触发重连）。

        兜底策略：若原 target 报 `BadNodeIdUnknown`——不一定是节点真的不存在，
        更常见的是**当前 session 里的 node handle 处于 stale 状态**（参见
        `probe_system_isready.py` 的离线验证：独立脚本能读到，项目里报错）。
        此时用标准 `Server_ServerStatus_State` (`ns=0;i=2259`) 再探一次：
          - 标准节点可读 → session 正常，把 target 一次性切到标准节点（后续静默）
          - 标准节点也失败 → 真链路问题，返回 False
        """
        if self.client is None:
            return False
        # 先按当前 target 探测
        node_id = self._resolve_probe_node_id(target)
        exc = self._try_read_node(node_id)
        if exc is None:
            return True

        # 原 target 读失败但可能只是 handle stale：用标准节点再试
        if node_id != self._KEEPALIVE_FALLBACK_NODE_ID and self._is_bad_node_id_error(exc):
            fb = self._KEEPALIVE_FALLBACK_NODE_ID
            fb_exc = self._try_read_node(fb)
            if fb_exc is None:
                # session 正常，一次性切换 target 到标准节点
                logger.warning(
                    f"GnPlcClient keepalive: 读取 {target}({node_id}) 报 "
                    f"{type(exc).__name__}，但标准节点 {fb} 可读——session 正常，"
                    f"已把 keepalive target 切换到标准节点。"
                )
                self._keepalive_target = fb
                self._keepalive_preferred_node = None
                return True
            logger.debug(f"GnPlcClient keepalive 兜底节点也失败: {fb_exc}")
            return False

        logger.debug(f"GnPlcClient keepalive probe 失败（target={target}）: {exc}")
        return False

    def _resolve_probe_node_id(self, target: str) -> str:
        """把 keepalive target (可能是 CSV 名或 nodeid 字符串) 解析为一个 node_id 字符串。"""
        if target == self._KEEPALIVE_FALLBACK_NODE_ID or target.startswith("ns="):
            return target
        node_info = self._node_registry.get(target)
        node_id = getattr(node_info, "node_id", None) if node_info else None
        return node_id or self._KEEPALIVE_FALLBACK_NODE_ID

    def _try_read_node(self, node_id: str) -> Optional[BaseException]:
        """按 node_id 读一次，返回 None 表示成功，返回异常实例表示失败。"""
        try:
            with self._client_lock:
                self.client.get_node(node_id).get_value()
            return None
        except BaseException as exc:  # noqa: BLE001
            return exc

    @staticmethod
    def _is_bad_node_id_error(exc: BaseException) -> bool:
        """粗判是否是"节点 id 无效"类错误（非链路问题）。

        以异常类名 + 消息双通道识别，避免依赖具体 opcua 库版本的 uaerrors 命名。
        """
        name = type(exc).__name__
        if "BadNodeId" in name or "BadBrowseName" in name:
            return True
        msg = str(exc)
        return "node id refers to a node that does not exist" in msg

    # ------------------------------------------------------------------
    # 等待原语（集中版）
    # ------------------------------------------------------------------

    def wait_true(
        self,
        node: str,
        timeout: float = 180.0,
        interval: float = 0.2,
        description: Optional[str] = None,
    ) -> bool:
        """等布尔节点为真。"""
        desc = description or node
        logger.info(f"等待 {desc} 为真（{node}）...")
        start = time.time()
        while time.time() - start < timeout:
            if self.read_with_retry(node, force_read=True):
                logger.info(f"✓ {desc}")
                return True
            time.sleep(interval)
        value = self.read_with_retry(node, force_read=True)
        logger.error(f"✗ 等待 {desc} 超时（{node}={value!r}）")
        return False

    def wait_false(
        self,
        node: str,
        timeout: float = 60.0,
        interval: float = 0.2,
        description: Optional[str] = None,
    ) -> bool:
        """等布尔节点复位为假。"""
        desc = description or node
        start = time.time()
        while time.time() - start < timeout:
            if not self.read_with_retry(node, force_read=True):
                return True
            time.sleep(interval)
        value = self.read_with_retry(node, force_read=True)
        logger.error(f"✗ {desc} 复位超时（{node}={value!r}）")
        return False

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
        """等数值反馈稳定在目标±容差内。"""
        desc = description or f"{fb_node}→{target}±{tolerance}"
        logger.info(f"等待到位 {desc} ...")
        start = time.time()
        stable = 0
        while time.time() - start < timeout:
            v = self.read_with_retry(fb_node, force_read=True)
            if v is not None and abs(v - target) <= tolerance:
                stable += 1
                if stable >= stable_samples:
                    logger.info(f"✓ 到位 {desc}（{fb_node}={v}）")
                    return True
            else:
                stable = 0
            time.sleep(interval)
        v = self.read_with_retry(fb_node, force_read=True)
        logger.error(f"✗ 到位超时 {desc}（{fb_node}={v!r}）")
        return False

    def wait_complete_value(
        self,
        complete_node: str,
        expected: int,
        timeout: float,
        interval: float = 0.05,
        description: str = "",
        fail_streak_threshold: int = 3,
    ) -> bool:
        """等 CompleteFB 达到期望值；连续读取失败 N 次即认定链路已断，直接返回 False。"""
        logger.info(f"等待 {description}（{complete_node}={expected}）...")
        start = time.monotonic()
        read_fail_streak = 0
        while time.monotonic() - start < timeout:
            value = self.read_with_retry(complete_node, force_read=True)
            if value is None:
                read_fail_streak += 1
                if read_fail_streak >= fail_streak_threshold:
                    logger.error(
                        f"✗ {description} 中止：{complete_node} 连续 {fail_streak_threshold} 次读取失败"
                    )
                    return False
            else:
                read_fail_streak = 0
                if value == expected:
                    logger.info(f"✓ {description}（{complete_node}={value}）")
                    return True
            time.sleep(interval)
        value = self.read_with_retry(complete_node, force_read=True)
        logger.error(
            f"✗ 等待 {description} 超时（{timeout}s，{complete_node}={value!r}，期望={expected}）"
        )
        return False

    def wait_positions_reached(
        self,
        position_targets: Dict[str, float],
        tolerance: float = 5,
        stable_samples: int = 3,
        interval: float = 0.1,
        sample_timeout: float = 2.0,
    ) -> bool:
        """位置反馈兜底：所有位置反馈同时稳定落在目标±容差内。"""
        if not position_targets:
            return False
        start = time.monotonic()
        stable_count = 0
        last_values: Dict[str, Any] = {}
        while time.monotonic() - start < sample_timeout:
            last_values = {
                node: self.read_with_retry(node, force_read=True)
                for node in position_targets
            }
            all_reached = all(
                (value is not None and abs(int(value) - target) <= tolerance)
                for node, target in position_targets.items()
                for value in (last_values[node],)
            )
            stable_count = stable_count + 1 if all_reached else 0
            if stable_count >= stable_samples:
                logger.info(f"✓ 位置到位兜底：{last_values}")
                return True
            time.sleep(interval)
        logger.warning(f"位置兜底未满足，当前={last_values}，目标={position_targets}")
        return False

    # ------------------------------------------------------------------
    # 临时订阅（供 gn_station_base.run_command 使用）
    # ------------------------------------------------------------------

    def subscribe_once(self, nodes: Sequence[str]):
        """为一组节点建立临时订阅，返回 subscription 对象。调用方需在完成后 `sub.delete()`。"""
        handler = OpcUaClientWithSubscription.SubscriptionHandler(self)
        with self._client_lock:
            sub = self.client.create_subscription(self._subscription_interval, handler)
            for name in nodes:
                chinese = self._name_mapping.get(name, name)
                ua_node = self._found_node_objects.get(chinese) or self.client.get_node(
                    self.use_node(chinese).node_id
                )
                sub.subscribe_data_change(ua_node)
        return sub


__all__ = ["GnPlcClient", "ReachCheck"]
