"""
堆栈（旋转堆栈）设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「堆栈」；节点：opcua_gn1.3.3.csv（前缀 Stack_）。

对外仅暴露 execute_command（Stack_CmdType + 写参）；测试流程预设供本地调试。

指令类型 (Stack_CmdType)：
    1=堆栈左旋转 2=堆栈右旋转 3=相机Z向上 4=相机Z向下
    5=堆栈旋转至目标位置 6=相机移动至目标位置并检测有无 7=复位
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

# OPC 1.3.3 Stack_CmdType（与 Excel 表头一致）
STACK_CMD_LABELS = {
    1: "堆栈左旋转",
    2: "堆栈右旋转",
    3: "相机Z向上",
    4: "相机Z向下",
    5: "堆栈旋转至目标位置",
    6: "相机移动至目标位置并检测有无",
    7: "复位",
}


class StackCommand(int, Enum):
    """旋转堆栈指令类型 (Stack_CmdType)"""

    ROTATE_LEFT = 1
    ROTATE_RIGHT = 2
    CAMERA_Z_UP = 3
    CAMERA_Z_DOWN = 4
    ROTATE_TO_TARGET = 5
    CAMERA_DETECT = 6
    RESET = 7


# 旋转堆栈测试流程预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS: list = []


_EXECUTE_CMD_DOC = (
    "按 Stack_CmdType 执行 OPC 1.3.3 指令。"
    "1=堆栈左旋转 2=堆栈右旋转 3=相机Z向上 4=相机Z向下 "
    "5=堆栈旋转至目标位置 6=相机移动至目标位置并检测有无 7=复位。"
    "写 r_pos(Stack_RPosSet)/z_pos(Stack_ZPosSet)/r_speed/z_speed；"
    "cmd_type=6 时响应含 detect_result。"
)


@device(
    id="gn_rotary_stack",
    display_name="旋转堆栈",
    category=["workstation"],
    description="GN 旋转堆栈：OPC UA 1.3.3，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class RotaryStackDevice(OpcUaClientWithSubscription):
    """旋转堆栈设备类（OPC 前缀 Stack_）"""

    CMD_TYPE_NODE = "Stack_CmdType"
    CMD_TRIG_NODE = "Stack_CmdTrig"
    COMPLETE_NODE = "Stack_CompleteFB"
    DETECT_RESULT_NODE = "Stack_DetectResult"

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

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        r_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        r_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
        timeout: float = 120.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB（不等待 CompleteFB 复位）。"""
        cmd = int(cmd_type)
        setpoints = self._build_setpoints(
            r_pos=r_pos, z_pos=z_pos, r_speed=r_speed, z_speed=z_speed,
        )
        label = STACK_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        result = self._run(cmd, label, setpoints, timeout=timeout)
        if cmd == int(StackCommand.CAMERA_DETECT):
            detect_result = self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)
            result["detect_result"] = detect_result
            result["message"] = f"检测完成，结果={detect_result}"
        return result

    @not_action
    def _build_setpoints(
        self,
        r_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        r_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
    ) -> dict:
        mapping = {
            "Stack_RPosSet": r_pos,
            "Stack_ZPosSet": z_pos,
            "Stack_RSpeed": r_speed,
            "Stack_ZSpeed": z_speed,
        }
        return {node: val for node, val in mapping.items() if val is not None}

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 120.0,
    ) -> dict:
        logger.info(f"旋转堆栈：{description} (CmdType={cmd_type})")
        if setpoints:
            for node, value in setpoints.items():
                self.set_node_value(node, value)
        return self._trigger_and_wait(cmd_type, description, timeout=timeout)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        if not self._wait_complete(timeout=timeout, description=description):
            self.set_node_value(self.CMD_TRIG_NODE, 0)
            raise ValueError(f"{description} 执行失败或超时")
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        logger.info(f"{description} 完成")
        return {
            "success": True,
            "message": f"{description} 完成",
            "cmd_type": int(cmd_type),
        }

    @not_action
    def _wait_complete(
        self,
        timeout: float = 120.0,
        interval: float = 0.2,
        description: str = "",
    ) -> bool:
        desc = description or self.COMPLETE_NODE
        start = time.time()
        while True:
            value = self.get_node_value(self.COMPLETE_NODE, force_read=True)
            if value:
                logger.info(f"✓ {desc} 完成 (CompleteFB={value})")
                return True
            if time.time() - start >= timeout:
                logger.error(f"✗ 等待 {desc} 完成超时（{timeout}s，当前={value!r}）")
                return False
            time.sleep(interval)

    @not_action
    def run_test_flow(self) -> dict:
        """按旋转堆栈测试预设依次 execute_command（本地调试用）"""
        logger.info("旋转堆栈：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            label = STACK_CMD_LABELS.get(int(cmd_type), str(cmd_type))
            self._run(int(cmd_type), f"{step_name}/{label}", self._build_setpoints(**preset))
        logger.info("旋转堆栈：整体测试流程完成")
        return {"success": True, "message": "旋转堆栈测试流程完成"}

    @not_action
    def get_status(self) -> dict:
        return {
            "complete": self.get_node_value(self.COMPLETE_NODE, force_read=True),
            "detect_result": self.get_node_value(self.DETECT_RESULT_NODE, force_read=True),
        }

    @not_action
    def get_positions(self) -> dict:
        return {
            "R": self.get_node_value("Stack_RPosFB"),
            "Z": self.get_node_value("Stack_ZPosFB"),
        }

    @not_action
    def get_detect_result(self) -> int:
        return self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    STACK_URL = "opc.tcp://192.168.6.6:4840"

    dev = RotaryStackDevice(url=STACK_URL, csv_path=DEFAULT_CSV_PATH)
    time.sleep(2)

    while True:
        print("请选择操作：")
        for cmd, label in STACK_CMD_LABELS.items():
            print(f"{cmd} {label} (CmdType={cmd})")
        if TEST_FLOW_PRESETS:
            print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入 CmdType 序号：").strip()
        if choice == "99":
            break
        if choice == "98" and TEST_FLOW_PRESETS:
            dev.run_test_flow()
        elif choice.isdigit() and int(choice) in STACK_CMD_LABELS:
            cmd = int(choice)
            r_pos = z_pos = r_speed = z_speed = None
            if cmd in (5,):
                r_pos = int(input("r_pos [0]: ").strip() or "0")
            if cmd in (3, 4, 6):
                z_pos = int(input("z_pos [0]: ").strip() or "0")
            if cmd in (1, 2, 5):
                r_speed_in = input("r_speed [留空跳过]: ").strip()
                r_speed = int(r_speed_in) if r_speed_in else None
            if cmd in (3, 4, 6):
                z_speed_in = input("z_speed [留空跳过]: ").strip()
                z_speed = int(z_speed_in) if z_speed_in else None
            dev.execute_command(
                cmd_type=cmd, r_pos=r_pos, z_pos=z_pos, r_speed=r_speed, z_speed=z_speed,
            )
        else:
            print("无效的操作序号，请重新输入。")

    dev.disconnect()
    print("退出程序。")
