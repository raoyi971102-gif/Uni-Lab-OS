"""
堆栈（旋转堆栈）设备驱动

协议：OPC_UA协议1.3.4(1).xlsx「堆栈」；节点：opcua_gn1.3.3.csv（前缀 Stack_）。

对外仅暴露 execute_command（Stack_CmdType + 写参）；测试流程预设供本地调试。

指令类型 (Stack_CmdType)：
    1=堆栈左旋转 2=堆栈右旋转 3=相机Z向上 4=相机Z向下
    5=堆栈旋转至目标位置 6=相机移动至目标位置并检测有无 7=复位
"""

import os
import time
import logging
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_opcua_device import GnOpcUaDevice

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

# OPC UA 1.3.4 Stack_CmdType
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
    "按 Stack_CmdType 执行 OPC UA 1.3.4 指令。"
    "1=堆栈左旋转 2=堆栈右旋转 3=相机Z向上 4=相机Z向下 "
    "5=堆栈旋转至目标位置 6=相机移动至目标位置并检测有无 7=复位。"
    "R轴命令(1/2/5)必须同时写 r_pos+r_speed；"
    "Z轴命令(3/4/6)必须同时写 z_pos+z_speed；"
    "cmd_type=6 时响应含 detect_result。"
)


@device(
    id="gn_rotary_stack",
    display_name="旋转堆栈",
    category=["workstation"],
    description="GN 旋转堆栈：OPC UA 1.3.4，按完成反馈边沿执行命令",
    icon="",
    version="2.0.0",
)
class RotaryStackDevice(GnOpcUaDevice):
    """旋转堆栈设备类（OPC 前缀 Stack_）"""

    CMD_TYPE_NODE = "Stack_CmdType"
    CMD_TRIG_NODE = "Stack_CmdTrig"
    COMPLETE_NODE = "Stack_CompleteFB"
    DETECT_RESULT_NODE = "Stack_DetectResult"
    R_POS_FB_NODE = "Stack_RPosFB"
    Z_POS_FB_NODE = "Stack_ZPosFB"
    R_POS_SET_NODE = "Stack_RPosSet"
    Z_POS_SET_NODE = "Stack_ZPosSet"
    R_SPEED_NODE = "Stack_RSpeed"
    Z_SPEED_NODE = "Stack_ZSpeed"

    def __init__(
        self,
        url: Optional[str] = None,
        plc_device_id: Optional[str] = None,
        csv_path: str = DEFAULT_CSV_PATH,
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
            csv_path=csv_path,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs,
        )

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
        """按 1.3.4 协议执行堆栈命令，并等待 CompleteFB 完成边沿。"""
        cmd = int(cmd_type)
        if cmd not in STACK_CMD_LABELS:
            raise ValueError(f"不支持的 Stack_CmdType={cmd}，支持: {sorted(STACK_CMD_LABELS)}")
        self._validate_parameters(cmd, r_pos, z_pos, r_speed, z_speed, timeout)
        setpoints = self._build_setpoints(r_pos, z_pos, r_speed, z_speed)
        label = STACK_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        result = self._execute(cmd, label, setpoints, timeout=timeout)
        if cmd == int(StackCommand.CAMERA_DETECT):
            detect_result = self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)
            result["detect_result"] = detect_result
            result["message"] = f"检测完成，结果={detect_result}"
        return result

    @not_action
    def _validate_parameters(
        self,
        cmd: int,
        r_pos: Optional[int],
        z_pos: Optional[int],
        r_speed: Optional[int],
        z_speed: Optional[int],
        timeout: float,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if cmd in (
            int(StackCommand.ROTATE_LEFT),
            int(StackCommand.ROTATE_RIGHT),
            int(StackCommand.ROTATE_TO_TARGET),
        ) and (r_pos is None or r_speed is None):
            raise ValueError(
                f"CmdType={cmd} 必须同时设置 r_pos（Stack_RPosSet）"
                "和 r_speed（Stack_RSpeed）"
            )
        if cmd in (
            int(StackCommand.CAMERA_Z_UP),
            int(StackCommand.CAMERA_Z_DOWN),
            int(StackCommand.CAMERA_DETECT),
        ) and (z_pos is None or z_speed is None):
            raise ValueError(
                f"CmdType={cmd} 必须同时设置 z_pos（Stack_ZPosSet）"
                "和 z_speed（Stack_ZSpeed）"
            )
        for name, value in (("r_pos", r_pos), ("z_pos", z_pos)):
            if value is not None and not -32768 <= int(value) <= 32767:
                raise ValueError(f"{name}={value} 超出 Int16 范围")
        for name, value in (("r_speed", r_speed), ("z_speed", z_speed)):
            if value is not None and not 0 <= int(value) <= 32767:
                raise ValueError(f"{name}={value} 必须在 0..32767 范围")

    @not_action
    def _build_setpoints(
        self,
        r_pos: Optional[int],
        z_pos: Optional[int],
        r_speed: Optional[int],
        z_speed: Optional[int],
    ) -> dict[str, int]:
        mapping = {
            self.R_POS_SET_NODE: r_pos,
            self.Z_POS_SET_NODE: z_pos,
            self.R_SPEED_NODE: r_speed,
            self.Z_SPEED_NODE: z_speed,
        }
        return {node: int(value) for node, value in mapping.items() if value is not None}

    @not_action
    def _write_required(self, node_name: str, value: int, verify: bool = True) -> None:
        if not self.set_node_value(node_name, int(value)):
            raise ValueError(f"写入 {node_name}={value} 失败")
        if not verify:
            return
        actual = self.get_node_value(node_name, force_read=True)
        if actual != int(value):
            raise ValueError(f"{node_name} 写入后回读不一致：期望 {value}，实际 {actual}")

    @not_action
    def _execute(
        self,
        cmd_type: int,
        description: str,
        setpoints: dict[str, int],
        timeout: float = 120.0,
    ) -> dict:
        logger.info(f"旋转堆栈：{description} (CmdType={cmd_type})")

        # CompleteFB 空闲时保持为 1。新命令必须先拉低触发，再等待
        # CompleteFB 出现 1→0（开始）和 0→1（完成）两个边沿。
        self._write_required(self.CMD_TRIG_NODE, 0)
        for node_name, value in setpoints.items():
            self._write_required(node_name, value)
        self._write_required(self.CMD_TYPE_NODE, cmd_type)

        started_at = time.monotonic()
        try:
            # CmdTrig 是瞬时触发量，PLC 扫描到 1 后可能立即自动清零；
            # 因此这里只校验 OPC UA 写入成功，不能要求回读仍为 1。
            self._write_required(self.CMD_TRIG_NODE, 1, verify=False)
            if not self._wait_complete_value(
                expected=0,
                timeout=min(10.0, timeout),
                description=f"{description}启动",
            ):
                raise ValueError(f"{description} 未启动：Stack_CompleteFB 未变为 0")

            elapsed = time.monotonic() - started_at
            remaining = max(0.1, timeout - elapsed)
            if not self._wait_complete_value(
                expected=1,
                timeout=remaining,
                description=f"{description}完成",
            ):
                raise ValueError(f"{description} 超时：Stack_CompleteFB 未恢复为 1")
        finally:
            # 无论成功、异常还是 Ctrl+C，都撤销触发并清空命令，避免重复执行。
            trigger_cleared = self.set_node_value(self.CMD_TRIG_NODE, 0)
            command_cleared = self.set_node_value(self.CMD_TYPE_NODE, 0)
            trigger_value = self.get_node_value(self.CMD_TRIG_NODE, force_read=True)
            command_value = self.get_node_value(self.CMD_TYPE_NODE, force_read=True)
            logger.info(
                f"旋转堆栈命令清理：CmdTrig={trigger_value!r}，CmdType={command_value!r}"
            )

        if (
            not trigger_cleared
            or not command_cleared
            or trigger_value != 0
            or command_value != 0
        ):
            raise ValueError(
                "动作已完成，但命令清零失败："
                f"Stack_CmdTrig={trigger_value!r}, Stack_CmdType={command_value!r}"
            )

        status = self.get_status()
        logger.info(f"旋转堆栈：{description}完成，状态={status}")
        return {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": int(cmd_type),
            "data": status,
        }

    @not_action
    def _wait_complete_value(
        self,
        expected: int,
        timeout: float,
        interval: float = 0.05,
        description: str = "",
    ) -> bool:
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            value = self.get_node_value(self.COMPLETE_NODE, force_read=True)
            if value == expected:
                logger.info(f"✓ {description}（{self.COMPLETE_NODE}={value}）")
                return True
            time.sleep(interval)
        value = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        logger.error(
            f"✗ {description}超时（等待 {self.COMPLETE_NODE}={expected}，当前={value!r}）"
        )
        return False

    @not_action
    def run_test_flow(self) -> dict:
        """按旋转堆栈测试预设依次 execute_command（本地调试用）"""
        logger.info("旋转堆栈：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            self.execute_command(cmd_type=int(cmd_type), **preset)
        logger.info("旋转堆栈：整体测试流程完成")
        return {"success": True, "message": "旋转堆栈测试流程完成"}

    @not_action
    def get_status(self) -> dict:
        return {
            "complete": self.get_node_value(self.COMPLETE_NODE, force_read=True),
            "detect_result": self.get_node_value(self.DETECT_RESULT_NODE, force_read=True),
            "cmd_type": self.get_node_value(self.CMD_TYPE_NODE, force_read=True),
            "cmd_trig": self.get_node_value(self.CMD_TRIG_NODE, force_read=True),
            "r_pos_fb": self.get_node_value(self.R_POS_FB_NODE, force_read=True),
            "z_pos_fb": self.get_node_value(self.Z_POS_FB_NODE, force_read=True),
        }

    @not_action
    def get_positions(self) -> dict:
        return {
            "R": self.get_node_value(self.R_POS_FB_NODE, force_read=True),
            "Z": self.get_node_value(self.Z_POS_FB_NODE, force_read=True),
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
            if cmd in (1, 2, 5):
                r_pos = int(input("r_pos [0]: ").strip() or "0")
            if cmd in (3, 4, 6):
                z_pos = int(input("z_pos [0]: ").strip() or "0")
            if cmd in (1, 2, 5):
                r_speed = int(input("r_speed [300]: ").strip() or "300")
            if cmd in (3, 4, 6):
                z_speed = int(input("z_speed [300]: ").strip() or "300")
            dev.execute_command(
                cmd_type=cmd, r_pos=r_pos, z_pos=z_pos, r_speed=r_speed, z_speed=z_speed,
            )
        else:
            print("无效的操作序号，请重新输入。")

    dev.disconnect()
    print("退出程序。")
