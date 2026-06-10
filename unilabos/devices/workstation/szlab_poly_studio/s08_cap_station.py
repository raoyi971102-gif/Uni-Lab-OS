"""
S08 开盖/关盖工位子设备。

通过 szlab_poly_plc 转发 OPC UA 读写，实现双单元（S081_1 / S081_2）开瓶盖与关瓶盖握手。
"""

from __future__ import annotations

import json
import threading
import time
from enum import IntEnum
from typing import Any, Optional

from rclpy.action import ActionClient
from unilabos_msgs.action import StrSingleInput

from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.resources.resource_tracker import JSON_UNILABOS_PARAM, PARAM_SAMPLE_UUIDS
from unilabos.utils.log import logger


NODE_HOME = "S08原点信号"
NODE_CAP_STORAGE_SLOT = "S082瓶盖暂存位"

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


class CapProcessTask(IntEnum):
    OPEN = 1
    CLOSE = 2


UNIT_VARIABLES: dict[int, dict[str, str]] = {
    1: {
        "allow_process": "S081_1允许加工",
        "process_task": "S081_1工艺任务",
        "params_written": "S081_1参数写入完成",
        "process_complete": "S081_1加工完成",
    },
    2: {
        "allow_process": "S081_2允许加工",
        "process_task": "S081_2工艺任务",
        "params_written": "S081_2参数写入完成",
        "process_complete": "S081_2加工完成",
    },
}


def _validate_unit_id(unit_id: int) -> None:
    if unit_id not in UNIT_VARIABLES:
        raise ValueError(f"unit_id 必须为 1 或 2，收到: {unit_id}")


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
        self._last_unit_status: dict[str, Any] = {}

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
    def _unit_nodes(self, unit_id: int) -> dict[str, str]:
        _validate_unit_id(unit_id)
        return UNIT_VARIABLES[unit_id]

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
    def _wait_rising_edge(self, node_name: str, timeout: Optional[float] = None) -> bool:
        timeout = self.process_timeout if timeout is None else timeout
        start = time.time()
        if bool(self._read_plc_variable(node_name)):
            logger.warning(f"{node_name} 仍为 True，先等待复位为 False")
            if not self._wait_plc_bool(node_name, False, timeout=timeout, description=f"{node_name} 复位"):
                return False
            remaining = max(timeout - (time.time() - start), 0.0)
        else:
            remaining = timeout
        return self._wait_plc_bool(
            node_name,
            True,
            timeout=remaining,
            description=f"{node_name} 置位",
        )

    @not_action
    def _read_unit_status(self, unit_id: int) -> dict[str, Any]:
        nodes = self._unit_nodes(unit_id)
        status = {
            "unit_id": unit_id,
            "station_ready": bool(self._read_plc_variable(NODE_HOME)),
            "allow_process": bool(self._read_plc_variable(nodes["allow_process"])),
            "process_task": int(self._read_plc_variable(nodes["process_task"]) or 0),
            "params_written": bool(self._read_plc_variable(nodes["params_written"])),
            "process_complete": bool(self._read_plc_variable(nodes["process_complete"])),
            "cap_storage_slot": int(self._read_plc_variable(NODE_CAP_STORAGE_SLOT) or 0),
        }
        self._last_unit_status = status
        return status

    @not_action
    def _reset_unit_flags(self, unit_id: int) -> None:
        nodes = self._unit_nodes(unit_id)
        self._write_plc_variable(nodes["params_written"], False)

    @not_action
    def _run_cap_process(
        self,
        unit_id: int,
        process_task: CapProcessTask,
        cap_storage_slot: int,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        _validate_unit_id(unit_id)
        _validate_cap_storage_slot(cap_storage_slot)
        timeout = self.process_timeout if timeout is None else timeout
        nodes = self._unit_nodes(unit_id)
        task_label = "开瓶盖" if process_task == CapProcessTask.OPEN else "关瓶盖"

        logger.info(f"S08 {task_label}: unit_id={unit_id}, cap_storage_slot={cap_storage_slot}")

        if self.require_station_ready:
            if not self._wait_plc_bool(NODE_HOME, True, timeout=timeout, description="S08 原点信号"):
                return {"success": False, "message": "S08 工位未就绪（原点信号为 False）"}

        if not self._wait_plc_bool(
            nodes["allow_process"],
            True,
            timeout=timeout,
            description=f"单元{unit_id} 允许加工",
        ):
            return {"success": False, "message": f"S08 单元{unit_id} 等待允许加工超时"}

        try:
            self._write_plc_variable(nodes["process_task"], int(process_task))
            self._write_plc_variable(NODE_CAP_STORAGE_SLOT, int(cap_storage_slot))
            self._write_plc_variable(nodes["params_written"], True)

            if not self._wait_rising_edge(nodes["process_complete"], timeout=timeout):
                self._reset_unit_flags(unit_id)
                return {
                    "success": False,
                    "message": f"S08 单元{unit_id} {task_label} 等待加工完成超时",
                }

            self._reset_unit_flags(unit_id)
            status = self._read_unit_status(unit_id)
            return {
                "success": True,
                "message": f"S08 单元{unit_id} {task_label} 完成",
                "unit_id": unit_id,
                "process_task": int(process_task),
                "cap_storage_slot": cap_storage_slot,
                "status": status,
            }
        except Exception as exc:
            logger.exception(f"S08 单元{unit_id} {task_label} 失败: {exc}")
            try:
                self._reset_unit_flags(unit_id)
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    @action(auto_prefix=True, always_free=True, description="等待 S08 工位原点信号就绪")
    def wait_station_ready(self, timeout: float = 300.0) -> dict[str, Any]:
        if self._wait_plc_bool(NODE_HOME, True, timeout=timeout, description="S08 原点信号"):
            return {"success": True, "message": "S08 工位已就绪"}
        return {"success": False, "message": "等待 S08 工位就绪超时"}

    @action(auto_prefix=True, always_free=True, description="等待指定单元允许加工")
    def wait_allow_process(self, unit_id: int = 1, timeout: float = 300.0) -> dict[str, Any]:
        try:
            nodes = self._unit_nodes(unit_id)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

        if self._wait_plc_bool(nodes["allow_process"], True, timeout=timeout, description=f"单元{unit_id} 允许加工"):
            return {"success": True, "message": f"S08 单元{unit_id} 已允许加工", "unit_id": unit_id}
        return {"success": False, "message": f"等待 S08 单元{unit_id} 允许加工超时"}

    @action(auto_prefix=True, always_free=True, description="读取 S08 指定单元 PLC 状态")
    def read_unit_status(self, unit_id: int = 1) -> dict[str, Any]:
        try:
            status = self._read_unit_status(unit_id)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        return {"success": True, "status": status}

    @action(auto_prefix=True, description="S08 开瓶盖")
    def open_cap(self, unit_id: int = 1, cap_storage_slot: int = 1, timeout: float = 300.0) -> dict[str, Any]:
        return self._run_cap_process(
            unit_id=unit_id,
            process_task=CapProcessTask.OPEN,
            cap_storage_slot=cap_storage_slot,
            timeout=timeout,
        )

    @action(auto_prefix=True, description="S08 关瓶盖")
    def close_cap(self, unit_id: int = 1, cap_storage_slot: int = 1, timeout: float = 300.0) -> dict[str, Any]:
        return self._run_cap_process(
            unit_id=unit_id,
            process_task=CapProcessTask.CLOSE,
            cap_storage_slot=cap_storage_slot,
            timeout=timeout,
        )

    @action(auto_prefix=True, always_free=True, description="复位 S08 单元参数写入完成标志")
    def reset_unit_flags(self, unit_id: int = 1) -> dict[str, Any]:
        try:
            self._reset_unit_flags(unit_id)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        return {"success": True, "message": f"S08 单元{unit_id} 标志已复位", "unit_id": unit_id}

    @topic_config(period=2.0)
    def last_unit_status(self) -> dict[str, Any]:
        return dict(self._last_unit_status)
