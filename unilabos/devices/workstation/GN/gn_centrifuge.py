"""
离心机 设备驱动

协议：opcua_gn1.3.6.csv「离心机」（前缀 Centrifuge_）。

对外仅暴露 execute_command（Centrifuge_CmdType + 写参）；测试流程 yaml 预设供本地调试。

指令类型 (Centrifuge_CmdType)：
    1=Y向左 2=Y向右 3=Z向左 4=Z向右
    5=放入物料 6=运行离心机 7=取出物料 8=复位
    9=夹爪张开 10=夹爪夹紧

YAML 字段 → CSV 节点映射：
    YPos   → Centrifuge_YPosSet
    Z1Pos  → Centrifuge_ZPosSet（台面Z）
    Z2Pos  → Centrifuge_InnerZPosSet（离心机内Z）
    RPM    → Centrifuge_RPM
    Time   → Centrifuge_Time
    YSpeed → Centrifuge_YSpeed
    ZSpeed → Centrifuge_ZSpeed
    PlateNo→ Centrifuge_PlateNo
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

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.6.csv")

# Centrifuge_CmdType（与 CSV 表头一致）
CENTRIFUGE_CMD_LABELS = {
    1: "Y向左",
    2: "Y向右",
    3: "Z向左",
    4: "Z向右",
    5: "放入物料",
    6: "运行离心机",
    7: "取出物料",
    8: "复位",
    9: "夹爪张开",
    10: "夹爪夹紧",
}


class CentrifugeCommand(int, Enum):
    """离心机指令类型 (Centrifuge_CmdType)"""

    Y_LEFT = 1
    Y_RIGHT = 2
    Z_LEFT = 3
    Z_RIGHT = 4
    LOAD_MATERIAL = 5
    RUN = 6
    UNLOAD_MATERIAL = 7
    RESET = 8
    JAW_OPEN = 9
    JAW_CLAMP = 10


# 离心机模块测试流程 yaml 预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS = [
    ("1.放入物料", CentrifugeCommand.LOAD_MATERIAL, dict(
        y_pos=-1700, z_pos=1000, inner_z_pos=3450,
        rpm=0, time_minutes=0, y_speed=300, z_speed=300, plate_no=2,
    )),
    ("2.离心机启动", CentrifugeCommand.RUN, dict(
        y_pos=0, z_pos=0, inner_z_pos=0,
        rpm=1000, time_minutes=1, y_speed=0, z_speed=0, plate_no=2,
    )),
    ("3.取出物料", CentrifugeCommand.UNLOAD_MATERIAL, dict(
        y_pos=-1700, z_pos=100, inner_z_pos=3450,
        rpm=0, time_minutes=0, y_speed=300, z_speed=300, plate_no=2,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 Centrifuge_CmdType 执行 OPC 指令。"
    "1=Y左 2=Y右 3=Z左 4=Z右 5=放入物料 6=运行离心机 7=取出物料 "
    "8=复位 9=夹爪张开 10=夹爪夹紧。"
    "写 y_pos/z_pos/inner_z_pos/rpm/time_minutes/y_speed/z_speed/plate_no。"
)


@device(
    id="gn_centrifuge",
    display_name="离心机",
    category=["workstation"],
    description="GN 离心机：OPC UA，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class CentrifugeDevice(GNStationClient):
    """离心机设备类（OPC 前缀 Centrifuge_，通过 self.plc 共享 GN 工站单例 OPC UA 会话）"""

    PREFIX = "Centrifuge_"
    CMD_TYPE_NODE = "Centrifuge_CmdType"
    CMD_TRIG_NODE = "Centrifuge_CmdTrig"
    COMPLETE_NODE = "Centrifuge_CompleteFB"

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
        self._command_lock = threading.Lock()

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        y_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        inner_z_pos: Optional[int] = None,
        rpm: Optional[int] = None,
        time_minutes: Optional[int] = None,
        y_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
        plate_no: Optional[int] = None,
        timeout: float = 120.0,
    ) -> dict:
        """唯一注册动作：清 CompleteFB → 写参 → CmdType → CmdTrig → 等 CompleteFB。

        下发前主动把 `Centrifuge_CompleteFB` 清 0，避免上一轮遗留的 1 让本次的
        `_wait_until_true` 立刻假阳性通过。写不进去（PLC 只读该点位等）时降级为
        warning，不阻断后续下发。
        """
        if not self.set_node_value(self.COMPLETE_NODE, 0):
            logger.warning(
                f"离心机：{self.COMPLETE_NODE} 预清零失败（可能只读或链路异常），继续下发指令"
            )
        cmd = int(cmd_type)
        effective_timeout = timeout
        if cmd == int(CentrifugeCommand.RUN) and timeout == 120.0:
            minutes = time_minutes if time_minutes is not None else 1
            effective_timeout = minutes * 60 + 180
        setpoints = self._build_setpoints(
            y_pos=y_pos, z_pos=z_pos, inner_z_pos=inner_z_pos,
            rpm=rpm, time_minutes=time_minutes,
            y_speed=y_speed, z_speed=z_speed, plate_no=plate_no,
        )
        label = CENTRIFUGE_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        return self._run(cmd, label, setpoints, timeout=effective_timeout)

    @action(description="运行离心 (cmd 6)")
    def run(
        self,
        rpm: int = 1000,
        minutes: int = 10,
        plate_no: int = 2,
        timeout: float = 120.0,
    ) -> dict:
        return self.execute_command(
            cmd_type=int(CentrifugeCommand.RUN),
            rpm=rpm,
            time_minutes=minutes,
            plate_no=plate_no,
            timeout=timeout,
        )

    @not_action
    def _build_setpoints(
        self,
        y_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        inner_z_pos: Optional[int] = None,
        rpm: Optional[int] = None,
        time_minutes: Optional[int] = None,
        y_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
        plate_no: Optional[int] = None,
    ) -> dict:
        mapping = {
            "Centrifuge_YPosSet": y_pos,
            "Centrifuge_ZPosSet": z_pos,
            "Centrifuge_InnerZPosSet": inner_z_pos,
            "Centrifuge_RPM": rpm,
            "Centrifuge_Time": time_minutes,
            "Centrifuge_YSpeed": y_speed,
            "Centrifuge_ZSpeed": z_speed,
            "Centrifuge_PlateNo": plate_no,
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
        with self._command_lock:
            logger.info(f"离心机：{description} (CmdType={cmd_type})")
            if setpoints:
                for node, value in setpoints.items():
                    ok = self.set_node_value(node, value)
                    if not ok:
                        raise ValueError(f"写入 {node}={value} 失败")
            return self._trigger_and_wait(cmd_type, description, timeout=timeout)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        """下发命令并等待 CompleteFB=1；完成后清零 CmdTrig/CmdType。"""
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if not self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type)):
            raise ValueError(f"Centrifuge_CmdType={cmd_type} 写入失败")
        if not self.set_node_value(self.CMD_TRIG_NODE, 1):
            raise ValueError("Centrifuge_CmdTrig=1 写入失败")

        completed = False
        try:
            completed = self._wait_until_true(
                self.COMPLETE_NODE,
                timeout=timeout,
                description=f"{description}完成",
            )
            if not completed:
                raise ValueError(
                    f"{description}失败，Centrifuge_CompleteFB 未变为 1"
                )
        finally:
            trigger_cleared = self.set_node_value(self.CMD_TRIG_NODE, 0)
            command_cleared = self.set_node_value(self.CMD_TYPE_NODE, 0)
            trigger_value = self.get_node_value(self.CMD_TRIG_NODE, force_read=True)
            command_value = self.get_node_value(self.CMD_TYPE_NODE, force_read=True)
            logger.info(
                "离心机命令清理："
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
                    f"Centrifuge_CmdTrig={trigger_value!r}, "
                    f"Centrifuge_CmdType={command_value!r}"
                )

        logger.info(f"{description}完成")
        self._log_positions(f"{description}后")
        return {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": int(cmd_type),
        }

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
        """按离心机模块测试流程 yaml 预设依次 execute_command（本地调试用）"""
        logger.info("离心机：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            label = CENTRIFUGE_CMD_LABELS.get(int(cmd_type), str(cmd_type))
            timeout = 120.0
            if int(cmd_type) == int(CentrifugeCommand.RUN):
                minutes = preset.get("time_minutes", 1)
                timeout = minutes * 60 + 180
            self._run(
                int(cmd_type), f"{step_name}/{label}",
                self._build_setpoints(**preset), timeout=timeout,
            )
        logger.info("离心机：整体测试流程完成")
        return {"success": True, "message": "离心机测试流程完成"}

    @not_action
    def get_positions(self) -> dict:
        return {
            "Y": self.get_node_value("Centrifuge_YPosFB"),
            "Z": self.get_node_value("Centrifuge_ZPosFB"),
        }

    @not_action
    def _log_positions(self, prefix: str = "位置反馈") -> None:
        pos = self.get_positions()
        complete = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        logger.info(f"{prefix}: Y={pos['Y']} Z={pos['Z']} 完成={complete}")


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    CENTRIFUGE_URL = "opc.tcp://192.168.6.6:4840"
    POSITION_LOG_INTERVAL = 15.0

    dev = CentrifugeDevice(url=CENTRIFUGE_URL, csv_path=DEFAULT_CSV_PATH)
    time.sleep(2)

    position_log_running = True

    def _position_log_worker():
        while position_log_running:
            try:
                dev._log_positions("实时位置")
            except Exception as e:
                logger.warning(f"位置反馈日志异常: {e}")
            time.sleep(POSITION_LOG_INTERVAL)

    threading.Thread(target=_position_log_worker, daemon=True, name="CentrifugePositionLog").start()

    JOG_PRESETS = {
        "11": ("Y向左", 1, dict(y_pos=-1700, y_speed=300)),
        "12": ("Y向右", 2, dict(y_pos=0, y_speed=300)),
        "13": ("Z向左", 3, dict(z_pos=300, z_speed=300)),
        "14": ("Z向右", 4, dict(z_pos=-300, z_speed=300)),
    }

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("8 复位 (CmdType=8)")
        print("--- 单点调试（指令类型 1-4） ---")
        for key, (label, cmd, _) in JOG_PRESETS.items():
            print(f"{key} {label} (CmdType={cmd})")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "98":
            dev.run_test_flow()
        elif choice == "8":
            dev.execute_command(cmd_type=8)
        elif choice in JOG_PRESETS:
            _, cmd_type, preset = JOG_PRESETS[choice]
            dev.execute_command(cmd_type=cmd_type, **preset)
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            kwargs = dict(cmd_type=int(cmd_type), **preset)
            if int(cmd_type) == int(CentrifugeCommand.RUN):
                minutes = preset.get("time_minutes", 1)
                kwargs["timeout"] = minutes * 60 + 180
            dev.execute_command(**kwargs)
        else:
            print("无效的操作序号，请重新输入。")

    position_log_running = False
    dev.disconnect()
    print("退出程序。")
