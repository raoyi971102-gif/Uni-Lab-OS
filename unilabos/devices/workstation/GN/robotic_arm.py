"""
机械手 设备驱动

协议：OPC_UA协议1.3.5（节点 opcua_gn1.3.5.csv，前缀 Robot_，旋转堆栈前缀 Stack_）。

对外核心动作：transfer_item（取位置 → 放位置，程序内部自动完成 X 定位、
旋转堆栈到列、抓/放、安全复位）；另提供 pick_at / place_at 单步，以及
enable/reset/go_home/stop 维护动作与 execute_command 底层调试入口。

抓放采用「工站动作序号」协议：Robot_ModuleNoSet 选中工站 → 写该工站动作节点
Robot_<工站>=动作序号 → Robot_CmdTrig 触发 → 等该工站 Robot_<工站>_Done。
旋转堆栈：同一 OPC 服务器上直接写 Stack_ 节点（CmdType=5 旋转至目标列）。
"""

import os
import time
import logging
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.5.csv")

X_POS_TOLERANCE = 1

ITEM_PLATE = "plate"    # 孔板/板子位
ITEM_BOTTLE = "bottle"  # 瓶子位

# 旋转堆栈旋转指令、默认速度、R 到位容差
STACK_ROTATE_TO_TARGET = 5
STACK_ROTATE_SPEED = 100
STACK_R_TOLERANCE = 1


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
STATIONS: dict[str, Station] = {
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

# 旋转堆栈 列号 -> Stack_RPosSet（R 轴目标位置）；待现场标定后填入
STACK_COLUMN_TO_R: dict[int, int] = {
    # 1: 0,
    # 2: 0,
    # 3: 0,
    # ...
}


_TRANSFER_DOC = (
    "机械手搬运：从 source 取、放到 target；程序内部自动完成 X 定位、"
    "（旋转堆栈）旋转到列、抓/放、安全复位。"
    f"工站可选: {', '.join(STATIONS)}。item_type: {ITEM_PLATE}/{ITEM_BOTTLE}（决定 X 微调）。"
    "source_action/target_action 为该工站的动作序号；旋转堆栈须给 source_column/target_column。"
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
    version="3.0.0",
)
class RoboticArmDevice(OpcUaClientWithSubscription):
    """机械手设备类（OPC 前缀 Robot_，可直接驱动 Stack_ 旋转堆栈）"""

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
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs,
        )
        if csv_path:
            self.load_nodes_from_csv(csv_path)

    # ---------------- 高层动作 ----------------

    @action(description=_TRANSFER_DOC)
    def transfer_item(
        self,
        source: str,
        target: str,
        item_type: str = ITEM_PLATE,
        source_action: int = 1,
        target_action: int = 1,
        source_column: Optional[int] = None,
        target_column: Optional[int] = None,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        """取位置 → 放位置 的完整搬运。"""
        self._check_item(item_type)
        logger.info(f"机械手：搬运 {source}→{target}（{item_type}）")
        self._operate("取", source, item_type, source_action, source_column, x_speed, timeout)
        self._operate("放", target, item_type, target_action, target_column, x_speed, timeout)
        return {"success": True, "message": f"搬运完成 {source}→{target}", "item_type": item_type}

    @action(description="机械手在指定工站取料（自动 X 定位 / 旋转堆栈到列 / 抓取 / 复位）")
    def pick_at(
        self,
        station: str,
        item_type: str = ITEM_PLATE,
        action: int = 1,
        column: Optional[int] = None,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        self._check_item(item_type)
        return self._operate("取", station, item_type, action, column, x_speed, timeout)

    @action(description="机械手在指定工站放料（自动 X 定位 / 旋转堆栈到列 / 放置 / 复位）")
    def place_at(
        self,
        station: str,
        item_type: str = ITEM_PLATE,
        action: int = 1,
        column: Optional[int] = None,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        self._check_item(item_type)
        return self._operate("放", station, item_type, action, column, x_speed, timeout)

    @action(description="使能机械手 (CmdType=9)")
    def robot_enable(self, timeout: float = 60.0) -> dict:
        return self._trigger_and_clear(int(RobotCommand.ENABLE), "使能", timeout=timeout)

    @action(description="机械手复位到安全姿态 (CmdType=7)")
    def robot_reset(self, timeout: float = 120.0) -> dict:
        return self._trigger_and_clear(int(RobotCommand.RESET), "复位", timeout=timeout)

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
            return self._move_x_absolute(x_pos, x_speed, timeout=timeout)
        if cmd in (int(RobotCommand.RESET), int(RobotCommand.DISABLE), int(RobotCommand.ENABLE)):
            return self._trigger_and_clear(cmd, label, timeout=timeout)
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
    def _operate(self, mode, station_key, item_type, action_index, column, x_speed, timeout) -> dict:
        """单工站取/放：移动到工站前 →（旋转堆栈到列）→ 抓/放 → 安全复位。"""
        st = self._station(station_key)
        target_x = st.x_for(item_type)
        logger.info(f"机械手：{mode} @{station_key}（module={st.module_no} X={target_x} {item_type}）")

        self._move_x_absolute(target_x, x_speed, timeout=timeout)

        if st.rotary:
            if column is None:
                raise ValueError(f"{station_key} 为旋转堆栈，必须提供 column（列号）")
            self._rotate_stack_to_column(column, timeout=timeout)

        self._run_station_action(st, action_index, mode, timeout=timeout)
        self._trigger_and_clear(int(RobotCommand.RESET), f"{mode}后复位", timeout=timeout)
        return {"success": True, "message": f"{mode}@{station_key} 完成", "x": target_x}

    @not_action
    def _run_station_action(self, station: Station, action_index: int, mode: str, timeout: float) -> None:
        """工站动作序号协议：ModuleNoSet → 工站动作节点 → CmdTrig → 等工站 _Done。"""
        desc = f"{mode} {station.action_node}=动作{action_index}"
        logger.info(f"机械手：{desc}（ModuleNo={station.module_no}）...")
        self.ensure_idle()
        self.set_node_value("Robot_ModuleNoSet", station.module_no)
        self.set_node_value(station.action_node, action_index)
        self.set_node_value("Robot_CmdTrig", 1)
        if not self._wait_until_true(station.done_node, timeout=timeout, description=f"{desc}完成"):
            err = self.get_node_value("Robot_Error_code", force_read=True)
            self._log_status(f"{desc}失败")
            raise ValueError(f"{desc}失败，{station.done_node} 未响应，Error_code={err}")
        self.set_node_value("Robot_CmdTrig", 0)
        self.set_node_value(station.action_node, 0)
        logger.info(f"{desc}完成")

    @not_action
    def _rotate_stack_to_column(self, column: int, timeout: float) -> None:
        """驱动 Stack_ 节点将旋转堆栈转到指定列；抓取前先校验到位。

        判据：Stack_CompleteFB=1 且 Stack_RPosFB 落在目标 R±容差内并稳定，
        任一不满足即报错，不允许继续抓取。
        """
        r_pos = STACK_COLUMN_TO_R.get(column)
        if r_pos is None:
            raise ValueError(f"未配置堆栈列 {column} 的 R 位置，请补 STACK_COLUMN_TO_R")
        logger.info(f"旋转堆栈：旋转至列 {column}（Stack_RPosSet={r_pos}）...")
        self.set_node_value("Stack_CmdTrig", 0)
        self.set_node_value("Stack_RPosSet", r_pos)
        self.set_node_value("Stack_RSpeed", STACK_ROTATE_SPEED)
        self.set_node_value("Stack_CmdType", STACK_ROTATE_TO_TARGET)
        self.set_node_value("Stack_CmdTrig", 1)
        reached = self._wait_stack_reached(r_pos, timeout=timeout, description=f"堆栈旋转至列{column}")
        self.set_node_value("Stack_CmdTrig", 0)
        if not reached:
            raise ValueError(f"堆栈旋转至列{column}未到位，禁止抓取（目标R={r_pos}）")
        logger.info(f"旋转堆栈：已到位列 {column}（RPosFB≈{r_pos}）")

    @not_action
    def _wait_stack_reached(self, target_r, tolerance=STACK_R_TOLERANCE, stable_samples=3, timeout=120.0, interval=0.2, description="") -> bool:
        """等旋转堆栈到位：Stack_CompleteFB=1 且 Stack_RPosFB 在容差内连续稳定。"""
        desc = description or f"堆栈R到达{target_r}"
        logger.info(f"等待 {desc}（Stack_CompleteFB + Stack_RPosFB±{tolerance}）...")
        start = time.time()
        stable = 0
        while time.time() - start < timeout:
            complete = self.get_node_value("Stack_CompleteFB", force_read=True)
            r = self.get_node_value("Stack_RPosFB", force_read=True)
            if complete and r is not None and abs(r - target_r) <= tolerance:
                stable += 1
                if stable >= stable_samples:
                    logger.info(f"✓ {desc}（RPosFB={r} CompleteFB={complete}）")
                    return True
            else:
                stable = 0
            time.sleep(interval)
        r = self.get_node_value("Stack_RPosFB", force_read=True)
        complete = self.get_node_value("Stack_CompleteFB", force_read=True)
        logger.error(f"✗ {desc} 超时（RPosFB={r} 目标={target_r} CompleteFB={complete}）")
        return False

    @not_action
    def _trigger_and_clear(self, cmd_type: int, description: str, timeout: float = 180.0) -> dict:
        """CmdType 通道：ensure_idle → CmdType+CmdTrig → 等 FinishFB → CmdTrig=0 → 清 Finish。"""
        self.ensure_idle()
        self.set_node_value("Robot_CmdType", int(cmd_type))
        self.set_node_value("Robot_CmdTrig", 1)
        if not self._wait_until_true("Robot_FinishFB", timeout=timeout, description=f"{description}完成"):
            self._log_status(f"{description}失败")
            err = self.get_node_value("Robot_Error_code", force_read=True)
            raise ValueError(f"{description}失败，FinishFB 未响应，Error_code={err}")
        self.set_node_value("Robot_CmdTrig", 0)
        self._clear_finish()
        logger.info(f"{description}完成")
        return {"success": True, "message": f"{description}完成", "cmd_type": int(cmd_type)}

    @not_action
    def _move_x_absolute(self, target_x: int, x_speed: int, tolerance: int = X_POS_TOLERANCE, timeout: float = 120.0) -> dict:
        """X 点动到绝对位置：按当前 X 选 CmdType 1/2，等 XPosFB 到位。"""
        current = self.get_x_position()
        if abs(current - target_x) <= tolerance:
            logger.info(f"机械手：X 已在 {target_x} 附近（current={current}）")
            return {"success": True, "message": f"X 已在 {target_x} 附近"}

        cmd = int(RobotCommand.X_RIGHT if current < target_x else RobotCommand.X_LEFT)
        desc = f"X{'向右' if current < target_x else '向左'}移至{target_x}"
        logger.info(f"机械手：{desc}（current={current}）...")
        self.ensure_idle()
        self.set_node_value("Robot_XPosSet", target_x)
        self.set_node_value("Robot_XSpeedSet", x_speed)
        self.set_node_value("Robot_CmdType", cmd)
        self.set_node_value("Robot_CmdTrig", 1)
        if not self._wait_x_reach(target_x, tolerance=tolerance, description=desc, timeout=timeout):
            self._log_status(f"{desc}失败")
            raise ValueError(f"{desc}失败，当前 X={self.get_x_position()}")
        self.set_node_value("Robot_CmdTrig", 0)
        if self.get_node_value("Robot_FinishFB", force_read=True):
            self._clear_finish()
        return {"success": True, "message": f"{desc}完成", "cmd_type": cmd}

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
        if not self._wait_until_false("Robot_FinishFB", timeout=timeout, description="FinishFB清除"):
            err = self.get_node_value("Robot_Error_code", force_read=True)
            raise ValueError(f"FinishFB 清除失败，Error_code={err}")
        self.set_node_value("Robot_CmdTrig", 0)

    @not_action
    def _go_home(self, timeout: float = 180.0) -> dict:
        """回原点：Robot_gohome=1，等 Robot_gohome_done=1。"""
        logger.info("机械手：回原点...")
        self.ensure_idle()
        self.set_node_value("Robot_gohome", 1)
        if not self._wait_until_true("Robot_gohome_done", timeout=timeout, description="回原点完成"):
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
    def _station(self, key: str) -> Station:
        st = STATIONS.get(key)
        if st is None:
            raise ValueError(f"未知工站 {key!r}，可选: {', '.join(STATIONS)}")
        return st

    @not_action
    def _check_item(self, item_type: str) -> None:
        if item_type not in (ITEM_PLATE, ITEM_BOTTLE):
            raise ValueError(f"item_type 必须是 {ITEM_PLATE!r} 或 {ITEM_BOTTLE!r}，收到 {item_type!r}")

    @not_action
    def _wait_x_reach(self, target_x, tolerance=X_POS_TOLERANCE, stable_samples=3, timeout=120.0, interval=0.2, description="") -> bool:
        desc = description or f"X到达{target_x}"
        logger.info(f"等待 {desc}（容差±{tolerance}，轮询 Robot_XPosFB）...")
        start = time.time()
        stable = 0
        while time.time() - start < timeout:
            x = self.get_x_position()
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
    def _wait_until_true(self, node_name, timeout=180.0, interval=0.2, description=None) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（轮询 {node_name}）...")
        start = time.time()
        while time.time() - start < timeout:
            if self.get_node_value(node_name, force_read=True):
                logger.info(f"✓ {desc}")
                return True
            time.sleep(interval)
        value = self.get_node_value(node_name, force_read=True)
        logger.error(f"✗ {desc} 超时（{node_name}={value!r}）")
        return False

    @not_action
    def _wait_until_false(self, node_name, timeout=30.0, interval=0.2, description=None) -> bool:
        desc = description or node_name
        start = time.time()
        while time.time() - start < timeout:
            if not self.get_node_value(node_name, force_read=True):
                return True
            time.sleep(interval)
        value = self.get_node_value(node_name, force_read=True)
        logger.error(f"✗ {desc} 超时（{node_name}={value!r}）")
        return False

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

    @not_action
    def _log_status(self, prefix: str = "状态") -> None:
        s = self.get_status()
        logger.info(
            f"{prefix}: X={s['X']} XSet={s['x_set']} Finish={s['finish']} "
            f"CmdType={s['cmd_type']} CmdTrig={s['cmd_trig']} ModuleNo={s['module_no']} "
            f"GoHomeDone={s['gohome_done']} Error={s['error_code']} Running={s['running_status']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    ROBOT_URL = "opc.tcp://192.168.6.6:4840"
    robot = RoboticArmDevice(url=ROBOT_URL, csv_path=DEFAULT_CSV_PATH, use_subscription=False)

    time.sleep(2)
    robot.ensure_idle()
    logger.info(f"机械手连通性: {robot.get_status()}")

    while True:
        print("请选择操作：")
        print(f"1  使能 (CmdType=9)")
        print(f"2  复位 (CmdType=7)")
        print(f"3  回原点")
        print(f"4  停止")
        print(f"5  取: 旋转堆栈 板子位 列1 动作1  (pick_at)")
        print(f"6  放: 离心机 板子位 动作1        (place_at)")
        print(f"7  搬运: 旋转堆栈->离心机 板子位   (transfer_item)")
        print(f"11 X 移至 3274")
        print(f"12 X 移至 100")
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
            robot.pick_at(station="stack", item_type="plate", action=1, column=1)
        elif choice == "6":
            robot.place_at(station="centrifuge", item_type="plate", action=1)
        elif choice == "7":
            robot.transfer_item(source="stack", target="centrifuge", item_type="plate", source_column=1)
        elif choice == "11":
            robot.execute_command(cmd_type=1, x_pos=3274, x_speed=300)
        elif choice == "12":
            robot.execute_command(cmd_type=1, x_pos=100, x_speed=300)
        else:
            print("无效的操作序号，请重新输入。")

    robot.disconnect()
    print("退出程序。")
