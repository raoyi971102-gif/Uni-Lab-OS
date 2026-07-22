"""
锁紧模块 设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「锁紧模块」；节点：opcua_gn1.3.3.csv（前缀 Lock_）。

对外仅暴露 execute_command（Lock_CmdType + 写参）；测试流程 yaml 预设供本地调试。
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

# OPC 1.3.3 Lock_CmdType（与 Excel 表头一致）
LOCK_CMD_LABELS = {
    1: "X向左",
    2: "X向右",
    3: "Y向左",
    4: "Y向右",
    5: "Z1向左",
    6: "Z1向右",
    7: "Z2向左",
    8: "Z2向右",
    9: "夹爪夹取",
    10: "夹爪放置",
    11: "电批拧紧",
    12: "电批拧松",
    13: "夹爪夹紧",
    14: "夹爪松开",
    16: "复位",
}


class LockCommand(int, Enum):
    """锁紧模块指令类型 (Lock_CmdType)"""

    X_LEFT = 1
    X_RIGHT = 2
    Y_LEFT = 3
    Y_RIGHT = 4
    Z1_LEFT = 5
    Z1_RIGHT = 6
    Z2_LEFT = 7
    Z2_RIGHT = 8
    JAW_PICK = 9
    JAW_PLACE = 10
    SCREW_TIGHTEN = 11
    SCREW_LOOSEN = 12
    JAW_CLAMP = 13
    JAW_RELEASE = 14
    RESET = 16


# 锁紧模块测试流程 yaml 预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS = [
    ("1.夹取耗材", LockCommand.JAW_PICK, dict(
        x_pos=3700, y_pos=930, z1_pos=0, z2_pos=1105,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=30.0, jaw_force=0.1,
    )),
    ("2.放置耗材", LockCommand.JAW_PLACE, dict(
        x_pos=760, y_pos=780, z1_pos=0, z2_pos=1150,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=11.0, jaw_force=0.1,
    )),
    ("3.夹取盖板", LockCommand.JAW_PICK, dict(
        x_pos=2290, y_pos=980, z1_pos=0, z2_pos=875,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=65.0, jaw_force=0.1,
    )),
    ("4.放置盖板", LockCommand.JAW_PLACE, dict(
        x_pos=760, y_pos=740, z1_pos=0, z2_pos=760,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=65.0, jaw_force=0.1,
    )),
    ("5.取螺丝（拧松）", LockCommand.SCREW_LOOSEN, dict(
        x_pos=1086, y_pos=430, z1_pos=1070, z2_pos=0,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=0.0, jaw_force=0.0,
    )),
    ("6.拧螺丝（拧紧）", LockCommand.SCREW_TIGHTEN, dict(
        x_pos=1270, y_pos=2030, z1_pos=1065, z2_pos=0,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=0.0, jaw_force=0.0,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 Lock_CmdType 执行 OPC 1.3.3 指令。"
    "1=X左 2=X右 3=Y左 4=Y右 5=Z1左 6=Z1右 7=Z2左 8=Z2右 "
    "9=夹爪夹取 10=夹爪放置 11=电批拧紧 12=电批拧松 13=夹爪夹紧 14=夹爪松开 16=复位。"
    "轴运动写对应 PosSet/Speed；夹取/放置/电批另写 jaw_position/jaw_force。"
)


@device(
    id="gn_locking_mechanism",
    display_name="锁紧模块",
    category=["workstation"],
    description="GN 锁紧模块：OPC UA 1.3.3，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class LockingMechanismDevice(OpcUaClientWithSubscription):
    """锁紧模块设备类（OPC 前缀 Lock_）"""

    CMD_TYPE_NODE = "Lock_CmdType"
    CMD_TRIG_NODE = "Lock_CmdTrig"
    COMPLETE_NODE = "Lock_CompleteFB"

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
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        jaw_position: Optional[float] = None,
        jaw_force: Optional[float] = None,
        timeout: float = 120.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        setpoints = self._build_setpoints(
            x_pos=x_pos, y_pos=y_pos, z1_pos=z1_pos, z2_pos=z2_pos,
            x_speed=x_speed, y_speed=y_speed, z1_speed=z1_speed, z2_speed=z2_speed,
            jaw_position=jaw_position, jaw_force=jaw_force,
        )
        label = LOCK_CMD_LABELS.get(int(cmd_type), f"CmdType={int(cmd_type)}")
        return self._run(int(cmd_type), label, setpoints, timeout=timeout)

    @not_action
    def _build_setpoints(
        self,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        jaw_position: Optional[float] = None,
        jaw_force: Optional[float] = None,
    ) -> dict:
        mapping = {
            "Lock_XPosSet": x_pos,
            "Lock_YPosSet": y_pos,
            "Lock_Z1PosSet": z1_pos,
            "Lock_Z2PosSet": z2_pos,
            "Lock_XSpeed": x_speed,
            "Lock_YSpeed": y_speed,
            "Lock_Z1Speed": z1_speed,
            "Lock_Z2Speed": z2_speed,
            "Lock_JawPosition": jaw_position,
            "Lock_JawForce": jaw_force,
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
        logger.info(f"锁紧模块：{description} (CmdType={cmd_type})")
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
        """按锁紧模块测试流程 yaml 预设依次 execute_command（本地调试用）"""
        logger.info("锁紧模块：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            label = LOCK_CMD_LABELS.get(int(cmd_type), str(cmd_type))
            self._run(int(cmd_type), f"{step_name}/{label}", self._build_setpoints(**preset))
        logger.info("锁紧模块：整体测试流程完成")
        return {"success": True, "message": "锁紧模块测试流程完成"}

    @not_action
    def get_positions(self) -> dict:
        return {
            "X": self.get_node_value("Lock_XPosFB"),
            "Y": self.get_node_value("Lock_YPosFB"),
            "Z1": self.get_node_value("Lock_Z1PosFB"),
            "Z2": self.get_node_value("Lock_Z2PosFB"),
        }

    @not_action
    def _log_positions(self, prefix: str = "位置反馈") -> None:
        pos = self.get_positions()
        complete = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        logger.info(
            f"{prefix}: X={pos['X']} Y={pos['Y']} Z1={pos['Z1']} Z2={pos['Z2']} 完成={complete}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    LOCKING_MECHANISM_URL = "opc.tcp://192.168.6.6:4840"
    POSITION_LOG_INTERVAL = 15.0

    dev = LockingMechanismDevice(url=LOCKING_MECHANISM_URL, csv_path=DEFAULT_CSV_PATH)
    time.sleep(2)

    position_log_running = True

    def _position_log_worker():
        while position_log_running:
            try:
                dev._log_positions("实时位置")
            except Exception as e:
                logger.warning(f"位置反馈日志异常: {e}")
            time.sleep(POSITION_LOG_INTERVAL)

    threading.Thread(target=_position_log_worker, daemon=True, name="LockPositionLog").start()

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("16 复位 (CmdType=16)")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "98":
            dev.run_test_flow()
        elif choice == "16":
            dev.execute_command(cmd_type=16)
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            dev.execute_command(cmd_type=int(cmd_type), **preset)
        else:
            print("无效的操作序号，请重新输入。")

    position_log_running = False
    dev.disconnect()
    print("退出程序。")
