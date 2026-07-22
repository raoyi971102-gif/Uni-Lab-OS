"""
快换模块 设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「快换」；节点：opcua_gn1.3.3.csv（前缀 QuickChange_）。

对外仅暴露 execute_command（QuickChange_CmdType + 写参）；测试流程 yaml 预设供本地调试。

指令类型 (QuickChange_CmdType)：
    1=X向左  2=X向右  3=Z1向左 4=Z1向右
    5=Z2向左 6=Z2向右 7=推轴向左 8=推轴向右
    9=Z3向左 10=Z3向右 11=物料顶出 12=物料放置
    13=磁力搅拌运行 14=复位

YAML 字段 → CSV 节点映射：
    XPos          → QuickChange_XPosSet
    Z1Pos         → QuickChange_TopZPosSet（顶料Z）
    Z2Pos         → QuickChange_TakeZPosSet（接料Z）
    PushBoardPos  → QuickChange_PushPosSet（推轴）
    Z3Pos         → QuickChange_PushZPosSet（压料Z）
    XSpeed        → QuickChange_XSpeed
    Z1Speed       → QuickChange_Z1Speed
    Z2Speed       → QuickChange_Z2Speed
    PushBoardSpeed→ QuickChange_PushSpeed
    Z3Speed       → QuickChange_Z3Speed
    RPM/Temp/Time → QuickChange_StirRPM/StirTemp/StirTime
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

# OPC 1.3.3 QuickChange_CmdType（与 Excel 表头一致）
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
    """快换模块指令类型 (QuickChange_CmdType)"""

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
    EJECT_MATERIAL = 11
    PLACE_MATERIAL = 12
    STIR_RUN = 13
    RESET = 14


# 快换模块测试流程 yaml 预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS = [
    ("1.物料顶出", QuickChangeCommand.EJECT_MATERIAL, dict(
        x_pos=0, top_z_pos=-830, take_z_pos=1800, push_pos=240, push_z_pos=0,
        x_speed=300, z1_speed=100, z2_speed=100, push_speed=50, z3_speed=0,
        stir_rpm=0, stir_temp=0, stir_time_minutes=0,
    )),
    ("2.物料放置", QuickChangeCommand.PLACE_MATERIAL, dict(
        x_pos=1810, top_z_pos=0, take_z_pos=1600, push_pos=240, push_z_pos=2100,
        x_speed=300, z1_speed=100, z2_speed=100, push_speed=50, z3_speed=100,
        stir_rpm=0, stir_temp=0, stir_time_minutes=0,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 QuickChange_CmdType 执行 OPC 1.3.3 指令。"
    "1=X左 2=X右 3=Z1左 4=Z1右 5=Z2左 6=Z2右 7=推轴左 8=推轴右 "
    "9=Z3左 10=Z3右 11=物料顶出 12=物料放置 13=磁力搅拌运行 14=复位。"
    "轴运动写 x_pos/top_z_pos/take_z_pos/push_pos/push_z_pos 及对应速度；"
    "搅拌写 stir_rpm/stir_temp/stir_time_minutes。"
)


@device(
    id="gn_quick_carrier_exchange",
    display_name="快换模块",
    category=["workstation"],
    description="GN 快换模块：OPC UA 1.3.3，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class QuickCarrierExchangeDevice(OpcUaClientWithSubscription):
    """快换模块设备类（OPC 前缀 QuickChange_）"""

    CMD_TYPE_NODE = "QuickChange_CmdType"
    CMD_TRIG_NODE = "QuickChange_CmdTrig"
    COMPLETE_NODE = "QuickChange_CompleteFB"

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
        timeout: float = 120.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        cmd = int(cmd_type)
        effective_timeout = timeout
        if cmd == int(QuickChangeCommand.STIR_RUN) and timeout == 120.0:
            minutes = stir_time_minutes if stir_time_minutes is not None else 1
            effective_timeout = minutes * 60 + 60
        setpoints = self._build_setpoints(
            x_pos=x_pos, top_z_pos=top_z_pos, take_z_pos=take_z_pos,
            push_pos=push_pos, push_z_pos=push_z_pos,
            x_speed=x_speed, z1_speed=z1_speed, z2_speed=z2_speed,
            push_speed=push_speed, z3_speed=z3_speed,
            stir_rpm=stir_rpm, stir_temp=stir_temp, stir_time_minutes=stir_time_minutes,
        )
        label = QUICK_CHANGE_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        return self._run(cmd, label, setpoints, timeout=effective_timeout)

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
            "QuickChange_PushPosSet": push_pos,
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
        return {node: val for node, val in mapping.items() if val is not None}

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 120.0,
    ) -> dict:
        logger.info(f"快换模块：{description} (CmdType={cmd_type})")
        if setpoints:
            for node, value in setpoints.items():
                self.set_node_value(node, value)
        return self._trigger_and_wait(cmd_type, description, timeout=timeout)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        if self._wait_until_true(self.COMPLETE_NODE, timeout=timeout, description=f"{description}完成"):
            self.set_node_value(self.CMD_TRIG_NODE, 0)
            if self._wait_until_false(self.COMPLETE_NODE, description=f"{description}完成复位"):
                logger.info(f"{description}完成")
                self._log_positions(f"{description}后")
                return {
                    "success": True,
                    "message": f"{description}完成",
                    "cmd_type": int(cmd_type),
                }
            raise ValueError(f"{description}失败，完成复位超时")
        raise ValueError(f"{description}失败，动作未完成")

    @not_action
    def _wait_until_true(
        self,
        node_name: str,
        timeout: float = 120.0,
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
                logger.error(f"✗ 等待 {desc} 超时（{timeout}s，[{node_name}]={value!r}）")
                return False
            time.sleep(interval)

    @not_action
    def _wait_until_false(
        self,
        node_name: str,
        timeout: float = 120.0,
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
                logger.error(f"✗ 等待 {desc} 超时（{timeout}s，[{node_name}]={value!r}）")
                return False
            time.sleep(interval)

    @not_action
    def run_test_flow(self) -> dict:
        """按快换模块测试流程 yaml 预设依次 execute_command（本地调试用）"""
        logger.info("快换模块：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            label = QUICK_CHANGE_CMD_LABELS.get(int(cmd_type), str(cmd_type))
            self._run(int(cmd_type), f"{step_name}/{label}", self._build_setpoints(**preset))
        logger.info("快换模块：整体测试流程完成")
        return {"success": True, "message": "快换模块测试流程完成"}

    @not_action
    def get_positions(self) -> dict:
        return {
            "X": self.get_node_value("QuickChange_XPosFB"),
            "Z1": self.get_node_value("QuickChange_Z1PosFB"),
            "Z2": self.get_node_value("QuickChange_Z2PosFB"),
            "Z3": self.get_node_value("QuickChange_Z3PosFB"),
            "Push": self.get_node_value("QuickChange_PushPosFB"),
        }

    @not_action
    def _log_positions(self, prefix: str = "位置反馈") -> None:
        pos = self.get_positions()
        complete = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        logger.info(
            f"{prefix}: X={pos['X']} Z1={pos['Z1']} Z2={pos['Z2']} "
            f"Z3={pos['Z3']} Push={pos['Push']} 完成={complete}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    QUICK_CHANGE_URL = "opc.tcp://192.168.6.6:4840"
    POSITION_LOG_INTERVAL = 15.0

    dev = QuickCarrierExchangeDevice(url=QUICK_CHANGE_URL, csv_path=DEFAULT_CSV_PATH)
    time.sleep(2)

    position_log_running = True

    def _position_log_worker():
        while position_log_running:
            try:
                dev._log_positions("实时位置")
            except Exception as e:
                logger.warning(f"位置反馈日志异常: {e}")
            time.sleep(POSITION_LOG_INTERVAL)

    threading.Thread(target=_position_log_worker, daemon=True, name="QuickChangePositionLog").start()

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("14 复位 (CmdType=14)")
        print("13 磁力搅拌运行（输入 RPM/温度/时间）")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "98":
            dev.run_test_flow()
        elif choice == "14":
            dev.execute_command(cmd_type=14)
        elif choice == "13":
            rpm = int(input("转速 RPM [100]: ").strip() or "100")
            temp = int(input("温度 [25]: ").strip() or "25")
            time_minutes = int(input("时间(分) [1]: ").strip() or "1")
            dev.execute_command(
                cmd_type=13, stir_rpm=rpm, stir_temp=temp, stir_time_minutes=time_minutes,
            )
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            dev.execute_command(cmd_type=int(cmd_type), **preset)
        else:
            print("无效的操作序号，请重新输入。")

    position_log_running = False
    dev.disconnect()
    print("退出程序。")
