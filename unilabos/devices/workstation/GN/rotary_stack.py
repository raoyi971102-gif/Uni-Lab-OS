"""
堆栈（旋转堆栈）设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「堆栈」；节点：opcua_gn1.3.3.csv（前缀 Stack_）。
握手统一由 GNStationClient.run_command 承担（写参 → CmdType → CmdTrig → 等到位）。

对外动作：
    execute_command  底层 Stack_CmdType 调试入口
    rotate_to_column 旋转到指定列并按 Stack_RPosFB 校验到位（供机械手抓取前调用）
    detect           相机检测有无（cmd 6，读 Stack_DetectResult）

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
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

STACK_ROTATE_SPEED = 100
# Stack_RPosFB 到位容差（现场标定后可收紧）
STACK_R_TOLERANCE = 50
# 列号 -> Stack_RPosSet（R 轴目标位置）；待现场标定后填入。机械手抓取共用此表（单一来源）。
STACK_COLUMN_TO_R: dict = {
    # 1: 0,
    # 2: 0,
    # ...
}

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
    description="GN 旋转堆栈：OPC UA 1.3.3，run_command 握手 + rotate_to_column 到位校验",
    icon="",
    version="3.0.0",
)
class RotaryStackDevice(GNStationClient):
    """旋转堆栈设备类（OPC 前缀 Stack_）"""

    PREFIX = "Stack_"
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
        """底层调试入口：写参 → CmdType → CmdTrig → 等 CompleteFB（不等复位）。"""
        cmd = int(cmd_type)
        setpoints = {
            "Stack_RPosSet": r_pos,
            "Stack_ZPosSet": z_pos,
            "Stack_RSpeed": r_speed,
            "Stack_ZSpeed": z_speed,
        }
        label = STACK_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        result = self.run_command(
            cmd, setpoints, clear_done=False,
            description=f"旋转堆栈:{label}", timeout=timeout,
        )
        if cmd == int(StackCommand.CAMERA_DETECT):
            detect = self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)
            result["detect_result"] = detect
            result["message"] = f"检测完成，结果={detect}"
        return result

    @action(description="旋转堆栈旋转至指定列，并按 Stack_RPosFB 校验到位（未到位报错）")
    def rotate_to_column(self, column: int, r_speed: int = STACK_ROTATE_SPEED, timeout: float = 120.0) -> dict:
        """旋转到列 column（Stack_CmdType=5），等 Stack_CompleteFB 且 Stack_RPosFB 到位。"""
        r_pos = STACK_COLUMN_TO_R.get(column)
        if r_pos is None:
            raise ValueError(f"未配置堆栈列 {column} 的 R 位置，请补 STACK_COLUMN_TO_R")
        self.run_command(
            int(StackCommand.ROTATE_TO_TARGET),
            {"Stack_RPosSet": r_pos, "Stack_RSpeed": r_speed},
            done_node=self.COMPLETE_NODE,
            reach_checks=[("Stack_RPosFB", r_pos, STACK_R_TOLERANCE)],
            clear_done=False,
            description=f"旋转堆栈至列{column}",
            timeout=timeout,
        )
        return {"success": True, "message": f"已到位列{column}", "column": column, "r_pos": r_pos}

    @action(description="相机检测目标位置有无 (cmd 6)，返回 detect_result")
    def detect(self, z_pos: Optional[int] = None, z_speed: Optional[int] = None, timeout: float = 120.0) -> dict:
        return self.execute_command(
            cmd_type=int(StackCommand.CAMERA_DETECT), z_pos=z_pos, z_speed=z_speed, timeout=timeout,
        )

    @not_action
    def get_status(self) -> dict:
        return {
            "complete": self.get_node_value(self.COMPLETE_NODE, force_read=True),
            "detect_result": self.get_node_value(self.DETECT_RESULT_NODE, force_read=True),
            "R": self.get_node_value("Stack_RPosFB", force_read=True),
            "Z": self.get_node_value("Stack_ZPosFB", force_read=True),
        }

    @not_action
    def get_positions(self) -> dict:
        return {
            "R": self.get_node_value("Stack_RPosFB", force_read=True),
            "Z": self.get_node_value("Stack_ZPosFB", force_read=True),
        }

    @not_action
    def get_detect_result(self) -> int:
        return self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    STACK_URL = "opc.tcp://192.168.6.6:4840"

    dev = RotaryStackDevice(url=STACK_URL, csv_path=DEFAULT_CSV_PATH, use_subscription=False)
    time.sleep(2)

    while True:
        print("请选择操作：")
        for cmd, label in STACK_CMD_LABELS.items():
            print(f"{cmd} {label} (CmdType={cmd})")
        print("21 旋转至指定列 (rotate_to_column)")
        print("99 退出")
        choice = input("请输入序号：").strip()
        if choice == "99":
            break
        if choice == "21":
            col = int(input("列号: ").strip() or "1")
            dev.rotate_to_column(column=col)
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
