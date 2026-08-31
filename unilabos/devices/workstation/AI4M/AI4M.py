"""AI4M（OP10）工作站驱动。"""

from enum import IntEnum
from pathlib import Path
import threading
import time
from typing import Optional

from unilabos.devices.workstation.AI4M.base_opcua_client import (
    OpcUaClientWithSubscription,
)
from unilabos.devices.workstation.AI4M.decks import AI4M_deck
from unilabos.registry.decorators import (
    ActionInputHandle,
    ActionOutputHandle,
    DataSource,
    action,
    device,
    not_action,
)
from unilabos.resources.resource_tracker import LabSample, SampleUUIDsType
from unilabos.utils.log import logger


OP10_NODE_TABLE = "opcua_nodes_OP10_UniLab.csv"


class RobotTargetPosition(IntEnum):
    """OP10 机械臂目标位置代码。"""

    BEAKER_RACK = 1
    REACTION_STATION = 2


class RobotAction(IntEnum):
    """OP10 机械臂动作代码。"""

    PICK = 1
    PLACE = 2


@device(
    id="AI4M_station",
    display_name="AI4M OP10 工作站",
    category=["workstation"],
    description="AI4M OP10 水凝胶反应工作站",
    icon="Hydrogel module.webp",
)
class AI4MDevice(OpcUaClientWithSubscription):
    """OP10 工作站；使用独立 OPC UA 客户端和 OP10 变量表。"""

    def __init__(
        self,
        url: str,
        deck: Optional[AI4M_deck] = None,
        csv_path: str = "opcua_nodes_OP10_UniLab.csv",
        username: str = None,
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        table_name = Path(csv_path or OP10_NODE_TABLE).name
        if table_name != OP10_NODE_TABLE:
            raise ValueError(
                f"AI4M 只能使用 OP10 变量表 {OP10_NODE_TABLE}，当前为 {table_name}"
            )

        # AI4C 基类为兼容旧驱动保留了类级默认字典。这里显式创建实例字典，
        # 防止 AI4M 与 AI4M002 的节点、缓存和订阅互相串用。
        self._node_registry = {}
        self._variables_to_find = {}
        self._name_mapping = {}
        self._reverse_mapping = {}
        self._found_node_objects = {}

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

        if deck is None or isinstance(
            deck.get("data") if isinstance(deck, dict) else deck, dict
        ):
            self.deck = AI4M_deck(setup=True)
        else:
            self.deck = deck.get("data") if isinstance(deck, dict) else deck
        if self.deck is None:
            raise ValueError("Deck 配置不能为空")

        self._robot_lock = threading.RLock()
        logger.info("AI4M OP10 机器人动作锁已初始化")
        if hasattr(self.deck, "children"):
            logger.info(f"AI4M Deck 初始化完成，加载 {len(self.deck.children)} 个资源")

        self.load_nodes_from_csv(OP10_NODE_TABLE)

    @not_action
    def post_init(self, ros_node) -> None:
        """ROS2 节点就绪后注册资源树。"""
        if not getattr(self, "deck", None):
            return
        if not getattr(ros_node, "resource_tracker", None):
            logger.warning("resource_tracker 不存在，无法注册 AI4M deck")
            return

        self._ros_node = ros_node
        ros_node.resource_tracker.add_resource(self.deck)
        self._sync_resource_to_frontend()

    @not_action
    def _sync_resource_to_frontend(self) -> None:
        """同步物料资源树；同步失败不影响硬件动作。"""
        if not getattr(self, "_ros_node", None):
            return
        try:
            from unilabos.ros.nodes.base_device_node import ROS2DeviceNode

            ROS2DeviceNode.run_async_func(
                self._ros_node.update_resource,
                True,
                resources=[self.deck],
            )
            logger.info("AI4M 资源树已同步到前端")
        except Exception as exc:
            logger.warning(f"AI4M 资源树同步失败（不影响硬件动作）: {exc}")

    @not_action
    def _write_node(self, name: str, value) -> None:
        """写入 OPC UA 节点，并把通信失败转换为动作异常。"""
        if not self.set_node_value(name, value):
            raise RuntimeError(f"写入 OPC UA 节点失败: {name}={value}")

    @not_action
    def _read_bool(self, name: str) -> bool:
        """强制读取布尔节点，避免使用过期的订阅缓存。"""
        value = self.get_node_value(name, force_read=True)
        if value is None:
            raise RuntimeError(f"读取 OPC UA 节点失败: {name}")
        return bool(value)

    @not_action
    def _wait_until(
        self,
        node_name: str,
        expected: bool,
        description: str,
        *,
        fault_node: Optional[str] = None,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
    ) -> None:
        """按 AI4C 的强制轮询模式等待状态，并监控超时和故障。"""
        started_at = time.monotonic()
        while self._read_bool(node_name) is not expected:
            if fault_node and self._read_bool(fault_node):
                raise RuntimeError(f"{description}期间检测到设备故障")
            if time.monotonic() - started_at >= timeout:
                raise TimeoutError(
                    f"等待{description}超时（{timeout}秒，节点 {node_name} 未变为 {expected}）"
                )
            logger.info(f"等待{description}...")
            time.sleep(poll_interval)

    @not_action
    def _run_robot_action(
        self,
        action_code: RobotAction,
        target_position: RobotTargetPosition,
        target_pick_place_code: int,
        description: str,
    ) -> None:
        """执行一次 OP10 统一机械臂握手。调用方必须持有机器人锁。"""
        self._wait_until("robot_idle", True, "机械臂空闲", fault_node="robot_fault")
        if self._read_bool("robot_fault"):
            raise RuntimeError("机械臂存在故障，无法执行动作")

        self._write_node("robot_action_code", int(action_code))
        self._write_node("robot_target_position_code", int(target_position))
        self._write_node("robot_target_pick_place_code", target_pick_place_code)
        self._write_node("robot_action_trigger", True)
        try:
            self._wait_until(
                "robot_action_complete",
                True,
                description,
                fault_node="robot_fault",
            )
        finally:
            self._write_node("robot_action_trigger", False)
        self._wait_until(
            "robot_action_complete",
            False,
            f"{description}完成信号复位",
            fault_node="robot_fault",
        )
        logger.info(f"{description}完成")

    @not_action
    def _station_is_free(self, station_id: int) -> bool:
        return not self._read_bool(f"station_{station_id}_occupied")

    @not_action
    def _wait_for_free_station(self, requested_station_id: Optional[int]) -> int:
        """在获取机器人锁之前等待空闲工站，避免阻塞放料动作。"""
        while True:
            station_ids = (
                (requested_station_id,)
                if requested_station_id is not None
                else (1, 2, 3)
            )
            for station_id in station_ids:
                if self._station_is_free(station_id):
                    return station_id
            logger.info("没有空闲反应工站，等待中...")
            time.sleep(1.0)

    @not_action
    def _sample_results(self, sample_uuids: SampleUUIDsType, extra: dict) -> list:
        return [
            LabSample(
                sample_uuid=sample_uuid,
                oss_path="",
                extra=extra if isinstance(content, str) else content.serialize(),
            )
            for sample_uuid, content in (
                sample_uuids.items() if sample_uuids else {}
            )
        ]

    @not_action
    def _assign_to_station(self, carrier, station_id: int) -> None:
        if carrier is None:
            return
        station = self.deck.warehouses[f"反应工站{station_id}"]
        site_key = list(station._ordering.keys())[0]
        station.assign_child_resource(
            carrier,
            location=station.child_locations[site_key],
            spot=0,
        )

    @action(auto_prefix=True, description="启动 OP10 指令作业模式")
    def start_manual_mode(self) -> dict:
        """新 OP10 表只提供直接指令接口，该模式即为指令作业模式。"""
        return {"message": "OP10 已处于直接指令作业模式"}

    @action(
        auto_prefix=True,
        description="机器人从烧杯堆栈取烧杯并放到反应工站",
        goal_default={"pick_beaker_id": None, "place_station_id": None},
        handles=[
            ActionInputHandle(
                key="beaker_input",
                data_type="ai4m_beaker",
                label="取烧杯编号",
                data_key="pick_beaker_id",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="station_output",
                data_type="ai4m_station",
                label="放置检测站编号",
                data_key="place_station_id",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def trigger_robot_pick_beaker(
        self,
        pick_beaker_id: int,
        place_station_id: Optional[int] = None,
        sample_uuids: SampleUUIDsType = None,
    ) -> dict:
        if pick_beaker_id not in range(1, 6):
            raise ValueError(f"取烧杯编号必须在 1-5 范围内: {pick_beaker_id}")
        if place_station_id is not None and place_station_id not in range(1, 4):
            raise ValueError(f"反应工站编号必须在 1-3 范围内: {place_station_id}")

        requested_station_id = place_station_id
        while True:
            place_station_id = self._wait_for_free_station(requested_station_id)
            with self._robot_lock:
                if not self._station_is_free(place_station_id):
                    logger.info(f"反应工站{place_station_id}状态已变化，重新选择")
                    continue

                rack = self.deck.warehouses["水凝胶烧杯堆栈"]
                rack_site_key = f"A{pick_beaker_id}"
                carrier = rack[rack_site_key]
                if carrier is None:
                    raise ValueError(f"堆栈位置 {rack_site_key} 没有载具")

                self._run_robot_action(
                    RobotAction.PICK,
                    RobotTargetPosition.BEAKER_RACK,
                    pick_beaker_id,
                    f"从堆栈位置{pick_beaker_id}取烧杯",
                )
                try:
                    rack.unassign_child_resource(carrier)
                except Exception as exc:
                    logger.warning(f"从堆栈解绑载具失败（不影响硬件操作）: {exc}")

                self._write_node(f"station_{place_station_id}_start", False)
                self._write_node(f"station_{place_station_id}_params_downloaded", False)
                self._run_robot_action(
                    RobotAction.PLACE,
                    RobotTargetPosition.REACTION_STATION,
                    place_station_id,
                    f"将烧杯放到反应工站{place_station_id}",
                )
                try:
                    self._assign_to_station(carrier, place_station_id)
                except Exception as exc:
                    logger.warning(f"绑定载具到反应工站失败（不影响硬件操作）: {exc}")
                self._sync_resource_to_frontend()
                break

        extra = {
            "carrier_info": {
                "name": carrier.name,
                "type": "carrier",
                "rack_location": rack_site_key,
                "station_id": place_station_id,
            },
            "pick_beaker_id": pick_beaker_id,
            "place_station_id": place_station_id,
        }
        return {
            "pick_beaker_id": pick_beaker_id,
            "place_station_id": place_station_id,
            "message": f"机器人取烧杯{pick_beaker_id}并放到反应工站{place_station_id}完成",
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @action(
        auto_prefix=True,
        description="机器人从反应工站取烧杯并放回堆栈",
        goal_default={"place_beaker_id": None, "pick_station_id": None},
        handles=[
            ActionInputHandle(
                key="station_input",
                data_type="ai4m_station",
                label="取检测站编号",
                data_key="pick_station_id",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="beaker_output",
                data_type="ai4m_beaker",
                label="放烧杯编号",
                data_key="place_beaker_id",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def trigger_robot_place_beaker(
        self,
        place_beaker_id: int,
        pick_station_id: int,
        sample_uuids: SampleUUIDsType = None,
    ) -> dict:
        if place_beaker_id not in range(1, 6):
            raise ValueError(f"放烧杯编号必须在 1-5 范围内: {place_beaker_id}")
        if pick_station_id not in range(1, 4):
            raise ValueError(f"反应工站编号必须在 1-3 范围内: {pick_station_id}")

        rack = self.deck.warehouses["水凝胶烧杯堆栈"]
        station = self.deck.warehouses[f"反应工站{pick_station_id}"]
        carrier = station.sites[0] if station.sites else None
        if carrier is not None and type(carrier).__name__ == "ResourceHolder":
            carrier = None
        rack_site_key = f"C{place_beaker_id}"

        with self._robot_lock:
            self._run_robot_action(
                RobotAction.PICK,
                RobotTargetPosition.REACTION_STATION,
                pick_station_id,
                f"从反应工站{pick_station_id}取烧杯",
            )
            if carrier is not None:
                try:
                    station.unassign_child_resource(carrier)
                except Exception as exc:
                    logger.warning(f"从反应工站解绑载具失败（不影响硬件操作）: {exc}")

            self._run_robot_action(
                RobotAction.PLACE,
                RobotTargetPosition.BEAKER_RACK,
                place_beaker_id,
                f"将烧杯放回堆栈位置{place_beaker_id}",
            )
            if carrier is not None:
                try:
                    rack_site_idx = list(rack._ordering.keys()).index(rack_site_key)
                    rack.assign_child_resource(
                        carrier,
                        location=rack.child_locations[rack_site_key],
                        spot=rack_site_idx,
                    )
                except Exception as exc:
                    logger.warning(f"绑定载具回堆栈失败（不影响硬件操作）: {exc}")
            self._sync_resource_to_frontend()

        extra = {
            "carrier_info": {
                "name": carrier.name if carrier is not None else None,
                "type": "carrier",
                "rack_location": rack_site_key,
                "station_id": pick_station_id,
            },
            "place_beaker_id": place_beaker_id,
            "pick_station_id": pick_station_id,
        }
        return {
            "place_beaker_id": place_beaker_id,
            "pick_station_id": pick_station_id,
            "message": f"机器人从反应工站{pick_station_id}取烧杯并放回位置{place_beaker_id}完成",
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @action(
        auto_prefix=True,
        description="等待 OP10 工站请求，下发参数并启动加工",
        goal_default={"station_id": None},
        handles=[
            ActionInputHandle(
                key="process_station_input",
                data_type="ai4m_station",
                label="检测站编号",
                data_key="station_id",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="process_station_output",
                data_type="ai4m_station",
                label="完成的检测站编号",
                data_key="station_id",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def trigger_station_process(
        self,
        station_id: int,
        mag_stir_stir_speed: int,
        mag_stir_heat_temp: int,
        mag_stir_time_set: int,
        syringe_pump_abs_position_set: int,
        sample_uuids: SampleUUIDsType = None,
    ) -> dict:
        if station_id not in range(1, 4):
            raise ValueError(f"检测站编号必须在 1-3 范围内: {station_id}")

        prefix = f"station_{station_id}"
        self._wait_until(f"{prefix}_request_process", True, f"检测站{station_id}请求加工")
        self._wait_until(f"{prefix}_occupied", True, f"检测站{station_id}占位")

        self._write_node(f"{prefix}_speed", mag_stir_stir_speed)
        self._write_node(f"{prefix}_temperature", mag_stir_heat_temp)
        self._write_node(f"{prefix}_time", mag_stir_time_set)
        self._write_node(f"{prefix}_syringe_position", syringe_pump_abs_position_set)
        self._write_node(f"{prefix}_params_downloaded", True)
        self._wait_until(f"{prefix}_params_executed", True, f"检测站{station_id}参数执行")
        self._write_node(f"{prefix}_params_downloaded", False)
        self._wait_until(
            f"{prefix}_params_executed",
            False,
            f"检测站{station_id}参数执行信号复位",
        )

        self._write_node(f"{prefix}_start", True)
        try:
            self._wait_until(
                f"{prefix}_complete",
                True,
                f"检测站{station_id}加工完成",
                poll_interval=5.0,
            )
        finally:
            self._write_node(f"{prefix}_start", False)
        self._wait_until(
            f"{prefix}_complete",
            False,
            f"检测站{station_id}加工完成信号复位",
        )

        extra = {
            "station_id": station_id,
            "mag_stir_stir_speed": mag_stir_stir_speed,
            "mag_stir_heat_temp": mag_stir_heat_temp,
            "mag_stir_time_set": mag_stir_time_set,
            "syringe_pump_abs_position_set": syringe_pump_abs_position_set,
        }
        return {
            "station_id": station_id,
            "message": f"检测站{station_id}工艺执行完成",
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @action(auto_prefix=True, description="初始化 OP10 机械臂和三个反应工站")
    def trigger_init(self) -> dict:
        self._write_node("robot_action_trigger", False)
        self._write_node("robot_reset", True)
        time.sleep(1.0)
        self._write_node("robot_reset", False)
        self._write_node("robot_initialize", False)
        self._wait_until(
            "robot_initialization_complete",
            False,
            "机械臂初始化完成信号复位",
            fault_node="robot_fault",
        )
        self._write_node("robot_initialize", True)
        self._wait_until(
            "robot_initialization_complete",
            True,
            "机械臂初始化",
            fault_node="robot_fault",
        )
        self._write_node("robot_initialize", False)

        for station_id in (1, 2, 3):
            node = f"station_{station_id}_initialize"
            self._write_node(node, False)
            self._wait_until(
                f"station_{station_id}_initialization_complete",
                False,
                f"反应工站{station_id}初始化完成信号复位",
            )
            self._write_node(node, True)
            self._wait_until(
                f"station_{station_id}_initialization_complete",
                True,
                f"反应工站{station_id}初始化",
            )
            self._write_node(node, False)
        return {"message": "OP10 机械臂和三个反应工站初始化完成"}

    @action(auto_prefix=True, description="向 OP10 三个反应工站批量下发参数")
    def download_auto_params(
        self,
        mag_stir_stir_speed: int,
        mag_stir_heat_temp: int,
        mag_stir_time_set: int,
        syringe_pump_abs_position_set: int,
        auto_job_stop_delay: int,
    ) -> dict:
        logger.warning(
            "OP10 新变量表没有 auto_job_stop_delay 节点，该兼容参数不会下发: "
            f"{auto_job_stop_delay}"
        )
        for station_id in (1, 2, 3):
            prefix = f"station_{station_id}"
            self._write_node(f"{prefix}_speed", mag_stir_stir_speed)
            self._write_node(f"{prefix}_temperature", mag_stir_heat_temp)
            self._write_node(f"{prefix}_time", mag_stir_time_set)
            self._write_node(f"{prefix}_syringe_position", syringe_pump_abs_position_set)
            self._write_node(f"{prefix}_params_downloaded", True)

        for station_id in (1, 2, 3):
            self._wait_until(
                f"station_{station_id}_params_executed",
                True,
                f"反应工站{station_id}参数执行",
            )
            self._write_node(f"station_{station_id}_params_downloaded", False)
            self._wait_until(
                f"station_{station_id}_params_executed",
                False,
                f"反应工站{station_id}参数执行信号复位",
            )
        return {"message": "三个反应工站参数下发完成"}

    @action(auto_prefix=True, description="兼容旧版自动作业入口")
    def start_auto_mode(self) -> dict:
        raise RuntimeError(
            "OP10 新变量表未提供自动模式切换、自动启动和自动完成节点；"
            "请使用机器人动作与 trigger_station_process 组合执行"
        )


if __name__ == "__main__":
    device_instance = AI4MDevice(
        url="opc.tcp://192.168.1.10:4840",
        csv_path=OP10_NODE_TABLE,
    )
    print(device_instance.trigger_init())
