"""
锁紧模块 设备驱动

基于 gn_solid_weighing / gn_standard_oven 同构 OPC 握手；
单点调试逻辑对齐 locking_mechanism.py；协议 opcua_gn1.3.6.csv「锁紧模块」（前缀 Lock_）。

对外仅暴露 execute_command（Lock_CmdType + 写参）；测试流程预设供本地调试。

指令类型 (Lock_CmdType)：
    1=X向左 2=X向右 3=Y向左 4=Y向右
    5=Z1向左 6=Z1向右 7=Z2向左 8=Z2向右
    9=夹爪夹取 10=夹爪放置 11=电批拧紧 12=电批拧松
    13=夹爪夹紧 14=夹爪松开 16=复位
"""

import os
import time
import logging
import threading
import traceback
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.device_comms.opcua_client.node.uniopcua import DataType
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

# CSV 写 DOUBLE，汇川 OPC 实际多为 FLOAT(REAL)；按 Double 写会 BadTypeMismatch
_JAW_FLOAT_NODES = ("Lock_JawPosition", "Lock_JawForce")

DEFAULT_XLSX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "opcua_gn1.3.6.csv",
)

# OPC UA 1.3.6 Lock_CmdType
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


# 锁紧模块测试流程预设（本地 run_test_flow，非注册动作）
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
    "按 Lock_CmdType 执行 OPC UA 1.3.6 指令。"
    "1=X左 2=X右 3=Y左 4=Y右 5=Z1左 6=Z1右 7=Z2左 8=Z2右 "
    "9=夹爪夹取 10=夹爪放置 11=电批拧紧 12=电批拧松 13=夹爪夹紧 14=夹爪松开 16=复位。"
    "轴运动写对应 PosSet/Speed；夹取/放置/电批另写 jaw_position/jaw_force。"
)


@device(
    id="gn_locking_mechanism",
    display_name="锁紧模块",
    category=["workstation"],
    description="GN 锁紧模块：OPC UA 1.3.6，按完成反馈边沿执行命令",
    icon="",
    version="2.0.0",
)
class LockingMechanismDevice(OpcUaClientWithSubscription):
    """锁紧模块设备类（OPC 前缀 Lock_），结构同 SolidWeighingDevice。"""

    CMD_TYPE_NODE = "Lock_CmdType"
    CMD_TRIG_NODE = "Lock_CmdTrig"
    COMPLETE_NODE = "Lock_CompleteFB"
    POSITION_NODES = {
        "Lock_XPosSet": "Lock_XPosFB",
        "Lock_YPosSet": "Lock_YPosFB",
        "Lock_Z1PosSet": "Lock_Z1PosFB",
        "Lock_Z2PosSet": "Lock_Z2PosFB",
    }
    # 仅等 CompleteFB（夹紧/松开/复位）
    _COMPLETE_FB_ONLY_CMDS = frozenset({
        int(LockCommand.JAW_CLAMP),
        int(LockCommand.JAW_RELEASE),
        int(LockCommand.RESET),
    })
    _OPC_WRITE_RETRIES = 2

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
        enable_connection_monitor: bool = False,
        *args,
        **kwargs,
    ):
        kwargs.pop("plc_device_id", None)
        # 实例隔离节点表，避免站内其它设备全表 load 抢走 Lock_*
        self._node_registry = {}
        self._variables_to_find = {}
        self._found_node_objects = {}
        self._name_mapping = {}
        self._reverse_mapping = {}
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
        self._connection_check_interval = 5.0
        self._command_lock = threading.Lock()
        path = csv_path or xlsx_path
        if path:
            self._load_nodes_from_xlsx(path)

    @not_action
    def _load_nodes_from_xlsx(self, xlsx_path: str) -> None:
        """从 opcua_gn CSV 加载 Lock_ 前缀节点。"""
        try:
            if not os.path.isabs(xlsx_path):
                xlsx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), xlsx_path)
            if not os.path.isfile(xlsx_path):
                logger.error(f"OPC UA 协议 CSV 不存在: {xlsx_path}")
                return

            logger.info(f"开始从 CSV 加载 Lock_ 节点: {xlsx_path}")
            all_nodes, name_mapping, reverse_mapping = self.load_csv(xlsx_path)

            nodes = [n for n in all_nodes if str(n.name).startswith("Lock_")]
            name_mapping = {
                en: cn for en, cn in name_mapping.items()
                if str(en).startswith("Lock_") or str(cn).startswith("Lock_")
            }
            reverse_mapping = {
                cn: en for cn, en in reverse_mapping.items()
                if str(cn).startswith("Lock_") or str(en).startswith("Lock_")
            }

            if not nodes:
                logger.error("CSV 中未解析到任何 Lock_ 节点")
                return

            self._name_mapping.update(name_mapping)
            self._reverse_mapping.update(reverse_mapping)
            self.register_node_list(nodes)

            if self.client and self._variables_to_find:
                logger.info(f"CSV 解析完成，待查找 {len(self._variables_to_find)} 个节点...")
                self._find_nodes()
            self._fix_jaw_node_dtypes()
            self._register_nodes_as_attributes()

            found_count = len(self._node_registry)
            total_count = len(self._variables_to_find)
            if found_count < total_count:
                logger.warning(f"节点查找完成：找到 {found_count}/{total_count} 个节点")
            else:
                logger.info(f"✓ 节点查找完成：所有 {found_count} 个节点均已找到")

            if self._use_subscription and found_count > 0:
                self._setup_subscriptions()
            logger.info(f"✓ 成功从 CSV 加载 {found_count} 个 Lock_ 节点")
        except Exception as exc:
            logger.error(f"从 CSV 加载节点失败 {xlsx_path}: {exc}")
            traceback.print_exc()

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
        timeout: float = 180.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        cmd = int(cmd_type)
        if timeout is None or float(timeout) <= 0:
            timeout = 180.0
        setpoints = self._build_setpoints(
            x_pos=x_pos, y_pos=y_pos, z1_pos=z1_pos, z2_pos=z2_pos,
            x_speed=x_speed, y_speed=y_speed, z1_speed=z1_speed, z2_speed=z2_speed,
            jaw_position=jaw_position, jaw_force=jaw_force,
        )
        label = LOCK_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        return self._run(cmd, label, setpoints, timeout=float(timeout))

    @not_action
    def _fix_jaw_node_dtypes(self) -> None:
        """夹爪位置/力度按 FLOAT 写入，避免 CSV DOUBLE 与 PLC REAL 类型不匹配。"""
        for name in _JAW_FLOAT_NODES:
            node = self._node_registry.get(name)
            if node is not None and hasattr(node, "_data_type"):
                if node._data_type != DataType.FLOAT:
                    logger.info(f"{name} 数据类型 {node._data_type} → FLOAT（对齐 PLC REAL）")
                    node._data_type = DataType.FLOAT

    @not_action
    def _refresh_node_handles(self) -> None:
        for node in self._node_registry.values():
            if hasattr(node, "_node"):
                node._node = None
            if hasattr(node, "_parent_node"):
                node._parent_node = None
            if hasattr(node, "_client") and self.client is not None:
                node._client = self.client
        if hasattr(self, "_found_node_objects"):
            self._found_node_objects.clear()
        self._fix_jaw_node_dtypes()

    @not_action
    def _reconnect_opcua(self) -> bool:
        """委托到基类幂等 _reconnect：先探活；Session 活着不重建，死了才 disconnect+connect。
        真正重连后（时间戳更新）自动刷新节点句柄。"""
        prev_ts = self._last_reconnect_ts
        ok = self._reconnect()
        if ok and self._last_reconnect_ts != prev_ts:
            try:
                self._refresh_node_handles()
                logger.info("锁紧模块 OPC UA 主动重连成功，已刷新节点句柄")
            except Exception as exc:
                logger.warning(f"重连后刷新节点句柄失败（忽略）: {exc}")
        return ok

    @not_action
    def _opc_write(self, name: str, value, retries: Optional[int] = None) -> bool:
        attempts = (self._OPC_WRITE_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            if self.set_node_value(name, value):
                return True
            self._refresh_node_handles()
            if self.set_node_value(name, value):
                return True
            if attempt + 1 < attempts:
                logger.warning(
                    f"写入 {name}={value} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect_opcua()
                time.sleep(0.3)
        return False

    @not_action
    def _opc_read(self, name: str, force_read: bool = False, retries: Optional[int] = None):
        attempts = (self._OPC_WRITE_RETRIES if retries is None else retries) + 1
        for attempt in range(attempts):
            value = self.get_node_value(name, force_read=force_read)
            if value is not None:
                return value
            self._refresh_node_handles()
            value = self.get_node_value(name, force_read=force_read)
            if value is not None:
                return value
            if attempt + 1 < attempts:
                logger.warning(
                    f"读取 {name} 失败，尝试重连 ({attempt + 1}/{attempts - 1})"
                )
                self._reconnect_opcua()
                time.sleep(0.3)
        return None

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
            # 显式 float，配合节点 DataType.FLOAT
            "Lock_JawPosition": float(jaw_position) if jaw_position is not None else None,
            "Lock_JawForce": float(jaw_force) if jaw_force is not None else None,
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
            logger.info(f"锁紧模块：{description} (CmdType={cmd_type})")
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
            raise ValueError(f"Lock_CmdType={cmd_type} 写入失败")
        if not self._opc_write(self.CMD_TRIG_NODE, 1):
            raise ValueError("Lock_CmdTrig=1 写入失败")

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
                raise ValueError(f"{description}失败，Lock_CompleteFB 未变为 1")
        finally:
            trigger_cleared = self._opc_write(self.CMD_TRIG_NODE, 0)
            command_cleared = self._opc_write(self.CMD_TYPE_NODE, 0)
            trigger_value = self._opc_read(self.CMD_TRIG_NODE, force_read=True)
            command_value = self._opc_read(self.CMD_TYPE_NODE, force_read=True)
            logger.info(
                f"锁紧模块命令清理：CmdTrig={trigger_value!r}，CmdType={command_value!r}"
            )
            if completed and (
                not trigger_cleared
                or not command_cleared
                or trigger_value != 0
                or command_value != 0
            ):
                raise ValueError(
                    "动作已完成，但命令清零失败："
                    f"Lock_CmdTrig={trigger_value!r}, Lock_CmdType={command_value!r}"
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
        tolerance: int = 5,
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
        if position_targets and self._positions_reached(position_targets):
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
    def run_test_flow(self) -> dict:
        """按锁紧模块测试流程预设依次 execute_command（本地调试用）"""
        logger.info("锁紧模块：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            self.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        logger.info("锁紧模块：整体测试流程完成")
        return {"success": True, "message": "锁紧模块测试流程完成"}

    @not_action
    def get_positions(self) -> dict:
        return {
            "X": self.get_node_value("Lock_XPosFB", force_read=True),
            "Y": self.get_node_value("Lock_YPosFB", force_read=True),
            "Z1": self.get_node_value("Lock_Z1PosFB", force_read=True),
            "Z2": self.get_node_value("Lock_Z2PosFB", force_read=True),
        }

    @not_action
    def get_status(self) -> dict:
        status = self.get_positions()
        status["complete"] = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        status["jaw_position"] = self.get_node_value("Lock_JawPosition", force_read=True)
        status["jaw_force"] = self.get_node_value("Lock_JawForce", force_read=True)
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: X={status['X']} Y={status['Y']} "
            f"Z1={status['Z1']} Z2={status['Z2']} "
            f"完成={status['complete']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    LOCKING_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    dev = LockingMechanismDevice(
        url=LOCKING_URL,
        xlsx_path=DEFAULT_XLSX_PATH,
        use_subscription=False,
    )
    time.sleep(2)
    logger.info(f"锁紧模块连通性测试: {dev.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                dev._log_status("实时位置")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    threading.Thread(target=_status_log_worker, daemon=True, name="LockStatusLog").start()

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
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            dev.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    dev.disconnect()
    print("退出程序。")
