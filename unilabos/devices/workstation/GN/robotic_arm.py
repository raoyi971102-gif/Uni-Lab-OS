"""
机械手 设备驱动

协议：OPC_UA协议1.3.5（节点 opcua_gn1.3.5.csv，前缀 Robot_，旋转堆栈前缀 Stack_）。
握手统一由 GNStationClient.run_command 承担（interlock → 写参 → 触发 → 等到位 → 复位）。

对外核心动作：transfer_item（取位置 → 放位置，程序内部自动完成 X 定位、
旋转堆栈到列到位校验、抓/放、安全空闲复位）；另提供 pick_at / place_at 单步，以及
enable/reset/go_home/stop 维护动作与 execute_command 底层调试入口。

抓放采用「工站动作序号」协议：Robot_ModuleNoSet 选中工站 → 写该工站动作节点
Robot_<工站>=动作序号 → Robot_CmdTrig 触发 → 等该工站 Robot_<工站>_Done。
旋转堆栈：同一 OPC 服务器上直接写 Stack_ 节点（CmdType=5 旋转至目标列），
并按 Stack_RPosFB 校验到位后才允许抓取。

============ 安全位 / 复位节点问题（待现场确认）============
现状：1.3.5 协议未提供机械手「安全位/复位姿态到位」的独立反馈节点。
- 抓/放的安全回位依赖 PLC 预编程动作在结束时自动回到工站安全姿态；上位机侧
  _ensure_safe_posture() 暂仅做 ensure_idle()（CmdTrig=0 + CmdType=10 清 FinishFB），
  不移动轴、不影响夹爪，避免误触发丢件。
- SAFE_POSTURE_FB_NODE / ROBOT_SAFE_X 为占位；现场一旦给出安全位反馈节点，
  _ensure_safe_posture() 改为 wait_true(SAFE_POSTURE_FB_NODE) 强校验即可。
- CmdType=7（复位）是否释放夹爪需现场确认；确认前取/放之间不自动下发 CmdType=7，
  防止持件状态下丢件（robot_reset 仅作为独立维护动作暴露）。
"""

import os
import time
import logging
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient
from unilabos.devices.workstation.GN.rotary_stack import (
    STACK_COLUMN_TO_R,
    STACK_R_TOLERANCE,
    STACK_ROTATE_SPEED,
)

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.5.csv")

X_POS_TOLERANCE = 1

ITEM_PLATE = "plate"    # 孔板/板子位
ITEM_BOTTLE = "bottle"  # 瓶子位

# 旋转堆栈旋转至目标列的指令号（Stack_CmdType）
STACK_ROTATE_TO_TARGET = 5

# ============ 安全位 / 复位节点占位（待现场确认，详见模块头注释）============
SAFE_POSTURE_FB_NODE = None   # 机械手安全/复位姿态到位反馈（现场确认后填入节点名）
ROBOT_SAFE_X = None           # 如需先回安全 X 再平移，填入安全平移 X（现暂不使用）


class RobotCommand(int, Enum):
    """机械手指令类型 (Robot_CmdType)"""

    X_LEFT = 1
    X_RIGHT = 2
    PICK = 3
    PLACE = 4
    RESET = 7
    DISABLE = 8
    ENABLE = 9
    ACTION_CMD_RESET = 10


# 伪指令（非 PLC 枚举，在 execute_command 内路由）
CMD_GO_HOME = 100
CMD_STOP = 101

ROBOT_CMD_LABELS = {
    1: "X向左", 2: "X向右", 7: "复位", 8: "失能", 9: "使能",
    10: "动作命令复位", 100: "回原点", 101: "停止",
}


class Station:
    """工站定义：模块号、瓶/板 X 位置、工站动作/完成节点、是否旋转堆栈。

    X 位置来自《X轴位置(1).txt》，module_no 来自 1.3.5 Robot_ModuleNoSet 描述：
    常规烘箱1、锁紧2、快换3、离心机4、真空烘箱5、9320设备6、离心管液体处理7、堆栈8、固体加样9。
    """

    def __init__(self, module_no, x_plate, x_bottle, action_node, done_node, rotary=False):
        self.module_no = module_no
        self.x_plate = x_plate
        self.x_bottle = x_bottle
        self.action_node = action_node
        self.done_node = done_node
        self.rotary = rotary

    def x_for(self, item_type: str) -> int:
        return self.x_bottle if item_type == ITEM_BOTTLE else self.x_plate


# 工站表（键为前端可选名）
STATIONS: dict = {
    "oven":         Station(1, -13278, -13278, "Robot_Oven", "Robot_Oven_Done"),
    "locking":      Station(2, -13278, -13278, "Robot_Locking_mechanism", "Robot_Locking_mechanism_Done"),
    "quick_change": Station(3, -10478, -10478, "Robot_Quick_change_mechanism", "Robot_Quick_change_mechanism_Done"),
    "centrifuge":   Station(4, -8582, -8582, "Robot_Centrifuge", "Robot_Centrifuge_Done"),
    "vacuum_oven":  Station(5, -6726, -6726, "Robot_Vacuum_oven", "Robot_Vacuum_oven_Done"),
    "nine9320":     Station(6, -3926, -3926, "Robot_9320", "Robot_Nine_9320_Done"),
    "tube":         Station(7, -1326, 874, "Robot_Centrifuge_tube_liquid_handling", "Robot_Centrifuge_tube_liquid_handling_Done"),
    "stack":        Station(8, 3274, 2473, "Robot_Stack", "Robot_Stack_Done", rotary=True),
    "solid":        Station(9, 6318, 5971, "Robot_Add_solid_sample", "Robot_Add_solid_sample_Done"),
}


_TRANSFER_DOC = (
    "机械手搬运：从 source 取、放到 target；程序内部自动完成 X 定位、"
    "（旋转堆栈）旋转到列并按 Stack_RPosFB 校验到位、抓/放、安全空闲复位。"
    f"工站可选: {', '.join(STATIONS)}。item_type: {ITEM_PLATE}/{ITEM_BOTTLE}（板位/瓶位，决定 X 微调）。"
    "source_slot/target_slot 为该工站上第几个板位/瓶位（写 Robot_<工站> 动作序号）；"
    "旋转堆栈须再给 source_column/target_column 指定第几列。"
)

_EXECUTE_CMD_DOC = (
    "底层调试入口，按 Robot_CmdType 执行：1=X左 2=X右 7=复位 8=失能 9=使能 "
    "10=动作命令复位 100=回原点 101=停止。1/2 需 x_pos+x_speed。"
)


@device(
    id="gn_robotic_arm",
    display_name="机械手",
    category=["workstation"],
    description="GN 机械手：OPC UA 1.3.5，transfer_item 高层搬运 + 工站动作序号协议",
    icon="",
    version="4.0.0",
)
class RoboticArmDevice(GNStationClient):
    """机械手设备类（OPC 前缀 Robot_，可直接驱动 Stack_ 旋转堆栈）"""

    PREFIX = "Robot_"
    CMD_TYPE_NODE = "Robot_CmdType"
    CMD_TRIG_NODE = "Robot_CmdTrig"
    COMPLETE_NODE = "Robot_FinishFB"

    def __init__(
        self,
        url: str,
        csv_path: str = DEFAULT_CSV_PATH,
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
            csv_path=csv_path,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs,
        )

    # ---------------- 高层动作 ----------------

    @action(description=_TRANSFER_DOC)
    def transfer_item(
        self,
        source: str,
        target: str,
        item_type: str = ITEM_PLATE,
        source_slot: int = 1,
        target_slot: int = 1,
        source_column: Optional[int] = None,
        target_column: Optional[int] = None,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        """取位置 → 放位置 的完整搬运。slot=第几个板位/瓶位，column=旋转堆栈第几列。"""
        self._check_item(item_type)
        logger.info(f"机械手：搬运 {source}→{target}（{item_type}）")
        self._operate("取", source, item_type, source_slot, source_column, x_speed, timeout)
        self._operate("放", target, item_type, target_slot, target_column, x_speed, timeout)
        return {"success": True, "message": f"搬运完成 {source}→{target}", "item_type": item_type}

    @action(description="机械手在指定工站取料：slot=第几个板位/瓶位，旋转堆栈须给 column=第几列（自动 X 定位 / 转列到位 / 抓取 / 安全复位）")
    def pick_at(
        self,
        station: str,
        item_type: str = ITEM_PLATE,
        slot: int = 1,
        column: Optional[int] = None,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        self._check_item(item_type)
        return self._operate("取", station, item_type, slot, column, x_speed, timeout)

    @action(description="机械手在指定工站放料：slot=第几个板位/瓶位，旋转堆栈须给 column=第几列（自动 X 定位 / 转列到位 / 放置 / 安全复位）")
    def place_at(
        self,
        station: str,
        item_type: str = ITEM_PLATE,
        slot: int = 1,
        column: Optional[int] = None,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        self._check_item(item_type)
        return self._operate("放", station, item_type, slot, column, x_speed, timeout)

    @action(description="使能机械手 (CmdType=9)")
    def robot_enable(self, timeout: float = 60.0) -> dict:
        return self._robot_cmd(int(RobotCommand.ENABLE), "使能", timeout=timeout)

    @action(description="机械手复位到安全姿态 (CmdType=7)。注意：是否释放夹爪待现场确认，勿在持件时调用")
    def robot_reset(self, timeout: float = 120.0) -> dict:
        return self._robot_cmd(int(RobotCommand.RESET), "复位", timeout=timeout)

    @action(description="机械手回原点 (Robot_gohome)")
    def robot_go_home(self, timeout: float = 180.0) -> dict:
        return self._go_home(timeout=timeout)

    @action(description="机械手紧急停止 (Robot_STOP)")
    def robot_stop(self) -> dict:
        return self._stop()

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """底层 CmdType 路由（调试用；抓放请用 transfer_item/pick_at/place_at）。"""
        cmd = int(cmd_type)
        label = ROBOT_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        logger.info(f"机械手：{label} (CmdType={cmd})")

        if cmd in (int(RobotCommand.X_LEFT), int(RobotCommand.X_RIGHT)):
            if x_pos is None or x_speed is None:
                raise ValueError(f"CmdType={cmd} 需要 x_pos 与 x_speed")
            return self._move_x(x_pos, x_speed, timeout=timeout)
        if cmd in (int(RobotCommand.RESET), int(RobotCommand.DISABLE), int(RobotCommand.ENABLE)):
            return self._robot_cmd(cmd, label, timeout=timeout)
        if cmd == int(RobotCommand.ACTION_CMD_RESET):
            self.ensure_idle()
            return {"success": True, "message": "动作命令复位完成", "cmd_type": cmd}
        if cmd == CMD_GO_HOME:
            return self._go_home(timeout=timeout)
        if cmd == CMD_STOP:
            return self._stop()
        raise ValueError(f"不支持的 CmdType={cmd}（抓放请用 transfer_item/pick_at/place_at）")

    # ---------------- 内部流程 ----------------

    @not_action
    def _operate(self, mode, station_key, item_type, slot, column, x_speed, timeout) -> dict:
        """单工站取/放：安全空闲 → 移动到工站前 →（旋转堆栈到列）→ 抓/放第几个板位 → 安全空闲复位。

        slot：该工站上第几个板位/瓶位（写 Robot_<工站> 动作序号）；column：旋转堆栈第几列。
        """
        st = self._station(station_key)
        target_x = st.x_for(item_type)
        logger.info(f"机械手：{mode} @{station_key}（module={st.module_no} X={target_x} {item_type} slot={slot} column={column}）")

        self._ensure_safe_posture()
        self._move_x(target_x, x_speed, timeout=timeout)
        self._ensure_station_ready(st, column, timeout=timeout)
        self._run_station_action(st, slot, mode, timeout=timeout)
        self._ensure_safe_posture()
        return {"success": True, "message": f"{mode}@{station_key} 完成", "x": target_x, "slot": slot, "column": column}

    @not_action
    def _ensure_station_ready(self, station: "Station", column: Optional[int], timeout: float) -> None:
        """抓/放前的工站就位门控：旋转堆栈须转到列并 Stack_RPosFB 到位。"""
        if station.rotary:
            if column is None:
                raise ValueError(f"{station.action_node} 为旋转堆栈，必须提供 column（列号）")
            self._rotate_stack_to_column(column, timeout=timeout)

    @not_action
    def _ensure_safe_posture(self) -> None:
        """确保机械手处于可平移的安全空闲态。

        安全位反馈节点待现场确认（见模块头）：现仅做 ensure_idle（不移动轴/不动夹爪）。
        现场给出 SAFE_POSTURE_FB_NODE 后，改为强校验到位。
        """
        if SAFE_POSTURE_FB_NODE:
            if not self.wait_true(SAFE_POSTURE_FB_NODE, description="机械手安全位到位"):
                raise ValueError("机械手未在安全位，禁止继续")
            return
        self.ensure_idle()

    @not_action
    def _run_station_action(self, station: "Station", slot: int, mode: str, timeout: float) -> None:
        """工站板位动作协议：ModuleNoSet + 工站动作节点=第几个板位 → CmdTrig → 等工站 _Done。"""
        desc = f"{mode} {station.action_node}=第{slot}个板位"
        self.run_command(
            cmd_type=None,
            setpoints={"Robot_ModuleNoSet": station.module_no, station.action_node: slot},
            done_node=station.done_node,
            clear_done=False,
            interlock=self.ensure_idle,
            description=desc,
            timeout=timeout,
        )
        self.set_node_value(station.action_node, 0)

    @not_action
    def _rotate_stack_to_column(self, column: int, timeout: float) -> None:
        """驱动 Stack_ 节点将旋转堆栈转到指定列；等 Stack_CompleteFB 且 Stack_RPosFB 到位。"""
        r_pos = STACK_COLUMN_TO_R.get(column)
        if r_pos is None:
            raise ValueError(f"未配置堆栈列 {column} 的 R 位置，请补 STACK_COLUMN_TO_R")
        self.run_command(
            cmd_type=STACK_ROTATE_TO_TARGET,
            setpoints={"Stack_RPosSet": r_pos, "Stack_RSpeed": STACK_ROTATE_SPEED},
            trig_node="Stack_CmdTrig",
            cmd_type_node="Stack_CmdType",
            done_node="Stack_CompleteFB",
            reach_checks=[("Stack_RPosFB", r_pos, STACK_R_TOLERANCE)],
            clear_done=False,
            description=f"堆栈旋转至列{column}",
            timeout=timeout,
        )

    @not_action
    def _move_x(self, target_x: int, x_speed: int, timeout: float = 120.0) -> dict:
        """X 点动到绝对位置：按当前 X 选 CmdType 1/2，等 XPosFB 到位（reach_checks）。"""
        current = self.get_x_position()
        if current is not None and abs(current - target_x) <= X_POS_TOLERANCE:
            logger.info(f"机械手：X 已在 {target_x} 附近（current={current}）")
            return {"success": True, "message": f"X 已在 {target_x} 附近"}

        cmd = int(RobotCommand.X_RIGHT if current < target_x else RobotCommand.X_LEFT)
        desc = f"X{'向右' if current < target_x else '向左'}移至{target_x}"
        self.run_command(
            cmd_type=cmd,
            setpoints={"Robot_XPosSet": target_x, "Robot_XSpeedSet": x_speed},
            done_node=None,
            reach_checks=[("Robot_XPosFB", target_x, X_POS_TOLERANCE)],
            clear_done=False,
            interlock=self.ensure_idle,
            description=desc,
            timeout=timeout,
        )
        if self.get_node_value("Robot_FinishFB", force_read=True):
            self._clear_finish()
        return {"success": True, "message": f"{desc}完成", "cmd_type": cmd}

    @not_action
    def _robot_cmd(self, cmd_type: int, description: str, timeout: float = 120.0) -> dict:
        """Robot_CmdType 通道：run_command 等 FinishFB，再 CmdType=10 清 FinishFB。"""
        result = self.run_command(
            cmd_type=int(cmd_type),
            done_node="Robot_FinishFB",
            clear_done=False,
            interlock=self.ensure_idle,
            description=description,
            timeout=timeout,
        )
        self._clear_finish()
        return result

    @not_action
    def ensure_idle(self) -> None:
        """触发前：CmdTrig=0，FinishFB=1 时发 CmdType=10 清除。"""
        self.set_node_value("Robot_CmdTrig", 0)
        time.sleep(0.05)
        self._clear_finish()

    @not_action
    def _clear_finish(self, timeout: float = 30.0) -> None:
        """CmdType=10 清除 Robot_FinishFB。"""
        if not self.get_node_value("Robot_FinishFB", force_read=True):
            return
        logger.info("机械手：CmdType=10 清除 FinishFB...")
        self.set_node_value("Robot_CmdTrig", 0)
        time.sleep(0.05)
        self.set_node_value("Robot_CmdType", int(RobotCommand.ACTION_CMD_RESET))
        self.set_node_value("Robot_CmdTrig", 1)
        if not self.wait_false("Robot_FinishFB", timeout=timeout, description="FinishFB清除"):
            err = self.get_node_value("Robot_Error_code", force_read=True)
            raise ValueError(f"FinishFB 清除失败，Error_code={err}")
        self.set_node_value("Robot_CmdTrig", 0)

    @not_action
    def _go_home(self, timeout: float = 180.0) -> dict:
        """回原点：Robot_gohome=1，等 Robot_gohome_done=1。"""
        logger.info("机械手：回原点...")
        self.ensure_idle()
        self.set_node_value("Robot_gohome", 1)
        if not self.wait_true("Robot_gohome_done", timeout=timeout, description="回原点完成"):
            err = self.get_node_value("Robot_Error_code", force_read=True)
            raise ValueError(f"回原点失败，Error_code={err}")
        self.set_node_value("Robot_gohome", 0)
        return {"success": True, "message": "回原点完成", "cmd_type": CMD_GO_HOME}

    @not_action
    def _stop(self) -> dict:
        """紧急停止：Robot_STOP=1 脉冲。"""
        logger.info("机械手：停止...")
        self.set_node_value("Robot_STOP", 1)
        time.sleep(0.2)
        self.set_node_value("Robot_STOP", 0)
        return {"success": True, "message": "停止命令已下发", "cmd_type": CMD_STOP}

    # ---------------- 工具 ----------------

    @not_action
    def _station(self, key: str) -> "Station":
        st = STATIONS.get(key)
        if st is None:
            raise ValueError(f"未知工站 {key!r}，可选: {', '.join(STATIONS)}")
        return st

    @not_action
    def _check_item(self, item_type: str) -> None:
        if item_type not in (ITEM_PLATE, ITEM_BOTTLE):
            raise ValueError(f"item_type 必须是 {ITEM_PLATE!r} 或 {ITEM_BOTTLE!r}，收到 {item_type!r}")

    @not_action
    def get_x_position(self) -> int:
        return self.get_node_value("Robot_XPosFB", force_read=True)

    @not_action
    def get_status(self) -> dict:
        return {
            "X": self.get_x_position(),
            "x_set": self.get_node_value("Robot_XPosSet", force_read=True),
            "finish": self.get_node_value("Robot_FinishFB", force_read=True),
            "cmd_type": self.get_node_value("Robot_CmdType", force_read=True),
            "cmd_trig": self.get_node_value("Robot_CmdTrig", force_read=True),
            "module_no": self.get_node_value("Robot_ModuleNoSet", force_read=True),
            "gohome_done": self.get_node_value("Robot_gohome_done", force_read=True),
            "error_code": self.get_node_value("Robot_Error_code", force_read=True),
            "running_status": self.get_node_value("Robot_Running_Status", force_read=True),
        }

    @not_action
    def get_stack_status(self) -> dict:
        """旋转堆栈反馈：R 实际位置 / 完成 / 物料检测结果。"""
        return {
            "r_pos": self.get_node_value("Stack_RPosFB", force_read=True),
            "z_pos": self.get_node_value("Stack_ZPosFB", force_read=True),
            "complete": self.get_node_value("Stack_CompleteFB", force_read=True),
            "detect_result": self.get_node_value("Stack_DetectResult", force_read=True),
        }


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    ROBOT_URL = "opc.tcp://192.168.6.6:4840"
    robot = RoboticArmDevice(url=ROBOT_URL, csv_path=DEFAULT_CSV_PATH, use_subscription=False)

    time.sleep(2)
    robot.ensure_idle()
    logger.info(f"机械手连通性: {robot.get_status()}")

    def _ask_int(prompt, default):
        raw = input(f"{prompt} [{default}]: ").strip()
        return int(raw) if raw else default

    def _ask_item():
        raw = input("物料类型 plate/bottle [plate]: ").strip().lower()
        return "bottle" if raw == "bottle" else "plate"

    def _pick_station(title="选择工站"):
        print(f"{title}：")
        print("  编号 名称          module  X板/X瓶")
        for i, (key, st) in enumerate(STATIONS.items(), start=1):
            flag = " [旋转堆栈，需列号]" if st.rotary else ""
            print(f"   {i:>2} {key:<12} module={st.module_no}  X板={st.x_plate} X瓶={st.x_bottle}{flag}")
        keys = list(STATIONS)
        idx = _ask_int("工站编号", 1)
        if not (1 <= idx <= len(keys)):
            print("无效工站编号")
            return None
        return keys[idx - 1]

    def _operate_station(mode_fn):
        key = _pick_station()
        if key is None:
            return
        st = STATIONS[key]
        item = _ask_item()
        column = _ask_int("第几列(仅旋转堆栈)", 1) if st.rotary else None
        slot = _ask_int("第几个板位/瓶位", 1)
        mode_fn(station=key, item_type=item, slot=slot, column=column)

    while True:
        print("请选择操作：")
        print(f"1  使能 (CmdType=9)")
        print(f"2  复位 (CmdType=7)")
        print(f"3  回原点")
        print(f"4  停止")
        print(f"--- 工站 取/放 调试（选工站 → 第几列/第几个板位）---")
        print(f"5  取料 pick_at   (任一工站的板位/瓶位)")
        print(f"6  放料 place_at  (任一工站的板位/瓶位)")
        print(f"7  取: 遍历旋转堆栈所有列 (逐列 pick_at)")
        print(f"8  放: 遍历旋转堆栈所有列 (逐列 place_at)")
        print(f"9  搬运 transfer_item (源工站 -> 目标工站)")
        print(f"10 仅 X 平移到某工站 (不抓放)")
        print(f"99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "1":
            robot.robot_enable()
        elif choice == "2":
            robot.robot_reset()
        elif choice == "3":
            robot.robot_go_home()
        elif choice == "4":
            robot.robot_stop()
        elif choice == "5":
            _operate_station(robot.pick_at)
        elif choice == "6":
            _operate_station(robot.place_at)
        elif choice == "7":
            item, slot = _ask_item(), _ask_int("第几个板位/瓶位", 1)
            if not STACK_COLUMN_TO_R:
                print("STACK_COLUMN_TO_R 为空，请先在 rotary_stack.py 标定各列 R 位置")
            for col in STACK_COLUMN_TO_R:
                print(f">>> 抓取 旋转堆栈 第{col}列 第{slot}个板位")
                robot.pick_at(station="stack", item_type=item, slot=slot, column=col)
        elif choice == "8":
            item, slot = _ask_item(), _ask_int("第几个板位/瓶位", 1)
            if not STACK_COLUMN_TO_R:
                print("STACK_COLUMN_TO_R 为空，请先在 rotary_stack.py 标定各列 R 位置")
            for col in STACK_COLUMN_TO_R:
                print(f">>> 放置 旋转堆栈 第{col}列 第{slot}个板位")
                robot.place_at(station="stack", item_type=item, slot=slot, column=col)
        elif choice == "9":
            src = _pick_station("选择 源 工站")
            dst = _pick_station("选择 目标 工站") if src else None
            if src and dst:
                item = _ask_item()
                src_col = _ask_int("source 第几列", 1) if STATIONS[src].rotary else None
                dst_col = _ask_int("target 第几列", 1) if STATIONS[dst].rotary else None
                src_slot = _ask_int("source 第几个板位/瓶位", 1)
                dst_slot = _ask_int("target 第几个板位/瓶位", 1)
                robot.transfer_item(source=src, target=dst, item_type=item,
                                    source_slot=src_slot, target_slot=dst_slot,
                                    source_column=src_col, target_column=dst_col)
        elif choice == "10":
            key = _pick_station("选择要平移到的工站")
            if key:
                x = STATIONS[key].x_for(_ask_item())
                robot.execute_command(cmd_type=1, x_pos=x, x_speed=300)
        else:
            print("无效的操作序号，请重新输入。")

    robot.disconnect()
    print("退出程序。")
