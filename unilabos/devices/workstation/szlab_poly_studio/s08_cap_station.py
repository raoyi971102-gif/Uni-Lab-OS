"""
S08 开盖/关盖工位子设备。

通过 szlab_poly_plc 转发 OPC UA 读写，实现 S08 开关盖工站统一握手（新版 PLC 协议）。

对外暴露 6 个工艺 Action，与「S08工艺选择 / S08工艺完成」1–6 一一对应：
  open_sample_vial_500ml_cap / close_sample_vial_500ml_cap
  open_sample_vial_250ml_cap / close_sample_vial_250ml_cap
  open_liquid_vial_100ml_cap / close_liquid_vial_100ml_cap

瓶盖暂存映射由 UniLab 写入/读取 OPC UA「S082_{1..5}数据缓存」INT[30]（样品 ID），
配合「S082瓶盖暂存位」下发工艺。开盖时分配第一个空闲暂存位并绑定样品 ID；
关盖时按样品 ID 查找对应暂存位。样品 ID 通过 action 入参 sample_id（list[int]，最长 30）传入。

复位原则：UniLab 写入的变量由 UniLab 在收到匹配的 S08工艺完成 后复位；
S08工艺完成 由 PLC 提供并由 PLC 复位，UniLab 只读。
"""

from __future__ import annotations

import json
import threading
import time
from enum import IntEnum
from typing import Any, Optional, Sequence

from rclpy.action import ActionClient
from unilabos_msgs.action import StrSingleInput

from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.resources.resource_tracker import JSON_UNILABOS_PARAM, PARAM_SAMPLE_UUIDS
from unilabos.utils.log import logger


NODE_HOME = "S08原点信号"
NODE_ALLOW_PROCESS = "S08允许加工"
NODE_PROCESS_SELECT = "S08工艺选择"
NODE_PARAMS_WRITTEN = "S08参数写入完成"
NODE_PROCESS_COMPLETE = "S08工艺完成"
NODE_CAP_STORAGE_SLOT = "S082瓶盖暂存位"

CAP_CACHE_LENGTH = 30
CAP_STORAGE_SLOTS = tuple(range(1, 6))

SENSOR_CAP_STATION = {
    1: "传感器状态_上位机[3].NO[14]",
    2: "传感器状态_上位机[3].NO[15]",
}

CAP_STORAGE_SLOT_SENSORS = {
    1: "传感器状态_上位机[4].NO[0]",
    2: "传感器状态_上位机[4].NO[1]",
    3: "传感器状态_上位机[4].NO[2]",
    4: "传感器状态_上位机[4].NO[3]",
    5: "传感器状态_上位机[4].NO[4]",
}


class S08ProcessType(IntEnum):
    OPEN_SAMPLE_VIAL_500ML = 1
    CLOSE_SAMPLE_VIAL_500ML = 2
    OPEN_SAMPLE_VIAL_250ML = 3
    CLOSE_SAMPLE_VIAL_250ML = 4
    OPEN_LIQUID_VIAL_100ML = 5
    CLOSE_LIQUID_VIAL_100ML = 6


OPEN_PROCESS_IDS = frozenset(
    {
        S08ProcessType.OPEN_SAMPLE_VIAL_500ML,
        S08ProcessType.OPEN_SAMPLE_VIAL_250ML,
        S08ProcessType.OPEN_LIQUID_VIAL_100ML,
    }
)


def _cap_cache_element_name(slot: int, index: int) -> str:
    _validate_cap_storage_slot(slot)
    if index not in range(CAP_CACHE_LENGTH):
        raise ValueError(f"缓存下标必须在 0-{CAP_CACHE_LENGTH - 1} 范围内，收到: {index}")
    return f"S082_{slot}数据缓存[{index}]"


def _normalize_sample_id(sample_id: Sequence[int] | None) -> list[int]:
    if sample_id is None:
        return [0] * CAP_CACHE_LENGTH
    values = [int(v) for v in sample_id]
    if not values:
        raise ValueError("sample_id 不能为空")
    if len(values) > CAP_CACHE_LENGTH:
        raise ValueError(f"sample_id 长度不能超过 {CAP_CACHE_LENGTH}")
    return values + [0] * (CAP_CACHE_LENGTH - len(values))


def _sample_id_is_empty(sample_id: Sequence[int]) -> bool:
    return all(int(v) == 0 for v in sample_id)


def _sample_ids_match(left: Sequence[int], right: Sequence[int]) -> bool:
    left_norm = _normalize_sample_id(list(left))
    right_norm = _normalize_sample_id(list(right))
    return left_norm == right_norm and not _sample_id_is_empty(left_norm)


def _validate_cap_storage_slot(cap_storage_slot: int) -> None:
    if cap_storage_slot not in range(1, 6):
        raise ValueError(f"cap_storage_slot 必须在 1-5 范围内，收到: {cap_storage_slot}")


@device(
    id="szlab_s08_cap_station",
    display_name="S08 开盖工位",
    category=["workstation", "szlab"],
    description="苏州实验室 S08 开盖/关盖工位，通过 szlab_poly_plc 转发 PLC 读写",
)
class SZLabS08CapStationDevice:
    def __init__(
        self,
        plc_device_id: str = "szlab_poly_plc",
        plc_action_timeout: float = 30.0,
        process_timeout: float = 300.0,
        poll_interval: float = 0.2,
        require_station_ready: bool = True,
        *args,
        **kwargs,
    ):
        self.plc_device_id = plc_device_id
        self.plc_action_timeout = plc_action_timeout
        self.process_timeout = process_timeout
        self.poll_interval = poll_interval
        self.require_station_ready = require_station_ready
        self._ros_node = None
        self._plc_command_client: Optional[ActionClient] = None
        self._last_status: dict[str, Any] = {}

    @not_action
    def post_init(self, ros_node) -> None:
        self._ros_node = ros_node
        self._plc_command_client = ActionClient(
            ros_node,
            StrSingleInput,
            f"/devices/{self.plc_device_id}/_execute_driver_command",
            callback_group=ros_node.callback_group,
        )

    @not_action
    def _wait_future(self, future, timeout: float, description: str):
        done = threading.Event()
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout):
            raise TimeoutError(f"{description} 超时 ({timeout}s)")
        return future.result()

    @not_action
    def _call_plc_command(self, function_name: str, function_args: dict[str, Any]) -> Any:
        if self._plc_command_client is None:
            raise RuntimeError("szlab_poly_plc action client 尚未初始化")

        if not self._plc_command_client.wait_for_server(timeout_sec=self.plc_action_timeout):
            raise TimeoutError(f"等待 {self.plc_device_id} 命令服务超时")

        command = {
            "function_name": function_name,
            "function_args": function_args,
            JSON_UNILABOS_PARAM: {PARAM_SAMPLE_UUIDS: {}},
        }
        goal = StrSingleInput.Goal()
        goal.string = json.dumps(command, ensure_ascii=False)

        goal_handle = self._wait_future(
            self._plc_command_client.send_goal_async(goal),
            self.plc_action_timeout,
            f"发送 PLC 命令 {function_name}",
        )
        if not goal_handle.accepted:
            raise RuntimeError(f"{self.plc_device_id} 拒绝执行命令: {function_name}")

        result_wrapper = self._wait_future(
            goal_handle.get_result_async(),
            self.plc_action_timeout,
            f"等待 PLC 命令 {function_name} 返回",
        )
        result = result_wrapper.result
        result_info = json.loads(result.return_info or "{}")
        if not result.success or not result_info.get("suc", False):
            raise RuntimeError(result_info.get("error") or f"{self.plc_device_id} 命令失败: {function_name}")
        return result_info.get("return_value")

    @not_action
    def _read_plc_variable(self, node_name: str) -> Any:
        return self._call_plc_command(
            "read_variable",
            {"node_name": node_name, "use_cache": False},
        )

    @not_action
    def _write_plc_variable(self, node_name: str, value: Any) -> None:
        self._call_plc_command(
            "write_variable",
            {"node_name": node_name, "value": value},
        )

    @not_action
    def _wait_plc_bool(
        self,
        node_name: str,
        expected: bool,
        timeout: Optional[float] = None,
        description: Optional[str] = None,
    ) -> bool:
        timeout = self.process_timeout if timeout is None else timeout
        desc = description or node_name
        logger.info(f"等待 {desc} == {expected}")
        start = time.time()
        while time.time() - start < timeout:
            if bool(self._read_plc_variable(node_name)) is expected:
                logger.info(f"✓ {desc} 已变为 {expected}")
                return True
            time.sleep(self.poll_interval)
        logger.error(f"✗ 等待 {desc} 超时 ({timeout}s)")
        return False

    @not_action
    def _wait_process_complete(
        self,
        expected: int,
        timeout: Optional[float] = None,
        description: Optional[str] = None,
    ) -> bool:
        timeout = self.process_timeout if timeout is None else timeout
        desc = description or f"{NODE_PROCESS_COMPLETE} == {expected}"
        logger.info(f"等待 {desc}")
        start = time.time()
        while time.time() - start < timeout:
            current = int(self._read_plc_variable(NODE_PROCESS_COMPLETE) or 0)
            if current == expected:
                logger.info(f"✓ {desc}")
                return True
            time.sleep(self.poll_interval)
        logger.error(f"✗ 等待 {desc} 超时 ({timeout}s)")
        return False

    @not_action
    def _read_s08_status(self) -> dict[str, Any]:
        status = {
            "station_ready": bool(self._read_plc_variable(NODE_HOME)),
            "allow_process": bool(self._read_plc_variable(NODE_ALLOW_PROCESS)),
            "process_select": int(self._read_plc_variable(NODE_PROCESS_SELECT) or 0),
            "params_written": bool(self._read_plc_variable(NODE_PARAMS_WRITTEN)),
            "process_complete": int(self._read_plc_variable(NODE_PROCESS_COMPLETE) or 0),
            "cap_storage_slot": int(self._read_plc_variable(NODE_CAP_STORAGE_SLOT) or 0),
        }
        self._last_status = status
        return status

    @not_action
    def _reset_unilab_written_params(self) -> None:
        self._write_plc_variable(NODE_PROCESS_SELECT, 0)
        self._write_plc_variable(NODE_PARAMS_WRITTEN, False)
        self._write_plc_variable(NODE_CAP_STORAGE_SLOT, 0)

    @not_action
    def _read_sample_id_from_plc(self, slot: int) -> list[int]:
        return [
            int(self._read_plc_variable(_cap_cache_element_name(slot, index)) or 0)
            for index in range(CAP_CACHE_LENGTH)
        ]

    @not_action
    def _write_sample_id_to_slot_cache(self, slot: int, sample_id: Sequence[int]) -> None:
        normalized = _normalize_sample_id(sample_id)
        for index, value in enumerate(normalized):
            self._write_plc_variable(_cap_cache_element_name(slot, index), value)

    @not_action
    def _clear_slot_cache(self, slot: int) -> None:
        self._write_sample_id_to_slot_cache(slot, [0] * CAP_CACHE_LENGTH)

    @not_action
    def _read_cap_storage_registry(self) -> dict[int, list[int]]:
        registry: dict[int, list[int]] = {}
        for slot in CAP_STORAGE_SLOTS:
            cached = self._try_read_sample_id_from_plc(slot)
            registry[slot] = cached if cached is not None else [0] * CAP_CACHE_LENGTH
        return registry

    @not_action
    def _try_read_sample_id_from_plc(self, slot: int) -> Optional[list[int]]:
        try:
            return self._read_sample_id_from_plc(slot=slot)
        except Exception as exc:
            logger.warning(f"读取 S082_{slot} 数据缓存失败: {exc}")
            return None

    @not_action
    def _find_free_cap_slot(self) -> Optional[int]:
        for slot in CAP_STORAGE_SLOTS:
            cached = self._try_read_sample_id_from_plc(slot)
            if cached is not None and _sample_id_is_empty(cached):
                return slot
        return None

    @not_action
    def _find_cap_slot_by_sample_id(self, sample_id: Sequence[int]) -> Optional[int]:
        normalized = _normalize_sample_id(sample_id)
        if _sample_id_is_empty(normalized):
            raise ValueError("sample_id 不能全为 0")
        for slot in CAP_STORAGE_SLOTS:
            cached = self._try_read_sample_id_from_plc(slot)
            if cached is not None and _sample_ids_match(cached, normalized):
                return slot
        return None

    @not_action
    def _resolve_sample_id_for_open(self, sample_id: Sequence[int] | None) -> list[int]:
        if sample_id is None:
            raise ValueError("开盖 action 必须传入 sample_id（list[int]，最长 30）")
        normalized = _normalize_sample_id(sample_id)
        if _sample_id_is_empty(normalized):
            raise ValueError("sample_id 不能全为 0")
        return normalized

    @not_action
    def _resolve_cap_storage_slot_for_open(
        self,
        sample_id: Sequence[int],
        cap_storage_slot: Optional[int] = None,
    ) -> int:
        normalized = _normalize_sample_id(sample_id)
        if cap_storage_slot is not None:
            _validate_cap_storage_slot(cap_storage_slot)
            cached = self._try_read_sample_id_from_plc(cap_storage_slot)
            if cached is not None and not _sample_id_is_empty(cached) and not _sample_ids_match(cached, normalized):
                raise ValueError(f"暂存位 {cap_storage_slot} 已被其他样品占用")
            return cap_storage_slot
        existing_slot = self._find_cap_slot_by_sample_id(normalized)
        if existing_slot is not None:
            return existing_slot
        free_slot = self._find_free_cap_slot()
        if free_slot is None:
            raise ValueError("无可用瓶盖暂存位（1-5 均已绑定样品 ID）")
        return free_slot

    @not_action
    def _resolve_cap_storage_slot_for_close(self, sample_id: Sequence[int]) -> int:
        slot = self._find_cap_slot_by_sample_id(sample_id)
        if slot is None:
            raise ValueError("未找到该样品 ID 对应的瓶盖暂存位")
        return slot

    @not_action
    def _run_cap_process(
        self,
        process_type: S08ProcessType,
        cap_storage_slot: int,
        sample_id: Sequence[int],
        timeout: Optional[float] = None,
        clear_cache_on_complete: bool = False,
    ) -> dict[str, Any]:
        _validate_cap_storage_slot(cap_storage_slot)
        timeout = self.process_timeout if timeout is None else timeout
        process_id = int(process_type)
        is_open = process_type in OPEN_PROCESS_IDS
        task_label = "开瓶盖" if is_open else "关瓶盖"
        normalized_sample_id = _normalize_sample_id(sample_id)

        logger.info(
            f"S08 {task_label}: process={process_id}, cap_storage_slot={cap_storage_slot}, "
            f"sample_id={normalized_sample_id[:8]}..."
        )

        if self.require_station_ready:
            if not self._wait_plc_bool(NODE_HOME, True, timeout=timeout, description="S08 原点信号（机械臂安全位）"):
                return {"success": False, "message": "机械臂未回到 S08 安全位（S08原点信号为 False）"}

        if not self._wait_plc_bool(
            NODE_ALLOW_PROCESS,
            True,
            timeout=timeout,
            description="S08 允许加工",
        ):
            return {"success": False, "message": "等待 S08 允许加工超时"}

        if int(self._read_plc_variable(NODE_PROCESS_COMPLETE) or 0) != 0:
            if not self._wait_process_complete(
                0,
                timeout=min(10.0, timeout),
                description=f"{NODE_PROCESS_COMPLETE} 清零（PLC 复位）",
            ):
                return {"success": False, "message": "上一轮 S08工艺完成 尚未由 PLC 清零"}

        try:
            if is_open:
                self._write_sample_id_to_slot_cache(cap_storage_slot, normalized_sample_id)
            self._write_plc_variable(NODE_CAP_STORAGE_SLOT, int(cap_storage_slot))
            self._write_plc_variable(NODE_PROCESS_SELECT, process_id)
            self._write_plc_variable(NODE_PARAMS_WRITTEN, True)

            if not self._wait_process_complete(process_id, timeout=timeout):
                self._reset_unilab_written_params()
                return {
                    "success": False,
                    "message": f"S08 {task_label} 等待工艺完成超时（期望 {NODE_PROCESS_COMPLETE}={process_id}）",
                }

            self._reset_unilab_written_params()
            if clear_cache_on_complete:
                self._clear_slot_cache(cap_storage_slot)
            status = self._read_s08_status()
            return {
                "success": True,
                "message": f"S08 {task_label} 完成",
                "process_type": process_id,
                "cap_storage_slot": cap_storage_slot,
                "sample_id": normalized_sample_id,
                "status": status,
            }
        except Exception as exc:
            logger.exception(f"S08 {task_label} 失败: {exc}")
            try:
                self._reset_unilab_written_params()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    @not_action
    def _open_cap(
        self,
        process_type: S08ProcessType,
        sample_id: Sequence[int] | None = None,
        cap_storage_slot: Optional[int] = None,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        try:
            resolved_sample_id = self._resolve_sample_id_for_open(sample_id)
            slot = self._resolve_cap_storage_slot_for_open(resolved_sample_id, cap_storage_slot)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        return self._run_cap_process(
            process_type=process_type,
            cap_storage_slot=slot,
            sample_id=resolved_sample_id,
            timeout=timeout,
        )

    @not_action
    def _close_cap(
        self,
        process_type: S08ProcessType,
        sample_id: Sequence[int],
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        try:
            normalized = _normalize_sample_id(sample_id)
            slot = self._resolve_cap_storage_slot_for_close(normalized)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        return self._run_cap_process(
            process_type=process_type,
            cap_storage_slot=slot,
            sample_id=normalized,
            timeout=timeout,
            clear_cache_on_complete=True,
        )

    @action(auto_prefix=True, always_free=True, description="等待机械臂回到 S08 安全位（S08原点信号）")
    def wait_station_ready(self, timeout: float = 300.0) -> dict[str, Any]:
        if self._wait_plc_bool(NODE_HOME, True, timeout=timeout, description="S08 原点信号（机械臂安全位）"):
            return {"success": True, "message": "机械臂已在 S08 安全位"}
        return {"success": False, "message": "等待机械臂回到 S08 安全位超时"}

    @not_action
    def _read_cap_slot_occupancy(self) -> dict[int, bool]:
        return {
            slot: bool(self._read_plc_variable(node_name))
            for slot, node_name in CAP_STORAGE_SLOT_SENSORS.items()
        }

    @action(auto_prefix=True, always_free=True, description="读取 S082 瓶盖暂存位 1-5 占用传感器")
    def read_cap_slot_occupancy(self) -> dict[str, Any]:
        try:
            occupancy = self._read_cap_slot_occupancy()
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        free_slots = [slot for slot, occupied in occupancy.items() if not occupied]
        return {
            "success": True,
            "occupancy": occupancy,
            "free_slots": free_slots,
        }

    @action(auto_prefix=True, always_free=True, description="读取 S082 暂存位 1-5 绑定的样品 ID（数据缓存）")
    def read_cap_storage_registry(self) -> dict[str, Any]:
        try:
            registry = self._read_cap_storage_registry()
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        free_slots = [slot for slot, sample in registry.items() if _sample_id_is_empty(sample)]
        return {
            "success": True,
            "registry": registry,
            "free_slots": free_slots,
        }

    @action(auto_prefix=True, always_free=True, description="等待 S08 允许加工")
    def wait_allow_process(self, timeout: float = 300.0) -> dict[str, Any]:
        if self._wait_plc_bool(NODE_ALLOW_PROCESS, True, timeout=timeout, description="S08 允许加工"):
            return {"success": True, "message": "S08 已允许加工"}
        return {"success": False, "message": "等待 S08 允许加工超时"}

    @action(auto_prefix=True, always_free=True, description="读取 S08 工位 PLC 状态")
    def read_s08_status(self) -> dict[str, Any]:
        try:
            status = self._read_s08_status()
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        return {"success": True, "status": status}

    @action(auto_prefix=True, always_free=True, description="复位 UniLab 写入的 S08 工艺参数（调试用）")
    def reset_s08_flags(self) -> dict[str, Any]:
        try:
            self._reset_unilab_written_params()
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        return {"success": True, "message": "S08 UniLab 侧工艺参数已复位"}

    @action(auto_prefix=True, description="固定工位1 样品瓶500ml 开盖；sample_id 绑定瓶盖暂存位")
    def open_sample_vial_500ml_cap(
        self,
        sample_id: list[int],
        cap_storage_slot: Optional[int] = None,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        return self._open_cap(
            process_type=S08ProcessType.OPEN_SAMPLE_VIAL_500ML,
            sample_id=sample_id,
            cap_storage_slot=cap_storage_slot,
            timeout=timeout,
        )

    @action(auto_prefix=True, description="固定工位1 样品瓶500ml 关盖；按样品 ID 查找瓶盖暂存位")
    def close_sample_vial_500ml_cap(self, sample_id: list[int], timeout: float = 300.0) -> dict[str, Any]:
        return self._close_cap(
            process_type=S08ProcessType.CLOSE_SAMPLE_VIAL_500ML,
            sample_id=sample_id,
            timeout=timeout,
        )

    @action(auto_prefix=True, description="固定工位1 样品瓶250ml 开盖；sample_id 绑定瓶盖暂存位")
    def open_sample_vial_250ml_cap(
        self,
        sample_id: list[int],
        cap_storage_slot: Optional[int] = None,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        return self._open_cap(
            process_type=S08ProcessType.OPEN_SAMPLE_VIAL_250ML,
            sample_id=sample_id,
            cap_storage_slot=cap_storage_slot,
            timeout=timeout,
        )

    @action(auto_prefix=True, description="固定工位1 样品瓶250ml 关盖；按样品 ID 查找瓶盖暂存位")
    def close_sample_vial_250ml_cap(self, sample_id: list[int], timeout: float = 300.0) -> dict[str, Any]:
        return self._close_cap(
            process_type=S08ProcessType.CLOSE_SAMPLE_VIAL_250ML,
            sample_id=sample_id,
            timeout=timeout,
        )

    @action(auto_prefix=True, description="固定工位2 液体瓶100ml 开盖；sample_id 绑定瓶盖暂存位")
    def open_liquid_vial_100ml_cap(
        self,
        sample_id: list[int],
        cap_storage_slot: Optional[int] = None,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        return self._open_cap(
            process_type=S08ProcessType.OPEN_LIQUID_VIAL_100ML,
            sample_id=sample_id,
            cap_storage_slot=cap_storage_slot,
            timeout=timeout,
        )

    @action(auto_prefix=True, description="固定工位2 液体瓶100ml 关盖；按样品 ID 查找瓶盖暂存位")
    def close_liquid_vial_100ml_cap(self, sample_id: list[int], timeout: float = 300.0) -> dict[str, Any]:
        return self._close_cap(
            process_type=S08ProcessType.CLOSE_LIQUID_VIAL_100ML,
            sample_id=sample_id,
            timeout=timeout,
        )

    @topic_config(period=2.0)
    def last_s08_status(self) -> dict[str, Any]:
        return dict(self._last_status)
