"""
机械手 设备驱动

协议：OPC_UA协议1.3.4(1).xlsx「机械手」（前缀 Robot_）。

工站夹放流程（板位 1-9，坐标见 X轴位置(1).txt）：
  阶段1：CmdType 1/2 时 Robot_XPosSet=|目标-当前|（相对位移），等 XPosFB 到达绝对目标
  阶段2：Robot_XPosSet=绝对坐标 → ModuleNoSet → 工位动作 → Pick → CmdType=0 → CmdTrig=1
其余 CmdType 按 xlsx：1/2=X 点动，7/8/9/10=复位/失能/使能/动作复位。
伪指令：100=回原点 101=停止。
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
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaNode
from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_opcua_device import GnOpcUaDevice

DEFAULT_XLSX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "OPC_UA协议1.3.4(1).xlsx",
)

X_POS_TOLERANCE = 100
# xlsx 文档为 Robot_Pick up or place；PLC 实际节点为 Robot_Pick_up_or_place
PICK_PLACE_NODE = "Robot_Pick_up_or_place"
PICK_PLACE_NODE_ID = (
    "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA.Robot_Pick_up_or_place"
)
PLC_STATION_CMD_TYPE = 0

# OPC 1.3.4 Robot_CmdType + 伪指令
ROBOT_CMD_LABELS = {
    0: "工站夹放料",
    1: "X向左",
    2: "X向右",
    3: "去目标工站/板位夹料",
    4: "去目标工站/板位放料",
    7: "复位",
    8: "失能",
    9: "使能",
    10: "动作命令复位",
    100: "回原点",
    101: "停止",
}


class RobotCommand(int, Enum):
    """机械手指令类型 (Robot_CmdType)"""

    STATION_ACTION = 0
    X_LEFT = 1
    X_RIGHT = 2
    PICK = 3
    PLACE = 4
    RESET = 7
    DISABLE = 8
    ENABLE = 9
    ACTION_CMD_RESET = 10


# 伪指令（非 PLC 枚举）
CMD_GO_HOME = 100
CMD_STOP = 101

# OPC UA 1.3.4：ModuleNoSet(1-9) → 工位动作节点 / 完成反馈（名称与 xlsx 一致）
ROBOT_STATION_NODES: dict[int, tuple[str, str]] = {
    1: ("Robot_Locking_mechanism", "Robot_Locking_mechanism_Done"),
    2: ("Robot_Quick_change_mechanism ", "Robot_Quick_change_mechanism_Done"),
    3: ("Robot_Centrifuge", "Robot_Centrifuge_Done"),
    4: ("Robot_9320", "Robot_Nine_9320_Done"),
    5: ("Robot_Centrifuge_tube_liquid_handling", "Robot_Centrifuge_tube_liquid_handling_Done"),
    6: ("Robot_Stack", "Robot_Stack_Done"),
    7: ("Robot_Add_solid_sample", "Robot_Add_solid_sample_Done"),
    8: ("Robot_Oven", "Robot_Oven_Done "),
    9: ("Robot_Vacuum_oven", "Robot_Vacuum_oven_Done"),
    10: ("Robot_Finished_Product_Area", "Robot_Finished_Product_Area_Done"),
}

# X 轴工站坐标：unilabos/devices/workstation/GN/X轴位置(1).txt
ROBOT_DESTINATIONS: dict[str, tuple[int, int, int]] = {
    "locking": (1, -13278, 1),
    "stack_plate": (6, 3274, 1),
    "stack_bottle": (6, 2473, 1),
    "solid_feed_plate": (7, 6318, 1),
    "solid_feed_bottle": (7, 5971, 1),
    "tube_handler_bottle": (5, 874, 1),
    "tube_handler_reservoir": (5, -1326, 1),
    "prcxi": (4, -3926, 1),
    "magnetic_stirrer": (2, -10478, 1),
    "centrifuge": (3, -8582, 1),
    "oven": (8, -13278, 1),
    "vacuum_oven": (9, -6726, 1),
    # 兼容旧 destination 名
    "stack_reagent": (6, 3274, 2),
    "solid_feed": (7, 6318, 1),
    "tube_handler": (5, 874, 1),
}

# 板位 1-9 调试坐标（ModuleNo → XPosSet，来自 X轴位置(1).txt）
MODULE_BOARD_LABELS: dict[int, str] = {
    1: "锁紧结构",
    2: "快换结构",
    3: "离心机",
    4: "9320",
    5: "离心管液体处理",
    6: "堆栈(孔板)",
    7: "固体加样(孔板)",
    8: "烘箱",
    9: "真空烘箱",
}
MODULE_BOARD_X_POS: dict[int, int] = {
    1: -13278,
    2: -10478,
    3: -8582,
    4: -3926,
    5: 874,
    6: 3274,
    7: 6318,
    8: -13278,
    9: -6726,
}
DEFAULT_X_SPEED = 300
X_SET_MATCH_TOLERANCE = 5


_EXECUTE_CMD_DOC = (
    "按 Robot_CmdType 执行 OPC 1.3.4 指令。"
    "工站夹放：先 X 轴到绝对坐标（CmdType1/2 写相对位移），XPosFB 到位后再 ModuleNoSet + CmdType=0。"
    "1=X左 2=X右 7=复位 8=失能 9=使能 10=动作命令复位 100=回原点 101=停止。"
    "工站夹放需 module_no/station_action/pick_place；可选 x_pos/x_speed。"
    "兼容旧参数 stack=station_action；CmdType 3/4 会映射为 pick_place 且 PLC 仍写 0。"
)


@device(
    id="gn_robotic_arm",
    display_name="机械手",
    category=["workstation"],
    description="GN 机械手：OPC UA 1.3.4，板位号选工站后 CmdType=0 触发夹放",
    icon="",
    version="2.0.0",
)
class RoboticArmDevice(GnOpcUaDevice):
    """机械手设备类（OPC 前缀 Robot_）"""

    _OPC_WRITE_RETRIES = 2

    def __init__(
        self,
        url: Optional[str] = None,
        plc_device_id: Optional[str] = None,
        xlsx_path: str = DEFAULT_XLSX_PATH,
        username: str = None,
        password: str = None,
        use_subscription: bool = False,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        super().__init__(
            url=url,
            plc_device_id=plc_device_id,
            csv_path=None,
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
        self._pick_place_available = False
        if xlsx_path and not self.plc_device_id:
            self._load_nodes_from_xlsx(xlsx_path)
        if not self.plc_device_id:
            self._refresh_pick_place_available()

    @not_action
    def _load_nodes_from_xlsx(self, xlsx_path: str) -> None:
        """从 OPC_UA协议1.3.4(1).xlsx 加载 Robot_ 前缀节点。"""
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
                if not english_name.startswith("Robot_"):
                    continue
                # 1.3.4 xlsx 与 PLC 命名不一致：使用实际 OPC 节点
                if english_name == "Robot_Pick up or place":
                    english_name = PICK_PLACE_NODE
                    node_id = PICK_PLACE_NODE_ID

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
                logger.error("xlsx 中未解析到任何 Robot_ 节点")
                return

            self._name_mapping.update(name_mapping)
            self._reverse_mapping.update(reverse_mapping)
            self.register_node_list(nodes)

            if self.client and self._variables_to_find:
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

    @not_action
    def bind_plc_driver(self, plc_driver) -> None:
        super().bind_plc_driver(plc_driver)
        self._refresh_pick_place_available()

    @not_action
    def _node_registered(self, name: str) -> bool:
        """仅判断已在 OPC 服务器上成功注册的节点（不含待查找/未找到）。"""
        mapping = getattr(self, "_name_mapping", {})
        registry = getattr(self, "_node_registry", {})
        chinese = mapping.get(name, name)
        return name in registry or chinese in registry

    @not_action
    def _refresh_pick_place_available(self) -> None:
        self._pick_place_available = self._node_registered(PICK_PLACE_NODE)
        if not self._pick_place_available:
            logger.warning(
                f"PLC 未部署 {PICK_PLACE_NODE!r} 节点，工站夹放将无法写入夹/放方向"
            )

    @not_action
    def _reconnect_opcua(self) -> bool:
        """写/读失败时主动重连并恢复订阅。"""
        if self.plc_device_id:
            return False
        try:
            with self._client_lock:
                if not self.client:
                    return False
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client.connect()
                logger.info("机械手 OPC UA 主动重连成功")
                if self._use_subscription:
                    self._setup_subscriptions()
                return True
        except Exception as exc:
            logger.error(f"机械手 OPC UA 主动重连失败: {exc}")
            return False

    @not_action
    def _opc_write(self, name: str, value, retries: Optional[int] = None) -> bool:
        if self.plc_device_id:
            return bool(self.set_node_value(name, value))
        attempts = (self._OPC_WRITE_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            try:
                if self.set_node_value(name, value):
                    return True
            except Exception as exc:
                logger.warning(f"写入 {name}={value} 异常: {exc}")
            if attempt + 1 < attempts:
                logger.warning(
                    f"写入 {name}={value} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect_opcua()
                time.sleep(0.3)
        return False

    @not_action
    def _opc_read(self, name: str, force_read: bool = False, retries: Optional[int] = None):
        if self.plc_device_id:
            return self.get_node_value(name, force_read=force_read)
        attempts = (self._OPC_WRITE_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            try:
                value = self.get_node_value(name, force_read=force_read)
                if value is not None:
                    return value
            except Exception as exc:
                logger.warning(f"读取 {name} 异常: {exc}")
            if attempt + 1 < attempts:
                logger.warning(
                    f"读取 {name} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect_opcua()
                time.sleep(0.3)
        return None

    @not_action
    def _set_node_or_raise(self, name: str, value) -> None:
        if not self._opc_write(name, value):
            raise ValueError(f"写入 {name}={value} 失败")

    @not_action
    def _resolve_station_action(
        self,
        cmd_type: int,
        module_no: Optional[int],
        station_action: Optional[int],
        stack: Optional[int],
        pick_place: Optional[int],
    ) -> tuple[int, int, int]:
        """解析工站夹放参数；CmdType 0/3/4 均走 PLC CmdType=0。"""
        if cmd_type == int(RobotCommand.PLACE):
            expected_pick = 0
        elif cmd_type == int(RobotCommand.PICK):
            expected_pick = 1
        else:
            expected_pick = None

        eff_pick = pick_place if pick_place is not None else expected_pick
        if eff_pick not in (0, 1):
            raise ValueError("工站夹放需 pick_place=1（夹）或 0（放）")
        if pick_place is not None and expected_pick is not None and pick_place != expected_pick:
            raise ValueError(
                f"CmdType={cmd_type} 与 pick_place={pick_place} 冲突，应为 {expected_pick}"
            )

        eff_action = station_action if station_action is not None else stack
        if module_no is None or eff_action is None:
            raise ValueError("工站夹放需要 module_no 与 station_action（或 stack）")
        if module_no not in ROBOT_STATION_NODES:
            raise ValueError(
                f"未知机械手模块号 {module_no}，支持: {sorted(ROBOT_STATION_NODES)}"
            )
        return module_no, int(eff_action), int(eff_pick)

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        module_no: Optional[int] = None,
        station_action: Optional[int] = None,
        stack: Optional[int] = None,
        x_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        pick_place: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """唯一注册动作：按 CmdType 路由至对应 OPC 流程。"""
        label = ROBOT_CMD_LABELS.get(int(cmd_type), f"CmdType={int(cmd_type)}")
        return self._run(
            int(cmd_type),
            label,
            module_no=module_no,
            station_action=station_action,
            stack=stack,
            x_pos=x_pos,
            x_speed=x_speed,
            pick_place=pick_place,
            timeout=timeout,
        )

    @action(
        description="机械臂取放载体至目标工站；可用 destination 预设，"
        "或显式 module_no + x_pos（高级）",
    )
    def transfer_carrier(
        self,
        destination: Optional[str] = None,
        module_no: Optional[int] = None,
        x_pos: Optional[int] = None,
        stack: Optional[int] = None,
        x_speed: int = 300,
        pick_place: int = 1,
        timeout: float = 180.0,
    ) -> dict:
        if pick_place not in (0, 1):
            raise ValueError("pick_place 只能为 1（夹料）或 0（放料）")
        eff_module = module_no
        eff_x = x_pos
        eff_stack = stack if stack is not None else 1
        if eff_module is None or eff_x is None:
            if not destination:
                raise ValueError("需 destination 或 module_no+x_pos")
            preset = ROBOT_DESTINATIONS.get(destination)
            if preset is None:
                raise ValueError(
                    f"未知 destination={destination!r}，"
                    f"可选: {', '.join(sorted(ROBOT_DESTINATIONS))}"
                )
            eff_module, eff_x, default_stack = preset
            if stack is None:
                eff_stack = default_stack
        return self.execute_command(
            cmd_type=int(RobotCommand.STATION_ACTION),
            module_no=eff_module,
            station_action=eff_stack,
            x_pos=eff_x,
            x_speed=x_speed,
            pick_place=pick_place,
            timeout=timeout,
        )

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        module_no: Optional[int] = None,
        station_action: Optional[int] = None,
        stack: Optional[int] = None,
        x_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        pick_place: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        logger.info(f"机械手：{description} (CmdType={cmd_type})")

        if cmd_type in (int(RobotCommand.X_LEFT), int(RobotCommand.X_RIGHT)):
            if x_pos is None or x_speed is None:
                raise ValueError(f"CmdType={cmd_type} 需要 x_pos 与 x_speed")
            return self._move_x_absolute(x_pos, x_speed, timeout=timeout)

        if cmd_type in (
            int(RobotCommand.STATION_ACTION),
            int(RobotCommand.PICK),
            int(RobotCommand.PLACE),
        ):
            eff_module, eff_action, eff_pick = self._resolve_station_action(
                cmd_type, module_no, station_action, stack, pick_place
            )
            station = ROBOT_STATION_NODES[eff_module]
            return self._run_station_action(
                description=description,
                module_no=eff_module,
                station_action=eff_action,
                action_node=station[0],
                done_node=station[1],
                x_pos=x_pos,
                x_speed=x_speed,
                pick_place=eff_pick,
                timeout=timeout,
            )

        if cmd_type in (
            int(RobotCommand.RESET),
            int(RobotCommand.DISABLE),
            int(RobotCommand.ENABLE),
        ):
            return self._run_control_command(cmd_type, description, timeout=timeout)

        if cmd_type == int(RobotCommand.ACTION_CMD_RESET):
            self._reset_action_command()
            self._log_status("动作命令复位后")
            return {"success": True, "message": "动作命令复位完成", "cmd_type": cmd_type}

        if cmd_type == CMD_GO_HOME:
            return self._go_home(timeout=timeout)

        if cmd_type == CMD_STOP:
            return self._stop()

        raise ValueError(f"不支持的 CmdType={cmd_type}")

    @not_action
    def _clear_station_actions(self) -> bool:
        """清空所有工位动作并回读确认，防止动作残留导致 PLC 重复执行。"""
        success = True
        for action_node, _ in ROBOT_STATION_NODES.values():
            if self._node_registered(action_node):
                if not self._opc_write(action_node, 0):
                    logger.error(f"机械手：工位动作 {action_node} 清零写入失败")
                    success = False
        for action_node, _ in ROBOT_STATION_NODES.values():
            if not self._node_registered(action_node):
                continue
            value = self._opc_read(action_node, force_read=True)
            if value is None:
                logger.error(f"机械手：工位动作 {action_node} 清零回读失败")
                success = False
            elif value != 0:
                logger.error(f"机械手：工位动作 {action_node} 清零失败，回读值={value}")
                success = False
        return success

    @not_action
    def _run_station_action(
        self,
        description: str,
        module_no: int,
        station_action: int,
        action_node: str,
        done_node: str,
        pick_place: int,
        x_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """两阶段：先 X 轴到绝对坐标，XPosFB 到位后再下发工站 CmdType=0。"""
        if x_pos is None:
            x_pos = MODULE_BOARD_X_POS.get(module_no)
        if x_pos is None:
            raise ValueError(
                f"板位{module_no} 缺少 X 坐标，请参考 unilabos/devices/workstation/GN/X轴位置(1).txt"
            )
        speed = x_speed if x_speed is not None else DEFAULT_X_SPEED

        # 阶段1：CmdType 1/2 时 XPosSet 为相对位移，等到 XPosFB 到达绝对坐标 x_pos
        self.ensure_idle()
        logger.info(
            f"机械手阶段1：X 轴移动到绝对位置 {x_pos}（板位{module_no}，参考 X轴位置(1).txt）..."
        )
        self._ensure_x_at_absolute(x_pos, x_speed=speed, timeout=timeout)
        x_fb = self.get_x_position()
        if x_fb is None or abs(int(x_fb) - x_pos) > X_SET_MATCH_TOLERANCE:
            raise ValueError(
                f"X 未到达绝对位置，禁止工站动作：XPosFB={x_fb} 目标={x_pos}"
            )
        logger.info(
            f"机械手阶段1完成：XPosFB={x_fb} 已到达绝对位置 {x_pos}，开始工站动作"
        )

        # 阶段2：写入绝对 XPosSet，再下发 ModuleNo + 工位动作 + 夹放
        if not self._wait_until_true("Robot_FinishFB", timeout=timeout, description="机械臂复位就绪"):
            err = self._opc_read("Robot_Error_code", force_read=True)
            raise ValueError(f"机械臂未复位就绪，FinishFB 未置 1，Error_code={err}")

        self._opc_write("Robot_CmdTrig", 0)
        time.sleep(0.05)
        if not self._clear_station_actions():
            raise ValueError("执行动作前无法清空旧工位动作，已停止下发")

        self._set_node_or_raise("Robot_XPosSet", x_pos)
        self._set_node_or_raise("Robot_XSpeedSet", speed)
        self._set_node_or_raise("Robot_ModuleNoSet", module_no)
        self._set_node_or_raise(action_node, station_action)
        if self._pick_place_available:
            self._set_node_or_raise(PICK_PLACE_NODE, pick_place)
        else:
            raise ValueError(f"缺少 {PICK_PLACE_NODE} 节点，无法下发工站夹放")
        logger.info(
            f"机械手阶段2参数: ModuleNo={module_no} {action_node}={station_action} "
            f"{PICK_PLACE_NODE}={pick_place} XPosSet={x_pos}(绝对)"
        )

        actions_cleared = False
        try:
            self._set_node_or_raise("Robot_CmdType", PLC_STATION_CMD_TYPE)
            self._set_node_or_raise("Robot_CmdTrig", 1)

            self._wait_station_done_feedback(
                done_node=done_node,
                timeout=timeout,
                description=description,
            )

            self._set_node_or_raise(action_node, 0)
            action_value = self._opc_read(action_node, force_read=True)
            if action_value != 0:
                raise ValueError(
                    f"收到 {done_node}=1 后，{action_node} 清零失败，回读值={action_value}"
                )
            logger.info(f"机械手：收到 {done_node}=1，已写入 {action_node}=0")
        finally:
            actions_cleared = self._clear_station_actions()
            self._opc_write("Robot_CmdType", int(RobotCommand.ACTION_CMD_RESET))
            time.sleep(0.05)
            self._opc_write("Robot_CmdTrig", 0)
            logger.info(
                f"机械手：工位动作清零 {'成功' if actions_cleared else '失败'}，"
                f"{action_node}=0"
            )

        if not actions_cleared:
            raise ValueError("工位动作结束后清零失败，已停止后续动作")

        logger.info(f"{description}完成")
        self._log_status(f"{description}后")
        return {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": PLC_STATION_CMD_TYPE,
            "module_no": module_no,
            "station_action": station_action,
            "pick_place": pick_place,
            "done_node": done_node,
        }

    @not_action
    def _run_control_command(self, cmd_type: int, description: str, timeout: float = 180.0) -> dict:
        """执行使能/失能/复位命令；FinishFB=1 表示机械臂复位就绪。"""
        self._opc_write("Robot_CmdTrig", 0)
        time.sleep(0.05)
        self._set_node_or_raise("Robot_CmdType", int(cmd_type))
        self._set_node_or_raise("Robot_CmdTrig", 1)
        if not self._wait_until_true("Robot_FinishFB", timeout=timeout, description=f"{description}完成"):
            self._log_status(f"{description}失败")
            err = self._opc_read("Robot_Error_code", force_read=True)
            raise ValueError(f"{description}失败，FinishFB 未响应，Error_code={err}")
        logger.info(f"{description}完成")
        return {"success": True, "message": f"{description}完成", "cmd_type": int(cmd_type)}

    @not_action
    def _get_node_value_optional(self, name: str):
        if not self._node_registered(name):
            return None
        return self._opc_read(name, force_read=True)

    @not_action
    def ensure_idle(self) -> None:
        """确认机械臂已经复位就绪；FinishFB=1 时 X 轴才允许前往板位。"""
        if not self._wait_until_true("Robot_FinishFB", timeout=30.0, description="机械臂复位就绪"):
            err = self._opc_read("Robot_Error_code", force_read=True)
            raise ValueError(f"机械臂未复位就绪，FinishFB 未置 1，Error_code={err}")

    @not_action
    def _reset_action_command(self, timeout: float = 30.0) -> None:
        """清空工位动作并发送 CmdType=10，结束当前动作。"""
        self._clear_station_actions()
        self._opc_write("Robot_CmdType", int(RobotCommand.ACTION_CMD_RESET))
        self._opc_write("Robot_CmdTrig", 0)
        logger.info("机械手：工位动作已清空，CmdType=10")

    @not_action
    def _move_x_absolute(
        self,
        target_x: int,
        x_speed: int,
        tolerance: int = X_POS_TOLERANCE,
        timeout: float = 120.0,
    ) -> dict:
        """X 点动到绝对坐标：CmdType 1/2 时 Robot_XPosSet 写相对位移 |目标-当前|，等 XPosFB 到位"""
        current = self.get_x_position()
        if current is None:
            raise ValueError("无法读取 Robot_XPosFB，OPC 连接异常")
        if abs(current - target_x) <= tolerance:
            logger.info(f"机械手：X 已在绝对位置 {target_x}（XPosFB={current}）")
            return {"success": True, "message": f"X 已在绝对位置 {target_x}"}

        delta = abs(target_x - current)
        cmd_type = int(RobotCommand.X_RIGHT if current < target_x else RobotCommand.X_LEFT)
        direction = "向右" if current < target_x else "向左"
        desc = f"X{direction}移动到绝对位置{target_x}"
        logger.info(
            f"机械手：{desc}（XPosFB={current}，相对位移={delta}，非写绝对值{target_x}）..."
        )

        self.ensure_idle()
        self._opc_write("Robot_CmdTrig", 0)
        time.sleep(0.05)
        self._set_node_or_raise("Robot_XPosSet", delta)
        self._set_node_or_raise("Robot_XSpeedSet", x_speed)
        self._set_node_or_raise("Robot_CmdType", int(cmd_type))
        self._set_node_or_raise("Robot_CmdTrig", 1)

        if not self._wait_x_reach(
            target_x, tolerance=tolerance, description=desc, timeout=timeout
        ):
            self._log_status(f"{desc}失败")
            x_fb = self.get_x_position()
            raise ValueError(
                f"{desc}失败，XPosFB={x_fb} 目标绝对位置={target_x}"
            )

        self._reset_action_command()
        self._log_status(f"{desc}后")
        return {"success": True, "message": f"{desc}完成", "cmd_type": int(cmd_type)}

    @not_action
    def _ensure_x_at_absolute(
        self,
        target_x: int,
        x_speed: int = DEFAULT_X_SPEED,
        timeout: float = 120.0,
    ) -> None:
        """阶段1：X 轴移动到 X轴位置(1).txt 绝对坐标，以 XPosFB 到位为准。"""
        current = self.get_x_position()
        if current is not None and abs(current - target_x) <= X_SET_MATCH_TOLERANCE:
            logger.info(f"机械手：X 已在绝对位置 {target_x}（XPosFB={current}）")
            return

        self._move_x_absolute(
            target_x,
            x_speed,
            tolerance=X_SET_MATCH_TOLERANCE,
            timeout=timeout,
        )

    @not_action
    def _go_home(self, timeout: float = 180.0) -> dict:
        """回原点：Robot_gohome=1，等待 Robot_gohome_done=1"""
        logger.info("机械手：回原点...")
        self.ensure_idle()
        self._set_node_or_raise("Robot_gohome", 1)
        if not self._wait_until_true("Robot_gohome_done", timeout=timeout, description="回原点完成"):
            err = self._opc_read("Robot_Error_code", force_read=True)
            raise ValueError(f"回原点失败，Error_code={err}")
        self._opc_write("Robot_gohome", 0)
        self._log_status("回原点后")
        return {"success": True, "message": "回原点完成", "cmd_type": CMD_GO_HOME}

    @not_action
    def _stop(self) -> dict:
        """紧急停止：Robot_STOP=1 脉冲"""
        logger.info("机械手：停止...")
        self._opc_write("Robot_STOP", 1)
        time.sleep(0.2)
        self._opc_write("Robot_STOP", 0)
        self._log_status("停止后")
        return {"success": True, "message": "停止命令已下发", "cmd_type": CMD_STOP}

    @not_action
    def _wait_x_reach(
        self,
        target_x: int,
        tolerance: int = X_POS_TOLERANCE,
        stable_samples: int = 3,
        timeout: float = 120.0,
        interval: float = 0.2,
        description: str = "",
    ) -> bool:
        desc = description or f"X到达{target_x}"
        logger.info(f"等待 {desc}（容差±{tolerance}，轮询 Robot_XPosFB）...")
        start = time.time()
        stable = 0
        read_fail_streak = 0
        while time.time() - start < timeout:
            x = self.get_x_position()
            if x is None:
                read_fail_streak += 1
                if read_fail_streak >= 3:
                    logger.error(f"✗ {desc}中止：Robot_XPosFB 连续读取失败")
                    return False
            else:
                read_fail_streak = 0
                if abs(x - target_x) <= tolerance:
                    stable += 1
                    if stable >= stable_samples:
                        logger.info(f"✓ {desc}（Robot_XPosFB={x}）")
                        return True
                else:
                    stable = 0
            time.sleep(interval)
        logger.error(f"✗ {desc} 超时，当前 X={self.get_x_position()}，目标={target_x}")
        return False

    @not_action
    def _wait_station_done_feedback(
        self,
        done_node: str,
        timeout: float,
        description: str,
    ) -> None:
        """工位 Done 反馈：空闲多为 1 时先等 1→0（启动），再等 0→1（完成）。"""
        start = time.monotonic()
        initial = self._opc_read(done_node, force_read=True)
        if initial == 1:
            start_timeout = min(10.0, timeout)
            if not self._wait_until_false(
                done_node,
                timeout=start_timeout,
                interval=0.05,
                description=f"{description}启动（{done_node} 1→0）",
            ):
                self._log_status(f"{description}失败")
                err = self._opc_read("Robot_Error_code", force_read=True)
                raise ValueError(
                    f"{description}未启动，{done_node} 未变为 0，Error_code={err}"
                )

        remaining = max(0.1, timeout - (time.monotonic() - start))
        if not self._wait_until_true(
            done_node,
            timeout=remaining,
            interval=0.05,
            description=f"{description}完成（{done_node}→1）",
        ):
            self._log_status(f"{description}失败")
            err = self._opc_read("Robot_Error_code", force_read=True)
            raise ValueError(
                f"{description}超时，{done_node} 未置 1，Error_code={err}"
            )

    @not_action
    def _wait_until_true(
        self,
        node_name: str,
        timeout: float = 180.0,
        interval: float = 0.2,
        description: str = None,
        log_each: bool = False,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（轮询 {node_name}）...")
        start = time.time()
        read_fail_streak = 0
        while time.time() - start < timeout:
            value = self._opc_read(node_name, force_read=True)
            if value is None:
                read_fail_streak += 1
                if read_fail_streak >= 3:
                    logger.error(
                        f"✗ {desc}中止：{node_name} 连续读取失败，"
                        "OPC 连接已断开，请退出并重启脚本"
                    )
                    return False
            else:
                read_fail_streak = 0
                if log_each:
                    logger.info(f"轮询 {node_name}={value}")
                if value:
                    logger.info(f"✓ {desc}（{node_name}={value}）")
                    return True
            time.sleep(interval)
        value = self._opc_read(node_name, force_read=True)
        logger.error(f"✗ {desc} 超时（{node_name}={value!r}）")
        return False

    @not_action
    def _wait_until_false(
        self,
        node_name: str,
        timeout: float = 30.0,
        interval: float = 0.2,
        description: str = None,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（轮询 {node_name}）...")
        start = time.time()
        read_fail_streak = 0
        while time.time() - start < timeout:
            value = self._opc_read(node_name, force_read=True)
            if value is None:
                read_fail_streak += 1
                if read_fail_streak >= 3:
                    logger.error(
                        f"✗ {desc}中止：{node_name} 连续读取失败，"
                        "OPC 连接已断开，请退出并重启脚本"
                    )
                    return False
            else:
                read_fail_streak = 0
                if not value:
                    logger.info(f"✓ {desc}（{node_name}={value}）")
                    return True
            time.sleep(interval)
        value = self._opc_read(node_name, force_read=True)
        logger.error(f"✗ {desc} 超时（{node_name}={value!r}）")
        return False

    @not_action
    def run_board_sequence_test(
        self,
        station_action: int = 1,
        pick_place: int = 1,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        """依次运行板位 ModuleNo 1-9：每站先 X 轴到位再工位动作 CmdType=0。"""
        if pick_place not in (0, 1):
            raise ValueError("pick_place 只能为 1（夹料）或 0（放料）")
        logger.info("机械手：开始依次测试板位 1-9（先 X 到绝对坐标，到位后再动臂）...")
        self.ensure_idle()
        for module_no in range(1, 10):
            label = MODULE_BOARD_LABELS.get(module_no, f"板位{module_no}")
            x_pos = MODULE_BOARD_X_POS.get(module_no)
            if x_pos is None:
                raise ValueError(
                    f"板位{module_no} 缺少 X 坐标，请补全 X轴位置(1).txt 映射"
                )
            kwargs = dict(
                cmd_type=int(RobotCommand.STATION_ACTION),
                module_no=module_no,
                station_action=station_action,
                pick_place=pick_place,
                x_pos=x_pos,
                x_speed=x_speed,
                timeout=timeout,
            )
            logger.info(f"--- 板位{module_no} {label} X={x_pos} ---")
            self.execute_command(**kwargs)
        logger.info("机械手：板位 1-9 测试完成")
        return {"success": True, "message": "板位1-9测试完成"}

    @not_action
    def get_x_position(self):
        return self._opc_read("Robot_XPosFB", force_read=True)

    @not_action
    def get_status(self) -> dict:
        return {
            "X": self.get_x_position(),
            "x_set": self._opc_read("Robot_XPosSet", force_read=True),
            "finish": self._opc_read("Robot_FinishFB", force_read=True),
            "cmd_type": self._opc_read("Robot_CmdType", force_read=True),
            "cmd_trig": self._opc_read("Robot_CmdTrig", force_read=True),
            "module_no": self._opc_read("Robot_ModuleNoSet", force_read=True),
            "stack": self._opc_read("Robot_Stack", force_read=True),
            "stack_done": self._opc_read("Robot_Stack_Done", force_read=True),
            "pick_or_place": self._get_node_value_optional(PICK_PLACE_NODE),
            "gohome_done": self._opc_read("Robot_gohome_done", force_read=True),
            "error_code": self._opc_read("Robot_Error_code", force_read=True),
            "running_status": self._opc_read("Robot_Running_Status", force_read=True),
        }

    @not_action
    def _log_status(self, prefix: str = "状态") -> None:
        s = self.get_status()
        logger.info(
            f"{prefix}: X={s['X']} XSet={s['x_set']} Finish={s['finish']} "
            f"CmdType={s['cmd_type']} CmdTrig={s['cmd_trig']} "
            f"ModuleNo={s['module_no']} Stack={s['stack']} "
            f"PickPlace={s['pick_or_place']} GoHomeDone={s['gohome_done']} "
            f"Error={s['error_code']} Running={s['running_status']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    ROBOT_URL = "opc.tcp://192.168.6.6:4840"

    robot = RoboticArmDevice(
        url=ROBOT_URL,
        xlsx_path=DEFAULT_XLSX_PATH,
        use_subscription=False,
    )
    time.sleep(2)
    logger.info(f"机械手使能前状态: {robot.get_status()}")
    robot.execute_command(cmd_type=int(RobotCommand.ENABLE))
    robot.execute_command(cmd_type=int(RobotCommand.RESET))
    logger.info(f"机械手复位就绪状态: {robot.get_status()}")

    while True:
        print("请选择操作：")
        print("1  依次测试板位1-9（ModuleNo 1→9，station_action=1，夹料）")
        print("3  复位 (CmdType=7)")
        print("4  使能 (CmdType=9)")
        print("5  失能 (CmdType=8)")
        print("6  动作命令复位 (CmdType=10)")
        print("7  回原点 (CmdType=100)")
        print("8  停止 (CmdType=101)")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "1":
            if input("确认安全区域无人，输入 y 依次测试板位1-9: ").strip().lower() == "y":
                robot.run_board_sequence_test()
        elif choice == "3":
            robot.execute_command(cmd_type=7)
        elif choice == "4":
            robot.execute_command(cmd_type=9)
        elif choice == "5":
            robot.execute_command(cmd_type=8)
        elif choice == "6":
            robot.execute_command(cmd_type=10)
        elif choice == "7":
            robot.execute_command(cmd_type=100)
        elif choice == "8":
            robot.execute_command(cmd_type=101)
        else:
            print("无效的操作序号，请重新输入。")

    robot.disconnect()
    print("退出程序。")
