"""AI4M002（OP20）工作站驱动。"""

from enum import IntEnum
from pathlib import Path
import threading
import time
from typing import List, Optional

import requests

from unilabos.devices.workstation.AI4M.base_opcua_client import (
    OpcUaClientWithSubscription,
)
from unilabos.devices.workstation.AI4M.decks import AI4M002_deck
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


OP20_NODE_TABLE = "opcua_nodes_OP20_UniLab.csv"


class AxisTargetPosition(IntEnum):
    """OP20 三轴目标位置代码：磁搅[1]为工位1，磁搅[0]为工位2。"""

    RAW_ELECTRODE = 1
    STIRRER_1 = 2
    STIRRER_2 = 3
    ACID_WASH = 4
    WATER_WASH = 5
    FINISHED_ELECTRODE = 6


class AxisAction(IntEnum):
    PICK = 1
    PLACE = 2
    HOLD_AND_PROCESS = 3


@device(
    id="AI4M002_station",
    display_name="AI4M002 OP20 工作站",
    category=["workstation"],
    description="AI4M002 OP20 电极处理工作站",
    icon="Electrode_Module.jpg",
)
class AI4M002Device(OpcUaClientWithSubscription):
    """OP20 工作站；使用独立 OPC UA 客户端和 OP20 变量表。"""

    def __init__(
        self,
        url: str,
        deck: Optional[AI4M002_deck] = None,
        csv_path: str = "opcua_nodes_OP20_UniLab.csv",
        username: str = None,
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        bts_base_url: str = "http://localhost:8089",
        bts_validate_code: str = "bts-validate-code-2024",
        bts_request_timeout: float = 10.0,
        *args,
        **kwargs,
    ):
        table_name = Path(csv_path or OP20_NODE_TABLE).name
        if table_name != OP20_NODE_TABLE:
            raise ValueError(
                f"AI4M002 只能使用 OP20 变量表 {OP20_NODE_TABLE}，当前为 {table_name}"
            )

        # 为每个设备实例建立独立节点域，避免 OP10/OP20 节点和订阅互相污染。
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
            self.deck = AI4M002_deck(setup=True)
        else:
            self.deck = deck.get("data") if isinstance(deck, dict) else deck
        if self.deck is None:
            raise ValueError("Deck 配置不能为空")

        self._3axis_lock = threading.RLock()
        logger.info("AI4M002 OP20 三轴动作锁已初始化")
        if hasattr(self.deck, "children"):
            logger.info(f"AI4M002 Deck 初始化完成，加载 {len(self.deck.children)} 个资源")

        self._bts_base_url = bts_base_url.rstrip("/")
        self._bts_validate_code = bts_validate_code
        self._bts_request_timeout = bts_request_timeout
        self._bts_session = requests.Session()
        self._bts_validated = False

        self.load_nodes_from_csv(OP20_NODE_TABLE)

    @not_action
    def post_init(self, ros_node) -> None:
        if not getattr(self, "deck", None):
            return
        if not getattr(ros_node, "resource_tracker", None):
            logger.warning("resource_tracker 不存在，无法注册 AI4M002 deck")
            return
        self._ros_node = ros_node
        ros_node.resource_tracker.add_resource(self.deck)
        self._sync_resource_to_frontend()

    @not_action
    def _sync_resource_to_frontend(self) -> None:
        if not getattr(self, "_ros_node", None):
            return
        try:
            from unilabos.ros.nodes.base_device_node import ROS2DeviceNode

            ROS2DeviceNode.run_async_func(
                self._ros_node.update_resource,
                True,
                resources=[self.deck],
            )
            logger.info("AI4M002 资源树已同步到前端")
        except Exception as exc:
            logger.warning(f"AI4M002 资源树同步失败（不影响硬件动作）: {exc}")

    @not_action
    def _write_node(self, name: str, value) -> None:
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
    def _run_axis_action(
        self,
        action_code: AxisAction,
        target_position: AxisTargetPosition,
        target_pick_place_code: Optional[int],
        description: str,
    ) -> None:
        """执行一次 OP20 三轴动作握手。调用方必须持有三轴锁。"""
        self._wait_until("axis_idle", True, "三轴空闲", fault_node="axis_fault")
        if self._read_bool("axis_fault"):
            raise RuntimeError("三轴存在故障，无法执行动作")

        self._write_node("axis_action_code", int(action_code))
        self._write_node("axis_target_position_code", int(target_position))
        if target_pick_place_code is not None:
            self._write_node("axis_target_pick_place_code", target_pick_place_code)
        self._write_node("axis_action_trigger", True)
        try:
            self._wait_until(
                "axis_action_complete",
                True,
                description,
                fault_node="axis_fault",
            )
        finally:
            self._write_node("axis_action_trigger", False)
        self._wait_until(
            "axis_action_complete",
            False,
            f"{description}完成信号复位",
            fault_node="axis_fault",
        )
        logger.info(f"{description}完成")

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
    def _first_material(self, warehouse):
        try:
            material = warehouse.sites[0] if warehouse.sites else None
        except Exception:
            return None
        return None if type(material).__name__ == "ResourceHolder" else material

    @not_action
    def _unassign_material(self, warehouse, material, description: str) -> None:
        if material is None:
            return
        try:
            warehouse.unassign_child_resource(material)
            logger.info(f"已从{description}解绑载具 {material.name}")
        except Exception as exc:
            logger.warning(f"从{description}解绑载具失败（不影响硬件动作）: {exc}")

    @not_action
    def _assign_material(self, warehouse, material, site_key: str, description: str) -> None:
        if material is None:
            return
        try:
            site_idx = list(warehouse._ordering.keys()).index(site_key)
            warehouse.assign_child_resource(
                material,
                location=warehouse.child_locations[site_key],
                spot=site_idx,
            )
            logger.info(f"已绑定载具 {material.name} 到{description}")
        except Exception as exc:
            logger.warning(f"绑定载具到{description}失败（不影响硬件动作）: {exc}")

    @not_action
    def _cell_position(self, cell_id: int) -> AxisTargetPosition:
        return (
            AxisTargetPosition.STIRRER_1
            if cell_id == 1
            else AxisTargetPosition.STIRRER_2
        )

    @not_action
    def _wait_for_free_cell(self, requested_cell_id: Optional[int]) -> int:
        while True:
            cell_ids = (requested_cell_id,) if requested_cell_id else (1, 2)
            for cell_id in cell_ids:
                if not self._read_bool(f"stirrer_{cell_id}_occupied"):
                    return cell_id
            logger.info("两个电解池均有占位，等待空闲...")
            time.sleep(1.0)

    @action(auto_prefix=True, description="初始化 OP20 三轴和两个磁搅工站")
    def trigger_s02_init(self) -> dict:
        self._write_node("axis_action_trigger", False)
        self._write_node("axis_reset", True)
        time.sleep(1.0)
        self._write_node("axis_reset", False)
        self._write_node("axis_initialize", False)
        self._wait_until(
            "axis_initialization_complete",
            False,
            "三轴初始化完成信号复位",
            fault_node="axis_fault",
        )
        self._write_node("axis_initialize", True)
        self._wait_until(
            "axis_initialization_complete",
            True,
            "三轴初始化",
            fault_node="axis_fault",
        )
        self._write_node("axis_initialize", False)

        for station_id in (1, 2):
            node = f"stirrer_{station_id}_initialize"
            self._write_node(node, False)
            self._wait_until(
                f"stirrer_{station_id}_initialization_complete",
                False,
                f"磁搅工站{station_id}初始化完成信号复位",
            )
            self._write_node(node, True)
            self._wait_until(
                f"stirrer_{station_id}_initialization_complete",
                True,
                f"磁搅工站{station_id}初始化",
            )
            self._write_node(node, False)
        return {"message": "OP20 三轴和两个磁搅工站初始化完成"}

    @action(
        auto_prefix=True,
        description="从原始电极堆栈取料并放到电解池",
        goal_default={"pick_code": None, "electrolytic_cell_id": None},
        handles=[
            ActionInputHandle(
                key="raw_electrode_input",
                data_type="ai4m002_raw_electrode",
                label="原始电极堆栈位置",
                data_key="pick_code",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="electrolytic_cell_output",
                data_type="ai4m002_electrolytic_cell",
                label="电解池编号",
                data_key="electrolytic_cell_id",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def trigger_3axis_pick_from_raw_and_place_to_electrolytic_cell(
        self,
        pick_code: int,
        electrolytic_cell_id: Optional[int] = None,
        sample_uuids: SampleUUIDsType = None,
    ) -> dict:
        if pick_code not in range(1, 16):
            raise ValueError(f"原始电极位置必须在 1-15 范围内: {pick_code}")
        if electrolytic_cell_id is not None and electrolytic_cell_id not in (1, 2):
            raise ValueError(f"电解池编号必须为 1 或 2: {electrolytic_cell_id}")

        requested_cell_id = electrolytic_cell_id
        while True:
            target_cell_id = self._wait_for_free_cell(requested_cell_id)
            with self._3axis_lock:
                if self._read_bool(f"stirrer_{target_cell_id}_occupied"):
                    logger.info(f"电解池{target_cell_id}状态已变化，重新选择")
                    continue

                raw = self.deck.warehouses["原始电极堆栈"]
                cell = self.deck.warehouses[f"搅拌仪{target_cell_id}"]
                raw_site_key = str(pick_code)
                try:
                    material = raw[raw_site_key]
                except Exception:
                    material = None
                if material is None:
                    logger.warning(
                        f"原始电极位置 {pick_code} 没有前端载具，硬件动作仍继续"
                    )

                self._run_axis_action(
                    AxisAction.PICK,
                    AxisTargetPosition.RAW_ELECTRODE,
                    pick_code,
                    f"从原始电极位置{pick_code}取料",
                )
                self._unassign_material(raw, material, f"原始电极位置{pick_code}")
                self._run_axis_action(
                    AxisAction.PLACE,
                    self._cell_position(target_cell_id),
                    1,
                    f"将电极放到电解池{target_cell_id}",
                )
                self._write_node(f"Electrolytic_Cell_{target_cell_id}_Done", False)
                cell_site_key = list(cell._ordering.keys())[0]
                self._assign_material(cell, material, cell_site_key, f"电解池{target_cell_id}")
                self._sync_resource_to_frontend()
                break

        extra = {"electrolytic_cell_id": target_cell_id, "pick_code": pick_code}
        return {
            "electrolytic_cell_id": target_cell_id,
            "electrolytic_cell_name": f"电解池{target_cell_id}（搅拌仪{target_cell_id}）",
            "pick_code": pick_code,
            "message": f"从原始电极取料并放到电解池{target_cell_id}完成",
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @action(
        auto_prefix=True,
        description="从电解池取料，经水洗后放到完成电极堆栈",
        goal_default={"electrolytic_cell_id": None, "place_code": None},
        handles=[
            ActionInputHandle(
                key="electrolytic_cell_input",
                data_type="ai4m002_electrolytic_cell",
                label="电解池编号",
                data_key="electrolytic_cell_id",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="finished_electrode_output",
                data_type="ai4m002_finished_electrode",
                label="完成电极堆栈位置",
                data_key="place_code",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def trigger_3axis_pick_from_electrolytic_cell_and_place_to_finished(
        self,
        electrolytic_cell_id: int,
        cleaning_time: int,
        nitrogen_time: int,
        place_code: int,
        sample_uuids: SampleUUIDsType = None,
    ) -> dict:
        if electrolytic_cell_id not in (1, 2):
            raise ValueError(f"电解池编号必须为 1 或 2: {electrolytic_cell_id}")
        if place_code not in range(1, 16):
            raise ValueError(f"完成电极位置必须在 1-15 范围内: {place_code}")

        self._wait_until(
            f"Electrolytic_Cell_{electrolytic_cell_id}_Done",
            True,
            f"电解池{electrolytic_cell_id}加工完成",
        )
        cell = self.deck.warehouses[f"搅拌仪{electrolytic_cell_id}"]
        water = self.deck.warehouses["水洗池"]
        finished = self.deck.warehouses["完成电极堆栈"]

        with self._3axis_lock:
            material = self._first_material(cell)
            self._run_axis_action(
                AxisAction.PICK,
                self._cell_position(electrolytic_cell_id),
                1,
                f"从电解池{electrolytic_cell_id}取料",
            )
            self._write_node(f"Electrolytic_Cell_{electrolytic_cell_id}_Done", False)
            self._unassign_material(cell, material, f"电解池{electrolytic_cell_id}")

            self._write_node("cleaning_time", cleaning_time)
            self._write_node("blowing_time", nitrogen_time)
            self._run_axis_action(
                AxisAction.HOLD_AND_PROCESS,
                AxisTargetPosition.WATER_WASH,
                None,
                "水洗和吹气处理",
            )
            water_site_key = list(water._ordering.keys())[0]
            self._assign_material(water, material, water_site_key, "水洗池")
            self._sync_resource_to_frontend()
            self._unassign_material(water, material, "水洗池")

            self._run_axis_action(
                AxisAction.PLACE,
                AxisTargetPosition.FINISHED_ELECTRODE,
                place_code,
                f"将电极放到完成位置{place_code}",
            )
            self._assign_material(finished, material, str(place_code), f"完成电极位置{place_code}")
            self._sync_resource_to_frontend()

        extra = {
            "electrolytic_cell_id": electrolytic_cell_id,
            "cleaning_time": cleaning_time,
            "nitrogen_time": nitrogen_time,
            "place_code": place_code,
        }
        return {
            **extra,
            "electrolytic_cell_name": f"电解池{electrolytic_cell_id}（搅拌仪{electrolytic_cell_id}）",
            "message": f"从电解池{electrolytic_cell_id}取料，经水洗后放到完成电极完成",
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @action(
        auto_prefix=True,
        description="从原始电极取料，经酸洗、水洗后放到完成电极堆栈",
        goal_default={"pick_code": None, "place_code": None},
        handles=[
            ActionInputHandle(
                key="raw_electrode_input",
                data_type="ai4m002_raw_electrode",
                label="原始电极堆栈位置",
                data_key="pick_code",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="finished_electrode_output",
                data_type="ai4m002_finished_electrode",
                label="完成电极堆栈位置",
                data_key="place_code",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def trigger_3axis_pick_from_raw_and_process_to_finished(
        self,
        pick_code: int,
        pickling_time: int,
        cleaning_time: int,
        nitrogen_time: int,
        place_code: int,
        sample_uuids: SampleUUIDsType = None,
    ) -> dict:
        if pick_code not in range(1, 16) or place_code not in range(1, 16):
            raise ValueError("原始电极和完成电极位置必须在 1-15 范围内")

        raw = self.deck.warehouses["原始电极堆栈"]
        acid = self.deck.warehouses["酸洗池"]
        water = self.deck.warehouses["水洗池"]
        finished = self.deck.warehouses["完成电极堆栈"]
        try:
            material = raw[str(pick_code)]
        except Exception:
            material = None
        if material is None:
            logger.warning(f"原始电极位置 {pick_code} 没有前端载具，硬件动作仍继续")

        with self._3axis_lock:
            self._run_axis_action(
                AxisAction.PICK,
                AxisTargetPosition.RAW_ELECTRODE,
                pick_code,
                f"从原始电极位置{pick_code}取料",
            )
            self._unassign_material(raw, material, f"原始电极位置{pick_code}")

            self._write_node("soaking_time", pickling_time)
            self._run_axis_action(
                AxisAction.HOLD_AND_PROCESS,
                AxisTargetPosition.ACID_WASH,
                None,
                "酸洗处理",
            )
            acid_site_key = list(acid._ordering.keys())[0]
            self._assign_material(acid, material, acid_site_key, "酸洗池")
            self._sync_resource_to_frontend()
            self._unassign_material(acid, material, "酸洗池")

            self._write_node("cleaning_time", cleaning_time)
            self._write_node("blowing_time", nitrogen_time)
            self._run_axis_action(
                AxisAction.HOLD_AND_PROCESS,
                AxisTargetPosition.WATER_WASH,
                None,
                "水洗和吹气处理",
            )
            water_site_key = list(water._ordering.keys())[0]
            self._assign_material(water, material, water_site_key, "水洗池")
            self._sync_resource_to_frontend()
            self._unassign_material(water, material, "水洗池")

            self._run_axis_action(
                AxisAction.PLACE,
                AxisTargetPosition.FINISHED_ELECTRODE,
                place_code,
                f"将电极放到完成位置{place_code}",
            )
            self._assign_material(finished, material, str(place_code), f"完成电极位置{place_code}")
            self._sync_resource_to_frontend()

        extra = {
            "pick_code": pick_code,
            "pickling_time": pickling_time,
            "cleaning_time": cleaning_time,
            "nitrogen_time": nitrogen_time,
            "place_code": place_code,
        }
        return {
            **extra,
            "message": "从原始电极取料，经酸洗、水洗后放到完成电极完成",
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @action(auto_prefix=True, description="向指定 OP20 磁搅工站下发参数")
    def set_stirrer_params(
        self,
        station_id: int,
        stir_speed: int,
        heat_temp: int,
        time_set: int,
        sample_uuids: SampleUUIDsType = None,
    ) -> dict:
        if station_id not in (1, 2):
            raise ValueError(f"磁搅工站编号必须为 1 或 2: {station_id}")
        prefix = f"stirrer_{station_id}"
        self._wait_until(f"{prefix}_request_process", True, f"磁搅工站{station_id}请求加工")
        self._wait_until(f"{prefix}_occupied", True, f"磁搅工站{station_id}占位")
        self._write_node(f"{prefix}_speed", stir_speed)
        self._write_node(f"{prefix}_temperature", heat_temp)
        self._write_node(f"{prefix}_time", time_set)
        self._write_node(f"{prefix}_params_downloaded", True)
        self._wait_until(f"{prefix}_params_executed", True, f"磁搅工站{station_id}参数执行")
        self._write_node(f"{prefix}_params_downloaded", False)
        self._wait_until(
            f"{prefix}_params_executed",
            False,
            f"磁搅工站{station_id}参数执行信号复位",
        )

        extra = {
            "station_id": station_id,
            "stir_speed": stir_speed,
            "heat_temp": heat_temp,
            "time_set": time_set,
        }
        return {
            **extra,
            "message": f"磁搅工站{station_id}参数设置完成",
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @action(
        auto_prefix=True,
        description="触发电解池 BTS 反应，并与 OP20 加工信号联动",
        goal_default={"electrolytic_cell_id": None, "simulate_bts": False},
        handles=[
            ActionInputHandle(
                key="electrolytic_cell_input",
                data_type="ai4m002_electrolytic_cell",
                label="电解池编号",
                data_key="electrolytic_cell_id",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="electrolytic_cell_output",
                data_type="ai4m002_electrolytic_cell",
                label="电解池编号",
                data_key="electrolytic_cell_id",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def trigger_electrolytic_cell_bts_reaction(
        self,
        electrolytic_cell_id: int,
        sample_uuids: SampleUUIDsType = None,
        duration_sec: int = 20,
        current: float = 50.0,
        simulate_bts: bool = False,
    ) -> dict:
        if electrolytic_cell_id not in (1, 2):
            raise ValueError(f"电解池编号必须为 1 或 2: {electrolytic_cell_id}")

        prefix = f"stirrer_{electrolytic_cell_id}"
        self._wait_until(
            f"{prefix}_request_process",
            True,
            f"电解池{electrolytic_cell_id}请求加工",
        )
        self._wait_until(
            f"{prefix}_occupied",
            True,
            f"电解池{electrolytic_cell_id}占位",
        )
        done_node = f"Electrolytic_Cell_{electrolytic_cell_id}_Done"
        self._write_node(done_node, False)
        self._write_node(f"{prefix}_start", True)
        plc_completed = False
        try:
            if simulate_bts:
                logger.info(f"电解池{electrolytic_cell_id}启用 BTS 仿真，跳过 API 调用")
                bts_result = {
                    "success": True,
                    "message": "BTS仿真完成",
                    "simulated": True,
                    "test_id": None,
                }
            else:
                try:
                    bts_result = self.bts_start_cp_test(
                        chl_list=[electrolytic_cell_id - 1],
                        duration_sec=duration_sec,
                        current=current,
                    )
                except Exception as exc:
                    raise RuntimeError(f"BTS 执行失败: {exc}") from exc
                if not bts_result.get("success", False):
                    raise RuntimeError(
                        f"BTS 执行失败: {bts_result.get('message', '未知错误')}"
                    )

            self._write_node(done_node, True)
            logger.info(f"BTS完成，已写入电解池{electrolytic_cell_id}加工完成信号")
            self._wait_until(
                f"{prefix}_complete",
                True,
                f"电解池{electrolytic_cell_id} PLC 加工完成确认",
                timeout=max(300.0, float(duration_sec) + 120.0),
            )
            plc_completed = True
        finally:
            self._write_node(f"{prefix}_start", False)
        if plc_completed:
            self._wait_until(
                f"{prefix}_complete",
                False,
                f"电解池{electrolytic_cell_id} PLC 加工完成信号复位",
            )

        extra = {
            "electrolytic_cell_id": electrolytic_cell_id,
            "simulate_bts": simulate_bts,
        }
        return {
            "electrolytic_cell_id": electrolytic_cell_id,
            "message": f"电解池{electrolytic_cell_id} BTS反应完成",
            "bts_result": bts_result,
            "unilabos_samples": self._sample_results(sample_uuids, extra),
        }

    @not_action
    def _bts_validate(self) -> dict:
        url = f"{self._bts_base_url}/api/bts/validate"
        payload = {
            "cmd-type": 1,
            "request-id": f"validate-{int(time.time())}",
            "data": {"check-id": self._bts_validate_code},
        }
        response = self._bts_session.post(
            url,
            json=payload,
            timeout=self._bts_request_timeout,
        )
        self._bts_validated = response.status_code == 200
        logger.info(f"BTS 校验: 状态码={response.status_code}, 响应={response.text}")
        return {
            "success": self._bts_validated,
            "message": "BTS校验成功" if self._bts_validated else "BTS校验失败",
            "response": response.text,
        }

    @not_action
    def bts_start_cp_test(
        self,
        chl_list: List[int],
        duration_sec: int = 20,
        current: float = 50.0,
        dev_uuid: Optional[str] = None,
    ) -> dict:
        validation = self._bts_validate()
        if not validation["success"]:
            return {**validation, "test_id": None}

        info_url = f"{self._bts_base_url}/api/bts/device/info"
        response = self._bts_session.get(
            info_url,
            json={"cmd-type": 2, "request-id": f"device-info-{int(time.time())}"},
            timeout=self._bts_request_timeout,
        )
        if response.status_code != 200:
            return {
                "success": False,
                "message": f"获取BTS设备信息失败: {response.text}",
                "test_id": None,
            }
        devices = response.json().get("data", {}).get("dev-list", [])
        if dev_uuid is None:
            if len(devices) != 1:
                return {
                    "success": False,
                    "message": "BTS设备数量不是1，必须指定 dev_uuid",
                    "test_id": None,
                }
            dev_uuid = devices[0]["dev-uuid"]

        self.bts_stop_test(dev_uuid, chl_list)
        start_url = f"{self._bts_base_url}/api/bts/test/start"
        test_id = f"test-cp-{int(time.time())}"
        payload = {
            "cmd-type": 3,
            "request-id": f"start-test-{int(time.time())}",
            "data": {
                "test-id": test_id,
                "dev-ip": dev_uuid,
                "chl-list": chl_list,
                "globalProtect": {
                    "voltageProtect": {
                        "underVoltage": -5,
                        "overVoltage": 5,
                        "enableUnderVoltage": True,
                        "enableOverVoltage": True,
                        "enableRangeProtect": False,
                        "delayTime": 0,
                        "enableDelay": False,
                    },
                    "currentProtect": {
                        "charge": 100,
                        "discharge": 100,
                        "enableCharge": True,
                        "enableDischarge": True,
                        "enableRangeProtect": False,
                    },
                },
                "globalRecordCondi": {
                    "electricCurrent": 0,
                    "enable_electricCurrent": False,
                    "enable_time": True,
                    "enable_voltage": False,
                    "time": 1000,
                    "voltage": 0,
                },
                "batteryInfo": {
                    "creator": "unilabos",
                    "weight": 100,
                    "batteryBatchNum": "",
                    "currentUpperLimit": 100,
                    "voltageUpperLimit": 5,
                    "voltageLowerLimit": -5,
                },
                "stepList": [
                    {
                        "type": 21,
                        "pType": 0,
                        "mode": 2,
                        "mPara": current,
                        "rateMode": False,
                        "rateValue": 0,
                        "recordCondi": {
                            "enable_time": True,
                            "time": 1000,
                            "enable_voltage": False,
                            "voltage": 0,
                        },
                        "endCondi": [
                            {
                                "also": True,
                                "rateMode": False,
                                "rateModeType": 0,
                                "rateValue": 0,
                                "relation": 1,
                                "type": 3,
                                "userCustomVariable-arithmetic": 0,
                                "userCustomVariable-isVariable": False,
                                "userCustomVariable-value": 0,
                                "userCustomVariable-value2": 0,
                                "userCustomVariable-variableParam": 0,
                                "userCustomVariable-variableParam2": 0,
                                "value": duration_sec * 1000,
                            }
                        ],
                    }
                ],
            },
        }
        response = self._bts_session.post(
            start_url,
            json=payload,
            timeout=self._bts_request_timeout,
        )
        logger.info(f"BTS 启动 CP 测试: 状态码={response.status_code}, 响应={response.text}")
        if response.status_code != 200:
            return {
                "success": False,
                "test_id": None,
                "message": "启动BTS测试失败",
                "response": response.text,
            }

        deadline = time.monotonic() + duration_sec + 15
        time.sleep(min(5.0, max(1.0, duration_sec / 2)))
        while time.monotonic() < deadline:
            state = self.bts_get_channel_state(chl_list, dev_uuid)
            if not state.get("success"):
                return {
                    "success": False,
                    "test_id": test_id,
                    "message": state.get("message", "查询BTS状态失败"),
                    "response": state.get("response"),
                }
            if state.get("all_idle"):
                stop = self.bts_stop_test(dev_uuid, chl_list)
                if not stop.get("success"):
                    return {**stop, "test_id": test_id}
                return {
                    "success": True,
                    "test_id": test_id,
                    "message": "BTS CP测试完成",
                    "response": response.text,
                }
            time.sleep(2.0)

        self.bts_stop_test(dev_uuid, chl_list)
        return {
            "success": False,
            "test_id": test_id,
            "message": "BTS故障：等待通道空闲超时",
        }

    @not_action
    def bts_get_channel_state(
        self,
        chl_list: List[int],
        dev_uuid: Optional[str] = None,
    ) -> dict:
        if not self._bts_validated:
            validation = self._bts_validate()
            if not validation["success"]:
                return validation
        if dev_uuid is None:
            info_url = f"{self._bts_base_url}/api/bts/device/info"
            response = self._bts_session.get(
                info_url,
                json={"cmd-type": 2, "request-id": f"device-info-{int(time.time())}"},
                timeout=self._bts_request_timeout,
            )
            if response.status_code != 200:
                return {"success": False, "message": "获取BTS设备信息失败", "response": response.text}
            devices = response.json().get("data", {}).get("dev-list", [])
            if len(devices) != 1:
                return {"success": False, "message": "BTS设备数量不是1，必须指定 dev_uuid"}
            dev_uuid = devices[0]["dev-uuid"]

        state_url = f"{self._bts_base_url}/api/bts/test/state"
        payload = {
            "cmd-type": 5,
            "request-id": f"channel-state-{int(time.time())}",
            "data": [{"dev-uuid": dev_uuid, "chl-list": chl_list}],
        }
        response = self._bts_session.post(
            state_url,
            json=payload,
            timeout=self._bts_request_timeout,
        )
        if response.status_code != 200:
            return {"success": False, "message": "查询BTS状态失败", "response": response.text}
        dev_info = response.json().get("data", {}).get("dev-info", [])
        states = {
            channel["chl"]: channel.get("state")
            for device in dev_info
            for channel in device.get("chl-state", [])
            if channel.get("chl") in chl_list
        }
        all_idle = bool(states) and all(states.get(chl) == 0 for chl in chl_list)
        logger.info(f"BTS 通道状态: {states}")
        return {
            "success": True,
            "all_idle": all_idle,
            "states": states,
            "response": response.text,
        }

    @not_action
    def bts_stop_test(self, dev_uuid: str, chl_list: List[int]) -> dict:
        if not self._bts_validated:
            return {"success": False, "message": "请先通过 BTS 校验"}
        stop_url = f"{self._bts_base_url}/api/bts/test/stop"
        payload = {
            "cmd-type": 4,
            "request-id": f"stop-test-{int(time.time())}",
            "data": {"dev-ip": dev_uuid, "chl-list": chl_list},
        }
        response = self._bts_session.post(
            stop_url,
            json=payload,
            timeout=self._bts_request_timeout,
        )
        success = response.status_code == 200
        logger.info(f"BTS 停止测试: 状态码={response.status_code}, 响应={response.text}")
        return {
            "success": success,
            "message": "停止BTS测试成功" if success else "停止BTS测试失败",
            "response": response.text,
        }


if __name__ == "__main__":
    device_instance = AI4M002Device(
        url="opc.tcp://192.168.1.10:4840",
        csv_path=OP20_NODE_TABLE,
    )
    print(device_instance.trigger_s02_init())
