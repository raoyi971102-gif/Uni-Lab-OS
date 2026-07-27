"""
快换模块设备驱动。

协议：opcua_gn1.3.6.csv「快换模块」（前缀 QuickChange_）。

对外仅暴露 execute_command，执行顺序为：
    写参数 → QuickChange_CmdType → QuickChange_CmdTrig=1
    → 等待 QuickChange_CompleteFB=1 → 清零命令。

QuickChange_CmdType：
    1=X向左 2=X向右 3=Z1向左 4=Z1向右
    5=Z2向左 6=Z2向右 7=推轴向左 8=推轴向右
    9=Z3向左 10=Z3向右 11=物料顶出 12=物料放置
    13=磁力搅拌运行 14=复位
"""

import logging
import os
import threading
import time
from enum import Enum
from typing import Optional

from unilabos.device_comms.opcua_client.node.uniopcua import DataType
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient
from unilabos.registry.decorators import action, device, not_action
from unilabos.utils.log import logger


_REAL_NODES = ("QuickChange_PushPosSet",)

DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "opcua_gn1.3.6.csv",
)


QUICK_CHANGE_CMD_LABELS = {
    1: "X向左",
    2: "X向右",
    3: "Z1向左",
    4: "Z1向右",
    5: "Z2向左",
    6: "Z2向右",
    7: "推轴向左",
    8: "推轴向右",
    9: "Z3向左",
    10: "Z3向右",
    11: "物料顶出",
    12: "物料放置",
    13: "磁力搅拌运行",
    14: "复位",
}


class QuickChangeCommand(int, Enum):
    """快换模块指令类型 (QuickChange_CmdType)。"""

    X_LEFT = 1
    X_RIGHT = 2
    Z1_LEFT = 3
    Z1_RIGHT = 4
    Z2_LEFT = 5
    Z2_RIGHT = 6
    PUSH_LEFT = 7
    PUSH_RIGHT = 8
    Z3_LEFT = 9
    Z3_RIGHT = 10
    MATERIAL_EJECT = 11
    MATERIAL_PLACE = 12
    MAGNETIC_STIR = 13
    RESET = 14


# 《快换模块测试流程.yaml》参数：
# XPos/Z1Pos/Z2Pos/PushBoardPos/Z3Pos 分别对应以下 OPC UA 写入参数。
TEST_FLOW_PRESETS = {
    int(QuickChangeCommand.MATERIAL_EJECT): {
        "x_pos": 0,
        "top_z_pos": -830,
        "take_z_pos": 1800,
        "push_pos": 240,
        "push_z_pos": 0,
        "x_speed": 300,
        "z1_speed": 100,
        "z2_speed": 100,
        "push_speed": 50,
        "z3_speed": 0,
        "stir_rpm": 0,
        "stir_temp": 0,
        "stir_time_minutes": 0,
    },
    int(QuickChangeCommand.MATERIAL_PLACE): {
        "x_pos": 1800,
        "top_z_pos": 0,
        "take_z_pos": 1600,
        "push_pos": 184.9480,
        "push_z_pos": 2100,
        "x_speed": 300,
        "z1_speed": 100,
        "z2_speed": 100,
        "push_speed": 50,
        "z3_speed": 100,
        "stir_rpm": 0,
        "stir_temp": 0,
        "stir_time_minutes": 0,
    },
}


_EXECUTE_CMD_DOC = (
    "按 QuickChange_CmdType 执行 OPC UA 1.3.6 指令。"
    "1=X左 2=X右 3=Z1左 4=Z1右 5=Z2左 6=Z2右 "
    "7=推轴左 8=推轴右 9=Z3左 10=Z3右 "
    "11=物料顶出 12=物料放置 13=磁力搅拌运行 14=复位。"
    "位置参数：x_pos/top_z_pos/take_z_pos/push_pos/push_z_pos；"
    "速度参数：x_speed/z1_speed/z2_speed/push_speed/z3_speed；"
    "搅拌参数：stir_rpm/stir_temp/stir_time_minutes。"
)


@device(
    id="gn_quick_carrier_exchange",
    display_name="快换模块",
    category=["workstation"],
    description="GN 快换模块：OPC UA 1.3.6，按完成反馈执行命令",
    icon="",
    version="2.0.0",
)
class QuickCarrierExchangeDevice(GNStationClient):
    """快换模块设备类（OPC 前缀 QuickChange_，通过 self.plc 共享 GN 工站单例 OPC UA 会话）。"""

    PREFIX = "QuickChange_"
    CMD_TYPE_NODE = "QuickChange_CmdType"
    CMD_TRIG_NODE = "QuickChange_CmdTrig"
    COMPLETE_NODE = "QuickChange_CompleteFB"

    POSITION_NODES = {
        "QuickChange_XPosSet": "QuickChange_XPosFB",
        "QuickChange_TopZPosSet": "QuickChange_Z1PosFB",
        "QuickChange_TakeZPosSet": "QuickChange_Z2PosFB",
        "QuickChange_PushPosSet": "QuickChange_PushPosFB",
        "QuickChange_PushZPosSet": "QuickChange_Z3PosFB",
    }
    _POSITION_COMMANDS = frozenset(range(1, 11))

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
        # 本设备内部串行化 execute_command，防同一设备多线程并发下发指令；
        # OPC UA 会话保活已上移到 GnPlcClient（整站共用一个后台线程）。
        self._command_lock = threading.Lock()
        # 按 PLC 当前 REAL 定义修正 CSV 中仍标记为 INT16 的节点类型
        self._fix_real_node_dtypes()

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        top_z_pos: Optional[int] = None,
        take_z_pos: Optional[int] = None,
        push_pos: Optional[float] = None,
        push_z_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        push_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        stir_rpm: Optional[int] = None,
        stir_temp: Optional[int] = None,
        stir_time_minutes: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        cmd = int(cmd_type)
        if cmd not in QUICK_CHANGE_CMD_LABELS:
            raise ValueError(
                f"不支持的 QuickChange_CmdType={cmd}，有效范围为 1-14"
            )

        setpoints = self._build_setpoints(
            x_pos=x_pos,
            top_z_pos=top_z_pos,
            take_z_pos=take_z_pos,
            push_pos=push_pos,
            push_z_pos=push_z_pos,
            x_speed=x_speed,
            z1_speed=z1_speed,
            z2_speed=z2_speed,
            push_speed=push_speed,
            z3_speed=z3_speed,
            stir_rpm=stir_rpm,
            stir_temp=stir_temp,
            stir_time_minutes=stir_time_minutes,
        )

        if timeout is None or float(timeout) <= 0:
            if cmd == int(QuickChangeCommand.MAGNETIC_STIR):
                minutes = stir_time_minutes if stir_time_minutes is not None else 0
                timeout = float(minutes) * 60 + 300.0
            else:
                timeout = 180.0

        return self._run(
            cmd_type=cmd,
            description=QUICK_CHANGE_CMD_LABELS[cmd],
            setpoints=setpoints,
            timeout=float(timeout),
        )

    @not_action
    def _build_setpoints(
        self,
        x_pos: Optional[int] = None,
        top_z_pos: Optional[int] = None,
        take_z_pos: Optional[int] = None,
        push_pos: Optional[float] = None,
        push_z_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        push_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        stir_rpm: Optional[int] = None,
        stir_temp: Optional[int] = None,
        stir_time_minutes: Optional[int] = None,
    ) -> dict:
        mapping = {
            "QuickChange_XPosSet": x_pos,
            "QuickChange_TopZPosSet": top_z_pos,
            "QuickChange_TakeZPosSet": take_z_pos,
            "QuickChange_PushPosSet": (
                float(push_pos) if push_pos is not None else None
            ),
            "QuickChange_PushZPosSet": push_z_pos,
            "QuickChange_XSpeed": x_speed,
            "QuickChange_Z1Speed": z1_speed,
            "QuickChange_Z2Speed": z2_speed,
            "QuickChange_PushSpeed": push_speed,
            "QuickChange_Z3Speed": z3_speed,
            "QuickChange_StirRPM": stir_rpm,
            "QuickChange_StirTemp": stir_temp,
            "QuickChange_StirTime": stir_time_minutes,
        }
        return {
            node_name: value
            for node_name, value in mapping.items()
            if value is not None
        }

    @not_action
    def _fix_real_node_dtypes(self) -> None:
        """按 PLC 当前 REAL 定义修正 CSV 中仍标记为 INT16 的节点类型。"""
        for node_name in _REAL_NODES:
            node = self._node_registry.get(node_name)
            if node is not None and hasattr(node, "_data_type"):
                if node._data_type != DataType.FLOAT:
                    logger.info(
                        f"{node_name} 数据类型 {node._data_type} → FLOAT"
                        "（对齐 PLC REAL）"
                    )
                    node._data_type = DataType.FLOAT

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 180.0,
    ) -> dict:
        with self._command_lock:
            logger.info(f"快换模块：{description} (CmdType={cmd_type})")
            for node_name, value in (setpoints or {}).items():
                if not self._opc_write(node_name, value):
                    raise ValueError(f"写入 {node_name}={value} 失败")
            return self._trigger_and_wait(
                cmd_type=cmd_type,
                description=description,
                setpoints=setpoints or {},
                timeout=timeout,
            )

    @not_action
    def _trigger_and_wait(
        self,
        cmd_type: int,
        description: str,
        setpoints: dict,
        timeout: float,
    ) -> dict:
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if not self._opc_write(self.CMD_TYPE_NODE, int(cmd_type)):
            raise ValueError(f"QuickChange_CmdType={cmd_type} 写入失败")
        if not self._opc_write(self.CMD_TRIG_NODE, 1):
            raise ValueError("QuickChange_CmdTrig=1 写入失败")

        completed = False
        try:
            completed = self._wait_complete(
                setpoints=setpoints,
                allow_position_fallback=cmd_type in self._POSITION_COMMANDS,
                timeout=timeout,
                description=f"{description}完成",
            )
            if not completed:
                raise ValueError(
                    f"{description}失败，QuickChange_CompleteFB 未变为 1"
                )
        finally:
            trigger_cleared = self._opc_write(self.CMD_TRIG_NODE, 0)
            command_cleared = self._opc_write(self.CMD_TYPE_NODE, 0)
            trigger_value = self._opc_read(self.CMD_TRIG_NODE, force_read=True)
            command_value = self._opc_read(self.CMD_TYPE_NODE, force_read=True)
            logger.info(
                "快换模块命令清理："
                f"CmdTrig={trigger_value!r}，CmdType={command_value!r}"
            )
            if completed and (
                not trigger_cleared
                or not command_cleared
                or trigger_value != 0
                or command_value != 0
            ):
                raise ValueError(
                    "动作已完成，但命令清零失败："
                    f"QuickChange_CmdTrig={trigger_value!r}, "
                    f"QuickChange_CmdType={command_value!r}"
                )

        self._log_status(f"{description}后")
        return {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": int(cmd_type),
        }

    @not_action
    def _wait_complete(
        self,
        setpoints: dict,
        allow_position_fallback: bool,
        timeout: float,
        description: str,
        interval: float = 0.1,
    ) -> bool:
        logger.info(f"等待 {description}（{self.COMPLETE_NODE}=1）...")
        start = time.monotonic()
        read_fail_streak = 0
        while time.monotonic() - start < timeout:
            value = self._opc_read(self.COMPLETE_NODE, force_read=True)
            if value is None:
                read_fail_streak += 1
                if read_fail_streak >= 3:
                    logger.error(
                        f"✗ {description}中止：{self.COMPLETE_NODE} 连续读取失败"
                    )
                    return False
            else:
                read_fail_streak = 0
                if int(value) == 1:
                    logger.info(f"✓ {description}（{self.COMPLETE_NODE}={value}）")
                    return True
            time.sleep(interval)

        if allow_position_fallback:
            targets = self._position_targets(setpoints)
            if targets and self._positions_reached(targets):
                logger.warning(
                    f"{description}：完成反馈未回 1，但位置已到位，作超时兜底"
                )
                return True
        complete = self._opc_read(self.COMPLETE_NODE, force_read=True)
        logger.error(
            f"✗ 等待 {description}超时（{timeout}s，"
            f"{self.COMPLETE_NODE}={complete!r}）"
        )
        return False

    @not_action
    def _position_targets(self, setpoints: dict) -> dict:
        return {
            feedback_node: float(setpoints[setpoint_node])
            for setpoint_node, feedback_node in self.POSITION_NODES.items()
            if setpoint_node in setpoints
        }

    @not_action
    def _positions_reached(
        self,
        targets: dict,
        tolerance: int = 5,
        stable_samples: int = 3,
        interval: float = 0.1,
        sample_timeout: float = 2.0,
    ) -> bool:
        start = time.monotonic()
        stable_count = 0
        while time.monotonic() - start < sample_timeout:
            values = {
                node_name: self._opc_read(node_name, force_read=True)
                for node_name in targets
            }
            reached = all(
                values[node_name] is not None
                and abs(int(values[node_name]) - target) <= tolerance
                for node_name, target in targets.items()
            )
            stable_count = stable_count + 1 if reached else 0
            if stable_count >= stable_samples:
                logger.info(f"✓ 快换模块位置到位：{values}")
                return True
            time.sleep(interval)
        return False

    @not_action
    def run_test_flow(self) -> dict:
        """按《快换模块测试流程.yaml》依次执行物料顶出和物料放置。"""
        for cmd_type in (
            int(QuickChangeCommand.MATERIAL_EJECT),
            int(QuickChangeCommand.MATERIAL_PLACE),
        ):
            logger.info(
                f"--- 测试流程：{QUICK_CHANGE_CMD_LABELS[cmd_type]} "
                f"(CmdType={cmd_type}) ---"
            )
            self.execute_command(
                cmd_type=cmd_type,
                **TEST_FLOW_PRESETS[cmd_type],
            )
        return {"success": True, "message": "快换模块测试流程完成"}

    @not_action
    def get_status(self) -> dict:
        return {
            "X": self._opc_read("QuickChange_XPosFB", force_read=True),
            "Z1": self._opc_read("QuickChange_Z1PosFB", force_read=True),
            "Z2": self._opc_read("QuickChange_Z2PosFB", force_read=True),
            "Push": self._opc_read("QuickChange_PushPosFB", force_read=True),
            "Z3": self._opc_read("QuickChange_Z3PosFB", force_read=True),
            "complete": self._opc_read(self.COMPLETE_NODE, force_read=True),
            "stir_rpm": self._opc_read("QuickChange_StirRPM", force_read=True),
            "stir_temp": self._opc_read("QuickChange_StirTemp", force_read=True),
            "stir_time_minutes": self._opc_read(
                "QuickChange_StirTime",
                force_read=True,
            ),
        }

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: X={status['X']} Z1={status['Z1']} Z2={status['Z2']} "
            f"推轴={status['Push']} Z3={status['Z3']} "
            f"完成={status['complete']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    QUICK_CHANGE_URL = "opc.tcp://192.168.6.6:4840"
    dev = QuickCarrierExchangeDevice(
        url=QUICK_CHANGE_URL,
        csv_path=DEFAULT_CSV_PATH,
        use_subscription=False,
    )
    time.sleep(2)
    logger.info(f"快换模块连通性测试: {dev.get_status()}")

    try:
        while True:
            print("\n请选择快换模块操作：")
            for cmd, label in QUICK_CHANGE_CMD_LABELS.items():
                print(f"{cmd:>2} {label}")
            print("97 执行完整测试流程（物料顶出 → 物料放置）")
            print("98 查看状态")
            print("99 退出")
            choice = input("请输入操作序号：").strip()
            if choice == "99":
                break
            if choice == "97":
                dev.run_test_flow()
                continue
            if choice == "98":
                print(dev.get_status())
                continue
            if choice.isdigit() and int(choice) in QUICK_CHANGE_CMD_LABELS:
                cmd_type = int(choice)
                preset = TEST_FLOW_PRESETS.get(cmd_type, {})
                dev.execute_command(cmd_type=cmd_type, **preset)
            else:
                print("无效的操作序号，请重新输入。")
    finally:
        dev.disconnect()
        print("退出程序。")
