"""
快换模块设备驱动。

协议：opcua_gn1.3.7.csv「快换模块」（前缀 QuickChange_）。

对外仅暴露 execute_command，执行顺序为：
    清 CompleteFB → 写参数 → QuickChange_CmdType → QuickChange_CmdTrig=1
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

from unilabos.devices.workstation.GN.gn_station_base import GNStationClient
from unilabos.registry.decorators import action, device, not_action
from unilabos.utils.log import logger


DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "opcua_gn1.3.7.csv",
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
        "push_pos": 185,
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
    "按 QuickChange_CmdType 执行 OPC UA 1.3.7 指令。"
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
    description="GN 快换模块：OPC UA 1.3.7，按完成反馈执行命令",
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

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        top_z_pos: Optional[int] = None,
        take_z_pos: Optional[int] = None,
        push_pos: Optional[int] = None,
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
        """唯一注册动作：持锁间隔 → 清 CompleteFB → 写参 → CmdType → CmdTrig → 等 CompleteFB。

        在 ``_command_lock`` 内先 ``sleep(5)`` 再写 OPC，避免云端连续 job 时上一条
        动作未完成就预清零 CompleteFB 或写入下一条 Set 参数。
        """
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
            allow_position_fallback=cmd in self._POSITION_COMMANDS,
        )

    @not_action
    def _build_setpoints(
        self,
        x_pos: Optional[int] = None,
        top_z_pos: Optional[int] = None,
        take_z_pos: Optional[int] = None,
        push_pos: Optional[int] = None,
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
                int(push_pos) if push_pos is not None else None
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
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 180.0,
        allow_position_fallback: bool = False,
    ) -> dict:
        with self._command_lock:
            # 云端可能连续下发多条 job；持锁后先等 5s，避免上一条 PLC 动作未结束就写 Set/清 CompleteFB
            time.sleep(5)
            if not self.set_node_value(self.COMPLETE_NODE, 0):
                logger.warning(
                    f"快换模块：{self.COMPLETE_NODE} 预清零失败"
                    "（可能只读或链路异常），继续下发指令"
                )
            logger.info(f"快换模块：{description} (CmdType={cmd_type})")
            if setpoints:
                for node, value in setpoints.items():
                    ok = self.set_node_value(node, value)
                    if not ok:
                        raise ValueError(f"写入 {node}={value} 失败")
            return self._trigger_and_wait(
                cmd_type,
                description,
                setpoints=setpoints or {},
                allow_position_fallback=allow_position_fallback,
                timeout=timeout,
            )

    @not_action
    def _trigger_and_wait(
        self,
        cmd_type: int,
        description: str,
        setpoints: dict,
        allow_position_fallback: bool,
        timeout: float,
    ) -> dict:
        """下发命令并等待 CompleteFB=1；完成后清零 CmdTrig/CmdType。"""
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        # 先清 CmdTrig，保证本次 CmdTrig=1 为上升沿（与机械手/离心机 finally 清理衔接）
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        if not self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type)):
            raise ValueError(f"QuickChange_CmdType={cmd_type} 写入失败")
        if not self.set_node_value(self.CMD_TRIG_NODE, 1):
            raise ValueError("QuickChange_CmdTrig=1 写入失败")

        completed = False
        try:
            completed = self._wait_until_true(
                self.COMPLETE_NODE,
                timeout=timeout,
                description=f"{description}完成",
            )
            if not completed and allow_position_fallback:
                targets = self._position_targets(setpoints)
                if targets and self._positions_reached(targets):
                    logger.warning(
                        f"{description}：CompleteFB 未回 1，但位置已到位，作超时兜底"
                    )
                    completed = True
            if not completed:
                raise ValueError(
                    f"{description}失败，QuickChange_CompleteFB 未变为 1"
                )
        finally:
            trigger_cleared = self.set_node_value(self.CMD_TRIG_NODE, 0)
            command_cleared = self.set_node_value(self.CMD_TYPE_NODE, 0)
            trigger_value = self.get_node_value(self.CMD_TRIG_NODE, force_read=True)
            command_value = self.get_node_value(self.CMD_TYPE_NODE, force_read=True)
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
    def _wait_until_true(
        self,
        node_name: str,
        timeout: float = 180.0,
        interval: float = 0.2,
        description: str = None,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（节点: {node_name}）...")
        start = time.time()
        while True:
            value = self.get_node_value(node_name, force_read=True)
            if value:
                logger.info(f"✓ {desc}（[{node_name}]={value}）")
                return True
            if time.time() - start >= timeout:
                logger.error(
                    f"✗ 等待 {desc} 超时（{timeout}s，[{node_name}]={value!r}）"
                )
                return False
            time.sleep(interval)

    @not_action
    def _wait_until_false(
        self,
        node_name: str,
        timeout: float = 180.0,
        interval: float = 0.2,
        description: str = None,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc} 复位（节点: {node_name}）...")
        start = time.time()
        while True:
            value = self.get_node_value(node_name, force_read=True)
            if not value:
                logger.info(f"✓ {desc}（[{node_name}]={value}）")
                return True
            if time.time() - start >= timeout:
                logger.error(
                    f"✗ 等待 {desc} 超时（{timeout}s，[{node_name}]={value!r}）"
                )
                return False
            time.sleep(interval)

    @not_action
    def _position_targets(self, setpoints: dict) -> dict:
        return {
            feedback_node: int(setpoints[setpoint_node])
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
            last_values = {
                node_name: self.get_node_value(node_name, force_read=True)
                for node_name in targets
            }
            reached = all(
                last_values[node_name] is not None
                and abs(int(last_values[node_name]) - target) <= tolerance
                for node_name, target in targets.items()
            )
            stable_count = stable_count + 1 if reached else 0
            if stable_count >= stable_samples:
                logger.info(f"✓ 快换模块位置到位：{last_values}")
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
            "X": self.get_node_value("QuickChange_XPosFB", force_read=True),
            "Z1": self.get_node_value("QuickChange_Z1PosFB", force_read=True),
            "Z2": self.get_node_value("QuickChange_Z2PosFB", force_read=True),
            "Push": self.get_node_value("QuickChange_PushPosFB", force_read=True),
            "Z3": self.get_node_value("QuickChange_Z3PosFB", force_read=True),
            "complete": self.get_node_value(self.COMPLETE_NODE, force_read=True),
            "stir_rpm": self.get_node_value("QuickChange_StirRPM", force_read=True),
            "stir_temp": self.get_node_value("QuickChange_StirTemp", force_read=True),
            "stir_time_minutes": self.get_node_value(
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
