"""
固体加样 设备驱动

协议：OPC_UA协议1.3.4(1).xlsx「固体加样」（前缀 Solid_）。

对外仅暴露 execute_command（Solid_CmdType + 写参）；测试流程 yaml 预设供本地调试。
"""

import os
import time
import logging
import threading
import traceback
from enum import Enum
from typing import Optional

import pandas as pd

from unilabos.device_comms.opcua_client.node.uniopcua import DataType, NodeType
from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action

# 导入通讯基类
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription, OpcUaNode

DEFAULT_XLSX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "OPC_UA协议1.3.4(1).xlsx",
)

# OPC UA 1.3.4 Solid_CmdType
SOLID_CMD_LABELS = {
    1: "X向左",
    2: "X向右",
    3: "Y向里",
    4: "Y向外",
    5: "夹爪Z向上",
    6: "夹爪Z向下",
    7: "D开门",
    8: "D关门",
    9: "取料筒时Y轴向里",
    10: "取料筒时Y轴向外",
    11: "加料",
    12: "夹爪夹料",
    13: "夹爪放料",
    14: "天枰去皮",
    15: "天枰称重",
    16: "料筒Z向上",
    17: "料筒Z向下",
    18: "放料筒时Y轴向里",
    19: "放料筒时Y轴向外",
    20: "复位",
    21: "夹爪夹紧",
    22: "夹爪松开",
    23: "xyz回原点",
}


class SolidCommand(int, Enum):
    """固体加样指令类型 (Solid_CmdType)"""

    X_LEFT = 1
    X_RIGHT = 2
    Y_IN = 3
    Y_OUT = 4
    GRIPPER_Z_UP = 5
    GRIPPER_Z_DOWN = 6
    DOOR_OPEN = 7
    DOOR_CLOSE = 8
    TAKE_CYLINDER_Y_IN = 9
    TAKE_CYLINDER_Y_OUT = 10
    DISPENSE = 11
    GRIPPER_PICK = 12
    GRIPPER_PLACE = 13
    BALANCE_TARE = 14
    BALANCE_WEIGH = 15
    CYLINDER_Z_UP = 16
    CYLINDER_Z_DOWN = 17
    PLACE_CYLINDER_Y_IN = 18
    PLACE_CYLINDER_Y_OUT = 19
    RESET = 20
    GRIPPER_CLAMP = 21
    GRIPPER_RELEASE = 22
    HOME_XYZ = 23


# 固体加样测试流程 yaml 预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS = [
    ("1.夹爪夹取（料架）", SolidCommand.GRIPPER_PICK, dict(
        x_pos=20, y_pos=2100, material_z_pos=0, gripper_z_pos=2200,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("2.夹爪放置（加样工位）", SolidCommand.GRIPPER_PLACE, dict(
        x_pos=-1730, y_pos=1540, material_z_pos=0, gripper_z_pos=1130,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("3.取料筒后向外", SolidCommand.TAKE_CYLINDER_Y_OUT, dict(
        x_pos=-1900, y_pos=2220, material_z_pos=235000, gripper_z_pos=0,
        door_pos=0, volune_weight=0,
        x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=0,
    )),
    ("4.料筒加样", SolidCommand.DISPENSE, dict(
        x_pos=-300, y_pos=700, material_z_pos=40000, gripper_z_pos=0,
        door_pos=3700, volune_weight=30,
        x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=150,
        timeout=600.0,
    )),
    ("5.放料筒向里", SolidCommand.PLACE_CYLINDER_Y_IN, dict(
        x_pos=-1900, y_pos=2220, material_z_pos=235000, gripper_z_pos=0,
        door_pos=0, volune_weight=0,
        x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=0,
    )),
    ("6.夹爪夹取（加样工位）", SolidCommand.GRIPPER_PICK, dict(
        x_pos=-1730, y_pos=1540, material_z_pos=0, gripper_z_pos=1130,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("7.夹爪放置（料架）", SolidCommand.GRIPPER_PLACE, dict(
        x_pos=20, y_pos=2100, material_z_pos=0, gripper_z_pos=2180,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("8.复位", SolidCommand.RESET, dict(
        x_pos=0, y_pos=0, material_z_pos=0, gripper_z_pos=0,
        door_pos=0, volune_weight=0,
        x_speed=0, y_speed=0, material_z_speed=0, gripper_z_speed=0, door_speed=0,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 Solid_CmdType 执行 OPC UA 1.3.4 指令。"
    "1=X左 2=X右 3=Y向里 4=Y向外 5=夹爪Z上 6=夹爪Z下 7=D开门 8=D关门 "
    "9=取料筒Y向里 10=取料筒Y向外 11=加料 12=夹爪夹料 13=夹爪放料 "
    "14=天枰去皮 15=天枰称重 16=料筒Z上 17=料筒Z下 18=放料筒Y向里 19=放料筒Y向外 "
    "20=复位 21=夹爪夹紧 22=夹爪松开 23=xyz回原点。"
    "可选写参：x/y/material_z/gripper_z/door 位置与速度、volune_weight。"
)


@device(
    id="gn_solid_weighing",
    display_name="固体加样",
    category=["workstation"],
    description="GN 固体加样：OPC UA 1.3.4，按完成反馈边沿执行命令",
    icon="",
    version="2.0.0",
)
class SolidWeighingDevice(OpcUaClientWithSubscription):
    """固体加样设备类（OPC 前缀 Solid_）"""

    CMD_TYPE_NODE = "Solid_CmdType"
    CMD_TRIG_NODE = "Solid_CmdTrig"
    COMPLETE_NODE = "Solid_CompleteFB"
    RESET_POSITION_NODES = {
        "Solid_XPosSet": "Solid_XPosFB",
        "Solid_YPosSet": "Solid_YPosFB",
        "Solid_MaterialZPosSet": "Solid_MaterialZPosFB",
        "Solid_GripperZPosSet": "Solid_GripperZPosFB",
        "Solid_DoorPosSet": "Solid_DoorPosFB",
    }
    # 仅等 CompleteFB 的命令（11/14/15）
    _COMPLETE_FB_ONLY_CMDS = frozenset({
        int(SolidCommand.DISPENSE),
        int(SolidCommand.BALANCE_TARE),
        int(SolidCommand.BALANCE_WEIGH),
    })
    _OPC_WRITE_RETRIES = 2

    def __init__(
        self,
        url: str,
        xlsx_path: str = DEFAULT_XLSX_PATH,
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
        self._connection_check_interval = 5.0
        self._command_lock = threading.Lock()
        if xlsx_path:
            self._load_nodes_from_xlsx(xlsx_path)

    @not_action
    def _load_nodes_from_xlsx(self, xlsx_path: str) -> None:
        """从 OPC_UA协议1.3.4(1).xlsx 加载 Solid_ 前缀节点。"""
        try:
            if not os.path.isabs(xlsx_path):
                xlsx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), xlsx_path)
            if not os.path.isfile(xlsx_path):
                logger.error(f"OPC UA 协议 xlsx 不存在: {xlsx_path}")
                return

            logger.info(f"开始从 xlsx 加载节点: {xlsx_path}")
            df = pd.read_excel(xlsx_path, sheet_name=0, header=None)
            nodes = []
            name_mapping = {}
            reverse_mapping = {}
            module_short = ""

            for i in range(5, len(df)):
                row = df.iloc[i]
                mod = row[0]
                if pd.notna(mod) and str(mod).strip():
                    module_short = str(mod).strip().split("\n")[0].strip()

                node_id = row[5]
                point_name = row[6]
                dtype = row[7]
                if pd.isna(node_id) or pd.isna(point_name):
                    continue

                node_id = str(node_id).strip()
                point_name = str(point_name).strip()
                english_name = node_id.rsplit(".", 1)[-1]
                if not english_name.startswith("Solid_"):
                    continue

                chinese_name = f"{module_short}{point_name}"
                dtype_str = str(dtype).strip().upper() if pd.notna(dtype) else "INT16"
                if dtype_str not in ("INT16", "INT32", "DOUBLE"):
                    dtype_str = "INT16"
                try:
                    data_type = DataType[dtype_str]
                except KeyError:
                    data_type = DataType.INT16

                nodes.append(
                    OpcUaNode(
                        name=chinese_name,
                        node_type=NodeType.VARIABLE,
                        node_id=node_id,
                        data_type=data_type,
                    )
                )
                name_mapping[english_name] = chinese_name
                reverse_mapping[chinese_name] = english_name

            if not nodes:
                logger.error("xlsx 中未解析到任何 Solid_ 节点")
                return

            self._name_mapping.update(name_mapping)
            self._reverse_mapping.update(reverse_mapping)
            self.register_node_list(nodes)

            if self.client and self._variables_to_find:
                logger.info(f"xlsx 解析完成，待查找 {len(self._variables_to_find)} 个节点...")
                self._find_nodes()
            self._register_nodes_as_attributes()

            found_count = len(self._node_registry)
            total_count = len(self._variables_to_find)
            if found_count < total_count:
                logger.warning(f"节点查找完成：找到 {found_count}/{total_count} 个节点")
            else:
                logger.info(f"✓ 节点查找完成：所有 {found_count} 个节点均已找到")

            if self._use_subscription and found_count > 0:
                self._setup_subscriptions()
            logger.info(f"✓ 成功从 xlsx 加载 {found_count} 个节点")
        except Exception as exc:
            logger.error(f"从 xlsx 加载节点失败 {xlsx_path}: {exc}")
            traceback.print_exc()

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        material_z_pos: Optional[int] = None,
        gripper_z_pos: Optional[int] = None,
        door_pos: Optional[int] = None,
        volune_weight: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        material_z_speed: Optional[int] = None,
        gripper_z_speed: Optional[int] = None,
        door_speed: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        setpoints = self._build_setpoints(
            x_pos=x_pos, y_pos=y_pos,
            material_z_pos=material_z_pos, gripper_z_pos=gripper_z_pos,
            door_pos=door_pos, volune_weight=volune_weight,
            x_speed=x_speed, y_speed=y_speed,
            material_z_speed=material_z_speed, gripper_z_speed=gripper_z_speed,
            door_speed=door_speed,
        )
        label = SOLID_CMD_LABELS.get(int(cmd_type), f"CmdType={int(cmd_type)}")
        return self._run(int(cmd_type), label, setpoints, timeout=timeout)

    @action(description="粉末定量加样 (cmd 11)，默认坐标为加样工位")
    def dispense_powder(
        self,
        weight_mg: int = 30,
        x_pos: int = -300,
        y_pos: int = 700,
        material_z_pos: int = 40000,
        gripper_z_pos: int = 0,
        door_pos: int = 3700,
        x_speed: int = 500,
        y_speed: int = 500,
        door_speed: int = 150,
        timeout: float = 600.0,
    ) -> dict:
        return self.execute_command(
            cmd_type=int(SolidCommand.DISPENSE),
            x_pos=x_pos,
            y_pos=y_pos,
            material_z_pos=material_z_pos,
            gripper_z_pos=gripper_z_pos,
            door_pos=door_pos,
            volune_weight=weight_mg,
            x_speed=x_speed,
            y_speed=y_speed,
            door_speed=door_speed,
            timeout=timeout,
        )

    @not_action
    def _reconnect_opcua(self) -> bool:
        """写/读失败时主动重连并恢复订阅。"""
        try:
            with self._client_lock:
                if not self.client:
                    return False
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client.connect()
                logger.info("固体加样 OPC UA 主动重连成功")
                if self._use_subscription:
                    self._setup_subscriptions()
                return True
        except Exception as exc:
            logger.error(f"固体加样 OPC UA 主动重连失败: {exc}")
            return False

    @not_action
    def _opc_write(self, name: str, value, retries: Optional[int] = None) -> bool:
        attempts = (self._OPC_WRITE_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            if self.set_node_value(name, value):
                return True
            if attempt + 1 < attempts:
                logger.warning(
                    f"写入 {name}={value} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect_opcua()
                time.sleep(0.3)
        return False

    @not_action
    def _opc_read(self, name: str, force_read: bool = False, retries: Optional[int] = None):
        attempts = (self._OPC_WRITE_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            value = self.get_node_value(name, force_read=force_read)
            if value is not None:
                return value
            if attempt + 1 < attempts:
                logger.warning(
                    f"读取 {name} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect_opcua()
                time.sleep(0.3)
        return None

    @not_action
    def _build_setpoints(
        self,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        material_z_pos: Optional[int] = None,
        gripper_z_pos: Optional[int] = None,
        door_pos: Optional[int] = None,
        volune_weight: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        material_z_speed: Optional[int] = None,
        gripper_z_speed: Optional[int] = None,
        door_speed: Optional[int] = None,
    ) -> dict:
        mapping = {
            "Solid_XPosSet": x_pos,
            "Solid_YPosSet": y_pos,
            "Solid_MaterialZPosSet": material_z_pos,
            "Solid_GripperZPosSet": gripper_z_pos,
            "Solid_DoorPosSet": door_pos,
            "Solid_VoluneWeightSet": volune_weight,
            "Solid_XSpeed": x_speed,
            "Solid_YSpeed": y_speed,
            "Solid_MaterialZSpeed": material_z_speed,
            "Solid_GripperZSpeed": gripper_z_speed,
            "Solid_DoorSpeed": door_speed,
        }
        return {node: val for node, val in mapping.items() if val is not None}

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 180.0,
    ) -> dict:
        with self._command_lock:
            logger.info(f"固体加样：{description} (CmdType={cmd_type})")
            if setpoints:
                for node, value in setpoints.items():
                    if not self._opc_write(node, value):
                        raise ValueError(f"写入 {node}={value} 失败")
            result = self._trigger_and_wait(
                cmd_type,
                description,
                setpoints=setpoints,
                timeout=timeout,
            )
            if cmd_type == int(SolidCommand.BALANCE_WEIGH):
                weight = self._opc_read("Solid_WeightFB", force_read=True)
                result["weight"] = weight
                result["message"] = f"称重完成，重量={weight}"
            return result

    @not_action
    def _trigger_and_wait(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 180.0,
    ) -> dict:
        """下发 CmdType → CmdTrig=1，等待 CompleteFB=1 后清理（同 centrifuge_tube，不要求下发前 idle）。"""
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if not self._opc_write(self.CMD_TYPE_NODE, int(cmd_type)):
            raise ValueError(f"Solid_CmdType={cmd_type} 写入失败")
        if not self._opc_write(self.CMD_TRIG_NODE, 1):
            raise ValueError("Solid_CmdTrig=1 写入失败")

        completed = False
        try:
            if int(cmd_type) in self._COMPLETE_FB_ONLY_CMDS:
                completed = self._wait_complete_value(
                    expected=1,
                    timeout=timeout,
                    description=f"{description}完成",
                )
            else:
                completed = self._wait_motion_complete(
                    setpoints=setpoints or {},
                    timeout=timeout,
                    description=f"{description}完成",
                )
            if not completed:
                raise ValueError(f"{description}失败，Solid_CompleteFB 未变为 1")
        finally:
            trigger_cleared = self._opc_write(self.CMD_TRIG_NODE, 0)
            command_cleared = self._opc_write(self.CMD_TYPE_NODE, 0)
            trigger_value = self._opc_read(self.CMD_TRIG_NODE, force_read=True)
            command_value = self._opc_read(self.CMD_TYPE_NODE, force_read=True)
            logger.info(
                f"固体加样命令清理：CmdTrig={trigger_value!r}，CmdType={command_value!r}"
            )
            if completed and (
                not trigger_cleared
                or not command_cleared
                or trigger_value != 0
                or command_value != 0
            ):
                raise ValueError(
                    "动作已完成，但命令清零失败："
                    f"Solid_CmdTrig={trigger_value!r}, Solid_CmdType={command_value!r}"
                )

        logger.info(f"{description}完成")
        self._log_status(f"{description}后")
        return {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": int(cmd_type),
        }

    @not_action
    def _wait_reset_positions(
        self,
        setpoints: dict,
        timeout: float,
        tolerance: int = 5,
        interval: float = 0.1,
        stable_samples: int = 3,
    ) -> bool:
        targets = {
            feedback_node: int(setpoints.get(setpoint_node, 0))
            for setpoint_node, feedback_node in self.RESET_POSITION_NODES.items()
        }
        logger.info(f"等待复位到位反馈：{targets}，容差={tolerance}")
        start = time.monotonic()
        stable_count = 0
        last_values = {}
        while time.monotonic() - start < timeout:
            last_values = {
                node: self.get_node_value(node, force_read=True)
                for node in targets
            }
            all_reached = all(
                value is not None and abs(int(value) - target) <= tolerance
                for node, target in targets.items()
                for value in (last_values[node],)
            )
            stable_count = stable_count + 1 if all_reached else 0
            if stable_count >= stable_samples:
                logger.info(f"✓ 复位到位，位置反馈={last_values}")
                return True
            time.sleep(interval)
        logger.error(f"✗ 等待复位到位超时，目标={targets}，当前={last_values}")
        return False

    @not_action
    def _position_targets_from_setpoints(self, setpoints: dict) -> dict:
        """从本次写参提取需要核对的位置反馈（MaterialZ Set=0 时跳过）。"""
        targets = {}
        for setpoint_node, feedback_node in self.RESET_POSITION_NODES.items():
            if setpoint_node not in setpoints:
                continue
            target = int(setpoints[setpoint_node])
            if setpoint_node == "Solid_MaterialZPosSet" and target == 0:
                continue
            targets[feedback_node] = target
        return targets

    @not_action
    def _positions_reached(
        self,
        position_targets: dict,
        tolerance: int = 5,
        stable_samples: int = 3,
        interval: float = 0.1,
        sample_timeout: float = 2.0,
    ) -> bool:
        if not position_targets:
            return False
        start = time.monotonic()
        stable_count = 0
        last_values = {}
        while time.monotonic() - start < sample_timeout:
            last_values = {
                node: self._opc_read(node, force_read=True)
                for node in position_targets
            }
            all_reached = all(
                value is not None and abs(int(value) - target) <= tolerance
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

    @not_action
    def _wait_motion_complete(
        self,
        setpoints: dict,
        timeout: float,
        description: str = "",
    ) -> bool:
        """运动类命令：优先等 CompleteFB=1，超时后再用位置反馈兜底。"""
        position_targets = self._position_targets_from_setpoints(setpoints)
        logger.info(
            f"等待 {description}（{self.COMPLETE_NODE}=1"
            + (f"，超时后位置兜底 {position_targets}" if position_targets else "")
            + "）..."
        )
        if self._wait_complete_value(
            expected=1,
            timeout=timeout,
            description=description,
        ):
            return True
        if position_targets and self._positions_reached(position_targets):
            logger.warning(
                f"{description}：{self.COMPLETE_NODE} 未回 1，但位置已到位，作超时兜底"
            )
            return True
        complete = self._opc_read(self.COMPLETE_NODE, force_read=True)
        logger.error(
            f"✗ 等待 {description} 超时（{timeout}s，{self.COMPLETE_NODE}={complete!r}）"
        )
        return False

    @not_action
    def _wait_complete_value(
        self,
        expected: int,
        timeout: float,
        interval: float = 0.05,
        description: str = "",
    ) -> bool:
        logger.info(
            f"等待 {description}（{self.COMPLETE_NODE}={expected}）..."
        )
        start = time.monotonic()
        read_fail_streak = 0
        while time.monotonic() - start < timeout:
            value = self._opc_read(self.COMPLETE_NODE, force_read=True)
            if value is None:
                read_fail_streak += 1
                if read_fail_streak >= 3:
                    logger.error(
                        f"✗ {description}中止：{self.COMPLETE_NODE} 连续读取失败，"
                        "OPC 连接已断开，请退出并重启脚本"
                    )
                    return False
            else:
                read_fail_streak = 0
                if value == expected:
                    logger.info(f"✓ {description}（{self.COMPLETE_NODE}={value}）")
                    return True
            time.sleep(interval)
        value = self._opc_read(self.COMPLETE_NODE, force_read=True)
        logger.error(
            f"✗ 等待 {description} 超时（{timeout}s，"
            f"{self.COMPLETE_NODE}={value!r}，期望={expected}）"
        )
        return False

    @not_action
    def run_test_flow(self) -> dict:
        """按固体加样测试流程 yaml 预设依次 execute_command（本地调试用）"""
        logger.info("固体加样：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            self.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        logger.info("固体加样：整体测试流程完成")
        return {"success": True, "message": "固体加样测试流程完成"}

    @not_action
    def get_weight(self) -> int:
        return self.get_node_value("Solid_WeightFB", force_read=True)

    @not_action
    def get_positions(self) -> dict:
        return {
            "X": self.get_node_value("Solid_XPosFB"),
            "Y": self.get_node_value("Solid_YPosFB"),
            "MaterialZ": self.get_node_value("Solid_MaterialZPosFB"),
            "GripperZ": self.get_node_value("Solid_GripperZPosFB"),
            "Door": self.get_node_value("Solid_DoorPosFB"),
        }

    @not_action
    def get_status(self) -> dict:
        status = self.get_positions()
        status["weight"] = self.get_weight()
        status["complete"] = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: X={status['X']} Y={status['Y']} "
            f"MaterialZ={status['MaterialZ']} GripperZ={status['GripperZ']} "
            f"Door={status['Door']} 重量={status['weight']} 完成={status['complete']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    SOLID_FEED_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    dev = SolidWeighingDevice(url=SOLID_FEED_URL, xlsx_path=DEFAULT_XLSX_PATH)
    time.sleep(2)
    logger.info(f"固体加样连通性测试: {dev.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                dev._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    threading.Thread(target=_status_log_worker, daemon=True, name="SolidWeighingStatusLog").start()

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("14 天枰去皮 (CmdType=14)")
        print("15 天枰称重 (CmdType=15)")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "98":
            dev.run_test_flow()
        elif choice == "14":
            dev.execute_command(cmd_type=14)
        elif choice == "15":
            dev.execute_command(cmd_type=15)
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            dev.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    dev.disconnect()
    print("退出程序。")
