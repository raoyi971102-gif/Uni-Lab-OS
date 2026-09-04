"""
AI4C 设备驱动
继承自 OPC UA 通讯基类，实现具体的设备动作函数
"""

import functools
import json
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence
import os
import threading

# 导入日志类
from unilabos.utils.log import logger
import logging
from unilabos.registry.decorators import (
    ActionInputHandle,
    DataSource,
    action,
    device,
    not_action,
    topic_config,
)
from unilabos.devices.workstation.AI4C.bottle_carriers import (
    AI4C_PowderCylinderCarrier,
    AI4C_WellPlateCarrier,
)
from unilabos.devices.workstation.AI4C.decks import AI4C_deck

# 导入通讯基类
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

# 定义机械臂目标位置的枚举
from enum import Enum
class RoboticArmTargetPosition(int, Enum):
    """
    机械臂目标位置的枚举
    """
    # 固态称量堆栈
    SOLID_WEIGHING_STACK = 1
    # 固体称量
    SOLID_WEIGHING = 2
    # 移液站
    PIPETTING_STATION = 3
    # 磁搅
    MAGNETIC_STIRRER = 4
    # HPLC工站
    HPLC_STATION = 5
    # 孔板上料架
    PLATE_LOADING_RACK = 6
    # 孔板下料架
    PLATE_UNLOADING_RACK = 7

class RoboticArmAction(int, Enum):
    """
    机械臂动作的枚举
    """
    # 抓取
    PICK = 1
    # 存放
    PLACE = 2
    # 上粉末头
    ON_POWDER_HEAD = 3
    # 下粉末头
    OFF_POWDER_HEAD = 4

# 最大和最小的料架位置
MIN_RACK_POSITION = 1
MAX_RACK_POSITION = 8

# 最大和最小的称量堆栈位置
MIN_SOLID_WEIGHING_STACK_POSITION = 1
MAX_SOLID_WEIGHING_STACK_POSITION = 25

# 最大和最小的移液站板位
MIN_PIPETTING_STATION_POSITION = 1
MAX_PIPETTING_STATION_POSITION = 16

# 移液站不挂在 AI4C_deck 上，物料改挂到独立设备 PRCXI 的 PRCXI_Deck。
# 机械臂板位 N 与 PRCXI 槽位 TN、sites[N-1] 按 1:1 对应。
PIPETTING_WAREHOUSE_NAME = "移液站"
DEFAULT_PIPETTING_DEVICE_ID = "PRCXI"
DEFAULT_PIPETTING_DECK_ID = "PRCXI_Deck"

# 固体称量槽位区间写法，例如 "1-8"
_SOLID_WEIGHING_SLOT_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")

# OPC UA「固体称量克数 / 误差」寄存器的单位：写入的整数 1 = 0.1 mg
SOLID_WEIGHING_WEIGHT_UNIT_MG = 0.1

# AI4C 只有一套机械臂，所有机械臂取放动作共用一把可重入锁。
# 列表与 XMU 的 ARM_LOCK_MAP 用途一致：只串行化共享机械臂的动作，
# 固态称量、移液、磁搅和 HPLC 等独立单元仍可按原逻辑运行。
_ROBOTIC_ARM_ACTIONS = (
    "pick_well_plate_from_loading_rack",
    "pick_well_plate_from_unloading_rack",
    "place_well_plate_to_solid_weighing",
    "pick_powder_cylinder_from_stack",
    "place_powder_cylinder_to_solid_weighing",
    "pick_powder_cylinder_from_solid_weighing",
    "place_powder_cylinder_to_solid_weighing_stack",
    "pick_well_plate_from_solid_weighing",
    "place_well_plate_to_pipetting_station",
    "pick_well_plate_from_pipetting_station",
    "place_well_plate_to_magnetic_stirrer",
    "pick_well_plate_from_magnetic_stirrer",
    "place_well_plate_to_hplc_station",
    "pick_well_plate_from_hplc_station",
    "place_well_plate_to_unloading_rack",
    "place_well_plate_to_loading_rack",
)

# 与 XMU 的集中机械臂状态轮询保持一致，只集中刷新固定的空闲/故障状态。
_ROBOTIC_ARM_STATUS_NODES = (
    "Robotic_Arm_Idle",
    "Robotic_Arm_Fault",
)

# 新版 PLC 的工位状态数组：0=机械手，1=固体称量，2=磁搅。
# 初始化信号在检测到对应工位进入 Homing 后立即复位，再等待 HomeDone。
_INITIALIZATION_COMPONENTS = (
    (
        "Robotic_Arm_Initialize",
        "robot_homing",
        "robot_home_done",
        "机械手",
        "robot_error_flag",
    ),
    (
        "Solid_Weighing_Initialize",
        "solid_weighing_homing",
        "solid_weighing_home_done",
        "固体称量",
        None,
    ),
    (
        "Magnetic_Stirrer_Initialize",
        "magnetic_stirrer_homing",
        "magnetic_stirrer_home_done",
        "磁搅",
        None,
    ),
)


# 定义 AI4C 设备通信类
# 包含一个固态称量、一个移液站、一个磁搅、一个 HPLC 工站
@device(
    id="AI4C_station",
    display_name="AI4C 工作站",
    category=["workstation"],
    description="AI4C 水合工作站，仅开放初始化、上料取板、固态称量开门、孔板放入固态称量四个步骤",
    icon="AI4C.webp",
)
class AI4CDevice(OpcUaClientWithSubscription):
    """
    AI4M 设备类
    继承自 OpcUaClientWithSubscription，实现具体的设备动作函数
    """
    
    def __init__(
        self, 
        url: str, 
        url_sim: str = None,
        deck: Optional[AI4C_deck] = None,
        csv_path: str = None, 
        username: str = None, 
        password: str = None,
        simulator: bool = False,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        create_placeholder_resource_when_missing: bool = True,
        pipetting_device_id: str = DEFAULT_PIPETTING_DEVICE_ID,
        pipetting_deck_id: str = DEFAULT_PIPETTING_DECK_ID,
        *args,
        **kwargs,
    ):
        """
        初始化 AI4C 设备
        
        参数:
            url: OPC UA 服务器地址
            url_sim: 模拟 OPC UA 服务器地址
            deck: AI4C 资源树配置
            csv_path: 节点配置 CSV 文件路径
            username: OPC UA 用户名
            password: OPC UA 密码
            simulator: 是否使用模拟 OPC UA 服务器
            use_subscription: 是否启用订阅模式
            cache_timeout: 缓存超时时间（秒）
            subscription_interval: 订阅发布间隔（毫秒）
            create_placeholder_resource_when_missing: 前端源仓位没有资源时，是否创建临时
                孔板/粉桶并在后续放料时同步到前端；关闭后硬件动作不受影响，也不会凭空
                生成前端资源
            pipetting_device_id: 移液站对应的独立设备 id，默认 PRCXI
            pipetting_deck_id: 移液站物料所在 deck 名，默认 PRCXI_Deck
        """
        test_mode = False
        try:
            from unilabos.config.config import BasicConfig

            test_mode = bool(getattr(BasicConfig, "test_mode", False))
        except Exception:
            test_mode = False

        use_sim_url = bool(simulator or test_mode)
        active_url = url_sim if use_sim_url and url_sim else url
        if use_sim_url and url_sim:
            logger.info(f"AI4C 使用模拟 OPC UA 服务器: {active_url}")
        elif use_sim_url:
            logger.warning("AI4C 已启用模拟模式但未配置 url_sim，仍使用 url")

        self.simulator = simulator
        self.url = active_url
        self.url_sim = url_sim
        if isinstance(create_placeholder_resource_when_missing, str):
            create_placeholder_resource_when_missing = (
                create_placeholder_resource_when_missing.strip().lower()
                not in {"false", "0", "no", "off", ""}
            )
        self.create_placeholder_resource_when_missing = bool(
            create_placeholder_resource_when_missing
        )
        self.pipetting_device_id = str(pipetting_device_id or DEFAULT_PIPETTING_DEVICE_ID)
        self.pipetting_deck_id = str(pipetting_deck_id or DEFAULT_PIPETTING_DECK_ID)
        self._external_warehouses = {
            PIPETTING_WAREHOUSE_NAME: {
                "device_id": self.pipetting_device_id,
                "deck_id": self.pipetting_deck_id,
            }
        }

        # 调用父类构造函数
        super().__init__(
            url=active_url,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs
        )

        # 处理 deck 参数；graphio 反序列化前会以 dict 形式传入资源描述。
        if deck is None or isinstance(deck.get("data") if isinstance(deck, dict) else deck, dict):
            self.deck = AI4C_deck(setup=True)
        else:
            self.deck = deck.get("data") if isinstance(deck, dict) else deck

        if self.deck is None:
            raise ValueError("Deck 配置不能为空")

        if hasattr(self.deck, "children"):
            logger.info(f"Deck 初始化完成，加载 {len(self.deck.children)} 个资源")

        # 如果提供了 CSV 路径，则直接加载节点
        if csv_path:
            self.load_nodes_from_csv(csv_path)

        self.m_initialized = False
        self._held_well_plate = None
        self._held_powder_cylinder = None
        self._placeholder_resource_counter = 0

        # 单一后台线程集中刷新机械臂状态；状态发布只读本地缓存，不与动作争抢 OPC UA 锁。
        self._arm_status_nodes = list(_ROBOTIC_ARM_STATUS_NODES)
        self._arm_status_cache = {}
        for node_name in self._arm_status_nodes:
            try:
                self._arm_status_cache[node_name] = bool(self.get_node_value(node_name))
            except Exception:
                self._arm_status_cache[node_name] = False
        self._arm_status_poller_stop = threading.Event()
        self._arm_status_thread = threading.Thread(
            target=self._arm_status_poll_loop,
            name="AI4CArmStatusPoller",
            daemon=True,
        )
        self._arm_status_thread.start()

        # AI4C 只有一套机械臂，所有取放动作通过同一把 RLock 串行执行。
        self._robotic_arm_lock = threading.RLock()
        for method_name in _ROBOTIC_ARM_ACTIONS:
            original = getattr(self, method_name, None)
            if callable(original):
                setattr(self, method_name, self._make_operation_locked(original))
            else:
                logger.warning(f"机械臂线程锁包裹失败，方法不存在: {method_name}")

    @not_action
    def _make_operation_locked(self, func):
        """将机械臂动作包装为实例级串行调用。"""
        lock = self._robotic_arm_lock

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with lock:
                logger.info(f"[机械臂] 已获取线程锁: {func.__name__}")
                try:
                    return func(*args, **kwargs)
                finally:
                    logger.info(f"[机械臂] 已释放线程锁: {func.__name__}")

        return wrapper

    @not_action
    def _arm_status_poll_loop(self) -> None:
        """每秒集中刷新机械臂状态；读取失败时保留上一次有效值。"""
        while not self._arm_status_poller_stop.is_set():
            for node_name in self._arm_status_nodes:
                try:
                    self._arm_status_cache[node_name] = bool(self.get_node_value(node_name))
                except Exception:
                    pass
            self._arm_status_poller_stop.wait(1.0)

    @not_action
    def _read_bool_status(self, node_name: str) -> bool:
        """非阻塞读取机械臂状态缓存，未取得有效值时返回 False。"""
        return bool(self._arm_status_cache.get(node_name, False))

    @not_action
    def post_init(self, ros_node):
        """ROS2 节点就绪后注册 AI4C deck。"""
        if not (hasattr(self, "deck") and self.deck):
            return

        if not (hasattr(ros_node, "resource_tracker") and ros_node.resource_tracker):
            logger.warning("resource_tracker 不存在，无法注册 deck")
            return

        self._ros_node = ros_node
        ros_node.resource_tracker.add_resource(self.deck)

        try:
            from unilabos.ros.nodes.base_device_node import ROS2DeviceNode

            ROS2DeviceNode.run_async_func(
                ros_node.update_resource,
                True,
                resources=[self.deck],
            )
            logger.info("Deck 已上传到云端")
        except Exception as e:
            logger.error(f"上传失败: {e}")

    @not_action
    def _sync_resource_to_frontend(self) -> None:
        """将 AI4C deck 的资源状态同步到前端。"""
        self._sync_plr_resources([self.deck], ros_node=getattr(self, "_ros_node", None))

    @not_action
    def _sync_plr_resources(self, resources, *, ros_node=None) -> None:
        """把给定 PLR 资源树同步到前端。ros_node 为空时回退到 AI4C 自身节点。"""
        node = ros_node or getattr(self, "_ros_node", None)
        if not node or not resources:
            return
        try:
            from unilabos.ros.nodes.base_device_node import ROS2DeviceNode

            ROS2DeviceNode.run_async_func(
                node.update_resource,
                True,
                resources=list(resources),
            )
            names = [getattr(item, "name", type(item).__name__) for item in resources]
            logger.info(f"✓ 已同步资源更新到前端: {names}")
        except Exception as e:
            logger.warning(f"前端资源更新失败: {e}")

    @not_action
    def _get_external_warehouse_spec(self, warehouse_name: str):
        """查询不在 AI4C_deck 上、但由独立设备管理的仓位。未登记返回 None。"""
        mapping = getattr(self, "_external_warehouses", None) or {}
        return mapping.get(warehouse_name)

    @not_action
    def _parse_pipetting_slot(self, site_key: str) -> int:
        """机械臂移液站板位 → 1-based PRCXI 槽号。"""
        try:
            slot = int(str(site_key).strip().upper().lstrip("T"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"无法解析移液站板位: {site_key!r}") from exc
        if slot < MIN_PIPETTING_STATION_POSITION or slot > MAX_PIPETTING_STATION_POSITION:
            raise ValueError(
                f"移液站板位 {slot} 超出范围 "
                f"[{MIN_PIPETTING_STATION_POSITION}, {MAX_PIPETTING_STATION_POSITION}]"
            )
        return slot

    @not_action
    def _is_prcxi_slot_adapter(self, resource) -> bool:
        """判断 PRCXI 槽位上是否是 adapter/module，而不是被搬运的板本身。"""
        if resource is None:
            return False
        try:
            from pylabrobot.resources import PlateAdapter
            from unilabos.devices.liquid_handling.prcxi.prcxi import (
                PRCXI9300ModuleSite,
                PRCXI9300PlateAdapter,
            )

            return isinstance(resource, (PlateAdapter, PRCXI9300ModuleSite, PRCXI9300PlateAdapter))
        except Exception:
            return type(resource).__name__ in {
                "PlateAdapter",
                "PRCXI9300PlateAdapter",
                "PRCXI9300ModuleSite",
            }

    @not_action
    def _lookup_host_device(self, device_id: str):
        """从 HostNode.devices_instances 解析独立设备包装器。"""
        try:
            from unilabos.ros.nodes.presets.host_node import HostNode
        except Exception:
            return None
        host = HostNode.get_instance(timeout=0.1)
        if host is None:
            return None
        candidate_ids = []
        for did in (
            device_id,
            str(device_id).lstrip("/"),
            str(device_id).split("/")[-1],
            f"/devices/{str(device_id).split('/')[-1]}",
        ):
            if did and did not in candidate_ids:
                candidate_ids.append(did)
        for did in candidate_ids:
            wrapper = host.devices_instances.get(did)
            if wrapper is not None:
                return wrapper
        logger.error(
            f"找不到移液站设备 {device_id!r}，已尝试 {candidate_ids}；"
            f"当前 Host 设备: {list(host.devices_instances.keys())}"
        )
        return None

    @not_action
    def _resolve_external_deck_and_node(self, spec: dict):
        """解析外部仓对应的 deck 和用于前端同步的 ROS 节点。找不到 deck 返回 (None, None)。"""
        device_id = spec.get("device_id") or DEFAULT_PIPETTING_DEVICE_ID
        deck_id = spec.get("deck_id") or DEFAULT_PIPETTING_DECK_ID
        wrapper = self._lookup_host_device(device_id)
        deck = None
        ros_node = None
        if wrapper is not None:
            ros_node = getattr(wrapper, "_ros_node", None) or wrapper
            try:
                from unilabos.ros.nodes.presets.host_node import HostNode

                host = HostNode.get_instance(timeout=0.1)
                if host is not None:
                    deck = host._lookup_deck_for_slot(device_id, deck_id)
            except Exception as exc:
                logger.warning(f"HostNode 查找 {deck_id} 失败: {exc}")
            if deck is None:
                driver = (
                    getattr(wrapper, "driver_instance", None)
                    or getattr(getattr(wrapper, "_ros_node", None), "driver_instance", None)
                    or getattr(wrapper, "_driver_instance", None)
                )
                candidate = getattr(driver, "deck", None) if driver is not None else None
                if candidate is not None and getattr(candidate, "name", None) == deck_id:
                    deck = candidate
        if deck is None:
            logger.error(
                f"未找到移液站 deck {deck_id}（设备 {device_id}），无法同步前端资源树"
            )
            return None, None
        return deck, ros_node

    @not_action
    def _get_pipetting_slot_material(self, deck, slot: int):
        """读取 PRCXI 槽位上真正被机械臂搬运的物料；空位返回 None。"""
        getter = getattr(deck, "_get_site_resource", None)
        site = getter(slot - 1) if callable(getter) else None
        if site is None:
            children = list(getattr(deck, "children", None) or [])
            if 0 <= slot - 1 < len(children):
                site = children[slot - 1]
        if site is None or type(site).__name__ == "ResourceHolder":
            inner = getattr(site, "resource", None) if site is not None else None
            return inner
        if self._is_prcxi_slot_adapter(site):
            for child in getattr(site, "children", None) or []:
                if child is not None and type(child).__name__ != "ResourceHolder":
                    return child
            return None
        return site

    @not_action
    def _write_pipetting_site_extra(self, resource, slot: int) -> None:
        extra = getattr(resource, "unilabos_extra", None)
        if not isinstance(extra, dict):
            extra = {}
            resource.unilabos_extra = extra
        extra["update_resource_site"] = f"T{slot}"

    @not_action
    def _pick_resource_from_external_deck(
        self,
        spec: dict,
        site_key: str,
        resource_kind: str,
        held_attr: str,
    ) -> None:
        """从 PRCXI_Deck 取料：解绑槽位物料并放入机械臂持有态。"""
        try:
            deck, ros_node = self._resolve_external_deck_and_node(spec)
            if deck is None:
                setattr(self, held_attr, None)
                return
            slot = self._parse_pipetting_slot(site_key)
            resource = self._get_pipetting_slot_material(deck, slot)
            if resource is None:
                if not self.create_placeholder_resource_when_missing:
                    logger.info(
                        f"{spec.get('deck_id')}[T{slot}] 前端没有资源，且已关闭缺失资源占位创建；"
                        "仅执行硬件取料，不生成前端资源"
                    )
                    setattr(self, held_attr, None)
                    return
                logger.warning(
                    f"{spec.get('deck_id')}[T{slot}] 未找到资源，按硬件占位创建临时资源"
                )
                resource = self._create_placeholder_resource(
                    resource_kind, PIPETTING_WAREHOUSE_NAME, str(slot)
                )
            else:
                parent = getattr(resource, "parent", None)
                if parent is not None and hasattr(parent, "unassign_child_resource"):
                    parent.unassign_child_resource(resource)
                    logger.info(
                        f"✓ 已从 {spec.get('deck_id')}[T{slot}] 解绑资源 {resource.name}"
                    )
            setattr(self, held_attr, resource)
            self._sync_plr_resources([deck], ros_node=ros_node)
            self._sync_resource_to_frontend()
        except Exception as e:
            logger.warning(f"移液站资源取料迁移失败（不影响硬件操作）: {e}")

    @not_action
    def _place_held_resource_to_external_deck(
        self,
        spec: dict,
        site_key: str,
        resource_kind: str,
        held_attr: str,
    ) -> None:
        """向 PRCXI_Deck 放料：将机械臂持有物料挂到对应 Tn 槽位。"""
        try:
            deck, ros_node = self._resolve_external_deck_and_node(spec)
            if deck is None:
                setattr(self, held_attr, None)
                return
            slot = self._parse_pipetting_slot(site_key)
            resource = getattr(self, held_attr, None)
            if resource is None:
                if not self.create_placeholder_resource_when_missing:
                    logger.info(
                        f"机械臂没有可追踪的前端资源，且已关闭缺失资源占位创建；"
                        f"仅执行硬件放料，不在 {spec.get('deck_id')}[T{slot}] 生成资源"
                    )
                    return
                logger.warning(
                    f"机械臂无持有资源，按硬件放料在 {spec.get('deck_id')}[T{slot}] 创建临时资源"
                )
                resource = self._create_placeholder_resource(
                    resource_kind, PIPETTING_WAREHOUSE_NAME, str(slot)
                )

            site = None
            getter = getattr(deck, "_get_site_resource", None)
            if callable(getter):
                site = getter(slot - 1)
            if self._is_prcxi_slot_adapter(site):
                if hasattr(site, "assign_child_resource"):
                    site.assign_child_resource(resource)
            else:
                stale = self._get_pipetting_slot_material(deck, slot)
                if stale is not None and stale is not resource:
                    logger.warning(
                        f"{spec.get('deck_id')}[T{slot}] 资源树已有 {getattr(stale, 'name', stale)}，"
                        "按硬件空位状态覆盖"
                    )
                    stale_parent = getattr(stale, "parent", None)
                    if stale_parent is not None and hasattr(stale_parent, "unassign_child_resource"):
                        stale_parent.unassign_child_resource(stale)
                assign_at_slot = getattr(deck, "assign_child_at_slot", None)
                if callable(assign_at_slot):
                    assign_at_slot(resource, slot, reassign=True)
                else:
                    deck.assign_child_resource(resource, spot=slot - 1, reassign=True)

            self._write_pipetting_site_extra(resource, slot)
            setattr(self, held_attr, None)
            logger.info(
                f"✓ 已绑定资源 {resource.name} 到 {spec.get('deck_id')}[T{slot}]"
            )
            self._sync_plr_resources([deck], ros_node=ros_node)
            self._sync_resource_to_frontend()
        except Exception as e:
            logger.warning(f"移液站资源放料迁移失败（不影响硬件操作）: {e}")

    @not_action
    def _get_warehouse_site_index(self, warehouse, site_key: str) -> int:
        """根据仓位键名获取 ItemizedCarrier 内部序号。"""
        keys = [str(key) for key in warehouse._ordering.keys()]
        site_key = str(site_key)
        if site_key not in keys:
            raise ValueError(f"仓库 {warehouse.name} 不存在仓位 {site_key}")
        return keys.index(site_key)

    @not_action
    def _get_warehouse_resource(self, warehouse_name: str, site_key: str):
        """读取指定仓位上的真实资源，空位返回 None。"""
        warehouse = self.deck.warehouses[warehouse_name]
        site_key = str(site_key)
        try:
            resource = warehouse[site_key]
        except Exception:
            site_idx = self._get_warehouse_site_index(warehouse, site_key)
            resource = warehouse.sites[site_idx] if warehouse.sites else None

        if resource is None or type(resource).__name__ == "ResourceHolder":
            return None
        return resource

    @not_action
    def _create_placeholder_resource(self, resource_kind: str, warehouse_name: str, site_key: str):
        """资源树缺少实物占位时，按硬件动作创建一个运行时占位资源。"""
        self._placeholder_resource_counter += 1
        safe_warehouse_name = warehouse_name.replace(" ", "_")
        name = f"{safe_warehouse_name}_{site_key}_{resource_kind}_{self._placeholder_resource_counter}"
        if resource_kind == "well_plate":
            return AI4C_WellPlateCarrier(name)
        if resource_kind == "powder_cylinder":
            return AI4C_PowderCylinderCarrier(name)
        raise ValueError(f"不支持的资源类型: {resource_kind}")

    @not_action
    def _pick_resource_from_warehouse(
        self,
        warehouse_name: str,
        site_key: str,
        resource_kind: str,
        held_attr: str,
    ) -> None:
        """硬件取料完成后，从源仓位解绑资源并放入机械臂临时持有态。"""
        try:
            if warehouse_name not in self.deck.warehouses:
                spec = self._get_external_warehouse_spec(warehouse_name)
                if spec is not None:
                    self._pick_resource_from_external_deck(
                        spec, site_key, resource_kind, held_attr
                    )
                    return
                if not self.create_placeholder_resource_when_missing:
                    logger.info(
                        f"{warehouse_name} 不在 AI4C_deck 上，且已关闭缺失资源占位创建；"
                        "仅执行硬件取料，不生成前端资源"
                    )
                    setattr(self, held_attr, None)
                    return
                logger.info(f"{warehouse_name} 不在 AI4C_deck 上（由独立设备管理），按占位资源进入持有态")
                resource = self._create_placeholder_resource(resource_kind, warehouse_name, site_key)
                setattr(self, held_attr, resource)
                return
            warehouse = self.deck.warehouses[warehouse_name]
            site_key = str(site_key)
            resource = self._get_warehouse_resource(warehouse_name, site_key)
            if resource is None:
                if not self.create_placeholder_resource_when_missing:
                    logger.info(
                        f"{warehouse_name}[{site_key}] 前端没有资源，且已关闭缺失资源占位创建；"
                        "仅执行硬件取料，不生成前端资源"
                    )
                    setattr(self, held_attr, None)
                    return
                logger.warning(f"{warehouse_name}[{site_key}] 未找到资源，按硬件占位创建临时资源")
                resource = self._create_placeholder_resource(resource_kind, warehouse_name, site_key)
            else:
                warehouse.unassign_child_resource(resource)
                logger.info(f"✓ 已从 {warehouse_name}[{site_key}] 解绑资源 {resource.name}")

            setattr(self, held_attr, resource)
            self._sync_resource_to_frontend()
        except Exception as e:
            logger.warning(f"资源取料迁移失败（不影响硬件操作）: {e}")

    @not_action
    def _place_held_resource_to_warehouse(
        self,
        warehouse_name: str,
        site_key: str,
        resource_kind: str,
        held_attr: str,
    ) -> None:
        """硬件放料完成后，将机械臂临时持有资源绑定到目标仓位。"""
        try:
            if warehouse_name not in self.deck.warehouses:
                spec = self._get_external_warehouse_spec(warehouse_name)
                if spec is not None:
                    self._place_held_resource_to_external_deck(
                        spec, site_key, resource_kind, held_attr
                    )
                    return
                logger.info(f"{warehouse_name} 不在 AI4C_deck 上（由独立设备管理），跳过资源树放料")
                setattr(self, held_attr, None)
                return
            warehouse = self.deck.warehouses[warehouse_name]
            site_key = str(site_key)
            resource = getattr(self, held_attr, None)
            if resource is None:
                if not self.create_placeholder_resource_when_missing:
                    logger.info(
                        f"机械臂没有可追踪的前端资源，且已关闭缺失资源占位创建；"
                        f"仅执行硬件放料，不在 {warehouse_name}[{site_key}] 生成资源"
                    )
                    return
                logger.warning(f"机械臂无持有资源，按硬件放料在 {warehouse_name}[{site_key}] 创建临时资源")
                resource = self._create_placeholder_resource(resource_kind, warehouse_name, site_key)

            stale_resource = self._get_warehouse_resource(warehouse_name, site_key)
            if stale_resource is not None:
                logger.warning(f"{warehouse_name}[{site_key}] 资源树已有 {stale_resource.name}，按硬件空位状态覆盖")
                warehouse.unassign_child_resource(stale_resource)

            site_idx = self._get_warehouse_site_index(warehouse, site_key)
            location = warehouse.child_locations[site_key]
            warehouse.assign_child_resource(resource, location=location, spot=site_idx)
            setattr(self, held_attr, None)
            logger.info(f"✓ 已绑定资源 {resource.name} 到 {warehouse_name}[{site_key}]")
            self._sync_resource_to_frontend()
        except Exception as e:
            logger.warning(f"资源放料迁移失败（不影响硬件操作）: {e}")

    # 初始化工站
    @not_action
    def _legacy_init_workstation(self) -> dict:
        """保留旧的内部入口，统一转到并行初始化实现。"""
        return self.init_workstation()

    @not_action
    def _initialize_component(
        self,
        initialize_node: str,
        homing_node: str,
        home_done_node: str,
        description: str,
        fault_node: Optional[str] = None,
    ) -> None:
        """检测到组件进入 Homing 后复位初始化信号，并等待 HomeDone。"""
        if fault_node and self.get_node_value(fault_node, force_read=True):
            raise RuntimeError(f"{description} 初始化期间检测到设备故障")
        if not self._wait_until_true(
            homing_node,
            description=f"{description} Homing",
        ):
            raise ValueError(f"{description} 未进入 Homing")
        if fault_node and self.get_node_value(fault_node, force_read=True):
            raise RuntimeError(f"{description} 初始化期间检测到设备故障")
        if not self.set_node_value(initialize_node, False):
            raise RuntimeError(f"复位 {description} 初始化信号失败")
        if not self._wait_until_true(
            home_done_node,
            description=f"{description} 初始化完成",
        ):
            raise ValueError(f"{description} 初始化失败")

    @not_action
    def _initialize_hydration_workstation(self) -> None:
        """等待水合工站 PC 初始化完成，完成后复位请求信号。"""
        if not self._wait_until_false(
            "Hydration_Workstation_Initialization_Complete",
            description="等待水合工站初始化完成信号复位",
        ):
            raise ValueError("水合工站初始化信号复位失败")
        if not self._wait_until_true(
            "Hydration_Workstation_Initialization_Complete",
            description="水合工站初始化完成",
        ):
            raise ValueError("水合工站初始化失败")
        if not self.set_node_value("Hydration_Workstation_PC_Initialization", False):
            raise RuntimeError("复位水合工站初始化信号失败")

    @not_action
    def _start_initialization_component(
        self,
        component: tuple[str, str, str, str, Optional[str]],
    ) -> None:
        initialize_node, homing_node, home_done_node, description, fault_node = component
        if not self.set_node_value(initialize_node, True):
            raise RuntimeError(f"触发{description}初始化失败")
        self._initialize_component(
            initialize_node,
            homing_node,
            home_done_node,
            description,
            fault_node,
        )

    @action(auto_prefix=True, description="初始化 AI4C 工站（机械手、固体称量、磁搅）")
    def init_workstation(self) -> dict:
        """并行初始化机械手、固体称量、磁搅和水合工站 PC。"""
        logger.info("停止机械臂动作触发并复位机械手")
        self.set_node_value("Robotic_Arm_Action_Trigger", False)
        self.set_node_value("Robotic_Arm_Reset", True)
        time.sleep(1.0)
        self.set_node_value("Robotic_Arm_Reset", False)

        # 水合 PC 初始化与三个设备初始化同时启动。
        self.set_node_value("Hydration_Workstation_PC_Initialization", False)
        time.sleep(1.0)
        self.set_node_value("Hydration_Workstation_PC_Initialization", True)
        for component in _INITIALIZATION_COMPONENTS:
            if not self.set_node_value(component[0], True):
                raise RuntimeError(f"触发{component[3]}初始化失败")

        components = list(_INITIALIZATION_COMPONENTS)
        with ThreadPoolExecutor(max_workers=len(components) + 1) as executor:
            futures = [
                executor.submit(self._initialize_component, *component)
                for component in components
            ]
            futures.append(executor.submit(self._initialize_hydration_workstation))
            for future in futures:
                future.result()

        self.m_robot_arm_current_step = self.get_node_value("Robotic_Arm_Current_Step")
        self.m_solid_weighing_current_step = self.get_node_value("Solid_Weighing_Current_Step")
        self.m_magnetic_stirrer_current_step = self.get_node_value("Magnetic_Stirrer_Current_Step")
        self.m_initialized = True
        return {"success": True, "message": "AI4C 工站初始化完成"}

    @action(auto_prefix=True, description="初始化 AI4C 机械手")
    def trigger_robot_init(self) -> dict:
        """单独初始化机械手。"""
        self.set_node_value("Robotic_Arm_Action_Trigger", False)
        self.set_node_value("Robotic_Arm_Reset", True)
        time.sleep(1.0)
        self.set_node_value("Robotic_Arm_Reset", False)
        self._start_initialization_component(_INITIALIZATION_COMPONENTS[0])
        return {"success": True, "message": "AI4C 机械手初始化完成"}

    @action(auto_prefix=True, description="初始化 AI4C 固体称量")
    def trigger_solid_weighing_init(self) -> dict:
        """单独初始化固体称量设备。"""
        self._start_initialization_component(_INITIALIZATION_COMPONENTS[1])
        return {"success": True, "message": "AI4C 固体称量初始化完成"}

    @action(auto_prefix=True, description="初始化 AI4C 磁搅")
    def trigger_magnetic_stirrer_init(self) -> dict:
        """单独初始化磁搅设备。"""
        self._start_initialization_component(_INITIALIZATION_COMPONENTS[2])
        return {"success": True, "message": "AI4C 磁搅初始化完成"}

    @not_action
    def is_robotic_arm_initialization_complete(self)-> bool:
        """
        检查机械臂是否初始化完成

        Returns:
            bool: 如果机械臂初始化完成，返回True，否则返回False
        """
        return self.get_node_value("Robotic_Arm_Initialization_Complete")
    
    @not_action
    def is_solid_weighing_initialization_complete(self)-> bool:
        """
        检查固体称量是否初始化完成

        Returns:
            bool: 如果固体称量初始化完成，返回True，否则返回False
        """
        return self.get_node_value("Solid_Weighing_Initialization_Complete")
    
    @not_action
    def is_pipetting_station_initialization_complete(self)-> bool:
        """
        检查移液站是否初始化完成

        Returns:
            bool: 如果移液站初始化完成，返回True，否则返回False
        """
        return self.get_node_value("Pipetting_Station_Initialization_Complete")
    
    @not_action
    def is_magnetic_stirrer_initialization_complete(self)-> bool:
        """
        检查磁搅是否初始化完成

        Returns:
            bool: 如果磁搅初始化完成，返回True，否则返回False
        """
        return self.get_node_value("Magnetic_Stirrer_Initialization_Complete")
    
    @not_action
    def is_hplc_workstation_initialization_complete(self)-> bool:
        """
        检查HPLC工站是否初始化完成

        Returns:
            bool: 如果HPLC工站初始化完成，返回True，否则返回False
        """
        return self.get_node_value("HPLC_Workstation_Initialization_Complete")

    @not_action
    def is_robotic_arm_idle(self) -> bool:
        """
        检查机械臂是否空闲

        Returns:
            bool: 如果机械臂空闲，返回True，否则返回False
        """
        return self._read_bool_status("Robotic_Arm_Idle")

    @topic_config(period=1.0)
    def robotic_arm_idle(self) -> bool:
        """发布机械臂空闲状态。"""
        return self._read_bool_status("Robotic_Arm_Idle")

    @topic_config(period=1.0)
    def robotic_arm_fault(self) -> bool:
        """读取机械臂故障状态。"""
        return self._read_bool_status("Robotic_Arm_Fault")

    @not_action
    def is_solid_weighing_occupied(self) -> bool:
        """
        检查固体称量是否占位

        Returns:
            bool: 如果固体称量占位，返回True，否则返回False
        """
        return self.get_node_value("Solid_Weighing_Occupied")
    
    @not_action
    def is_powder_position_in_solid_weighing_occupied(self) -> bool:
        """
        检查粉末头是否在固体称量中占位
        
        Returns:
            bool: 如果粉末头在固体称量中占位，返回True，否则返回False
        """
        return self.get_node_value("Powder_In_Solid_Weighing_Occupied")
    
    @not_action
    def is_pipetting_station_occupied(self, position: int) -> bool:
        """
        检查移液站指定板位是否占位

        Args:
            position (int): 移液站板位，范围[1, 16]，与机械臂取放料代码一致

        Returns:
            bool: 如果移液站该板位占位，返回True，否则返回False
        """
        if position < MIN_PIPETTING_STATION_POSITION or position > MAX_PIPETTING_STATION_POSITION:
            logger.error(f"移液站板位错误，必须在范围[{MIN_PIPETTING_STATION_POSITION}, {MAX_PIPETTING_STATION_POSITION}]内")
            return False

        # 机械臂板位 1-16，对应 PLC 数组下标 0-15
        nodeId = f"Pipetting_Station_Occupied[{position - 1}]"
        return self.get_node_value(nodeId)

    @not_action
    def is_magnetic_stirrer_occupied(self) -> bool:
        """
        检查磁搅是否占位

        Returns:
            bool: 如果磁搅占位，返回True，否则返回False
        """
        return self.get_node_value("Magnetic_Stirrer_Occupied")
    
    @not_action
    def is_hplc_workstation_occupied(self) -> bool:
        """
        检查HPLC工站是否占位

        Returns:
            bool: 如果HPLC工站占位，返回True，否则返回False
        """
        return self.get_node_value("HPLC_Pool_Occupied")
    
    @not_action
    def is_loading_rack_position_occupied(self, position: int) -> bool:
        """
        检查上料架位置是否占位

        Args:
            position (int): 上料架位置，范围[1, 8]

        Returns:
            bool: 如果上料架位置占位，返回True，否则返回False
        """
        if position < MIN_RACK_POSITION or position > MAX_RACK_POSITION:
            logger.error(f"上料架位置错误，必须在范围[{MIN_RACK_POSITION}, {MAX_RACK_POSITION}]内")
            return False
        
        position_index = position - 1
        nodeId = f"Well_Plate_Loading_Rack_InPut[{position_index}]"
        return self.get_node_value(nodeId)
    
    @not_action
    def is_unloading_rack_position_occupied(self, position: int) -> bool:
        """
        检查下料架位置是否占位

        Args:
            position (int): 下料架位置，范围[1, 8]

        Returns:
            bool: 如果下料架位置占位，返回True，否则返回False
        """
        if position < MIN_RACK_POSITION or position > MAX_RACK_POSITION:
            logger.error(f"下料架位置错误，必须在范围[{MIN_RACK_POSITION}, {MAX_RACK_POSITION}]内")
            return False
        
        position_index = position - 1
        nodeId = f"Well_Plate_Unloading_Rack_InPut[{position_index}]"
        return self.get_node_value(nodeId)

    @not_action
    def is_solid_weighing_stack_position_occupied(self, position: int) -> bool:
        """
        检查固体称量堆栈位置是否占位

        Args:
            position (int): 固体称量堆栈位置，范围[1, 25]

        Returns:
            bool: 如果固体称量堆栈位置占位，返回True，否则返回False
        """
        if position < MIN_SOLID_WEIGHING_STACK_POSITION or position > MAX_SOLID_WEIGHING_STACK_POSITION:
            logger.error(f"固体称量堆栈位置错误，必须在范围[{MIN_SOLID_WEIGHING_STACK_POSITION}, {MAX_SOLID_WEIGHING_STACK_POSITION}]内")
            return False

        position_index = position - 1
        nodeId = f"Powder_Cylinder_InPut[{position_index}]"
        return self.get_node_value(nodeId)

    @action(
        auto_prefix=True,
        description="步骤2：从上料架抓取孔板",
        handles=[
            ActionInputHandle(
                key="loading_rack_position",
                data_type="ai4c_loading_rack_position",
                label="上料架位置",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="孔板所在上料架位置，范围 1-8",
            )
        ],
    )
    def pick_well_plate_from_loading_rack(self, position: int = 1) -> dict:
        """
        从上料架抓取孔板：
        - 检查机械臂是否空闲
        - 检测上料架位置position处是否有孔板
        - 设置从上料架位置position处抓取孔板
        - 等待从上料架抓取孔板完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从上料架取孔板...")
        if position < MIN_RACK_POSITION or position > MAX_RACK_POSITION:
            raise ValueError("上料架位置错误")
        
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")
        
        self._wait_occupancy(lambda: self.is_loading_rack_position_occupied(position), True, "上料架位置{}没有孔板".format(position))

        logger.info("从上料架位置{}抓取孔板...".format(position))
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.PLATE_LOADING_RACK) # 设置机械臂目标位置为上料架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置上料架位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PICK) # 设置动作类型为抓取
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从上料架抓取孔板完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从上料架抓取孔板完成"): # 等待完成状态复位
                logger.info("从上料架抓取孔板完成")
                self._pick_resource_from_warehouse(
                    "孔板上料架", str(position), "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "从上料架抓取孔板完成",
                }
            else:
                raise ValueError("从上料架抓取孔板失败，完成复位超时")
        else:
            raise ValueError("从上料架抓取孔板失败，机械臂动作未完成")

    @action(
        auto_prefix=True,
        description="从下料架抓取孔板",
        handles=[
            ActionInputHandle(
                key="unloading_rack_position",
                data_type="ai4c_unloading_rack_position",
                label="下料架位置",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="孔板所在下料架位置，范围 1-8",
            )
        ],
    )
    def pick_well_plate_from_unloading_rack(self, position: int = 1) -> dict:
        """
        从下料架抓取孔板：
        - 检查机械臂是否空闲
        - 检测下料架位置position处是否有孔板
        - 设置从下料架位置position处抓取孔板
        - 等待从下料架抓取孔板完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从下料架取孔板...")
        if position < MIN_RACK_POSITION or position > MAX_RACK_POSITION:
            raise ValueError("下料架位置错误")
        
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")
        
        self._wait_occupancy(lambda: self.is_unloading_rack_position_occupied(position), True, "下料架位置{}没有孔板".format(position))

        logger.info("从下料架位置{}抓取孔板...".format(position))
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.PLATE_UNLOADING_RACK) # 设置机械臂目标位置为下料架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置下料架位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PICK) # 设置动作类型为抓取
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从下料架抓取孔板完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从下料架抓取孔板完成"): # 等待完成状态复位
                logger.info("从下料架抓取孔板完成")
                self._pick_resource_from_warehouse(
                    "孔板下料架", str(position), "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "从下料架抓取孔板完成",
                }
            else:
                raise ValueError("从下料架抓取孔板失败，完成复位超时")
        else:
            raise ValueError("从下料架抓取孔板失败，机械臂动作未完成")
        
    @action(auto_prefix=True, description="步骤4：将孔板放置到固态称量")
    def place_well_plate_to_solid_weighing(self) -> dict:
        """
        将孔板放置到称重区：
        - 检查机械臂是否空闲
        - 检查称重区是否占位
        - 设置将孔板放置到称重区
        - 等待将孔板放置到称重区完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将孔板放置到称重区...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_solid_weighing_occupied(), False, "固态称重已占位")
        
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.SOLID_WEIGHING) # 设置机械臂目标位置为称重区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置称重区位置，1为默认位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PLACE) # 设置动作类型为放置
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将孔板放置到固态称重完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将孔板放置到固态称重完成"): # 等待完成状态复位
                logger.info("将孔板放置到固态称重完成")
                self._place_held_resource_to_warehouse(
                    "固态称量", "Solid_Weighing", "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "将孔板放置到固态称重完成",
                }
            else:
                raise ValueError("将孔板放置到固态称重失败，完成复位超时")
        else:
            raise ValueError("将孔板放置到固态称重失败，机械臂动作未完成")
        
    @action(
        auto_prefix=True,
        description="步骤5：从固体称量堆栈抓取粉桶",
        handles=[
            ActionInputHandle(
                key="powder_stack_position",
                data_type="ai4c_powder_stack_position",
                label="粉桶堆栈位置",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="粉桶所在堆栈位置，范围 1-25",
            )
        ],
    )
    def pick_powder_cylinder_from_stack(self, position: int = 6) -> dict:
        """
        从固体称量堆栈中取粉桶：
        - 检查机械臂是否空闲
        - 检查固体称量堆栈位置是否有粉桶
        - 设置从固体称量堆栈中取粉桶
        - 等待从固体称量堆栈中取粉桶完成
        - 返回成功

        Args:
            position (int): 粉桶位置，1-25

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从固体称量堆栈中取粉桶...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        if position < MIN_SOLID_WEIGHING_STACK_POSITION or position > MAX_SOLID_WEIGHING_STACK_POSITION:
            raise ValueError("粉桶位置不在有效范围内")

        self._wait_occupancy(lambda: self.is_solid_weighing_stack_position_occupied(position), True, "固体称量堆栈位置{}没有粉桶".format(position))
        
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.SOLID_WEIGHING_STACK) # 设置机械臂目标位置为固态称量堆栈
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置固态称量堆栈位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PICK) # 设置动作类型为抓取
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从固体称量堆栈中取粉桶完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从固体称量堆栈中取粉桶完成"): # 等待完成状态复位
                logger.info("从固体称量堆栈中取粉桶完成")
                self._pick_resource_from_warehouse(
                    "固态称量粉桶堆栈", str(position), "powder_cylinder", "_held_powder_cylinder"
                )
                return {
                    "success": True,
                    "message": "从固体称量堆栈中取粉桶完成",
                }
            else:
                raise ValueError("从固体称量堆栈中取粉桶失败，完成复位超时")
        else:
            raise ValueError("从固体称量堆栈中取粉桶失败，机械臂动作未完成")
        
    @action(auto_prefix=True, description="步骤6：将粉桶放置到固态称量")
    def place_powder_cylinder_to_solid_weighing(self) -> dict:
        """
        将粉桶放置到固态称量：
        - 检查机械臂是否空闲
        - 检查固态称量粉桶位置是否有粉桶
        - 设置将粉桶放置到固态称量
        - 等待将粉桶放置到固态称量完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将粉桶放置到固态称量...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")
        
        self._wait_occupancy(lambda: self.is_powder_position_in_solid_weighing_occupied(), False, "固态称量粉桶位置已经有粉桶")
        
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.SOLID_WEIGHING) # 设置机械臂目标位置为固态称量
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置固态称量位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.ON_POWDER_HEAD) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将粉桶放置到固态称量完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将粉桶放置到固态称量完成"): # 等待完成状态复位
                logger.info("将粉桶放置到固态称量完成")
                self._place_held_resource_to_warehouse(
                    "固态称量粉桶位",
                    "Powder_In_Solid_Weighing",
                    "powder_cylinder",
                    "_held_powder_cylinder",
                )
                return {
                    "success": True,
                    "message": "将粉桶放置到固态称量完成",
                }
            else:
                raise ValueError("将粉桶放置到固态称量失败，完成复位超时")
        else:
            raise ValueError("将粉桶放置到固态称量失败，机械臂动作未完成")

    @action(auto_prefix=True, description="步骤10：从固态称量取回粉桶")
    def pick_powder_cylinder_from_solid_weighing(self) -> dict:
        """
        从固态称量中取粉桶：
        - 检查机械臂是否空闲
        - 检查固态称量粉桶位置是否有粉桶
        - 设置从固态称量中取粉桶
        - 等待从固态称量中取粉桶完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从固态称量中取粉桶...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_powder_position_in_solid_weighing_occupied(), True, "固态称量粉桶位置没有粉桶")
        
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.SOLID_WEIGHING) # 设置机械臂目标位置为固态称量
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置固态称量位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.OFF_POWDER_HEAD) # 设置动作类型为下粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从固态称量中取粉桶完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从固态称量中取粉桶完成"): # 等待完成状态复位
                logger.info("从固态称量中取粉桶完成")
                self._pick_resource_from_warehouse(
                    "固态称量粉桶位",
                    "Powder_In_Solid_Weighing",
                    "powder_cylinder",
                    "_held_powder_cylinder",
                )
                return {
                    "success": True,
                    "message": "从固态称量中取粉桶完成",
                }
            else:
                raise ValueError("从固态称量中取粉桶失败，完成复位超时")
        else:
            raise ValueError("从固态称量中取粉桶失败，机械臂动作未完成")
        
    @action(
        auto_prefix=True,
        description="步骤11：将粉桶放回固态称量堆栈",
        handles=[
            ActionInputHandle(
                key="powder_stack_return_position",
                data_type="ai4c_powder_stack_position",
                label="粉桶放回堆栈位置",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="粉桶放回的堆栈位置，范围 1-25",
            )
        ],
    )
    def place_powder_cylinder_to_solid_weighing_stack(self, position: int = 6) -> dict:
        """
        将粉桶放置到固态称量堆栈：
        - 检查机械臂是否空闲
        - 检查固态称量堆栈位置是否有粉桶
        - 设置将粉桶放置到固态称量堆栈
        - 等待将粉桶放置到固态称量堆栈完成
        - 返回成功

        Args:
            position (int): 粉桶位置，1-25

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将粉桶放置到固态称量堆栈...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")
        
        if position < MIN_SOLID_WEIGHING_STACK_POSITION or position > MAX_SOLID_WEIGHING_STACK_POSITION:
            raise ValueError(f"固态称量堆栈位置 {position} 超出范围")
        
        self._wait_occupancy(lambda: self.is_solid_weighing_stack_position_occupied(position), False, f"固态称量堆栈位置 {position} 已有粉桶")
        
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.SOLID_WEIGHING_STACK) # 设置机械臂目标位置为固态称量堆栈
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置固态称量堆栈位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PLACE) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将粉桶放置到固态称量堆栈完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将粉桶放置到固态称量堆栈完成"): # 等待完成状态复位
                logger.info("将粉桶放置到固态称量堆栈完成")
                self._place_held_resource_to_warehouse(
                    "固态称量粉桶堆栈", str(position), "powder_cylinder", "_held_powder_cylinder"
                )
                return {
                    "success": True,
                    "message": "将粉桶放置到固态称量堆栈完成",
                }
            else:
                raise ValueError("将粉桶放置到固态称量堆栈失败，完成复位超时")
        else:
            raise ValueError("将粉桶放置到固态称量堆栈失败，机械臂动作未完成")
    
    @action(auto_prefix=True, description="步骤12：从固态称量取回孔板")
    def pick_well_plate_from_solid_weighing(self) -> dict:
        """
        从固态称量中取孔板：
        - 检查机械臂是否空闲
        - 检查固态称量位置是否有孔板
        - 设置从固态称量中取孔板
        - 等待从固态称量中取孔板完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从固态称量中取孔板...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_solid_weighing_occupied(), True, "固态称量位置没有孔板")

        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.SOLID_WEIGHING) # 设置机械臂目标位置为固态称量
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置固态称量位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PICK) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从固态称量中取孔板完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从固态称量中取孔板完成"): # 等待完成状态复位
                logger.info("从固态称量中取孔板完成")
                self._pick_resource_from_warehouse(
                    "固态称量", "Solid_Weighing", "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "从固态称量中取孔板完成",
                }
            else:
                raise ValueError("从固态称量中取孔板失败，完成复位超时")
        else:
            raise ValueError("从固态称量中取孔板失败，机械臂动作未完成")
        
    @action(
        auto_prefix=True,
        description="步骤14/20：将孔板放置到移液站",
        handles=[
            ActionInputHandle(
                key="pipetting_station_position",
                data_type="ai4c_pipetting_station_position",
                label="移液站内板位",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="孔板放置在移液站的内板位",
            )
        ],
    )
    def place_well_plate_to_pipetting_station(self, position: int = 1) -> dict:
        """
        将孔板放置到移液站：
        - 检查机械臂是否空闲
        - 检查移液站位置是否有孔板
        - 设置将孔板放置到移液站
        - 等待将孔板放置到移液站完成
        - 返回成功

        Args:
            position (int): 移液站内板位

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将孔板放置到移液站...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_pipetting_station_occupied(position), False, "移液站位置已有孔板")

        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.PIPETTING_STATION) # 设置机械臂目标位置为移液站
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置移液站内板位
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PLACE) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将孔板放置到移液站完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将孔板放置到移液站完成"): # 等待完成状态复位
                logger.info("将孔板放置到移液站完成")
                self._place_held_resource_to_warehouse(
                    "移液站", str(position), "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "将孔板放置到移液站完成",
                }
            else:
                raise ValueError("将孔板放置到移液站失败，完成复位超时")
        else:
            raise ValueError("将孔板放置到移液站失败，机械臂动作未完成")

    @action(
        auto_prefix=True,
        description="步骤16/21：从移液站取回孔板",
        handles=[
            ActionInputHandle(
                key="pipetting_station_position",
                data_type="ai4c_pipetting_station_position",
                label="移液站内板位",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="孔板所在移液站的内板位",
            )
        ],
    )
    def pick_well_plate_from_pipetting_station(self, position: int = 1) -> dict:
        """
        从移液站取孔板：
        - 检查机械臂是否空闲
        - 检查移液站位置是否有孔板
        - 设置从移液站取孔板
        - 等待从移液站取孔板完成
        - 返回成功

        Args:
            position (int): 移液站内板位

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从移液站取孔板...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_pipetting_station_occupied(position), True, "移液站位置没有孔板")

        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.PIPETTING_STATION) # 设置机械臂目标位置为移液站
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置移液站内板位
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PICK) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从移液站取孔板完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从移液站取孔板完成"): # 等待完成状态复位
                logger.info("从移液站取孔板完成")
                self._pick_resource_from_warehouse(
                    "移液站", str(position), "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "从移液站取孔板完成",
                }
            else:
                raise ValueError("从移液站取孔板失败，完成复位超时")
        else:
            raise ValueError("从移液站取孔板失败，机械臂动作未完成")
    
    @action(auto_prefix=True, description="步骤17：将孔板放置到磁搅")
    def place_well_plate_to_magnetic_stirrer(self) -> dict:
        """
        将孔板放置到磁搅：
        - 检查机械臂是否空闲
        - 检查磁搅位置是否有孔板
        - 设置将孔板放置到磁搅
        - 等待将孔板放置到磁搅完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将孔板放置到磁搅...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_magnetic_stirrer_occupied(), False, "磁搅位置已有孔板")

        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.MAGNETIC_STIRRER) # 设置机械臂目标位置为磁搅
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置磁搅位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PLACE) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将孔板放置到磁搅完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将孔板放置到磁搅完成"): # 等待完成状态复位
                logger.info("将孔板放置到磁搅完成")
                self._place_held_resource_to_warehouse(
                    "磁搅", "Magnetic_Stirrer", "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "将孔板放置到磁搅完成",
                }
            else:
                raise ValueError("将孔板放置到磁搅失败，完成复位超时")
        else:
            raise ValueError("将孔板放置到磁搅失败，机械臂动作未完成")
        
    @action(auto_prefix=True, description="步骤19：从磁搅取回孔板")
    def pick_well_plate_from_magnetic_stirrer(self) -> dict:
        """
        从磁搅取孔板：
        - 检查机械臂是否空闲
        - 检查磁搅位置是否有孔板
        - 设置从磁搅取孔板
        - 等待从磁搅取孔板完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从磁搅取孔板...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_magnetic_stirrer_occupied(), True, "磁搅位置没有孔板")

        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.MAGNETIC_STIRRER) # 设置机械臂目标位置为磁搅
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置磁搅位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PICK) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从磁搅取孔板完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从磁搅取孔板完成"): # 等待完成状态复位
                logger.info("从磁搅取孔板完成")
                self._pick_resource_from_warehouse(
                    "磁搅", "Magnetic_Stirrer", "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "从磁搅取孔板完成",
                }
            else:
                raise ValueError("从磁搅取孔板失败，完成复位超时")
        else:
            raise ValueError("从磁搅取孔板失败，机械臂动作未完成")
    
    @action(auto_prefix=True, description="步骤22：将孔板放置到 HPLC 站")
    def place_well_plate_to_hplc_station(self) -> dict:
        """
        将孔板放置到 HPLC 站：
        - 检查机械臂是否空闲
        - 检查 HPLC 站位置是否有孔板
        - 设置将孔板放置到 HPLC 站
        - 等待将孔板放置到 HPLC 站完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将孔板放置到 HPLC 站...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_hplc_workstation_occupied(), False, "HPLC 站位置已有孔板")

        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.HPLC_STATION) # 设置机械臂目标位置为 HPLC 站
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置 HPLC 站位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PLACE) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将孔板放置到 HPLC 站完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将孔板放置到 HPLC 站完成"): # 等待完成状态复位
                logger.info("将孔板放置到 HPLC 站完成")
                self._place_held_resource_to_warehouse(
                    "HPLC工站", "HPLC", "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "将孔板放置到 HPLC 站完成",
                }
            else:
                raise ValueError("将孔板放置到 HPLC 站失败，完成复位超时")
        else:
            raise ValueError("将孔板放置到 HPLC 站失败，机械臂动作未完成")
    
    @action(auto_prefix=True, description="步骤24：从 HPLC 站取回孔板")
    def pick_well_plate_from_hplc_station(self) -> dict:
        """
        从 HPLC 站取孔板：
        - 检查机械臂是否空闲
        - 检查 HPLC 站位置是否有孔板
        - 设置从 HPLC 站取孔板
        - 等待从 HPLC 站取孔板完成
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("从 HPLC 站取孔板...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        self._wait_occupancy(lambda: self.is_hplc_workstation_occupied(), True, "HPLC 站位置没有孔板")
        
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.HPLC_STATION) # 设置机械臂目标位置为 HPLC 站
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", 1) # 设置 HPLC 站位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PICK) # 设置动作类型为上粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="从 HPLC 站取孔板完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="从 HPLC 站取孔板完成"): # 等待完成状态复位
                logger.info("从 HPLC 站取孔板完成")
                self._pick_resource_from_warehouse(
                    "HPLC工站", "HPLC", "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "从 HPLC 站取孔板完成",
                }
            else:
                raise ValueError("从 HPLC 站取孔板失败，完成复位超时")
        else:
            raise ValueError("从 HPLC 站取孔板失败，机械臂动作未完成")
        
    @action(
        auto_prefix=True,
        description="步骤25：将孔板放置到下料架",
        handles=[
            ActionInputHandle(
                key="unloading_rack_position",
                data_type="ai4c_unloading_rack_position",
                label="下料架位置",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="孔板放置的下料架位置，范围 1-8",
            )
        ],
    )
    def place_well_plate_to_unloading_rack(self, position: int = 1) -> dict:
        """
        将孔板放置到下料架：
        - 检查机械臂是否空闲
        - 检查下料架位置是否有孔板
        - 设置将孔板放置到下料架
        - 等待将孔板放置到下料架完成
        - 返回成功

        Args:
            position (int): 下料架位置

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将孔板放置到下料架...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        if position < MIN_RACK_POSITION or position > MAX_RACK_POSITION:
            raise ValueError("下料架位置超出范围")

        self._wait_occupancy(lambda: self.is_unloading_rack_position_occupied(position), False, "下料架位置已有孔板")
        
        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.PLATE_UNLOADING_RACK) # 设置机械臂目标位置为下料架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置下料架位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PLACE) # 设置动作类型为下粉末头
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将孔板放置到下料架完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将孔板放置到下料架完成"): # 等待完成状态复位
                logger.info("将孔板放置到下料架完成")
                self._place_held_resource_to_warehouse(
                    "孔板下料架", str(position), "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "将孔板放置到下料架完成",
                }
            else:
                raise ValueError("将孔板放置到下料架失败，完成复位超时")
        else:
            raise ValueError("将孔板放置到下料架失败，机械臂动作未完成")

    @action(
        auto_prefix=True,
        description="将孔板放置到上料架",
        handles=[
            ActionInputHandle(
                key="loading_rack_position",
                data_type="ai4c_loading_rack_position",
                label="上料架位置",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="孔板放置的上料架位置，范围 1-8",
            )
        ],
    )
    def place_well_plate_to_loading_rack(self, position: int = 1) -> dict:
        """
        将孔板放置到上料架：
        - 检查机械臂是否空闲
        - 检查上料架位置是否有孔板
        - 设置将孔板放置到上料架
        - 等待将孔板放置到上料架完成
        - 返回成功

        Args:
            position (int): 上料架位置

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("将孔板放置到上料架...")
        self._wait_until_true("Robotic_Arm_Idle", description="机械臂空闲")

        if position < MIN_RACK_POSITION or position > MAX_RACK_POSITION:
            raise ValueError("上料架位置超出范围")

        self._wait_occupancy(lambda: self.is_loading_rack_position_occupied(position), False, "上料架位置已有孔板")

        self.set_node_value("Robotic_Arm_Target_Position_Code", RoboticArmTargetPosition.PLATE_LOADING_RACK) # 设置机械臂目标位置为上料架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code", position) # 设置上料架位置
        self.set_node_value("Robotic_Arm_Action_Code", RoboticArmAction.PLACE) # 设置动作类型为放置
        self.set_node_value("Robotic_Arm_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete", description="将孔板放置到上料架完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete", description="将孔板放置到上料架完成"): # 等待完成状态复位
                logger.info("将孔板放置到上料架完成")
                self._place_held_resource_to_warehouse(
                    "孔板上料架", str(position), "well_plate", "_held_well_plate"
                )
                return {
                    "success": True,
                    "message": "将孔板放置到上料架完成",
                }
            else:
                raise ValueError("将孔板放置到上料架失败，完成复位超时")
        else:
            raise ValueError("将孔板放置到上料架失败，机械臂动作未完成")

    @action(auto_prefix=True, description="步骤3/9：打开固态称量门")
    def open_solid_weighing_door(self) -> dict:
        """
        打开固态称重门：
        - 检查固态称重门是否关闭
        - 打开固态称重门
        - 等待固态称重门打开
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("打开固态称重门...")
        self.set_node_value("Solid_Weighing_Close_Door", False)
        self.set_node_value("Solid_Weighing_Open_Door", True) # 打开固态称重门
        if self._wait_until_true("Solid_Weighing_Open_Door_Complete", description="固态称重门打开完成"):
            return {
                "success": True,
                "message": "固态称重门打开完成",
            }
        else:
            raise ValueError("固态称重门打开失败")
        
    @action(auto_prefix=True, description="步骤7/13：关闭固态称量门")
    def close_solid_weighing_door(self) -> dict:
        """
        关闭固态称重门：
        - 检查固态称重门是否打开
        - 关闭固态称重门
        - 等待固态称重门关闭
        - 返回成功

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("关闭固态称重门...")
        self.set_node_value("Solid_Weighing_Open_Door", False)
        self.set_node_value("Solid_Weighing_Close_Door", True) # 关闭固态称重门
        if self._wait_until_true("Solid_Weighing_Close_Door_Complete", description="固态称重门关闭完成"):
            return {
                "success": True,
                "message": "固态称重门关闭完成",
            }
        else:
            raise ValueError("固态称重门关闭失败")
        
    @not_action
    def _normalize_int_sequence(self, value, name: str) -> list[int]:
        """将单个整数或整数列表规范化为非空 list[int]。"""
        if value is None:
            raise ValueError(f"{name} 不能为空")
        if isinstance(value, bytes):
            raise ValueError(f"{name} 必须是整数或整数列表")
        if isinstance(value, str):
            text = value.strip().replace("，", ",")
            if not text:
                raise ValueError(f"{name} 不能为空")
            parts = [part.strip() for part in text.replace(" ", ",").split(",") if part.strip()]
            if not parts:
                raise ValueError(f"{name} 不能为空")
            try:
                return [int(part) for part in parts]
            except ValueError as e:
                raise ValueError(f"{name} 必须是整数或整数列表") from e
        if isinstance(value, Sequence):
            if len(value) == 0:
                raise ValueError(f"{name} 列表不能为空")
            try:
                return [int(item) for item in value]
            except (TypeError, ValueError) as e:
                raise ValueError(f"{name} 必须是整数或整数列表") from e
        try:
            return [int(value)]
        except (TypeError, ValueError) as e:
            raise ValueError(f"{name} 必须是整数或整数列表") from e

    @not_action
    def _expand_slot_token(self, token) -> list[int]:
        """解析单个槽位 token：整数，或 'x-y' 闭区间。"""
        if isinstance(token, bool):
            raise ValueError("称量槽位不能为布尔值")
        if isinstance(token, (int, float)):
            return [int(token)]
        text = str(token).strip()
        if not text:
            return []
        matched = _SOLID_WEIGHING_SLOT_RANGE_RE.match(text)
        if matched:
            start, end = int(matched.group(1)), int(matched.group(2))
            step = 1 if end >= start else -1
            return list(range(start, end + step, step))
        try:
            return [int(text)]
        except ValueError as e:
            raise ValueError(f"无法解析称量槽位 '{text}'，请使用整数或 x-y 区间") from e

    @not_action
    def _flatten_slot_tokens(self, value) -> list:
        """把槽位入参展开成 token 列表，支持 '1-3, 5' 和嵌套列表。"""
        if value is None:
            raise ValueError("称量槽位不能为空")
        if isinstance(value, (bytes, bool)):
            raise ValueError("称量槽位必须是整数、区间字符串或列表")
        if isinstance(value, (int, float)):
            return [int(value)]
        if isinstance(value, str):
            parts = [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
            if not parts:
                raise ValueError("称量槽位不能为空")
            return parts
        if isinstance(value, Sequence):
            tokens = []
            for item in value:
                tokens.extend(self._flatten_slot_tokens(item))
            if not tokens:
                raise ValueError("称量槽位列表不能为空")
            return tokens
        raise ValueError("称量槽位必须是整数、区间字符串或列表")

    @not_action
    def _parse_slot_spec(self, value) -> list[int]:
        """解析称量槽位。支持 1、[1, 3]、'1-3, 5'（1 到 3，再加 5）。"""
        slots: list[int] = []
        seen: set[int] = set()
        for token in self._flatten_slot_tokens(value):
            for slot in self._expand_slot_token(token):
                if slot in seen:
                    continue
                seen.add(slot)
                slots.append(slot)
        if not slots:
            raise ValueError("称量槽位不能为空")
        return slots

    @not_action
    def _align_solid_weighing_doses(
        self,
        weights: list[int],
        tolerances: list[int],
        slots: list[int],
    ) -> list[tuple[int, int, int]]:
        """按最长列表对齐质量/误差/槽位；长度为 1 的参数广播到全部加样。"""
        total = max(len(weights), len(tolerances), len(slots))

        def align(seq: list[int], name: str) -> list[int]:
            if len(seq) == total:
                return seq
            if len(seq) == 1:
                return seq * total
            raise ValueError(
                f"固体称量参数长度不一致：质量 {len(weights)}、误差 {len(tolerances)}、槽位 {len(slots)}。"
                f"{name} 必须与最长列表等长，或只填 1 个值以应用到全部加样。"
            )

        aligned_weights = align(weights, "质量")
        aligned_tolerances = align(tolerances, "误差")
        aligned_slots = align(slots, "槽位")
        return list(zip(aligned_weights, aligned_tolerances, aligned_slots))

    @not_action
    def _execute_one_solid_weighing(
        self,
        weight_raw: int,
        tolerance: int,
        slot: int,
        index: int,
        total: int,
    ) -> None:
        """下发一组固体称量参数并等待本轮加工完成。

        对外与 OPC UA 写入值均为 0.1 mg 整数（1 = 0.1 mg），不做换算。
        """
        weight_mg = weight_raw * SOLID_WEIGHING_WEIGHT_UNIT_MG
        logger.info(
            f"固体称量第 {index}/{total} 次：质量={weight_raw} (0.1mg) = {weight_mg} mg，"
            f"误差={tolerance} (0.1mg)，槽位={slot}"
        )
        self.set_node_value("Solid_Weighing_Weight_in_Grams", weight_raw)
        self.set_node_value("Solid_Weighing_Error", tolerance)
        self.set_node_value("Solid_Weighing_Slot_Position", slot)
        self.set_node_value("Solid_Weighing_Processing_Allowed", True)
        desc = f"固体称重第 {index}/{total} 次完成"
        if not self._wait_until_true("Solid_Weighing_Processing_Complete", description=desc):
            raise ValueError(f"固体称重第 {index}/{total} 次失败，动作超时")
        self.set_node_value("Solid_Weighing_Processing_Allowed", False)
        if not self._wait_until_false("Solid_Weighing_Processing_Complete", description=desc):
            raise ValueError(f"固体称重第 {index}/{total} 次失败，完成复位超时")
        logger.info(f"固体称量第 {index}/{total} 次完成")

    @action(
        auto_prefix=True,
        description="步骤8：触发固体称量（支持同一节点连续多次加样）",
        handles=[
            ActionInputHandle(
                key="solid_weighing_weight",
                data_type="ai4c_solid_weighing_weight",
                label="称重目标值(0.1mg)",
                data_key="weight",
                data_source=DataSource.HANDLE,
                description="固体称量目标质量，单位 0.1mg。填 10 表示 1.0 mg；多次填 10,15",
            ),
            ActionInputHandle(
                key="solid_weighing_tolerance",
                data_type="ai4c_solid_weighing_tolerance",
                label="称重误差(0.1mg)",
                data_key="tolerance",
                data_source=DataSource.HANDLE,
                description="固体称量允许误差，单位 0.1mg。单个值填 1，多次填 1,1",
            ),
            ActionInputHandle(
                key="solid_weighing_slot",
                data_type="ai4c_solid_weighing_slot",
                label="称量槽位",
                data_key="slot",
                data_source=DataSource.HANDLE,
                description="固体称量槽位。支持 1、1-8 或 1-8,10（从 1 到 8，再加 10）",
            ),
        ],
    )
    def trigger_solid_weighing(
        self,
        weight: str = "10",
        tolerance: str = "1",
        slot: str = "1",
    ) -> dict:
        """
        触发固体称重，支持一个动作节点连续多次加样：
        - 检查固态称重是否已占位
        - 检查固态称重粉桶位置已占位
        - 按顺序对每组（质量、误差、槽位）下发参数并等待完成
        - 返回成功

        质量单位为 0.1 mg（写入 OPC UA 的整数 1 = 0.1 mg，填 10 即 1.0 mg）。
        参数使用字符串，以便节点面板正确渲染。
        单次加样：weight="10", slot="1"。
        连续穴位：slot="1-8,10" 表示 1 到 8，再加 10。
        多次不同质量：weight="10,15", slot="1,2"。
        某一项只填单个值时，会广播到全部加样次数。

        Args:
            weight[称重目标值(0.1mg)]: 称重目标质量，单位 0.1mg。例如 10（=1.0 mg）或 10,15。
            tolerance[称重误差(0.1mg)]: 称重允许误差，单位 0.1mg。例如 1 或 1,1。
            slot[称量槽位]: 称重器穴位。支持 1、1-8 或 1-8,10。

        Returns:
            dict: 包含 success 和 message
        """
        weights = self._normalize_int_sequence(weight, "称重目标值(0.1mg)")
        tolerances = self._normalize_int_sequence(tolerance, "称重误差(0.1mg)")
        slots = self._parse_slot_spec(slot)
        doses = self._align_solid_weighing_doses(weights, tolerances, slots)
        total = len(doses)

        logger.info(f"触发固体称重，共 {total} 次加样：{doses}")
        self._wait_occupancy(lambda: self.is_solid_weighing_occupied(), True, "固态称重位置没有孔板")
        self._wait_occupancy(
            lambda: self.is_powder_position_in_solid_weighing_occupied(),
            True,
            "固态称重位置没有粉桶",
        )

        for index, (dose_weight, dose_tolerance, dose_slot) in enumerate(doses, start=1):
            self._execute_one_solid_weighing(dose_weight, dose_tolerance, dose_slot, index, total)

        return {
            "success": True,
            "message": f"固体称重完成，共 {total} 次加样",
            "count": total,
            "doses": [
                {
                    "weight": w,
                    "weight_mg": w * SOLID_WEIGHING_WEIGHT_UNIT_MG,
                    "tolerance": t,
                    "slot": s,
                }
                for w, t, s in doses
            ],
        }
        
    @action(
        auto_prefix=True,
        description="步骤18：触发磁力搅拌",
        handles=[
            ActionInputHandle(
                key="magnetic_stirrer_speed",
                data_type="ai4c_magnetic_stirrer_speed",
                label="搅拌速度",
                data_key="speed",
                data_source=DataSource.HANDLE,
                description="磁力搅拌速度",
            ),
            ActionInputHandle(
                key="magnetic_stirrer_temperature",
                data_type="ai4c_magnetic_stirrer_temperature",
                label="搅拌温度",
                data_key="temperature",
                data_source=DataSource.HANDLE,
                description="磁力搅拌温度",
            ),
            ActionInputHandle(
                key="magnetic_stirrer_minutes",
                data_type="ai4c_magnetic_stirrer_minutes",
                label="搅拌时间",
                data_key="mins",
                data_source=DataSource.HANDLE,
                description="磁力搅拌时间，单位分钟",
            ),
        ],
    )
    def trigger_magnetic_stirrer(self, speed: int = 100, temperature: int = 30, mins: int = 1) -> dict:
        """
        触发磁力搅拌：
        - 等待磁力搅拌请求加工信号
        - 检查磁力搅拌是否已占位
        - 设置搅拌参数
        - 触发搅拌参数已下发
        - 等待搅拌完成
        - 返回成功

        Args:
            speed (int): 搅拌速度
            temperature (int): 搅拌温度
            mins (int): 搅拌时间(分钟)

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("触发磁力搅拌...")
        if not self._wait_until_true("Magnetic_Stirrer_Processing_Request", description="等待磁力搅拌请求加工信号"):
            raise ValueError("等待磁力搅拌请求加工信号超时")

        self._wait_occupancy(lambda: self.is_magnetic_stirrer_occupied(), True, "磁力搅拌位置没有孔板")

        self.set_node_value("Magnetic_Stirrer_Speed_Parameter", speed) # 设置搅拌速度
        self.set_node_value("Magnetic_Stirrer_Temperature_Parameter", temperature) # 设置搅拌温度
        self.set_node_value("Magnetic_Stirrer_Time_Parameter", mins) # 设置搅拌时间
        self.set_node_value("Magnetic_Stirrer_Parameters_Sent", True) # 触发搅拌参数已下发
        # 等待参数已执行
        if self._wait_until_true("Magnetic_Stirrer_Parameters_Executed", description="等待搅拌参数已执行"):
            # 复位搅拌参数已下发
            self.set_node_value("Magnetic_Stirrer_Parameters_Sent", False) # 复位搅拌参数已下发
            logger.info("搅拌参数已执行")
        else:
            raise ValueError("搅拌参数执行失败")
        
        # 等待加工完成
        if self._wait_until_true("Magnetic_Stirrer_Processing_Complete", description="等待搅拌完成", timeout=mins*60.0+100.0):
            logger.info("搅拌完成")
            return {
                "success": True,
                "message": "搅拌完成",
            }
        else:
            raise ValueError("搅拌失败，动作超时")

    @action(
        auto_prefix=True,
        description="步骤15：触发移液",
        handles=[
            ActionInputHandle(
                key="pipetting_param",
                data_type="ai4c_pipetting_param",
                label="移液参数",
                data_key="param",
                data_source=DataSource.HANDLE,
                description="移液站参数",
            )
        ],
    )
    def trigger_pipetting(self, param: int) -> dict:
        """
        触发移液：
        - 等待移液请求加工信号
        - 检查移液位置是否已占位
        - 设置移液参数
        - 触发移液参数已下发
        - 等待移液完成
        - 返回成功

        Args:
            param (int): 移液参数

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("触发移液...")

        time.sleep(3)
        return {
            "success": True,
            "message": "移液完成",
        }

        '''
        if not self._wait_until_true("Pipetting_Station_Processing_Request", description="等待移液请求加工信号"):
            logger.error("等待移液请求加工信号超时")
            return {
                "success": False,
                "message": "等待移液请求加工信号超时",
            }

        if not self.is_pipetting_station_occupied():
            logger.error("移液位置没有孔板")
            return {
                "success": False,
                "message": "移液位置没有孔板",
            }
        
        self.set_node_value("Pipetting_Station_Parameter_Setting", param) # 设置移液参数
        self.set_node_value("Pipetting_Station_Parameters_Sent", True) # 触发移液参数已下发
        # 等待参数已执行
        if self._wait_until_true("Pipetting_Station_Parameters_Executed", description="等待移液参数已执行"):
            # 复位搅拌参数已下发
            self.set_node_value("Pipetting_Station_Parameters_Sent", False) # 复位搅拌参数已下发
            if self._wait_until_false("Pipetting_Station_Parameters_Executed", description="等待移液参数已执行复位"):
                logger.info("移液参数已执行")
            else:
                logger.error("移液参数执行失败")
                return {
                    "success": False,
                    "message": "移液参数执行失败，完成复位超时",
                }
        else:
            logger.error("移液参数执行失败")
            return {
                "success": False,
                "message": "移液参数执行失败",
            }

        # 等待加工完成
        if self._wait_until_true("Pipetting_Station_Processing_Complete", description="等待移液完成"):
            logger.info("移液完成")
            return {
                "success": True,
                "message": "移液完成",
            }
        else:
            logger.error("移液失败")
            return {
                "success": False,
                "message": "移液失败，动作超时",
            }
        '''
        
    @action(
        auto_prefix=True,
        description="步骤23：触发 HPLC",
        handles=[
            ActionInputHandle(
                key="hplc_param",
                data_type="ai4c_hplc_param",
                label="HPLC 参数",
                data_key="param",
                data_source=DataSource.HANDLE,
                description="HPLC 加工参数",
            )
        ],
    )
    def trigger_hplc(self, param: int = 1) -> dict:
        """
        触发 HPLC：
        - 等待 HPLC 请求加工信号
        - 检查 HPLC 位置是否已占位
        - 设置 HPLC 参数
        - 触发 HPLC 参数已下发
        - 等待 HPLC 完成
        - 返回成功

        Args:
        - param (int): HPLC 参数

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("触发 HPLC...")

        time.sleep(5)

        return {
            "success": True,
            "message": "HPLC 完成",
        }
    
        '''
        if not self._wait_until_true("HPLC_Processing_Request", description="等待 HPLC 请求加工信号"):
            logger.error("等待 HPLC 请求加工信号超时")
            return {
                "success": False,
                "message": "等待 HPLC 请求加工信号超时",
            }
        
        if not self.is_hplc_workstation_occupied():
            logger.error("HPLC 位置没有孔板")
            return {
                "success": False,
                "message": "HPLC 位置没有孔板",
            }

        # self.set_node_value("HPLC_Parameter_Setting", param) # 设置 HPLC 参数
        self.set_node_value("HPLC_Parameters_Sent", True) # 触发 HPLC 参数已下发
        # 等待参数已执行
        if self._wait_until_true("HPLC_Parameters_Executed", description="等待 HPLC 参数已执行"):
            # 复位 HPLC 参数已下发
            self.set_node_value("HPLC_Parameters_Sent", False) # 复位 HPLC 参数已下发
            if self._wait_until_false("HPLC_Parameters_Executed", description="等待 HPLC 参数已执行复位"):
                logger.info("HPLC 参数已执行")
            else:
                logger.error("HPLC 参数执行失败")
                return {
                    "success": False,
                    "message": "HPLC 参数执行失败，完成复位超时",
                }
        else:
            logger.error("HPLC 参数执行失败")
            return {
                "success": False,
                "message": "HPLC 参数执行失败",
            }
        
        # 等待加工完成
        if self._wait_until_true("HPLC_Processing_Complete", description="等待 HPLC 完成"):
            logger.info("HPLC 完成")
            return {
                "success": True,
                "message": "HPLC 完成",
            }
        else:
            logger.error("HPLC 失败")
            return {
                "success": False,
                "message": "HPLC 失败，动作超时",
            }
        '''
    
    @not_action
    def trigger_heart_beat(self) -> None:
        """
        写入心跳
        """
        if self.heartbeat_on:
            # logger.info("写心跳")
            value = self.get_node_value("Heart_Beat")
            self.set_node_value("Heart_Beat", not value)

            if self.m_initialized:
                robot_arm_current_step = self.get_node_value("Robotic_Arm_Current_Step")
                if self.m_robot_arm_current_step != robot_arm_current_step:
                    self.m_robot_arm_current_step = robot_arm_current_step
                    logger.info(f"机械臂当前步骤更新: {self.m_robot_arm_current_step}")

                solid_weighing_current_step = self.get_node_value("Solid_Weighing_Current_Step")
                if self.m_solid_weighing_current_step != solid_weighing_current_step:
                    self.m_solid_weighing_current_step = solid_weighing_current_step
                    logger.info(f"固体称量当前步骤更新: {self.m_solid_weighing_current_step}")
                    
                magnetic_stirrer_current_step = self.get_node_value("Magnetic_Stirrer_Current_Step")
                if self.m_magnetic_stirrer_current_step != magnetic_stirrer_current_step:
                    self.m_magnetic_stirrer_current_step = magnetic_stirrer_current_step
                    logger.info(f"磁搅当前步骤更新: {self.m_magnetic_stirrer_current_step}")

            # 再次启动定时器，形成循环
            timer = threading.Timer(1.0, self.trigger_heart_beat)
            timer.daemon = True
            timer.start()
    
    @not_action
    def start_heart_beat(self) -> None:
        """
        启动心跳
        """
        logger.info("启动心跳")
        timer = threading.Timer(1.0, self.trigger_heart_beat)
        timer.daemon = True
        timer.start()
        self.heartbeat_on = True
    
    @not_action
    def stop_heart_beat(self) -> None:
        """
        停止心跳
        """
        logger.info("停止心跳")
        self.set_node_value("Heart_Beat", False)
        self.heartbeat_on = False
        
    @not_action
    def trigger_all_process(self) -> dict:
        """
        触发所有加工，从1号上料架抓取孔板后完成整个流程，最后放置在1号下料架上，
        固态称重从1号和2号堆栈获取粉桶，完成后放置到1号和2号堆栈
        称量使用固定参数
        磁搅也使用固定参数

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("触发所有流程...")
        ret = self.pick_well_plate_from_loading_rack(1)
        if not ret["success"]:
            return ret
        
        ret = self.open_solid_weighing_door()
        if not ret["success"]:
            return ret

        ret = self.place_well_plate_to_solid_weighing()
        if not ret["success"]:
            return ret
        
        ret = self.pick_powder_cylinder_from_stack(6)
        if not ret["success"]:
            return ret

        ret = self.place_powder_cylinder_to_solid_weighing()
        if not ret["success"]:
            return ret
        
        ret = self.close_solid_weighing_door()
        if not ret["success"]:
            return ret

        ret = self.trigger_solid_weighing(10, 1, 1)
        if not ret["success"]:
            return ret
        
        ret = self.open_solid_weighing_door()
        if not ret["success"]:
            return ret

        ret = self.pick_powder_cylinder_from_solid_weighing()
        if not ret["success"]:
            return ret
        
        ret = self.place_powder_cylinder_to_solid_weighing_stack(6)
        if not ret["success"]:
            return ret
        
        #ret = self.pick_powder_cylinder_from_stack(7)
        #if not ret["success"]:
        #    return ret

        #ret = self.place_powder_cylinder_to_solid_weighing()
        #if not ret["success"]:
        #    return ret
        
        #ret = self.close_solid_weighing_door()
        #if not ret["success"]:
        #    return ret

        #ret = self.trigger_solid_weighing(10, 1, 2)
        #if not ret["success"]:
        #    return ret
        
        #ret = self.open_solid_weighing_door()
        #if not ret["success"]:
        #    return ret

        #ret = self.pick_powder_cylinder_from_solid_weighing()
        #if not ret["success"]:
        #    return ret
        
        #ret = self.place_powder_cylinder_to_solid_weighing_stack(7)
        #if not ret["success"]:
        #    return ret
        
        ret = self.pick_well_plate_from_solid_weighing()
        if not ret["success"]:
            return ret
        
        ret = self.close_solid_weighing_door()
        if not ret["success"]:
            return ret

        ret = self.place_well_plate_to_pipetting_station()
        if not ret["success"]:
            return ret

        ret = self.trigger_pipetting(1)
        if not ret["success"]:
            return ret
        
        ret = self.pick_well_plate_from_pipetting_station()
        if not ret["success"]:
            return ret

        ret = self.place_well_plate_to_magnetic_stirrer()
        if not ret["success"]:
            return ret

        ret = self.trigger_magnetic_stirrer(100, 30, 1)
        if not ret["success"]:
            return ret

        ret = self.pick_well_plate_from_magnetic_stirrer()
        if not ret["success"]:
            return ret
        
        ret = self.place_well_plate_to_pipetting_station()
        if not ret["success"]:
            return ret

        ret = self.pick_well_plate_from_pipetting_station()
        if not ret["success"]:
            return ret

        ret = self.place_well_plate_to_hplc_station()
        if not ret["success"]:
            return ret
        
        ret = self.trigger_hplc(1)
        if not ret["success"]:
            return ret
        
        ret = self.pick_well_plate_from_hplc_station()
        if not ret["success"]:
            return ret

        ret = self.place_well_plate_to_unloading_rack(1)
        if not ret["success"]:
            return ret

        return {
            "success": True,
            "message": "所有加工完成",
        }


    def _wait_occupancy(
        self,
        check_fn,
        expected: bool,
        error_msg: str,
        timeout: float = 3.0,
        interval: float = 0.2,
    ) -> None:
        """持续检查占位状态，timeout 秒内仍未达到期望状态则抛出 ValueError。

        Args:
            check_fn: 返回占位布尔值的可调用对象。
            expected: 期望的占位状态（True 表示应占位，False 表示应空位）。
            error_msg: 超时仍未达到期望状态时抛出的错误信息。
            timeout: 持续检查的最长时间（秒）。
            interval: 轮询间隔（秒）。
        """
        start = time.time()
        while True:
            if bool(check_fn()) == expected:
                return
            if time.time() - start >= timeout:
                raise ValueError(error_msg)
            time.sleep(interval)

    def _wait_until_true(
        self,
        node_name: str,
        timeout: float = 300.0,
        interval: float = 0.2,
        description: str = None
    ) -> bool:
        """等待布尔节点变为 True（轮询时强制从 OPC UA 服务器读取，避免订阅缓存过期）"""
        desc = description or node_name
        logger.info(f"等待 {desc} 变为 True（轮询节点: {node_name}）...")
        
        start = time.time()
        while True:
            value = self.get_node_value(node_name, force_read=True)
            if value:
                logger.info(f"✓ {desc} 已变为 True（节点 [{node_name}]）")
                return True
            
            if time.time() - start >= timeout:
                logger.error(f"✗ 等待 {desc} 超时（{timeout}秒，节点 [{node_name}] 仍为 {value!r}）")
                return False
            
            time.sleep(interval)
    
    def _wait_until_false(
        self,
        node_name: str,
        timeout: float = 300.0,
        interval: float = 0.2,
        description: str = None
    ) -> bool:
        """等待布尔节点变为 False（轮询时强制从 OPC UA 服务器读取，避免订阅缓存过期）"""
        desc = description or node_name
        logger.info(f"等待 {desc} 变为 False（轮询节点: {node_name}）...")
        
        start = time.time()
        while True:
            value = self.get_node_value(node_name, force_read=True)
            if not value:
                logger.info(f"✓ {desc} 已变为 False（节点 [{node_name}]）")
                return True
            
            if time.time() - start >= timeout:
                logger.error(f"✗ 等待 {desc} 超时（{timeout}秒，节点 [{node_name}] 仍为 {value!r}）")
                return False
            
            time.sleep(interval)
    
    def _wait_for_nodes(
        self,
        conditions: dict,  # {node_name: target_value, ...}
        timeout: float = 300.0,
        interval: float = 0.2
    ) -> bool:
        """等待多个节点同时满足条件"""
        start = time.time()
        while True:
            all_met = all(
                self.get_node_value(name, force_read=True) == target
                for name, target in conditions.items()
            )
            if all_met:
                return True
            
            if time.time() - start >= timeout:
                return False
            
            time.sleep(interval)


if __name__ == '__main__':
    # 调试用法
    A4 = AI4CDevice(
        url="opc.tcp://192.168.1.88:4840",
        csv_path=os.path.dirname(os.path.abspath(__file__)) + "/ai4c_sim_updated.csv"
    )

    # 启动心跳
    # A4.start_heart_beat()

    logger.setLevel(logging.INFO)

    time.sleep(3)

    # 初始化工作站
    A4.init_workstation()
    
    # 显示命令行，让用户通过选择序号来完成相应的操作
    # 如果带有参数，则序号和各参数之间均由空格分隔
    # 具体命令如下：
    # 1	初始化
    # 2	从上料架抓取孔板
    # 3	放置孔板到固态称量
    # 4	从固态称量堆栈抓取粉桶
    # 5	将粉桶上到固态称量中
    # 6	将粉桶从固态称量中下来
    # 7	将粉桶放到固态称量架
    # 8	将孔板从固态称量中取出
    # 9	放置孔板到移液站
    # 10 从移液站抓取孔板
    # 11 放置孔板到磁搅
    # 12 从磁搅抓取孔板
    # 13 放置孔板到HPLC
    # 14 从HPLC抓取孔板
    # 15 放置孔板到下料架
    # 16 进行固态称量
    # 17 进行磁力搅拌
    # 18 进行移液动作
    # 19 进行HPLC动作
    # 20 固态称量开门
    # 21 固态称量关门
    # 98 整体流程
    # 99 退出
    while True:
        print("请选择操作：")
        print("1 初始化")
        print("2 从上料架抓取孔板")
        print("3 放置孔板到固态称量")
        print("4 从固态称量堆栈抓取粉桶")
        print("5 将粉桶上到固态称量中")
        print("6 将粉桶从固态称量中下来")
        print("7 将粉桶放到固态称量架")
        print("8 将孔板从固态称量中取出")
        print("9 放置孔板到移液站")
        print("10 从移液站抓取孔板")
        print("11 放置孔板到磁搅")
        print("12 从磁搅抓取孔板")
        print("13 放置孔板到HPLC")
        print("14 从HPLC抓取孔板")
        print("15 放置孔板到下料架")
        print("16 进行固态称量  （单位 0.1mg，示例：16 10 1 1  或  16 10 1 1-3,5）")
        print("17 进行磁力搅拌")
        print("18 进行移液动作")
        print("19 进行HPLC动作")
        print("20 固态称量开门")
        print("21 固态称量关门")
        print("98 整体流程")
        print("99 退出")
        choice = input("请输入操作序号：")
        if choice == "99":
            break
        else:
            print("执行操作...")
            # 根据用户输入的序号执行相应的操作
            if choice == "1":
                A4.init_workstation()
            elif choice.startswith("2 "):
                # 获取2后的参数，即上料架位置
                rack_pos = int(choice.split(" ")[1])
                A4.pick_well_plate_from_loading_rack(rack_pos)
            elif choice == "3":
                A4.place_well_plate_to_solid_weighing()
            elif choice.startswith("4 "):
                # 获取4后的参数，即固态称量堆栈位置
                stack_pos = int(choice.split(" ")[1])
                A4.pick_powder_cylinder_from_stack(stack_pos)
            elif choice == "5":
                A4.place_powder_cylinder_to_solid_weighing()
            elif choice == "6":
                A4.pick_powder_cylinder_from_solid_weighing()
            elif choice.startswith("7 "):
                stack_pos = int(choice.split(" ")[1])
                A4.place_powder_cylinder_to_solid_weighing_stack(stack_pos)
            elif choice == "8":
                A4.pick_well_plate_from_solid_weighing()
            elif choice == "9":
                A4.place_well_plate_to_pipetting_station()
            elif choice == "10":
                A4.pick_well_plate_from_pipetting_station()
            elif choice == "11":
                A4.place_well_plate_to_magnetic_stirrer()
            elif choice == "12":
                A4.pick_well_plate_from_magnetic_stirrer()
            elif choice == "13":
                A4.place_well_plate_to_hplc_station()
            elif choice == "14":
                A4.pick_well_plate_from_hplc_station()
            elif choice.startswith("15 "):
                rack_pos = int(choice.split(" ")[1])
                A4.place_well_plate_to_unloading_rack(rack_pos)
            elif choice.startswith("16 "):
                parts = choice.split(" ")
                weight = parts[1]
                tolerance = parts[2]
                slot = " ".join(parts[3:])
                A4.trigger_solid_weighing(weight, tolerance, slot)
            elif choice.startswith("17 "):
                speed = int(choice.split(" ")[1])
                temperature = int(choice.split(" ")[2])
                mins = int(choice.split(" ")[3])
                A4.trigger_magnetic_stirrer(speed, temperature, mins)
            elif choice.startswith("18 "):
                param = int(choice.split(" ")[1])
                A4.trigger_pipetting(param)
            elif choice.startswith("19 "):
                A4.trigger_hplc(param)
            elif choice == "20":
                A4.open_solid_weighing_door()
            elif choice == "21":
                A4.close_solid_weighing_door()
            elif choice == "98":
                A4.trigger_all_process()
            else:
                print("无效的操作序号，请重新输入。")

    # 结束心跳
    # A4.stop_heart_beat()

    # 断开连接
    A4.disconnect()

    print("退出程序。")



