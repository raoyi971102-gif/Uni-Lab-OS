"""
旋转堆栈 设备驱动 + 旋转驱动函数

协议：OPC UA「堆栈」，前缀 Stack_。握手由 GNStationClient.run_command 承担。

- 模块级驱动函数（reset / rotate_to_column / current_column / at_column）：
  直接操作任一已加载 Stack_ 节点的 GNStationClient，供机械手在抓取前调用。
- RotaryStackDevice：独立设备（相机检测、旋转到列、调试入口）。

R 轴绝对定位：复位后第 1 列 R=0，之后每列 +300（30°/列），共 12 列，一圈 3600(360°)。
使用前须先 reset() 建立 R0 基准。PLC 可精确到位，故按 Stack_RPosFB == 目标 R 精确判定。

指令类型 (Stack_CmdType)：
    1=左旋转 2=右旋转 3=相机Z向上 4=相机Z向下 5=旋转至目标位置 6=相机检测有无 7=复位
"""

import os
import time
import logging
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.6.csv")

STACK_ROTATE_SPEED = 300
STACK_R_STEP = 300         # 每列间隔（30°/列）
STACK_COLUMN_COUNT = 12    # 共 12 列，一圈 3600(360°)

# 抓取数字（机械臂写入 PLC 的 Robot_Stack=Modbus 309 工位号）→ 所在列号。
# 来源：《PLC和机器人协议modbustcp1.2》309 映射，各列层数不一（50~55、84+ 为空号）。
COLUMN_NUMBERS = {
    1:  range(1, 9),     # 第一列   1~8
    2:  range(9, 17),    # 第二列   9~16
    3:  range(17, 25),   # 第三列   17~24
    4:  range(25, 31),   # 第四列   25~30
    5:  range(31, 41),   # 第五列   31~40
    6:  range(41, 50),   # 第六列   41~49（41~45 大瓶/板，46~49 小瓶子）
    7:  range(56, 60),   # 第七列   56~59（大瓶）
    8:  range(60, 64),   # 第八列   60~63（枪头盒）
    9:  range(64, 69),   # 第九列   64~68（枪头盒）
    10: range(69, 74),   # 第十列   69~73（大瓶）
    11: range(74, 79),   # 第十一列 74~78（枪头盒）
    12: range(79, 84),   # 第十二列 79~83（枪头盒）
}
# 抓取数字 → 列号（反查表）
NUMBER_TO_COLUMN = {n: col for col, nums in COLUMN_NUMBERS.items() for n in nums}


class StackCommand(int, Enum):
    """旋转堆栈指令类型 (Stack_CmdType)"""

    ROTATE_LEFT = 1
    ROTATE_RIGHT = 2
    CAMERA_Z_UP = 3
    CAMERA_Z_DOWN = 4
    ROTATE_TO_TARGET = 5
    CAMERA_DETECT = 6
    RESET = 7


STACK_CMD_LABELS = {
    1: "左旋转", 2: "右旋转", 3: "相机Z向上", 4: "相机Z向下",
    5: "旋转至目标位置", 6: "相机检测有无", 7: "复位",
}


# ==================== 旋转驱动函数（供机械手调用） ====================
# 均显式指定 Stack_ 节点，故 client 可以是旋转堆栈本身，也可以是机械手（Robot_ 前缀）。

def column_to_r(column: int) -> int:
    """列号 → R 轴目标位置。列N = (N-1)*300。"""
    if not 1 <= column <= STACK_COLUMN_COUNT:
        raise ValueError(f"列号须在 1~{STACK_COLUMN_COUNT}，收到 {column}")
    return (column - 1) * STACK_R_STEP


def reset(client: GNStationClient, timeout: float = 120.0) -> None:
    """复位建立 R0 基准（Stack_CmdType=7），复位后第 1 列 R=0。"""
    client.run_command(
        int(StackCommand.RESET),
        trig_node="Stack_CmdTrig",
        cmd_type_node="Stack_CmdType",
        done_node="Stack_CompleteFB",
        clear_done=False,
        description="旋转堆栈复位",
        timeout=timeout,
    )


def rotate_to_column(client: GNStationClient, column: int,
                     r_speed: int = STACK_ROTATE_SPEED, timeout: float = 120.0) -> int:
    """旋转到指定列（Stack_CmdType=5），按 Stack_RPosFB 精确到位。返回目标 R。"""
    r = column_to_r(column)
    client.run_command(
        int(StackCommand.ROTATE_TO_TARGET),
        {"Stack_RPosSet": r, "Stack_RSpeed": r_speed},
        trig_node="Stack_CmdTrig",
        cmd_type_node="Stack_CmdType",
        done_node="Stack_CompleteFB",
        reach_checks=[("Stack_RPosFB", r, 0)],  # PLC 精确到位，要求 Stack_RPosFB == 目标 R
        clear_done=False,
        description=f"旋转堆栈至第{column}列(R={r})",
        timeout=timeout,
    )
    return r


def column_of_number(number: int) -> int:
    """抓取数字 → 所在列号。"""
    col = NUMBER_TO_COLUMN.get(number)
    if col is None:
        raise ValueError(f"抓取数字 {number} 未对应任何列（有效号见 COLUMN_NUMBERS）")
    return col


def rotate_for_number(client: GNStationClient, number: int, reset_first: bool = False,
                      r_speed: int = STACK_ROTATE_SPEED, timeout: float = 120.0) -> int:
    """按机械臂传入的抓取数字旋转到其所在列：可选先复位建立 R0，再旋转并校验到位。返回列号。

    机械手抓取前调用；本函数返回即代表“旋转到位”（未到位会在 rotate_to_column 内报错）。
    """
    column = column_of_number(number)
    if reset_first:
        reset(client, timeout=timeout)
    rotate_to_column(client, column, r_speed=r_speed, timeout=timeout)
    return column


def current_column(client: GNStationClient) -> Optional[int]:
    """按 Stack_RPosFB 反推当前列号；非整列位置返回 None。"""
    r = client.get_node_value("Stack_RPosFB", force_read=True)
    if r is None:
        return None
    col, rem = divmod(int(r), STACK_R_STEP)
    return col + 1 if rem == 0 and 0 <= col < STACK_COLUMN_COUNT else None


def at_column(client: GNStationClient, column: int) -> bool:
    """当前 Stack_RPosFB 是否精确处于目标列。"""
    r = client.get_node_value("Stack_RPosFB", force_read=True)
    return r is not None and int(r) == column_to_r(column)


@device(
    id="gn_rotary_stack",
    display_name="旋转堆栈",
    category=["workstation"],
    description="GN 旋转堆栈：OPC UA，绝对列定位（复位后列N=(N-1)*300）",
    icon="",
    version="3.1.0",
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

    @action(description="复位建立 R0 基准（旋转/抓取前应先复位）")
    def do_reset(self, timeout: float = 120.0) -> dict:
        reset(self, timeout=timeout)
        return {"success": True, "message": "复位完成，当前为第1列(R0)"}

    @action(description="旋转至指定列，并按 Stack_RPosFB 精确校验到位（未到位报错）")
    def rotate_to(self, column: int, r_speed: int = STACK_ROTATE_SPEED, timeout: float = 120.0) -> dict:
        r = rotate_to_column(self, column, r_speed=r_speed, timeout=timeout)
        return {"success": True, "message": f"已到第{column}列", "column": column, "r_pos": r}

    @action(description="相机检测目标位置有无 (CmdType=6)，返回 detect_result")
    def detect(self, z_pos: Optional[int] = None, z_speed: Optional[int] = None, timeout: float = 120.0) -> dict:
        self.run_command(
            int(StackCommand.CAMERA_DETECT),
            {"Stack_ZPosSet": z_pos, "Stack_ZSpeed": z_speed},
            clear_done=False,
            description="旋转堆栈相机检测",
            timeout=timeout,
        )
        result = self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)
        return {"success": True, "message": f"检测完成，结果={result}", "detect_result": result}

    @action(auto_prefix=True, description=(
        "调试入口，按 Stack_CmdType 执行：1=左旋 2=右旋 3=相机Z上 4=相机Z下 "
        "5=旋转至目标(需 r_pos) 6=相机检测 7=复位。"))
    def execute_command(self, cmd_type: int, r_pos: Optional[int] = None, z_pos: Optional[int] = None,
                        r_speed: Optional[int] = None, z_speed: Optional[int] = None, timeout: float = 120.0) -> dict:
        cmd = int(cmd_type)
        result = self.run_command(
            cmd,
            {"Stack_RPosSet": r_pos, "Stack_ZPosSet": z_pos, "Stack_RSpeed": r_speed, "Stack_ZSpeed": z_speed},
            clear_done=False,
            description=f"旋转堆栈:{STACK_CMD_LABELS.get(cmd, cmd)}",
            timeout=timeout,
        )
        if cmd == int(StackCommand.CAMERA_DETECT):
            result["detect_result"] = self.get_node_value(self.DETECT_RESULT_NODE, force_read=True)
        return result

    @not_action
    def get_status(self) -> dict:
        return {
            "column": current_column(self),
            "R": self.get_node_value("Stack_RPosFB", force_read=True),
            "Z": self.get_node_value("Stack_ZPosFB", force_read=True),
            "complete": self.get_node_value(self.COMPLETE_NODE, force_read=True),
            "detect_result": self.get_node_value(self.DETECT_RESULT_NODE, force_read=True),
        }


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)
    dev = RotaryStackDevice(url="opc.tcp://192.168.6.6:4840", csv_path=DEFAULT_CSV_PATH, use_subscription=False)
    time.sleep(2)

    while True:
        print("\n请选择操作：")
        print("1 复位(建立R0)")
        print("2 输入抓取数字 → 旋转到对应列(联调用)")
        print("3 直接旋转到指定列(1~12)")
        print("4 相机检测")
        print("5 查看状态")
        print("6 单指令调试(execute_command)")
        print("99 退出")
        choice = input("请输入序号：").strip()
        if choice == "99":
            break
        # 单条命令失败（如连接瞬断）只提示并回到菜单，不退出脚本（避免重启再堆会话）
        try:
            if choice == "1":
                print(dev.do_reset())
            elif choice == "2":
                number = int(input("抓取数字(工位号): ").strip() or "1")
                column = rotate_for_number(dev, number)
                print({"success": True, "number": number, "column": column, "r_pos": column_to_r(column)})
            elif choice == "3":
                print(dev.rotate_to(column=int(input("列号(1~12): ").strip() or "1")))
            elif choice == "4":
                print(dev.detect())
            elif choice == "5":
                print(dev.get_status())
            elif choice == "6":
                cmd_type = int(input("CmdType(1左旋 2右旋 3相机Z上 4相机Z下 5旋转至目标 6相机检测 7复位): ").strip() or "7")
                r_pos = input("r_pos(旋转至目标时填, 可空): ").strip()
                print(dev.execute_command(cmd_type=cmd_type, r_pos=int(r_pos) if r_pos else None))
            else:
                print("无效序号")
        except Exception as e:
            print(f"操作失败: {e!r}")

    dev.disconnect()
    print("退出程序。")
