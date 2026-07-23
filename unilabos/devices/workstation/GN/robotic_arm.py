"""
机械手 设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「机械手」；节点：opcua_gn1.3.3.csv（前缀 Robot_）。

对外仅暴露 execute_command（Robot_CmdType + 写参）；测试流程预设供本地调试。
伪指令：100=回原点 101=停止（非 PLC 枚举，在 execute_command 内路由）。
"""

import os
import time
import logging
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

X_POS_TOLERANCE = 100
PICK_PLACE_NODE = "Robot_Pick up or place"

# OPC 1.3.3 Robot_CmdType + 伪指令
ROBOT_CMD_LABELS = {
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

# 工站预设（destination）；高级用户可显式传 module_no + x_pos 覆盖
ROBOT_DESTINATIONS: dict[str, tuple[int, int, int]] = {
    "stack_plate": (8, 3274, 1),
    "stack_reagent": (8, 3274, 2),
    "stack_bottle": (8, 2473, 1),
    "solid_feed": (7, 6318, 1),
    "tube_handler": (5, -1726, 1),
    "prcxi": (4, -3926, 1),
    "magnetic_stirrer": (2, -10478, 1),
    "centrifuge": (3, -8582, 1),
    "oven": (1, -13278, 1),
}

# 堆栈位置1测试流程预设
TEST_FLOW_PRESETS = [
    ("1.堆栈位置1夹料", RobotCommand.PICK, dict(
        module_no=6, stack=1, x_pos=3274, x_speed=300, pick_place=1,
    )),
    ("2.堆栈位置1放料", RobotCommand.PLACE, dict(
        module_no=6, stack=1, x_pos=3274, x_speed=300, pick_place=0,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 Robot_CmdType 执行 OPC 1.3.3 指令。"
    "1=X左 2=X右 3=夹料 4=放料 7=复位 8=失能 9=使能 10=动作命令复位 "
    "100=回原点 101=停止。"
    "CmdType 3/4 需 module_no/stack/x_pos/x_speed/pick_place；CmdType 1/2 需 x_pos/x_speed。"
)


@device(
    id="gn_robotic_arm",
    display_name="机械手",
    category=["workstation"],
    description="GN 机械手：OPC UA 1.3.3，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class RoboticArmDevice(OpcUaClientWithSubscription):
    """机械手设备类（OPC 前缀 Robot_）"""

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
        self._pick_place_available = self._has_opcua_node(PICK_PLACE_NODE)
        if not self._pick_place_available:
            logger.warning(
                "PLC 未部署 Robot_Pick up or place 节点，夹/放料将仅依赖 CmdType 3/4"
            )

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        module_no: Optional[int] = None,
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
            cmd_type=int(RobotCommand.PICK),
            module_no=eff_module,
            x_pos=eff_x,
            stack=eff_stack,
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

        if cmd_type in (int(RobotCommand.PICK), int(RobotCommand.PLACE)):
            if module_no is not None:
                self.set_node_value("Robot_ModuleNoSet", module_no)
            if stack is not None:
                self.set_node_value("Robot_Stack", stack)
            if x_pos is not None:
                self.set_node_value("Robot_XPosSet", x_pos)
            if x_speed is not None:
                self.set_node_value("Robot_XSpeedSet", x_speed)
            if pick_place is not None and self._pick_place_available:
                self.set_node_value(PICK_PLACE_NODE, pick_place)
            return self._trigger_and_clear(cmd_type, description, timeout=timeout)

        if cmd_type in (
            int(RobotCommand.RESET),
            int(RobotCommand.DISABLE),
            int(RobotCommand.ENABLE),
        ):
            return self._trigger_and_clear(cmd_type, description, timeout=timeout)

        if cmd_type == int(RobotCommand.ACTION_CMD_RESET):
            self.ensure_idle()
            self._log_status("动作命令复位后")
            return {"success": True, "message": "动作命令复位完成", "cmd_type": cmd_type}

        if cmd_type == CMD_GO_HOME:
            return self._go_home(timeout=timeout)

        if cmd_type == CMD_STOP:
            return self._stop()

        raise ValueError(f"不支持的 CmdType={cmd_type}")

    @not_action
    def _has_opcua_node(self, name: str) -> bool:
        chinese_name = self._name_mapping.get(name, name)
        return chinese_name in self._node_registry

    @not_action
    def _get_node_value_optional(self, name: str):
        if not self._has_opcua_node(name):
            return None
        return self.get_node_value(name, force_read=True)

    @not_action
    def ensure_idle(self) -> None:
        """连接后/触发前：CmdTrig=0，FinishFB=1 时发 CmdType=10 清除"""
        self.set_node_value("Robot_CmdTrig", 0)
        time.sleep(0.05)
        self._clear_finish()

    @not_action
    def _clear_finish(self, timeout: float = 30.0) -> None:
        """CmdType=10 清除 Robot_FinishFB"""
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
    def _trigger_and_clear(self, cmd_type: int, description: str, timeout: float = 180.0) -> dict:
        """ensure_idle → CmdType+CmdTrig → 等 FinishFB=1 → CmdTrig=0 → CmdType=10"""
        self.ensure_idle()
        self.set_node_value("Robot_CmdType", int(cmd_type))
        self.set_node_value("Robot_CmdTrig", 1)
        if not self._wait_until_true(
            "Robot_FinishFB", timeout=timeout, description=f"{description}完成"
        ):
            self._log_status(f"{description}失败")
            err = self.get_node_value("Robot_Error_code", force_read=True)
            raise ValueError(f"{description}失败，FinishFB 未响应，Error_code={err}")
        self.set_node_value("Robot_CmdTrig", 0)
        self._clear_finish()
        logger.info(f"{description}完成")
        self._log_status(f"{description}后")
        return {"success": True, "message": f"{description}完成", "cmd_type": int(cmd_type)}

    @not_action
    def _move_x_absolute(
        self,
        target_x: int,
        x_speed: int,
        tolerance: int = X_POS_TOLERANCE,
        timeout: float = 120.0,
    ) -> dict:
        """X 点动：Robot_XPosSet=绝对目标，按当前 X 选 CmdType 1/2，等 XPosFB 到位"""
        current = self.get_x_position()
        if abs(current - target_x) <= tolerance:
            logger.info(f"机械手：X 已在 {target_x} 附近（current={current}）")
            return {"success": True, "message": f"X 已在绝对位置 {target_x} 附近"}

        cmd_type = int(RobotCommand.X_RIGHT if current < target_x else RobotCommand.X_LEFT)
        direction = "向右" if current < target_x else "向左"
        desc = f"X{direction}移至绝对位置{target_x}"
        logger.info(f"机械手：{desc}（current={current}）...")

        self.ensure_idle()
        self.set_node_value("Robot_XPosSet", target_x)
        self.set_node_value("Robot_XSpeedSet", x_speed)
        self.set_node_value("Robot_CmdType", int(cmd_type))
        self.set_node_value("Robot_CmdTrig", 1)

        if not self._wait_x_reach(target_x, tolerance=tolerance, description=desc, timeout=timeout):
            self._log_status(f"{desc}失败")
            raise ValueError(f"{desc}失败，当前 X={self.get_x_position()}")

        self.set_node_value("Robot_CmdTrig", 0)
        if self.get_node_value("Robot_FinishFB", force_read=True):
            self._clear_finish()
        self._log_status(f"{desc}后")
        return {"success": True, "message": f"{desc}完成", "cmd_type": int(cmd_type)}

    @not_action
    def _go_home(self, timeout: float = 180.0) -> dict:
        """回原点：Robot_gohome=1，等待 Robot_gohome_done=1"""
        logger.info("机械手：回原点...")
        self.ensure_idle()
        self.set_node_value("Robot_gohome", 1)
        if not self._wait_until_true("Robot_gohome_done", timeout=timeout, description="回原点完成"):
            err = self.get_node_value("Robot_Error_code", force_read=True)
            raise ValueError(f"回原点失败，Error_code={err}")
        self.set_node_value("Robot_gohome", 0)
        self._log_status("回原点后")
        return {"success": True, "message": "回原点完成", "cmd_type": CMD_GO_HOME}

    @not_action
    def _stop(self) -> dict:
        """紧急停止：Robot_STOP=1 脉冲"""
        logger.info("机械手：停止...")
        self.set_node_value("Robot_STOP", 1)
        time.sleep(0.2)
        self.set_node_value("Robot_STOP", 0)
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
    def _wait_until_true(
        self,
        node_name: str,
        timeout: float = 180.0,
        interval: float = 0.2,
        description: str = None,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（轮询 {node_name}）...")
        start = time.time()
        while time.time() - start < timeout:
            value = self.get_node_value(node_name, force_read=True)
            if value:
                logger.info(f"✓ {desc}（{node_name}={value}）")
                return True
            time.sleep(interval)
        value = self.get_node_value(node_name, force_read=True)
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
        while time.time() - start < timeout:
            value = self.get_node_value(node_name, force_read=True)
            if not value:
                logger.info(f"✓ {desc}（{node_name}={value}）")
                return True
            time.sleep(interval)
        value = self.get_node_value(node_name, force_read=True)
        logger.error(f"✗ {desc} 超时（{node_name}={value!r}）")
        return False

    @not_action
    def run_test_flow(self) -> dict:
        """堆栈位置1：清状态 → 使能 → 夹料 → 放料"""
        logger.info("机械手：开始堆栈位置1测试流程...")
        self.ensure_idle()
        self.execute_command(cmd_type=int(RobotCommand.ENABLE))
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            self.execute_command(cmd_type=int(cmd_type), **preset)
        logger.info("机械手：堆栈位置1测试流程完成")
        return {"success": True, "message": "堆栈位置1测试流程完成"}

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
            "stack": self.get_node_value("Robot_Stack", force_read=True),
            "stack_done": self.get_node_value("Robot_Stack_Done", force_read=True),
            "pick_or_place": self._get_node_value_optional(PICK_PLACE_NODE),
            "gohome_done": self.get_node_value("Robot_gohome_done", force_read=True),
            "error_code": self.get_node_value("Robot_Error_code", force_read=True),
            "running_status": self.get_node_value("Robot_Running_Status", force_read=True),
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
        csv_path=DEFAULT_CSV_PATH,
        use_subscription=False,
    )

    time.sleep(2)
    robot.ensure_idle()
    logger.info(f"机械手连通性: {robot.get_status()}")

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("3  复位 (CmdType=7)")
        print("4  使能 (CmdType=9)")
        print("5  失能 (CmdType=8)")
        print("6  动作命令复位 (CmdType=10)")
        print("7  回原点 (CmdType=100)")
        print("8  停止 (CmdType=101)")
        print("11 X移至3274 (CmdType=1/2)")
        print("12 X移至100 (CmdType=1/2)")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "98":
            robot.run_test_flow()
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
        elif choice == "11":
            robot.execute_command(cmd_type=1, x_pos=3274, x_speed=300)
        elif choice == "12":
            robot.execute_command(cmd_type=1, x_pos=100, x_speed=300)
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            robot.execute_command(cmd_type=int(cmd_type), **preset)
        else:
            print("无效的操作序号，请重新输入。")

    robot.disconnect()
    print("退出程序。")
