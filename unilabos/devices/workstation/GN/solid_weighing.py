"""
固体加样 设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「固体加样」；节点：opcua_gn1.3.3.csv（前缀 Solid_）。

对外仅暴露 execute_command（Solid_CmdType + 写参）；测试流程 yaml 预设供本地调试。
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

# OPC 1.3.3 Solid_CmdType（与 Excel 表头一致）
SOLID_CMD_LABELS = {
    1: "X向左",
    2: "X向右",
    3: "Y向里",
    4: "Y向外",
    5: "夹爪Z向上",
    6: "夹爪Z向下",
    7: "D开门",
    8: "D关门",
    9: "取料筒时Y轴向里",
    10: "取料筒时Y轴向外",
    11: "加料",
    12: "夹爪夹料",
    13: "夹爪放料",
    14: "天枰去皮",
    15: "天枰称重",
    16: "料筒Z向上",
    17: "料筒Z向下",
    18: "放料筒时Y轴向里",
    19: "放料筒时Y轴向外",
    20: "复位",
    21: "夹爪夹紧",
    22: "夹爪松开",
    23: "xyz回原点",
}


class SolidCommand(int, Enum):
    """固体加样指令类型 (Solid_CmdType)"""

    X_LEFT = 1
    X_RIGHT = 2
    Y_IN = 3
    Y_OUT = 4
    GRIPPER_Z_UP = 5
    GRIPPER_Z_DOWN = 6
    DOOR_OPEN = 7
    DOOR_CLOSE = 8
    TAKE_CYLINDER_Y_IN = 9
    TAKE_CYLINDER_Y_OUT = 10
    DISPENSE = 11
    GRIPPER_PICK = 12
    GRIPPER_PLACE = 13
    BALANCE_TARE = 14
    BALANCE_WEIGH = 15
    CYLINDER_Z_UP = 16
    CYLINDER_Z_DOWN = 17
    PLACE_CYLINDER_Y_IN = 18
    PLACE_CYLINDER_Y_OUT = 19
    RESET = 20
    GRIPPER_CLAMP = 21
    GRIPPER_RELEASE = 22
    HOME_XYZ = 23


# 固体加样测试流程 yaml 预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS = [
    ("1.夹爪夹取（料架）", SolidCommand.GRIPPER_PICK, dict(
        x_pos=20, y_pos=2100, material_z_pos=0, gripper_z_pos=2200,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("2.夹爪放置（加样工位）", SolidCommand.GRIPPER_PLACE, dict(
        x_pos=-1730, y_pos=1540, material_z_pos=0, gripper_z_pos=1130,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("3.取料筒后向外", SolidCommand.TAKE_CYLINDER_Y_OUT, dict(
        x_pos=-1900, y_pos=2220, material_z_pos=235000, gripper_z_pos=0,
        door_pos=0, volune_weight=0,
        x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=0,
    )),
    ("4.料筒加样", SolidCommand.DISPENSE, dict(
        x_pos=-300, y_pos=700, material_z_pos=40000, gripper_z_pos=0,
        door_pos=3700, volune_weight=30,
        x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=150,
        timeout=600.0,
    )),
    ("5.放料筒向里", SolidCommand.PLACE_CYLINDER_Y_IN, dict(
        x_pos=-1900, y_pos=2220, material_z_pos=235000, gripper_z_pos=0,
        door_pos=0, volune_weight=0,
        x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=0,
    )),
    ("6.夹爪夹取（加样工位）", SolidCommand.GRIPPER_PICK, dict(
        x_pos=-1730, y_pos=1540, material_z_pos=0, gripper_z_pos=1130,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("7.夹爪放置（料架）", SolidCommand.GRIPPER_PLACE, dict(
        x_pos=20, y_pos=2100, material_z_pos=0, gripper_z_pos=2180,
        door_pos=0, volune_weight=0,
        x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
    )),
    ("8.复位", SolidCommand.RESET, dict(
        x_pos=0, y_pos=0, material_z_pos=0, gripper_z_pos=0,
        door_pos=0, volune_weight=0,
        x_speed=0, y_speed=0, material_z_speed=0, gripper_z_speed=0, door_speed=0,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 Solid_CmdType 执行 OPC 1.3.3 指令。"
    "1=X左 2=X右 3=Y向里 4=Y向外 5=夹爪Z上 6=夹爪Z下 7=D开门 8=D关门 "
    "9=取料筒Y向里 10=取料筒Y向外 11=加料 12=夹爪夹料 13=夹爪放料 "
    "14=天枰去皮 15=天枰称重 16=料筒Z上 17=料筒Z下 18=放料筒Y向里 19=放料筒Y向外 "
    "20=复位 21=夹爪夹紧 22=夹爪松开 23=xyz回原点。"
    "可选写参：x/y/material_z/gripper_z/door 位置与速度、volune_weight。"
)


@device(
    id="gn_solid_weighing",
    display_name="固体加样",
    category=["workstation"],
    description="GN 固体加样：OPC UA 1.3.3，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class SolidWeighingDevice(OpcUaClientWithSubscription):
    """固体加样设备类（OPC 前缀 Solid_）"""

    CMD_TYPE_NODE = "Solid_CmdType"
    CMD_TRIG_NODE = "Solid_CmdTrig"
    COMPLETE_NODE = "Solid_CompleteFB"

    def __init__(
        self,
        url: str,
        csv_path: str = DEFAULT_CSV_PATH,
        username: str = None,
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        enable_connection_monitor: bool = False,
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
            enable_connection_monitor=enable_connection_monitor,
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
        material_z_pos: Optional[int] = None,
        gripper_z_pos: Optional[int] = None,
        door_pos: Optional[int] = None,
        volune_weight: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        material_z_speed: Optional[int] = None,
        gripper_z_speed: Optional[int] = None,
        door_speed: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        setpoints = self._build_setpoints(
            x_pos=x_pos, y_pos=y_pos,
            material_z_pos=material_z_pos, gripper_z_pos=gripper_z_pos,
            door_pos=door_pos, volune_weight=volune_weight,
            x_speed=x_speed, y_speed=y_speed,
            material_z_speed=material_z_speed, gripper_z_speed=gripper_z_speed,
            door_speed=door_speed,
        )
        label = SOLID_CMD_LABELS.get(int(cmd_type), f"CmdType={int(cmd_type)}")
        return self._run(int(cmd_type), label, setpoints, timeout=timeout)

    @action(description="粉末定量加样 (cmd 11)，默认坐标为加样工位")
    def dispense_powder(
        self,
        weight_mg: int = 30,
        x_pos: int = -300,
        y_pos: int = 700,
        material_z_pos: int = 40000,
        gripper_z_pos: int = 0,
        door_pos: int = 3700,
        x_speed: int = 500,
        y_speed: int = 500,
        door_speed: int = 150,
        timeout: float = 600.0,
    ) -> dict:
        return self.execute_command(
            cmd_type=int(SolidCommand.DISPENSE),
            x_pos=x_pos,
            y_pos=y_pos,
            material_z_pos=material_z_pos,
            gripper_z_pos=gripper_z_pos,
            door_pos=door_pos,
            volune_weight=weight_mg,
            x_speed=x_speed,
            y_speed=y_speed,
            door_speed=door_speed,
            timeout=timeout,
        )

    @not_action
    def _build_setpoints(
        self,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        material_z_pos: Optional[int] = None,
        gripper_z_pos: Optional[int] = None,
        door_pos: Optional[int] = None,
        volune_weight: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        material_z_speed: Optional[int] = None,
        gripper_z_speed: Optional[int] = None,
        door_speed: Optional[int] = None,
    ) -> dict:
        mapping = {
            "Solid_XPosSet": x_pos,
            "Solid_YPosSet": y_pos,
            "Solid_MaterialZPosSet": material_z_pos,
            "Solid_GripperZPosSet": gripper_z_pos,
            "Solid_DoorPosSet": door_pos,
            "Solid_VoluneWeightSet": volune_weight,
            "Solid_XSpeed": x_speed,
            "Solid_YSpeed": y_speed,
            "Solid_MaterialZSpeed": material_z_speed,
            "Solid_GripperZSpeed": gripper_z_speed,
            "Solid_DoorSpeed": door_speed,
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
        logger.info(f"固体加样：{description} (CmdType={cmd_type})")
        if setpoints:
            for node, value in setpoints.items():
                self.set_node_value(node, value)
        result = self._trigger_and_wait(cmd_type, description, timeout=timeout)
        if cmd_type == int(SolidCommand.BALANCE_WEIGH):
            weight = self.get_node_value("Solid_WeightFB", force_read=True)
            result["weight"] = weight
            result["message"] = f"称重完成，重量={weight}"
        return result

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 180.0) -> dict:
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        if self._wait_until_true(self.COMPLETE_NODE, timeout=timeout, description=f"{description}完成"):
            self.set_node_value(self.CMD_TRIG_NODE, 0)
            if self._wait_until_false(self.COMPLETE_NODE, description=f"{description}完成复位"):
                logger.info(f"{description}完成")
                self._log_status(f"{description}后")
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
                logger.error(f"✗ 等待 {desc} 超时（{timeout}s，[{node_name}]={value!r}）")
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
                logger.error(f"✗ 等待 {desc} 超时（{timeout}s，[{node_name}]={value!r}）")
                return False
            time.sleep(interval)

    @not_action
    def run_test_flow(self) -> dict:
        """按固体加样测试流程 yaml 预设依次 execute_command（本地调试用）"""
        logger.info("固体加样：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            self.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        logger.info("固体加样：整体测试流程完成")
        return {"success": True, "message": "固体加样测试流程完成"}

    @not_action
    def get_weight(self) -> int:
        return self.get_node_value("Solid_WeightFB", force_read=True)

    @not_action
    def get_positions(self) -> dict:
        return {
            "X": self.get_node_value("Solid_XPosFB"),
            "Y": self.get_node_value("Solid_YPosFB"),
            "MaterialZ": self.get_node_value("Solid_MaterialZPosFB"),
            "GripperZ": self.get_node_value("Solid_GripperZPosFB"),
            "Door": self.get_node_value("Solid_DoorPosFB"),
        }

    @not_action
    def get_status(self) -> dict:
        status = self.get_positions()
        status["weight"] = self.get_weight()
        status["complete"] = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: X={status['X']} Y={status['Y']} "
            f"MaterialZ={status['MaterialZ']} GripperZ={status['GripperZ']} "
            f"Door={status['Door']} 重量={status['weight']} 完成={status['complete']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    SOLID_FEED_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    dev = SolidWeighingDevice(url=SOLID_FEED_URL, csv_path=DEFAULT_CSV_PATH)
    time.sleep(2)
    logger.info(f"固体加样连通性测试: {dev.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                dev._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    threading.Thread(target=_status_log_worker, daemon=True, name="SolidWeighingStatusLog").start()

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("14 天枰去皮 (CmdType=14)")
        print("15 天枰称重 (CmdType=15)")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "98":
            dev.run_test_flow()
        elif choice == "14":
            dev.execute_command(cmd_type=14)
        elif choice == "15":
            dev.execute_command(cmd_type=15)
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            dev.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    dev.disconnect()
    print("退出程序。")
