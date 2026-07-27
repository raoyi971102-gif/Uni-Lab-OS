"""
旋转堆栈 设备驱动

基于 gn_solid_weighing / gn_standard_oven 同构 OPC 握手；
业务逻辑对齐 rotary_stack.py；协议 opcua_gn1.3.6.csv「堆栈」（前缀 Stack_）。

对外仅暴露 execute_command（Stack_CmdType + 写参）；另提供复位/转列/检测便捷动作。

R 轴绝对定位：复位后第 1 列 R=0，之后每列 +300（30°/列），共 12 列，一圈 3600(360°)。
使用前须先复位建立 R0 基准。PLC 可精确到位时按 Stack_RPosFB == 目标 R 判定。

指令类型 (Stack_CmdType)：
    1=左旋转 2=右旋转 3=相机Z向上 4=相机Z向下
    5=旋转至目标位置 6=相机检测有无 7=复位
"""

import os
import time
import logging
import threading
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient

DEFAULT_XLSX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "opcua_gn1.3.6.csv",
)

STACK_ROTATE_SPEED = 300
STACK_R_STEP = 300
STACK_COLUMN_COUNT = 12

# 抓取数字（机械臂 Robot_Stack 工位号）→ 所在列号
COLUMN_NUMBERS = {
    1: range(1, 9),
    2: range(9, 17),
    3: range(17, 25),
    4: range(25, 31),
    5: range(31, 41),
    6: range(41, 50),
    7: range(56, 60),
    8: range(60, 64),
    9: range(64, 69),
    10: range(69, 74),
    11: range(74, 79),
    12: range(79, 84),
}
NUMBER_TO_COLUMN = {n: col for col, nums in COLUMN_NUMBERS.items() for n in nums}

STACK_CMD_LABELS = {
    1: "左旋转",
    2: "右旋转",
    3: "相机Z向上",
    4: "相机Z向下",
    5: "旋转至目标位置",
    6: "相机检测有无",
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


# 仅等 CompleteFB 的命令（无可靠位置反馈兜底）
_COMPLETE_FB_ONLY_CMDS = frozenset({
    int(StackCommand.ROTATE_LEFT),
    int(StackCommand.ROTATE_RIGHT),
    int(StackCommand.CAMERA_DETECT),
    int(StackCommand.RESET),
})

# 本地测试流程预设
TEST_FLOW_PRESETS = [
    ("1.复位", StackCommand.RESET, dict(timeout=120.0)),
    ("2.旋转至第1列", StackCommand.ROTATE_TO_TARGET, dict(
        r_pos=0, r_speed=STACK_ROTATE_SPEED, timeout=120.0,
    )),
    ("3.相机检测", StackCommand.CAMERA_DETECT, dict(timeout=120.0)),
]


_EXECUTE_CMD_DOC = (
    "按 Stack_CmdType 执行 OPC UA 1.3.6 指令。"
    "1=左旋 2=右旋 3=相机Z上 4=相机Z下 5=旋转至目标 6=相机检测 7=复位。"
    "可选写参：r_pos/z_pos/r_speed/z_speed。"
)


def column_to_r(column: int) -> int:
    """列号 → R 轴目标位置。列N = (N-1)*300。"""
    if not 1 <= column <= STACK_COLUMN_COUNT:
        raise ValueError(f"列号须在 1~{STACK_COLUMN_COUNT}，收到 {column}")
    return (column - 1) * STACK_R_STEP


def column_of_number(number: int) -> int:
    """抓取数字 → 所在列号。"""
    col = NUMBER_TO_COLUMN.get(number)
    if col is None:
        raise ValueError(f"抓取数字 {number} 未对应任何列（有效号见 COLUMN_NUMBERS）")
    return col


@device(
    id="gn_stack",
    display_name="旋转堆栈",
    category=["workstation"],
    description="GN 旋转堆栈：OPC UA 1.3.6，按完成反馈边沿执行命令，绝对列定位",
    icon="",
    version="2.0.0",
)
class StackDevice(GNStationClient):
    """旋转堆栈设备类（OPC 前缀 Stack_，通过 self.plc 共享 GN 工站单例 OPC UA 会话）。"""

    PREFIX = "Stack_"
    CMD_TYPE_NODE = "Stack_CmdType"
    CMD_TRIG_NODE = "Stack_CmdTrig"
    COMPLETE_NODE = "Stack_CompleteFB"
    DETECT_RESULT_NODE = "Stack_DetectResult"
    POSITION_NODES = {
        "Stack_RPosSet": "Stack_RPosFB",
        "Stack_ZPosSet": "Stack_ZPosFB",
    }
    _COMPLETE_FB_ONLY_CMDS = _COMPLETE_FB_ONLY_CMDS

    def __init__(
        self,
        url: str,
        xlsx_path: str = DEFAULT_XLSX_PATH,
        csv_path: str = None,
        username: str = None,
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        path = csv_path or xlsx_path
        super().__init__(
            url=url,
            csv_path=path,
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

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        r_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        r_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """唯一通用动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        cmd = int(cmd_type)
        if timeout is None or float(timeout) <= 0:
            timeout = 180.0
        setpoints = self._build_setpoints(
            r_pos=r_pos, z_pos=z_pos, r_speed=r_speed, z_speed=z_speed,
        )
        label = STACK_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        result = self._run(cmd, label, setpoints, timeout=float(timeout))
        if cmd == int(StackCommand.CAMERA_DETECT):
            detect = self._opc_read(self.DETECT_RESULT_NODE, force_read=True)
            result["detect_result"] = detect
            result["message"] = f"{label}完成，检测结果={detect}"
        return result

    @action(description="复位建立 R0 基准（旋转/抓取前应先复位）")
    def do_reset(self, timeout: float = 120.0) -> dict:
        return self.execute_command(cmd_type=int(StackCommand.RESET), timeout=timeout)

    @action(description="旋转至指定列，并按 Stack_RPosFB 校验到位")
    def rotate_to(
        self,
        column: int,
        r_speed: int = STACK_ROTATE_SPEED,
        timeout: float = 120.0,
    ) -> dict:
        r = column_to_r(column)
        result = self.execute_command(
            cmd_type=int(StackCommand.ROTATE_TO_TARGET),
            r_pos=r,
            r_speed=r_speed,
            timeout=timeout,
        )
        result["column"] = column
        result["r_pos"] = r
        result["message"] = f"已到第{column}列(R={r})"
        return result

    @action(description="按抓取数字旋转到对应列（可选先复位）")
    def rotate_for_number(
        self,
        number: int,
        reset_first: bool = False,
        r_speed: int = STACK_ROTATE_SPEED,
        timeout: float = 120.0,
    ) -> dict:
        column = column_of_number(number)
        if reset_first:
            self.do_reset(timeout=timeout)
        result = self.rotate_to(column=column, r_speed=r_speed, timeout=timeout)
        result["number"] = number
        return result

    @action(description="相机检测目标位置有无 (CmdType=6)")
    def detect(
        self,
        z_pos: Optional[int] = None,
        z_speed: Optional[int] = None,
        timeout: float = 120.0,
    ) -> dict:
        return self.execute_command(
            cmd_type=int(StackCommand.CAMERA_DETECT),
            z_pos=z_pos,
            z_speed=z_speed,
            timeout=timeout,
        )

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
        timeout: float = 180.0,
    ) -> dict:
        with self._command_lock:
            logger.info(f"旋转堆栈：{description} (CmdType={cmd_type})")
            if setpoints:
                for node, value in setpoints.items():
                    if not self._opc_write(node, value):
                        raise ValueError(f"写入 {node}={value} 失败")
            return self._trigger_and_wait(
                cmd_type,
                description,
                setpoints=setpoints,
                timeout=timeout,
            )

    @not_action
    def _trigger_and_wait(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 180.0,
    ) -> dict:
        """下发 CmdType → CmdTrig=1，等待 CompleteFB=1 后清理（同固体加样）。"""
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if not self._opc_write(self.CMD_TYPE_NODE, int(cmd_type)):
            raise ValueError(f"Stack_CmdType={cmd_type} 写入失败")
        if not self._opc_write(self.CMD_TRIG_NODE, 1):
            raise ValueError("Stack_CmdTrig=1 写入失败")

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
                raise ValueError(f"{description}失败，Stack_CompleteFB 未变为 1")
        finally:
            trigger_cleared = self._opc_write(self.CMD_TRIG_NODE, 0)
            command_cleared = self._opc_write(self.CMD_TYPE_NODE, 0)
            trigger_value = self._opc_read(self.CMD_TRIG_NODE, force_read=True)
            command_value = self._opc_read(self.CMD_TYPE_NODE, force_read=True)
            logger.info(
                f"旋转堆栈命令清理：CmdTrig={trigger_value!r}，CmdType={command_value!r}"
            )
            if completed and (
                not trigger_cleared
                or not command_cleared
                or trigger_value != 0
                or command_value != 0
            ):
                raise ValueError(
                    "动作已完成，但命令清零失败："
                    f"Stack_CmdTrig={trigger_value!r}, Stack_CmdType={command_value!r}"
                )

        logger.info(f"{description}完成")
        self._log_status(f"{description}后")
        return {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": int(cmd_type),
        }

    @not_action
    def _position_targets_from_setpoints(self, setpoints: dict) -> dict:
        targets = {}
        for setpoint_node, feedback_node in self.POSITION_NODES.items():
            if setpoint_node not in setpoints:
                continue
            targets[feedback_node] = int(setpoints[setpoint_node])
        return targets

    @not_action
    def _positions_reached(
        self,
        position_targets: dict,
        tolerance: int = 0,
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
        if position_targets and self._positions_reached(position_targets, tolerance=0):
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
    def current_column(self) -> Optional[int]:
        """按 Stack_RPosFB 反推当前列号；非整列位置返回 None。"""
        r = self._opc_read("Stack_RPosFB", force_read=True)
        if r is None:
            return None
        col, rem = divmod(int(r), STACK_R_STEP)
        return col + 1 if rem == 0 and 0 <= col < STACK_COLUMN_COUNT else None

    @not_action
    def at_column(self, column: int) -> bool:
        r = self._opc_read("Stack_RPosFB", force_read=True)
        return r is not None and int(r) == column_to_r(column)

    @not_action
    def run_test_flow(self) -> dict:
        logger.info("旋转堆栈：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            self.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        logger.info("旋转堆栈：整体测试流程完成")
        return {"success": True, "message": "旋转堆栈测试流程完成"}

    @not_action
    def get_positions(self) -> dict:
        return {
            "R": self.get_node_value("Stack_RPosFB", force_read=True),
            "Z": self.get_node_value("Stack_ZPosFB", force_read=True),
        }

    @not_action
    def get_status(self) -> dict:
        status = self.get_positions()
        status["column"] = self.current_column()
        status["complete"] = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        status["detect_result"] = self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: 列={status['column']} R={status['R']} Z={status['Z']} "
            f"完成={status['complete']} 检测={status['detect_result']}"
        )


# ==================== 模块级便捷函数（供机械手等调用） ====================

def reset(client: StackDevice, timeout: float = 120.0) -> None:
    client.do_reset(timeout=timeout)


def rotate_to_column(
    client: StackDevice,
    column: int,
    r_speed: int = STACK_ROTATE_SPEED,
    timeout: float = 120.0,
) -> int:
    client.rotate_to(column=column, r_speed=r_speed, timeout=timeout)
    return column_to_r(column)


def rotate_for_number(
    client: StackDevice,
    number: int,
    reset_first: bool = False,
    r_speed: int = STACK_ROTATE_SPEED,
    timeout: float = 120.0,
) -> int:
    client.rotate_for_number(
        number=number,
        reset_first=reset_first,
        r_speed=r_speed,
        timeout=timeout,
    )
    return column_of_number(number)


def current_column(client: StackDevice) -> Optional[int]:
    return client.current_column()


def at_column(client: StackDevice, column: int) -> bool:
    return client.at_column(column)


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    STACK_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    dev = StackDevice(url=STACK_URL, xlsx_path=DEFAULT_XLSX_PATH, use_subscription=False)
    time.sleep(2)
    logger.info(f"旋转堆栈连通性测试: {dev.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                dev._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    threading.Thread(target=_status_log_worker, daemon=True, name="StackStatusLog").start()

    while True:
        print("请选择操作：")
        print("1 复位(建立R0)")
        print("2 输入抓取数字 → 旋转到对应列")
        print("3 直接旋转到指定列(1~12)")
        print("4 相机检测")
        print("5 查看状态")
        print("6 单指令调试(execute_command)")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入序号：").strip()
        if choice == "99":
            break
        try:
            if choice == "1":
                print(dev.do_reset())
            elif choice == "2":
                number = int(input("抓取数字(工位号): ").strip() or "1")
                print(dev.rotate_for_number(number=number))
            elif choice == "3":
                print(dev.rotate_to(column=int(input("列号(1~12): ").strip() or "1")))
            elif choice == "4":
                print(dev.detect())
            elif choice == "5":
                print(dev.get_status())
            elif choice == "6":
                cmd_type = int(input(
                    "CmdType(1左旋 2右旋 3相机Z上 4相机Z下 5旋转至目标 6相机检测 7复位): "
                ).strip() or "7")
                r_pos = input("r_pos(旋转至目标时填, 可空): ").strip()
                print(dev.execute_command(
                    cmd_type=cmd_type,
                    r_pos=int(r_pos) if r_pos else None,
                ))
            elif choice == "98":
                print(dev.run_test_flow())
            else:
                print("无效序号")
        except Exception as e:
            print(f"操作失败: {e!r}")

    status_log_running = False
    dev.disconnect()
    print("退出程序。")
