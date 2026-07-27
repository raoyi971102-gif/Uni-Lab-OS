"""
机械手 设备驱动（OPC UA 1.3.6，前缀 Robot_）

职责：只负责在各工站抓 / 放。参数 = 工站(station) + 物料(item_type) + 抓取数字(number)。
流程（各步逐个触发、等 FinishFB=1 完成后再下一步）：
  1) X 小车移动：读当前 X → 距离=目标绝对坐标−当前 → 写距离到 Robot_XPosSet，
     CmdType 恒=2，触发一次，等 Robot_XPosFB 到达目标绝对坐标（X 移动全程 FinishFB=1）；
  2)（旋转堆栈）按 number 转到对应列，等旋转到位；
  3) 抓/放：写模块号+板位到对应寄存器 → CmdType=3(夹料)/4(放料) → 触发，等 FinishFB 完成。

抓放协议（1.3.6）：
    Robot_ModuleNoSet 选工站 → Robot_<工站>=number（工位号）→ Robot_CmdType(3/4)
    → Robot_CmdTrig 触发 → 等 Robot_FinishFB 先转 0(忙) 再回 1(完成) → CmdTrig 置 0。
    （Robot_FinishFB：1=就绪/空闲，0=执行中；上电即为 1 表示空闲可运行。
      1.3.6 已删除各工站独立的 *_Done 节点，统一用 Robot_FinishFB 的忙→闲边沿判完成。）

旋转堆栈(stack)：旋转与抓取是两个独立动作，由抓放驱动组合——机械手抓取前先请求
    旋转堆栈（rotary_stack.rotate_for_number：按 number 转到对应列→等旋转到位），
    到位后再写 Robot_Stack=number 执行抓取。number 与列的对应表固定在
    rotary_stack.COLUMN_NUMBERS，机械手只传 number，列号由堆栈侧解析。
    绝对 R 定位依赖 R0 基准，需在开机/换批时外部单独调一次 rotary_stack.reset()。

module_no 对应 1.3.6 Robot_ModuleNoSet（注意：模块号相较 1.3.5 已重排）：
    常规烘箱1 锁紧2 快换3 离心机4 真空烘箱5 9320设备6 离心管液体处理7 堆栈8 固体加样9 机械手放置板位10
X 位置来自《X轴位置(1).txt》；成品放置区 X=5318。
系统级动作：回原点/复位走 System_ResetTrig→System_ResetCompleteFB；停止走 System_StopTrig；
    上电初始化状态读 System_IsReady（1=可发指令，0=上电回原点故障）。
"""

import os
import time
import logging
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient
from unilabos.devices.workstation.GN import rotary_stack

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.6.csv")

ITEM_PLATE = "plate"    # 孔板/板位
ITEM_BOTTLE = "bottle"  # 瓶位

X_REACH_TOL = 0         # X 到位容差（Robot_XPosFB 与目标绝对坐标之差 ≤ 此值即视为到位）


class RobotCommand(int, Enum):
    """机械手指令类型 (Robot_CmdType，1.3.6)。
    注意：X 小车移动统一用 CmdType=2 + 带符号的相对距离，方向由距离正负体现，
    不再按左右分 1/2。X_LEFT 仅作协议对照保留。"""

    X_LEFT = 1
    X_RIGHT = 2        # X 移动统一用此值（配合带符号距离）
    PICK = 3          # 夹料（取）
    PLACE = 4         # 放料（放）
    STOP = 6          # 停止
    RESET = 7
    DISABLE = 8
    ENABLE = 9


# 取 / 放 → Robot_CmdType（抓放只是写入值不同）
MODE_TO_CMD = {"取": RobotCommand.PICK, "放": RobotCommand.PLACE}


class Station:
    """工站：模块号、瓶/板 X 位置、工站动作节点、是否旋转堆栈。"""

    def __init__(self, module_no, x_plate, x_bottle, action_node, rotary=False):
        self.module_no = module_no
        self.x_plate = x_plate
        self.x_bottle = x_bottle
        self.action_node = action_node
        self.rotary = rotary

    def x_for(self, item_type: str) -> Optional[int]:
        return self.x_bottle if item_type == ITEM_BOTTLE else self.x_plate


# 工站表（键为前端可选名）。module_no 严格对应 1.3.6 Robot_ModuleNoSet（模块号相较 1.3.5 已重排）。
STATIONS: dict = {
    "locking":       Station(2,  -13278, -13278, "Robot_Locking_mechanism"),
    "quick_change":  Station(3,  -10478, -10478, "Robot_Quick_change_mechanism"),
    "centrifuge":    Station(4,  -8582,  -8582,  "Robot_Centrifuge"),
    "nine9320":      Station(6,  -3926,  -3926,  "Robot_9320"),
    "tube":          Station(7,  -1326,  874,    "Robot_Centrifuge_tube_liquid_handling"),
    "stack":         Station(8,  3274,   2473,   "Robot_Stack", rotary=True),
    "solid":         Station(9,  6318,   5971,   "Robot_Add_solid_sample"),
    "oven":          Station(1,  -13278, -13278, "Robot_Oven"),
    "vacuum_oven":   Station(5,  -6726,  -6726,  "Robot_Vacuum_oven"),
    "finished_area": Station(10, 5318,   5318,   "Robot_Finished_Product_Area"),
}

_STATION_HINT = f"工站可选: {', '.join(STATIONS)}；item_type: {ITEM_PLATE}/{ITEM_BOTTLE}（决定 X 微调）；number=写入 PLC 的抓取工位号（旋转堆栈按该数字自动转到对应列）。"


@device(
    id="gn_robotic_arm",
    display_name="机械手",
    category=["workstation"],
    description="GN 机械手：OPC UA 1.3.6，工站+子位置抓放，旋转堆栈调用 rotary_stack 旋转驱动",
    icon="",
    version="5.1.0",
)
class RoboticArmDevice(GNStationClient):
    """机械手设备类（OPC 前缀 Robot_，抓放旋转堆栈时驱动 Stack_ 节点）"""

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
        enable_connection_monitor: bool = False,
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
            enable_connection_monitor=enable_connection_monitor,
            *args,
            **kwargs,
        )

    # ==================== 抓 / 放 ====================

    @action(description="在工站抓取。" + _STATION_HINT)
    def pick(self, station: str, item_type: str = ITEM_PLATE, number: int = 1,
             x_speed: int = 300, timeout: float = 180.0) -> dict:
        return self._pick_or_place("取", station, item_type, number, x_speed, timeout)

    @action(description="在工站放置。" + _STATION_HINT)
    def place(self, station: str, item_type: str = ITEM_PLATE, number: int = 1,
              x_speed: int = 300, timeout: float = 180.0) -> dict:
        return self._pick_or_place("放", station, item_type, number, x_speed, timeout)

    @action(description="搬运：从 source 抓 → 放到 target。" + _STATION_HINT)
    def transfer(self, source: str, target: str, item_type: str = ITEM_PLATE,
                 source_number: int = 1, target_number: int = 1,
                 x_speed: int = 300, timeout: float = 180.0) -> dict:
        self.pick(source, item_type, source_number, x_speed, timeout)
        self.place(target, item_type, target_number, x_speed, timeout)
        return {"success": True, "message": f"搬运完成 {source}→{target}", "item_type": item_type}

    # ==================== 维护动作 ====================

    @action(description="使能机械手 (CmdType=9)")
    def enable(self, timeout: float = 60.0) -> dict:
        return self._robot_cmd(RobotCommand.ENABLE, "使能", timeout)

    @action(description="失能机械手 (CmdType=8)")
    def disable(self, timeout: float = 60.0) -> dict:
        return self._robot_cmd(RobotCommand.DISABLE, "失能", timeout)

    @action(description="复位到安全姿态 (CmdType=7)。是否松夹爪待现场确认，勿在持件时调用")
    def reset(self, timeout: float = 120.0) -> dict:
        return self._robot_cmd(RobotCommand.RESET, "复位", timeout)

    @action(description="系统复位/回原点 (System_ResetTrig → System_ResetCompleteFB)")
    def go_home(self, timeout: float = 180.0) -> dict:
        logger.info("机械手：系统复位/回原点...")
        self.ensure_idle()
        self.set_node_value("System_ResetTrig", 1)
        if not self.wait_true("System_ResetCompleteFB", timeout=timeout, description="系统复位完成"):
            raise ValueError(f"复位失败，Error_code={self.get_node_value('Robot_Error_code', force_read=True)}")
        self.set_node_value("System_ResetTrig", 0)
        return {"success": True, "message": "系统复位/回原点完成"}

    @action(description="紧急停止 (System_StopTrig)")
    def stop(self) -> dict:
        logger.info("机械手：停止...")
        self.set_node_value("System_StopTrig", 1)
        time.sleep(0.2)
        self.set_node_value("System_StopTrig", 0)
        return {"success": True, "message": "停止命令已下发"}

    @action(description="仅 X 平移到某工站，不抓放（调试用）。" + _STATION_HINT)
    def move_to_station(self, station: str, item_type: str = ITEM_PLATE,
                        x_speed: int = 300, timeout: float = 120.0) -> dict:
        return self.move_x(self._target_x(station, item_type), x_speed, timeout)

    # ==================== 内部流程 ====================

    @not_action
    def _pick_or_place(self, mode, station, item_type, number, x_speed, timeout) -> dict:
        """抓/放单工站（各步逐个触发、等 FinishFB=1 完成后再下一步）：
        1) 小车 move_x 到工站绝对坐标，等 FinishFB=1；
        2)（旋转堆栈）按 number（工站内子位置）转到对应列并校验旋转到位；
        3) 写模块号(Robot_ModuleNoSet=当前工站) → 写工站子位置(Robot_<工站>=number)
           → CmdType(3取/4放) → 触发，等 FinishFB=1。"""
        st = self._station(station)
        target_x = self._target_x(station, item_type)
        cmd = int(MODE_TO_CMD[mode])
        logger.info(f"机械手：{mode} @{station}（module={st.module_no} X={target_x} {item_type} number={number}）")

        # 1) 小车到工站绝对坐标（move_x 内部：读当前→算距离→CmdType2→触发→等 FinishFB）
        self.move_x(target_x, x_speed, timeout)
        logger.info(f"机械手：小车已到位 X={target_x}（FinishFB=1），准备{mode}")

        # 2) 旋转堆栈：按 number 转到对应列并校验到位（绝对 R 定位，依赖开机/换批时外部先 reset() 建 R0）
        if st.rotary:
            column = rotary_stack.rotate_for_number(self, number, timeout=timeout)
            logger.info(f"机械手：旋转堆栈已校验到位第{column}列，开始{mode}")

        # 3) 抓/放：写模块号 → 写工站子位置(number) → CmdType(3/4) → 触发，等 FinishFB=1
        self._run_action(
            cmd,
            setpoints={"Robot_ModuleNoSet": st.module_no, st.action_node: number},
            description=f"{mode}(CmdType={cmd}) 模块={st.module_no} {st.action_node}={number}",
            timeout=timeout,
        )
        self.set_node_value(st.action_node, 0)
        logger.info(f"机械手：{mode}@{station} 完成（number={number}）")
        return {"success": True, "message": f"{mode}@{station} 完成", "x": target_x, "number": number}

    @not_action
    def move_x(self, target_x: int, x_speed: int = 300, timeout: float = 120.0) -> dict:
        """X 移动到绝对坐标（相对移动实现）：
        读当前 X(Robot_XPosFB) → 距离 = 目标绝对坐标 − 当前 → 把距离写 Robot_XPosSet，
        CmdType 恒为 2，触发一次 → 等 Robot_XPosFB 到达目标绝对坐标（±X_REACH_TOL）判完成。
        （X 移动全程 FinishFB=1，不能用忙→闲判完成，故用位置反馈到位。）"""
        current = self.get_node_value("Robot_XPosFB", force_read=True)
        if current is None:
            raise ValueError("无法读取当前 X 位置(Robot_XPosFB)，连接可能已断开")
        distance = int(target_x) - int(current)
        if distance == 0:
            logger.info(f"X 已在目标 {target_x}，无需移动")
            return {"success": True, "message": f"X 已在 {target_x}"}

        # 移动指令类型恒为 2；移动距离(带符号)写 Robot_XPosSet；等 Robot_XPosFB 到达目标绝对坐标
        self._run_action(
            2,
            setpoints={"Robot_XPosSet": distance, "Robot_XSpeedSet": x_speed},
            reach=("Robot_XPosFB", int(target_x), X_REACH_TOL),
            description=f"X 移动距离 {distance}（{current}→{target_x}）",
            timeout=timeout,
        )
        after = self.get_node_value("Robot_XPosFB", force_read=True)
        logger.info(f"X 移动完成：当前 X={after}（目标 {target_x}）")
        return {"success": True, "message": f"X 到位 {target_x}", "x": after}

    @not_action
    def _robot_cmd(self, cmd: RobotCommand, desc: str, timeout: float) -> dict:
        """Robot_CmdType 通道：就绪/忙握手（见 _run_action）。"""
        self._run_action(int(cmd), description=desc, timeout=timeout)
        return {"success": True, "message": f"{desc}完成"}

    @not_action
    def _run_action(self, cmd_type: int, description: str, timeout: float,
                    setpoints: Optional[dict] = None, busy_timeout: float = 5.0,
                    reach=None) -> None:
        """指令触发 + 完成判定：
        确认就绪 → 写点位+CmdType → CmdTrig=1 → 判完成 → CmdTrig=0。
        完成判定两种：
          reach=(反馈节点, 目标, 容差) → 等该反馈到位（如 X 移动等 Robot_XPosFB 到目标绝对坐标）；
          reach=None → 走 FinishFB 就绪/忙握手（1=就绪/空闲，0=执行中，忙→闲判完成）。"""
        self.ensure_idle()
        if setpoints:
            for node, val in setpoints.items():
                if val is not None:
                    self.set_node_value(node, val)

        self.set_node_value("Robot_CmdTrig", 0)
        time.sleep(0.05)
        ok_type = self.set_node_value("Robot_CmdType", int(cmd_type))
        ok_trig = self.set_node_value("Robot_CmdTrig", 1)
        if not (ok_type and ok_trig):
            raise ValueError(f"{description} 指令写入失败（连接可能已断开，请重试）")

        if reach is not None:
            # 位置到位判据：等反馈节点到达目标（如 X 移动，FinishFB 全程为 1 不可用）
            fb_node, target, tol = reach
            done = self.wait_reached(fb_node, target, tol, timeout=timeout,
                                     description=f"{description} 到位")
        else:
            # 就绪/忙握手：先等 FinishFB 变 0（忙），再等其回 1（完成）
            if self.wait_false("Robot_FinishFB", timeout=busy_timeout, description=f"{description} 开始执行"):
                done = self.wait_true("Robot_FinishFB", timeout=timeout, description=f"{description} 完成")
            else:
                # 必须完整观察到 FinishFB 1→0→1 才能判定动作完成。
                # 若未进入忙状态，当前的 1 仍是触发前空闲态，不能当作完成反馈。
                logger.error(f"[{description}] 未观察到 FinishFB 变忙，PLC 未接受或未执行指令")
                done = False

        self.set_node_value("Robot_CmdTrig", 0)
        if not done:
            raise ValueError(f"{description} 未完成/未到位，Error_code={self.get_node_value('Robot_Error_code', force_read=True)}")
        logger.info(f"{description} 完成")

    @not_action
    def ensure_idle(self, timeout: float = 30.0) -> None:
        """触发前置：CmdTrig=0，并确认 Robot_FinishFB=1（就绪/空闲）。
        FinishFB=1 是正常空闲态，不做清零；若为 0(忙)则等其回到就绪。"""
        self.set_node_value("Robot_CmdTrig", 0)
        time.sleep(0.05)
        if not self.get_node_value("Robot_FinishFB", force_read=True):
            if not self.wait_true("Robot_FinishFB", timeout=timeout, description="机械手就绪(FinishFB=1)"):
                raise ValueError(f"机械手未就绪，Error_code={self.get_node_value('Robot_Error_code', force_read=True)}")

    # ==================== 工具 ====================

    @not_action
    def _station(self, key: str) -> "Station":
        st = STATIONS.get(key)
        if st is None:
            raise ValueError(f"未知工站 {key!r}，可选: {', '.join(STATIONS)}")
        return st

    @not_action
    def _target_x(self, station: str, item_type: str) -> int:
        if item_type not in (ITEM_PLATE, ITEM_BOTTLE):
            raise ValueError(f"item_type 必须为 {ITEM_PLATE!r} 或 {ITEM_BOTTLE!r}，收到 {item_type!r}")
        x = self._station(station).x_for(item_type)
        if x is None:
            raise ValueError(f"工站 {station} 未标定 {item_type} 的 X 位置（如成品放置区，请补 STATIONS）")
        return x

    @not_action
    def get_status(self) -> dict:
        return {
            "X": self.get_node_value("Robot_XPosFB", force_read=True),
            "finish": self.get_node_value("Robot_FinishFB", force_read=True),
            "cmd_type": self.get_node_value("Robot_CmdType", force_read=True),
            "cmd_trig": self.get_node_value("Robot_CmdTrig", force_read=True),
            "module_no": self.get_node_value("Robot_ModuleNoSet", force_read=True),
            # PLC 固件暂未支持以下 1.3.6 新增节点，先注释忽略，升级后再启用
            # "system_ready": self.get_node_value("System_IsReady", force_read=True),
            "reset_complete": self.get_node_value("System_ResetCompleteFB", force_read=True),
            "error_code": self.get_node_value("Robot_Error_code", force_read=True),
            "stack_column": rotary_stack.current_column(self),
        }


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    robot = RoboticArmDevice(url="opc.tcp://192.168.6.6:4840", csv_path=DEFAULT_CSV_PATH, use_subscription=False)
    time.sleep(2)
    robot.ensure_idle()
    logger.info(f"机械手连通性: {robot.get_status()}")

    rotary_stack.reset(robot)
    logger.info("旋转堆栈复位完成，已建立 R0 基准")

    def _ask_int(prompt, default):
        raw = input(f"{prompt} [{default}]: ").strip()
        return int(raw) if raw else default

    def _ask_item():
        return "bottle" if input("物料 plate/bottle [plate]: ").strip().lower() == "bottle" else "plate"

    def _pick_station(title="选择工站"):
        print(f"{title}：")
        for i, (key, st) in enumerate(STATIONS.items(), start=1):
            flag = " [旋转堆栈,需列号]" if st.rotary else ""
            print(f"  {i:>2} {key:<14} module={st.module_no} X板={st.x_plate} X瓶={st.x_bottle}{flag}")
        keys = list(STATIONS)
        idx = _ask_int("工站编号", 1)
        return keys[idx - 1] if 1 <= idx <= len(keys) else None

    def _run(fn):
        key = _pick_station()
        if not key:
            return
        item = _ask_item()
        number = _ask_int("抓取数字(工位号，旋转堆栈按此自动转列)", 1)
        fn(station=key, item_type=item, number=number)

    while True:
        print("\n请选择操作：")
        print("1 使能   2 失能   3 复位   4 回原点   5 停止")
        print("6 抓 pick   7 放 place   8 搬运 transfer   9 仅平移到工站   10 查看状态")
        print("99 退出")
        choice = input("请输入序号：").strip()
        if choice == "99":
            break
        elif choice == "1":
            robot.enable()
        elif choice == "2":
            robot.disable()
        elif choice == "3":
            robot.reset()
        elif choice == "4":
            robot.go_home()
        elif choice == "5":
            robot.stop()
        elif choice == "6":
            _run(robot.pick)
        elif choice == "7":
            _run(robot.place)
        elif choice == "8":
            src = _pick_station("选择 源 工站")
            dst = _pick_station("选择 目标 工站") if src else None
            if src and dst:
                item = _ask_item()
                robot.transfer(src, dst, item, _ask_int("source 抓取数字", 1), _ask_int("target 抓取数字", 1))
        elif choice == "9":
            key = _pick_station("平移到工站")
            if key:
                robot.move_to_station(station=key, item_type=_ask_item())
        elif choice == "10":
            print(robot.get_status())
        else:
            print("无效序号")

    robot.disconnect()
    print("退出程序。")
