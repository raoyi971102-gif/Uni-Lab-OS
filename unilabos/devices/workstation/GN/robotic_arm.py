"""
机械手 设备驱动

参照 centrifuge.py / locking_mechanism.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 opcua_gn1.3.3.csv（OPC UA 协议 1.3.5）中「机械手」(前缀 Robot_)。
各动作点位根据「机械手夹取放回堆栈位置1.yaml」写死。

指令类型 (Robot_CmdType)：
    1=X向左 2=X向右
    3=机械手去目标位置目标板位夹料 4=机械手去目标位置目标板位放料
    7=复位 8=失能 9=使能
    10=动作命令完成反馈后复位工站动作（1.3.5 新增）

注意：Robot_FinishFB 在 X 点动（CmdType 1/2）时可能始终为 0，X 到位应读 Robot_XPosFB 判断。
CmdType 3/4 优先等待对应工站 Robot_*_Done 反馈。

YAML 字段 → CSV 节点映射（1.3.5）：
    XPos           → Robot_XPosSet
    XSpeed         → Robot_XSpeedSet
    ModuleNo       → Robot_ModuleNoSet（见 ROBOT_MODULE_NO）
    Action         → Robot_Stack / Robot_Oven 等工站运行动作节点（替代 Robot_ActionSet）
    ManipulatorCmd → Robot_CmdType（GripperTake=3, GripperPut=4）
"""

import os
import time
import logging
import threading
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

# 工站名称 → Robot_ModuleNoSet（设备从左到右排列）
ROBOT_MODULE_NO = {
    "StandardOven": 1,
    "Lock": 2,
    "QuickCarrierExchange": 3,
    "Centrifuge": 4,
    "VacuumOven": 5,
    "N9320": 6,
    "CentrifugeTubeLiquid": 7,
    "Stack": 8,
    "SolidFeed": 9,
    "RobotRailStack": 10,
}

# 模块号 → (工站运行动作节点, 工站动作完成反馈节点) — OPC UA 1.3.5
MODULE_STATION_NODES = {
    1: ("Robot_Oven", "Robot_Oven_Done"),
    2: ("Robot_Locking_mechanism", "Robot_Locking_mechanism_Done"),
    3: ("Robot_Quick_change_mechanism", "Robot_Quick_change_mechanism_Done"),
    4: ("Robot_Centrifuge", "Robot_Centrifuge_Done"),
    5: ("Robot_Vacuum_oven", "Robot_Vacuum_oven_Done"),
    6: ("Robot_9320", "Robot_Nine_9320_Done"),
    7: ("Robot_Centrifuge_tube_liquid_handling", "Robot_Centrifuge_tube_liquid_handling_Done"),
    8: ("Robot_Stack", "Robot_Stack_Done"),
    9: ("Robot_Add_solid_sample", "Robot_Add_solid_sample_Done"),
    10: (None, None),  # 1.3.5 无独立节点，仅 ModuleNoSet
}


class RobotCommand(int, Enum):
    """机械手指令类型 (Robot_CmdType)"""
    X_LEFT = 1
    X_RIGHT = 2
    PICK = 3
    PLACE = 4
    RESET = 7
    DISABLE = 8
    ENABLE = 9
    ACTION_CMD_RESET = 10   # 1.3.5：工站动作完成后的动作命令复位


@device(
    id="gn_robotic_arm",
    display_name="机械手",
    category=["workstation"],
    description="GN 机械手：按测试流程完成 堆栈夹取/放回，OPC UA 1.3.5 控制",
    icon="",
)
class RoboticArmDevice(OpcUaClientWithSubscription):
    """机械手设备类（OPC 前缀 Robot_）"""

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

    # ==================== 动作函数（点位写死，来自机械手夹取放回堆栈位置1 yaml） ====================

    @action(auto_prefix=True, description="1.堆栈位置1夹爪夹料")
    def gripper_take_stack_slot1(self, wait: bool = True, move_x_first: bool = True) -> dict:
        logger.info("机械手：堆栈位置1夹爪夹料...")
        if move_x_first:
            self.move_x_to(3274)
        self._apply_setpoints(x_pos=3274, module_no=ROBOT_MODULE_NO["Stack"], station_action=1, x_speed=300)
        return self._trigger(RobotCommand.PICK, "堆栈位置1夹爪夹料", wait=wait,
                             module_no=ROBOT_MODULE_NO["Stack"])

    @action(auto_prefix=True, description="2.堆栈位置1夹爪放料")
    def gripper_put_stack_slot1(self, wait: bool = True, move_x_first: bool = False) -> dict:
        logger.info("机械手：堆栈位置1夹爪放料...")
        if move_x_first:
            self.move_x_to(3274)
        self._apply_setpoints(x_pos=3274, module_no=ROBOT_MODULE_NO["Stack"], station_action=1, x_speed=300)
        return self._trigger(RobotCommand.PLACE, "堆栈位置1夹爪放料", wait=wait,
                             module_no=ROBOT_MODULE_NO["Stack"])

    @action(auto_prefix=True, description="机械手复位")
    def reset(self) -> dict:
        logger.info("机械手：复位...")
        return self._trigger(RobotCommand.RESET, "复位", wait=True)

    @action(auto_prefix=True, description="机械手使能")
    def enable(self) -> dict:
        return self._trigger(RobotCommand.ENABLE, "使能", wait=True)

    @action(auto_prefix=True, description="机械手失能")
    def disable(self) -> dict:
        return self._trigger(RobotCommand.DISABLE, "失能", wait=True)

    @action(auto_prefix=True, description="机器人回原点")
    def go_home(self, wait: bool = True) -> dict:
        logger.info("机械手：回原点...")
        self.set_node_value("Robot_gohome", 1)
        if not wait:
            return {"success": True, "message": "回原点已下发"}
        if self._node_resolved("Robot_gohome_done"):
            if self._wait_until_true("Robot_gohome_done", timeout=180.0, description="回原点完成"):
                self.set_node_value("Robot_gohome", 0)
                return {"success": True, "message": "回原点完成"}
            raise ValueError("回原点失败")
        return self._trigger(RobotCommand.RESET, "回原点(无gohome_done节点，走复位)", wait=True)

    @action(auto_prefix=True, description="机器人停止")
    def stop(self) -> dict:
        logger.info("机械手：停止...")
        self.set_node_value("Robot_STOP", 1)
        time.sleep(0.2)
        self.set_node_value("Robot_STOP", 0)
        return {"success": True, "message": "停止命令已下发"}

    # ==================== 通用夹取/放置（可指定工站与板位） ====================

    @action(auto_prefix=True, description="去目标工站目标板位夹料")
    def pick(self, module_no, station_action: int = 1, x_pos: int = 3274,
             x_speed: int = 300, wait: bool = True) -> dict:
        module = self._resolve_module_no(module_no)
        logger.info(f"机械手：夹料 module={module} station_action={station_action}...")
        self._apply_setpoints(x_pos=x_pos, module_no=module, station_action=station_action, x_speed=x_speed)
        return self._trigger(RobotCommand.PICK, f"夹料(module={module}, action={station_action})",
                             wait=wait, module_no=module)

    @action(auto_prefix=True, description="去目标工站目标板位放料")
    def place(self, module_no, station_action: int = 1, x_pos: int = 3274,
              x_speed: int = 300, wait: bool = True) -> dict:
        module = self._resolve_module_no(module_no)
        logger.info(f"机械手：放料 module={module} station_action={station_action}...")
        self._apply_setpoints(x_pos=x_pos, module_no=module, station_action=station_action, x_speed=x_speed)
        return self._trigger(RobotCommand.PLACE, f"放料(module={module}, action={station_action})",
                             wait=wait, module_no=module)

    # ==================== X 轴移动（按目标位置自动选向左/向右） ====================

    @action(auto_prefix=True, description="X 轴移动到目标位置（自动选 CmdType 1/2）")
    def move_x_to(self, x_pos: int = 3274, x_speed: int = 300, tolerance: int = 100) -> dict:
        current = self.get_x_position()
        if abs(current - x_pos) <= tolerance:
            logger.info(f"机械手：X 已在目标附近 current={current} target={x_pos}")
            return {"success": True, "message": f"X 已在 {x_pos} 附近"}

        if current < x_pos:
            cmd = RobotCommand.X_RIGHT
            desc = f"X向右至{x_pos}"
        else:
            cmd = RobotCommand.X_LEFT
            desc = f"X向左至{x_pos}"

        logger.info(f"机械手：{desc}（current={current}）...")
        self.set_node_value("Robot_XPosSet", x_pos)
        self.set_node_value("Robot_XSpeedSet", x_speed)
        self._prepare_trigger()
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        logger.info(f"已触发: CmdType={int(cmd)} CmdTrig=1 XPosSet={x_pos}")

        if not self._wait_x_reach(x_pos, tolerance=tolerance, description=desc):
            raise ValueError(f"{desc} 失败，X 未到位（当前={self.get_x_position()}）")
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        self._log_status(f"{desc}后")
        return {"success": True, "message": f"{desc}完成"}

    @action(auto_prefix=True, description="单点调试：X 移动到堆栈位 3274")
    def jog_x_left(self) -> dict:
        return self.move_x_to(3274)

    @action(auto_prefix=True, description="单点调试：X 移动到 100")
    def jog_x_right(self) -> dict:
        return self.move_x_to(100)

    @action(auto_prefix=True, description="通用指令：按 Robot_CmdType 执行任意指令")
    def execute_command(self, cmd_type: int, module_no=None, station_action: int = 0,
                        x_pos: int = 0, x_speed: int = 300, wait: bool = True,
                        timeout: float = 180.0) -> dict:
        module = None
        if module_no is not None:
            module = self._resolve_module_no(module_no)
            self._apply_setpoints(
                x_pos=x_pos,
                module_no=module,
                station_action=station_action,
                x_speed=x_speed,
            )
        return self._trigger(int(cmd_type), f"指令{cmd_type}", wait=wait, timeout=timeout,
                             wait_x_target=x_pos if int(cmd_type) in (1, 2) else None,
                             module_no=module)

    # ==================== 内部逻辑 ====================

    @not_action
    def _node_resolved(self, node_name: str) -> bool:
        if node_name in self._name_mapping:
            return self._name_mapping[node_name] in self._node_registry
        return node_name in self._node_registry

    @not_action
    def _get_station_nodes(self, module_no: int) -> tuple[Optional[str], Optional[str]]:
        return MODULE_STATION_NODES.get(int(module_no), (None, None))

    @not_action
    def _resolve_module_no(self, module_no) -> int:
        if isinstance(module_no, str):
            if module_no not in ROBOT_MODULE_NO:
                raise ValueError(f"未知工站 {module_no}，可选: {list(ROBOT_MODULE_NO.keys())}")
            return ROBOT_MODULE_NO[module_no]
        return int(module_no)

    @not_action
    def _apply_setpoints(self, x_pos: int, module_no: int, station_action: int, x_speed: int) -> None:
        """写入 ModuleNo / 工站动作节点 / X 位置 / 速度（1.3.5 无 Robot_ActionSet）"""
        self.set_node_value("Robot_ModuleNoSet", module_no)
        self.set_node_value("Robot_XPosSet", x_pos)
        self.set_node_value("Robot_XSpeedSet", x_speed)
        action_node, _ = self._get_station_nodes(module_no)
        if action_node and self._node_resolved(action_node):
            self.set_node_value(action_node, station_action)
            logger.info(
                f"已写入 setpoint: ModuleNo={module_no} {action_node}={station_action} "
                f"XPos={x_pos} XSpeed={x_speed}"
            )
        else:
            logger.info(
                f"已写入 setpoint: ModuleNo={module_no} XPos={x_pos} XSpeed={x_speed} "
                f"（模块 {module_no} 无工站动作节点）"
            )

    @not_action
    def _prepare_trigger(self) -> None:
        complete = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        if complete:
            logger.warning("FinishFB=1，先拉低 CmdTrig")
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        time.sleep(0.1)

    @not_action
    def _reset_action_command(self) -> None:
        """1.3.5 CmdType=10：工站动作完成后复位动作命令"""
        if not self._node_resolved(self.CMD_TYPE_NODE):
            return
        logger.info("机械手：复位工站动作命令 (CmdType=10)...")
        self._prepare_trigger()
        self.set_node_value(self.CMD_TYPE_NODE, int(RobotCommand.ACTION_CMD_RESET))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        time.sleep(0.2)
        self.set_node_value(self.CMD_TRIG_NODE, 0)

    @not_action
    def _trigger(self, cmd_type, description: str, wait: bool = True,
                 timeout: float = 180.0, wait_x_target: int = None,
                 module_no: int = None) -> dict:
        self._prepare_trigger()
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        logger.info(f"已触发: CmdType={int(cmd_type)} CmdTrig=1")
        if not wait:
            time.sleep(0.2)
            self._log_status(f"{description}下发后")
            return {"success": True, "message": f"{description}已下发（不等待完成）"}
        if wait_x_target is not None:
            ok = self._wait_x_reach(wait_x_target, description=description, timeout=timeout)
            self.set_node_value(self.CMD_TRIG_NODE, 0)
            if ok:
                self._log_status(f"{description}后")
                return {"success": True, "message": f"{description}完成"}
            raise ValueError(f"{description} 失败，X 未到位")
        return self._wait_motion_complete(description, timeout=timeout, module_no=module_no,
                                          reset_action=int(cmd_type) in (3, 4))

    @not_action
    def _wait_motion_complete(self, description: str, timeout: float = 180.0,
                              module_no: int = None, reset_action: bool = False) -> dict:
        """等待夹/放料完成：优先工站 *_Done，其次 FinishFB"""
        ok = False
        _, done_node = self._get_station_nodes(module_no) if module_no else (None, None)
        if done_node and self._node_resolved(done_node):
            ok = self._wait_until_true(done_node, timeout=timeout,
                                       description=f"{description}({done_node})")
        if not ok:
            ok = self._wait_finish_or_timeout(description, timeout=timeout)

        if ok:
            self.set_node_value(self.CMD_TRIG_NODE, 0)
            if reset_action:
                self._reset_action_command()
            self._wait_until_false(self.COMPLETE_NODE, timeout=5.0,
                                   description=f"{description}FinishFB复位", log_error=False)
            logger.info(f"{description}完成")
            self._log_status(f"{description}后")
            return {"success": True, "message": f"{description}完成"}
        raise ValueError(f"{description}失败，动作未完成")

    @not_action
    def _wait_finish_or_timeout(self, description: str, timeout: float = 180.0,
                                fallback_wait: float = 5.0) -> bool:
        if self._wait_until_true(self.COMPLETE_NODE, timeout=fallback_wait,
                                 description=f"{description}完成(FinishFB)"):
            return True
        if self._wait_until_true(self.COMPLETE_NODE, timeout=timeout - fallback_wait,
                                 description=f"{description}完成(FinishFB续等)"):
            return True
        logger.warning(f"FinishFB 未置位，固定等待 {fallback_wait}s 后继续（{description}）")
        time.sleep(fallback_wait)
        return True

    @not_action
    def _wait_x_reach(self, target_x: int, tolerance: int = 100, stable_samples: int = 3,
                      timeout: float = 120.0, interval: float = 0.2, description: str = "") -> bool:
        desc = description or f"X到位({target_x})"
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
        logger.error(f"✗ {desc} 超时（{timeout}s，当前 X={self.get_x_position()}，目标={target_x}）")
        return False

    @not_action
    def _wait_until_true(self, node_name: str, timeout: float = 180.0,
                         interval: float = 0.2, description: str = None) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（轮询 {node_name}）...")
        start = time.time()
        while time.time() - start < timeout:
            if self.get_node_value(node_name, force_read=True):
                logger.info(f"✓ {desc}")
                return True
            time.sleep(interval)
        logger.error(f"✗ 等待 {desc} 超时（{timeout}s）")
        return False

    @not_action
    def _wait_until_false(self, node_name: str, timeout: float = 180.0,
                          interval: float = 0.2, description: str = None,
                          log_error: bool = True) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（轮询 {node_name}）...")
        start = time.time()
        while time.time() - start < timeout:
            if not self.get_node_value(node_name, force_read=True):
                logger.info(f"✓ {desc}")
                return True
            time.sleep(interval)
        if log_error:
            logger.error(f"✗ 等待 {desc} 超时（{timeout}s）")
        return False

    # ==================== 整体测试流程 ====================

    @not_action
    def run_test_flow(self, wait: bool = True) -> dict:
        logger.info("机械手：开始堆栈位置1测试流程...")
        try:
            self.enable()
        except ValueError as e:
            logger.warning(f"使能失败（可能已使能）: {e}")
        self.jog_x_left()
        self.gripper_take_stack_slot1(wait=wait, move_x_first=False)
        self.gripper_put_stack_slot1(wait=wait, move_x_first=False)
        logger.info("机械手：堆栈位置1测试流程完成")
        return {"success": True, "message": "机械手堆栈位置1测试流程完成"}

    # ==================== 状态读取 ====================

    @not_action
    def get_x_position(self) -> int:
        return self.get_node_value("Robot_XPosFB", force_read=True)

    @not_action
    def get_status(self) -> dict:
        module_no = self.get_node_value("Robot_ModuleNoSet", force_read=True)
        action_node, done_node = self._get_station_nodes(module_no or 0)
        station_action = None
        station_done = None
        if action_node and self._node_resolved(action_node):
            station_action = self.get_node_value(action_node, force_read=True)
        if done_node and self._node_resolved(done_node):
            station_done = self.get_node_value(done_node, force_read=True)
        status = {
            "X": self.get_x_position(),
            "finish": self.get_node_value(self.COMPLETE_NODE, force_read=True),
            "running": self._safe_read("Robot_Running_Status"),
            "error_code": self._safe_read("Robot_Error_code"),
            "cmd_type": self.get_node_value(self.CMD_TYPE_NODE, force_read=True),
            "cmd_trig": self.get_node_value(self.CMD_TRIG_NODE, force_read=True),
            "module_no": module_no,
            "station_action_node": action_node,
            "station_action": station_action,
            "station_done": station_done,
            "x_pos_set": self.get_node_value("Robot_XPosSet", force_read=True),
        }
        return status

    @not_action
    def _safe_read(self, node_name: str):
        if not self._node_resolved(node_name):
            return None
        try:
            return self.get_node_value(node_name, force_read=True)
        except Exception:
            return None

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        s = self.get_status()
        logger.info(
            f"{prefix}: X={s['X']} Finish={s['finish']} Running={s['running']} "
            f"ModuleNo={s['module_no']} StationAction={s['station_action']} "
            f"StationDone={s['station_done']} XSet={s['x_pos_set']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    ROBOT_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 10.0

    robot = RoboticArmDevice(
        url=ROBOT_URL,
        csv_path=DEFAULT_CSV_PATH,
        use_subscription=False,
    )

    time.sleep(2)
    init_status = robot.get_status()
    logger.info(f"机械手连通性测试: {init_status}")
    if init_status.get("finish") == 1:
        logger.warning("FinishFB=1，建议先选 5 复位")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                robot._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    status_log_thread = threading.Thread(
        target=_status_log_worker, daemon=True, name="RoboticArmStatusLog"
    )
    status_log_thread.start()
    logger.info(f"已启动状态反馈实时日志（间隔 {STATUS_LOG_INTERVAL}s，无订阅）")

    while True:
        print("请选择操作：")
        print("0  读取状态（连通性测试）")
        print("1  堆栈位置1夹爪夹料（先X点动，等待完成）")
        print("2  堆栈位置1夹爪放料（等待完成）")
        print("3  堆栈位置1夹爪夹料（不先X点动）")
        print("4  堆栈位置1夹爪放料（不先X点动）")
        print("5  机械手复位")
        print("6  机械手使能")
        print("7  机械手失能")
        print("8  机器人回原点")
        print("9  机器人停止")
        print("--- 单点调试 ---")
        print("11 X移至堆栈位3274")
        print("12 X移至100")
        print("10 工站动作命令复位(CmdType=10)")
        print("98 整体测试流程（使能→X→夹料→放料）")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "0":
            print(f"当前状态: {robot.get_status()}")
        elif choice == "1":
            robot.gripper_take_stack_slot1(wait=True, move_x_first=True)
        elif choice == "2":
            robot.gripper_put_stack_slot1(wait=True)
        elif choice == "3":
            robot.gripper_take_stack_slot1(wait=True, move_x_first=False)
        elif choice == "4":
            robot.gripper_put_stack_slot1(wait=True, move_x_first=False)
        elif choice == "5":
            robot.reset()
        elif choice == "6":
            robot.enable()
        elif choice == "7":
            robot.disable()
        elif choice == "8":
            robot.go_home()
        elif choice == "9":
            robot.stop()
        elif choice == "10":
            robot._reset_action_command()
        elif choice == "11":
            robot.jog_x_left()
        elif choice == "12":
            robot.jog_x_right()
        elif choice == "98":
            robot.run_test_flow(wait=True)
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    status_log_thread.join(timeout=STATUS_LOG_INTERVAL + 1)
    robot.disconnect()
    print("退出程序。")
