"""
离心管液体处理 设备驱动

参照 centrifuge.py / locking_mechanism.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 OPC_UA协议1.3.4(1).xlsx（前缀 Tube_）。
各动作点位写死在代码中（原测试流程 step0~step19）。

指令类型 (Tube_CmdType)：
    1=X向左 2=X向右 3=Y向前 4=Y向后
    5=8通道Z向上 6=8通道Z向下 7=8通道移液吸 8=8通道移液放
    9=大夹爪Z向上 10=大夹爪Z向下 11=单通道移液吸 12=单通道移液放
    13=单通道向上 14=单通道向下 15=小夹爪向上 16=小夹爪向下
    17=磁力架轴向上 18=磁力架轴向下
    19=8通道装载 20=8通道吸液 21=8通道放液 22=8通道卸载
    23=单通道装载 24=单通道吸液 25=单通道放液 26=单通道卸载
    27=小夹爪开盖 28=小夹爪关盖 29=小夹爪抓取 30=小夹爪放置
    31=大夹爪抓取 32=大夹爪放置 33=单通道混匀 34=8通道混匀 35=超声波混匀
    36=复位 37=xyz回原点
    38=模块1恒温打开 39=模块1恒温关闭 40=模块2恒温打开 41=模块2恒温关闭
    42=模块3恒温打开 43=模块3恒温关闭 44=模块4恒温打开 45=模块4恒温关闭
    46=模块4震荡打开 47=模块4震荡关闭

节点映射：
    XPos/YPos           → Tube_XPosSet / Tube_YPosSet
    Ch8ZPos/Ch8LiquidPos → Tube_Z1PosSet / Tube_P1PosSet
    BigGripperZPos      → Tube_Z2PosSet
    Ch1LiquidPos/Ch1ZPos → Tube_P2PosSet / Tube_Z3PosSet
    SmallGripperZPos    → Tube_Z4PosSet
    MagneticRackPos     → Tube_MPosSet
    Ch8Blow/Ch1Blow     → Tube_Ch8Blow / Tube_Ch1Blow
    Ch8ReverseAspirate/Ch1ReverseAspirate → Tube_Ch8ReverseAspirate / Tube_Ch1ReverseAspirate
    UltraSoundTime      → Tube_UltrasoundTime
    ThermoModule1~4     → Tube_ThermostaticModule_Temperature1~4 / Tube_ThermostaticModule_Time1~4
    ThermoModule4Shake  → Tube_ThermostaticModule4_Shaking_Speed / Tube_ThermostaticModule4_Shaking_Time
    Tube_UltrasoundSTOP → 超声波混匀立即停止（脉冲 1→0，不走 CmdType）
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


class TubeCommand(int, Enum):
    """离心管液体处理指令类型 (Tube_CmdType，OPC 1.3.4)"""
    X_LEFT = 1
    X_RIGHT = 2
    Y_FORWARD = 3
    Y_BACKWARD = 4
    CH8_Z_UP = 5
    CH8_Z_DOWN = 6
    CH8_PIPETTE_ASPIRATE = 7
    CH8_PIPETTE_DISPENSE = 8
    BIG_GRIPPER_Z_UP = 9
    BIG_GRIPPER_Z_DOWN = 10
    CH1_PIPETTE_ASPIRATE = 11
    CH1_PIPETTE_DISPENSE = 12
    CH1_Z_UP = 13
    CH1_Z_DOWN = 14
    SMALL_GRIPPER_Z_UP = 15
    SMALL_GRIPPER_Z_DOWN = 16
    MAGNETIC_RACK_UP = 17
    MAGNETIC_RACK_DOWN = 18
    CH8_LOAD_TIP = 19
    CH8_ASPIRATE_LIQUID = 20
    CH8_DISPENSE_LIQUID = 21
    CH8_UNLOAD_TIP = 22
    CH1_LOAD_TIP = 23
    CH1_ASPIRATE_LIQUID = 24
    CH1_DISPENSE_LIQUID = 25
    CH1_UNLOAD_TIP = 26
    SMALL_GRIPPER_OPEN_LID = 27
    SMALL_GRIPPER_CLOSE_LID = 28
    SMALL_GRIPPER_PICK = 29
    SMALL_GRIPPER_PLACE = 30
    BIG_GRIPPER_PICK = 31
    BIG_GRIPPER_PLACE = 32
    CH1_MIX = 33
    CH8_MIX = 34
    ULTRASOUND_MIX = 35
    RESET = 36
    HOME_XYZ = 37
    THERMO_MODULE1_OPEN = 38
    THERMO_MODULE1_CLOSE = 39
    THERMO_MODULE2_OPEN = 40
    THERMO_MODULE2_CLOSE = 41
    THERMO_MODULE3_OPEN = 42
    THERMO_MODULE3_CLOSE = 43
    THERMO_MODULE4_OPEN = 44
    THERMO_MODULE4_CLOSE = 45
    THERMO_MODULE4_SHAKE_OPEN = 46
    THERMO_MODULE4_SHAKE_CLOSE = 47


TUBE_CMD_LABELS = {
    1: "X向左",
    2: "X向右",
    3: "Y向前",
    4: "Y向后",
    5: "8通道Z向上",
    6: "8通道Z向下",
    7: "8通道移液吸",
    8: "8通道移液放",
    9: "大夹爪Z向上",
    10: "大夹爪Z向下",
    11: "单通道移液吸",
    12: "单通道移液放",
    13: "单通道向上",
    14: "单通道向下",
    15: "小夹爪向上",
    16: "小夹爪向下",
    17: "磁力架轴向上",
    18: "磁力架轴向下",
    19: "8通道装载",
    20: "8通道吸液",
    21: "8通道放液",
    22: "8通道卸载",
    23: "单通道装载",
    24: "单通道吸液",
    25: "单通道放液",
    26: "单通道卸载",
    27: "小夹爪开盖",
    28: "小夹爪关盖",
    29: "小夹爪抓取",
    30: "小夹爪放置",
    31: "大夹爪抓取",
    32: "大夹爪放置",
    33: "单通道混匀",
    34: "8通道混匀",
    35: "超声波混匀",
    36: "复位",
    37: "xyz回原点",
    38: "模块1恒温打开",
    39: "模块1恒温关闭",
    40: "模块2恒温打开",
    41: "模块2恒温关闭",
    42: "模块3恒温打开",
    43: "模块3恒温关闭",
    44: "模块4恒温打开",
    45: "模块4恒温关闭",
    46: "模块4震荡打开",
    47: "模块4震荡关闭",
}

_EXECUTE_CMD_DOC = (
    "按 Tube_CmdType 执行 OPC UA 指令。"
    "写参 → CmdType → CmdTrig → 等 CompleteFB。"
    "1~18 点动；19~35 业务动作；36 复位；37 xyz 回原点；38~47 恒温/震荡。"
    "ultrasound_stop=True 时仅脉冲 Tube_UltrasoundSTOP，不走 CmdType。"
)


@device(
    id="gn_centrifuge_tube_liquid_handling",
    display_name="离心管液体处理",
    category=["workstation"],
    description="GN 离心管液体处理：分样/移液/开关盖，OPC UA 控制",
    icon="",
)
class CentrifugeTubeLiquidHandlingDevice(GNStationClient):
    """离心管液体处理设备类（OPC 前缀 Tube_，通过 self.plc 共享 GN 工站单例 OPC UA 会话）"""

    PREFIX = "Tube_"
    ULTRASOUND_STOP_NODE = "Tube_UltrasoundSTOP"

    def __init__(
        self,
        url: str,
        xlsx_path: str = DEFAULT_XLSX_PATH,
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
            csv_path=xlsx_path,
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
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        p1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        p2_pos: Optional[int] = None,
        z3_pos: Optional[int] = None,
        z4_pos: Optional[int] = None,
        m_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        p1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        p2_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        z4_speed: Optional[int] = None,
        m_speed: Optional[int] = None,
        mix_counts: Optional[int] = None,
        ultrasound_time: Optional[int] = None,
        shaking_speed: Optional[int] = None,
        shaking_time: Optional[int] = None,
        small_gripper_angle: Optional[int] = None,
        small_gripper_force: Optional[int] = None,
        ultrasound_stop: bool = False,
        timeout: float = 180.0,
    ) -> dict:
        """通用入口：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        if ultrasound_stop:
            return self.ultrasound_stop()
        if timeout is None or float(timeout) <= 0:
            timeout = 180.0
        cmd = int(cmd_type)
        setpoints = self._build_setpoints(
            cmd_type=cmd,
            x_pos=x_pos,
            y_pos=y_pos,
            z1_pos=z1_pos,
            p1_pos=p1_pos,
            z2_pos=z2_pos,
            p2_pos=p2_pos,
            z3_pos=z3_pos,
            z4_pos=z4_pos,
            m_pos=m_pos,
            x_speed=x_speed,
            y_speed=y_speed,
            z1_speed=z1_speed,
            p1_speed=p1_speed,
            z2_speed=z2_speed,
            p2_speed=p2_speed,
            z3_speed=z3_speed,
            z4_speed=z4_speed,
            m_speed=m_speed,
            mix_counts=mix_counts,
            ultrasound_time=ultrasound_time,
            shaking_speed=shaking_speed,
            shaking_time=shaking_time,
            small_gripper_angle=small_gripper_angle,
            small_gripper_force=small_gripper_force,
        )
        for node, value in setpoints.items():
            self._set_node_or_raise(node, value)
        label = TUBE_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        logger.info(f"离心管液体处理：{label} (CmdType={cmd})")
        return self._trigger_and_wait(cmd, label, timeout=float(timeout))

    @not_action
    def _build_setpoints(
        self,
        cmd_type: Optional[int] = None,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        p1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        p2_pos: Optional[int] = None,
        z3_pos: Optional[int] = None,
        z4_pos: Optional[int] = None,
        m_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        p1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        p2_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        z4_speed: Optional[int] = None,
        m_speed: Optional[int] = None,
        mix_counts: Optional[int] = None,
        ultrasound_time: Optional[int] = None,
        shaking_speed: Optional[int] = None,
        shaking_time: Optional[int] = None,
        small_gripper_angle: Optional[int] = None,
        small_gripper_force: Optional[int] = None,
    ) -> dict:
        mapping = {
            "Tube_XPosSet": x_pos,
            "Tube_YPosSet": y_pos,
            "Tube_Z1PosSet": z1_pos,
            "Tube_P1PosSet": p1_pos,
            "Tube_Z2PosSet": z2_pos,
            "Tube_P2PosSet": p2_pos,
            "Tube_Z3PosSet": z3_pos,
            "Tube_Z4PosSet": z4_pos,
            "Tube_MPosSet": m_pos,
            "Tube_XSpeed": x_speed,
            "Tube_YSpeed": y_speed,
            "Tube_Z1Speed": z1_speed,
            "Tube_P1Speed": p1_speed,
            "Tube_Z2Speed": z2_speed,
            "Tube_P2Speed": p2_speed,
            "Tube_Z3Speed": z3_speed,
            "Tube_Z4Speed": z4_speed,
            "Tube_MSpeed": m_speed,
            "Tube_MixCounts": mix_counts,
            "Tube_UltrasoundTime": ultrasound_time,
            "Tube_ThermostaticModule4_Shaking_Speed": shaking_speed,
            "Tube_ThermostaticModule4_Shaking_Time": shaking_time,
            "Tube_SmallGripperAngle": small_gripper_angle,
            "Tube_SmallGripperForce": small_gripper_force,
        }
        setpoints = {node: val for node, val in mapping.items() if val is not None}
        # 震荡转速/时间仅 CmdType=46（模块4震荡打开）需要写入。
        # 云端 auto-execute_command 常对全部可选参数字段填 0；若一律写入，
        # 会在 PLC 未暴露 Shaking 节点（未进 OPC 注册表）时直接报错。
        # 专用动作 small_gripper_open_lid / reset 等也不写这两项。
        if cmd_type != int(TubeCommand.THERMO_MODULE4_SHAKE_OPEN):
            setpoints.pop("Tube_ThermostaticModule4_Shaking_Speed", None)
            setpoints.pop("Tube_ThermostaticModule4_Shaking_Time", None)
        return setpoints

    # ==================== 动作函数（点位写死） ====================

    @action(auto_prefix=True, description="1.8通道装载")
    def ch8_load(self) -> dict:
        """8通道装载（指令类型=19）"""
        logger.info("离心管液体处理：8通道装载...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 3095)
        self._set_node_or_raise("Tube_YPosSet", -2090)
        self._set_node_or_raise("Tube_Z1PosSet", 905)
        self._set_node_or_raise("Tube_P1PosSet", 500)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH8_LOAD_TIP, "8通道装载")

    @action(auto_prefix=True, description="2.8通道吸液")
    def ch8_aspirate(self) -> dict:
        """8通道吸液（指令类型=20）"""
        logger.info("离心管液体处理：8通道吸液...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 4563)
        self._set_node_or_raise("Tube_YPosSet", -2121)
        self._set_node_or_raise("Tube_Z1PosSet", 900)
        self._set_node_or_raise("Tube_P1PosSet", 300)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH8_ASPIRATE_LIQUID, "8通道吸液")

    @action(auto_prefix=True, description="3.8通道放液")
    def ch8_dispense(self) -> dict:
        """8通道放液（指令类型=21）"""
        logger.info("离心管液体处理：8通道放液...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 3093)
        self._set_node_or_raise("Tube_YPosSet", -3181)
        self._set_node_or_raise("Tube_Z1PosSet", 1330)
        self._set_node_or_raise("Tube_P1PosSet", 300)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH8_DISPENSE_LIQUID, "8通道放液")

    @action(auto_prefix=True, description="4.8通道混匀")
    def ch8_mix(self) -> dict:
        """8通道混匀（指令类型=34）"""
        logger.info("离心管液体处理：8通道混匀...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 3093)
        self._set_node_or_raise("Tube_YPosSet", -3181)
        self._set_node_or_raise("Tube_Z1PosSet", 1330)
        self._set_node_or_raise("Tube_P1PosSet", 300)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_MixCounts", 5)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH8_MIX, "8通道混匀")

    @action(auto_prefix=True, description="5.8通道卸载")
    def ch8_unload(self) -> dict:
        """8通道卸载（指令类型=22）"""
        logger.info("离心管液体处理：8通道卸载...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 2463)
        self._set_node_or_raise("Tube_YPosSet", -581)
        self._set_node_or_raise("Tube_Z1PosSet", 1027)
        self._set_node_or_raise("Tube_P1PosSet", 500)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 3000)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH8_UNLOAD_TIP, "8通道卸载")

    @action(auto_prefix=True, description="6.大夹爪抓取")
    def big_gripper_pick(self) -> dict:
        """大夹爪抓取（指令类型=31）"""
        logger.info("离心管液体处理：大夹爪抓取...")
        self._set_node_or_raise("Tube_XPosSet", 2490)
        self._set_node_or_raise("Tube_YPosSet", -3200)
        self._set_node_or_raise("Tube_Z2PosSet", 1420)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PICK, "大夹爪抓取")

    @action(auto_prefix=True, description="7.大夹爪放置")
    def big_gripper_place(self) -> dict:
        """大夹爪放置（指令类型=32）"""
        logger.info("离心管液体处理：大夹爪放置...")
        self._set_node_or_raise("Tube_XPosSet", 7100)
        self._set_node_or_raise("Tube_YPosSet", -3550)
        self._set_node_or_raise("Tube_Z2PosSet", 1215)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PLACE, "大夹爪放置")

    @action(auto_prefix=True, description="8.超声波混匀")
    def ultrasound_mix(self) -> dict:
        """超声波混匀（指令类型=35）"""
        logger.info("离心管液体处理：超声波混匀...")
        ultrasound_time = 5
        self._set_node_or_raise("Tube_MPosSet", 300)
        self._set_node_or_raise("Tube_MSpeed", 300)
        self._set_node_or_raise("Tube_UltrasoundTime", ultrasound_time)
        timeout = ultrasound_time * 60 + 60
        return self._trigger_and_wait(TubeCommand.ULTRASOUND_MIX, "超声波混匀", timeout=timeout)

    @action(auto_prefix=True, description="9.大夹爪抓取(2)")
    def big_gripper_pick_2(self) -> dict:
        """大夹爪抓取(2)（指令类型=31）"""
        logger.info("离心管液体处理：大夹爪抓取(2)...")
        self._set_node_or_raise("Tube_XPosSet", 7100)
        self._set_node_or_raise("Tube_YPosSet", -3550)
        self._set_node_or_raise("Tube_Z2PosSet", 1215)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PICK, "大夹爪抓取(2)")

    @action(auto_prefix=True, description="10.大夹爪放置(2)")
    def big_gripper_place_2(self) -> dict:
        """大夹爪放置(2)（指令类型=32）"""
        logger.info("离心管液体处理：大夹爪放置(2)...")
        self._set_node_or_raise("Tube_XPosSet", 2490)
        self._set_node_or_raise("Tube_YPosSet", -3200)
        self._set_node_or_raise("Tube_Z2PosSet", 1420)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PLACE, "大夹爪放置(2)")

    @action(auto_prefix=True, description="11.小夹爪开盖")
    def small_gripper_open_lid(self) -> dict:
        """小夹爪开盖（指令类型=27）"""
        logger.info("离心管液体处理：小夹爪开盖...")
        self._write_lid_gripper_params()
        self._set_node_or_raise("Tube_XPosSet", 6670)
        self._set_node_or_raise("Tube_YPosSet", -2300)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperAngle", 540)
        self._set_node_or_raise("Tube_SmallGripperForce", 500)
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_OPEN_LID, "小夹爪开盖")

    @action(auto_prefix=True, description="12.小夹爪放置")
    def small_gripper_place(self) -> dict:
        """小夹爪放置（指令类型=30）"""
        logger.info("离心管液体处理：小夹爪放置...")
        self._write_lid_gripper_params()
        self._set_node_or_raise("Tube_XPosSet", 6330)
        self._set_node_or_raise("Tube_YPosSet", -480)
        self._set_node_or_raise("Tube_Z4PosSet", 1375)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperAngle", 540)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_PLACE, "小夹爪放置")

    @action(auto_prefix=True, description="13.单通道装载")
    def ch1_load(self) -> dict:
        """单通道装载（指令类型=23）"""
        logger.info("离心管液体处理：单通道装载...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 720)
        self._set_node_or_raise("Tube_YPosSet", -850)
        self._set_node_or_raise("Tube_P2PosSet", 2000)
        self._set_node_or_raise("Tube_Z3PosSet", 1030)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH1_LOAD_TIP, "单通道装载")

    @action(auto_prefix=True, description="14.单通道吸液")
    def ch1_aspirate(self) -> dict:
        """单通道吸液（指令类型=24）"""
        logger.info("离心管液体处理：单通道吸液...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 7920)
        self._set_node_or_raise("Tube_YPosSet", -2250)
        self._set_node_or_raise("Tube_P1PosSet", 300)
        self._set_node_or_raise("Tube_P2PosSet", 2000)
        self._set_node_or_raise("Tube_Z3PosSet", 1130)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH1_ASPIRATE_LIQUID, "单通道吸液")

    @action(auto_prefix=True, description="15.单通道放液")
    def ch1_dispense(self) -> dict:
        """单通道放液（指令类型=25）"""
        logger.info("离心管液体处理：单通道放液...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 3920)
        self._set_node_or_raise("Tube_YPosSet", -2250)
        self._set_node_or_raise("Tube_P1PosSet", 300)
        self._set_node_or_raise("Tube_P2PosSet", 2000)
        self._set_node_or_raise("Tube_Z3PosSet", 930)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH1_DISPENSE_LIQUID, "单通道放液")

    @action(auto_prefix=True, description="16.单通道卸载")
    def ch1_unload(self) -> dict:
        """单通道卸载（指令类型=26）"""
        logger.info("离心管液体处理：单通道卸载...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 20)
        self._set_node_or_raise("Tube_YPosSet", -850)
        self._set_node_or_raise("Tube_P1PosSet", 500)
        self._set_node_or_raise("Tube_P2PosSet", 2000)
        self._set_node_or_raise("Tube_Z3PosSet", 1030)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH1_UNLOAD_TIP, "单通道卸载")

    @action(auto_prefix=True, description="17.小夹爪抓取")
    def small_gripper_pick(self) -> dict:
        """小夹爪抓取（指令类型=29）"""
        logger.info("离心管液体处理：小夹爪抓取...")
        self._write_lid_gripper_params()
        self._set_node_or_raise("Tube_XPosSet", 6330)
        self._set_node_or_raise("Tube_YPosSet", -480)
        self._set_node_or_raise("Tube_Z4PosSet", 1375)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperAngle", 540)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_PICK, "小夹爪抓取")

    @action(auto_prefix=True, description="18.小夹爪关盖")
    def small_gripper_close_lid(self) -> dict:
        """小夹爪关盖（指令类型=28）"""
        logger.info("离心管液体处理：小夹爪关盖...")
        self._write_lid_gripper_params()
        self._set_node_or_raise("Tube_XPosSet", 6670)
        self._set_node_or_raise("Tube_YPosSet", -2300)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_SmallGripperAngle", -540)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_CLOSE_LID, "小夹爪关盖")

    @action(auto_prefix=True, description="19.离心管液体处理复位")
    def reset(self) -> dict:
        """复位（指令类型=36）"""
        logger.info("离心管液体处理：复位...")
        self._set_node_or_raise("Tube_SmallGripperAngle", 180)
        self._set_node_or_raise("Tube_SmallGripperForce", 100)
        return self._trigger_and_wait(TubeCommand.RESET, "复位")

    @action(auto_prefix=True, description="20.超声波混匀立即停止")
    def ultrasound_stop(self) -> dict:
        """超声波混匀立即停止：脉冲写 Tube_UltrasoundSTOP=1 后复位为 0"""
        logger.info("离心管液体处理：超声波混匀立即停止...")
        with self._command_lock:
            if not self._opc_write(self.ULTRASOUND_STOP_NODE, 1):
                raise ValueError(f"写入 {self.ULTRASOUND_STOP_NODE}=1 失败")
            time.sleep(0.2)
            if not self._opc_write(self.ULTRASOUND_STOP_NODE, 0):
                raise ValueError(f"写入 {self.ULTRASOUND_STOP_NODE}=0 失败")
        logger.info("超声波混匀立即停止命令已下发")
        self._log_status("超声波混匀立即停止后")
        return {"success": True, "message": "超声波混匀立即停止命令已下发"}

    @action(auto_prefix=True, description="XYZ 回原点")
    def home_xyz(self) -> dict:
        """xyz 回原点（指令类型=37）"""
        logger.info("离心管液体处理：xyz 回原点...")
        return self._trigger_and_wait(TubeCommand.HOME_XYZ, "xyz回原点")

    @action(auto_prefix=True, description="单通道混匀（指令类型=33）")
    def ch1_mix(self) -> dict:
        """单通道混匀（指令类型=33）"""
        logger.info("离心管液体处理：单通道混匀...")
        self._write_pipette_option_params()
        self._set_node_or_raise("Tube_XPosSet", 3920)
        self._set_node_or_raise("Tube_YPosSet", -2250)
        self._set_node_or_raise("Tube_P1PosSet", 300)
        self._set_node_or_raise("Tube_P2PosSet", 2000)
        self._set_node_or_raise("Tube_Z3PosSet", 930)
        self._set_node_or_raise("Tube_XSpeed", 500)
        self._set_node_or_raise("Tube_YSpeed", 500)
        self._set_node_or_raise("Tube_Z1Speed", 500)
        self._set_node_or_raise("Tube_P1Speed", 500)
        self._set_node_or_raise("Tube_Z2Speed", 500)
        self._set_node_or_raise("Tube_P2Speed", 500)
        self._set_node_or_raise("Tube_Z3Speed", 500)
        self._set_node_or_raise("Tube_Z4Speed", 500)
        self._set_node_or_raise("Tube_MixCounts", 5)
        self._set_node_or_raise("Tube_SmallGripperForce", 300)
        return self._trigger_and_wait(TubeCommand.CH1_MIX, "单通道混匀")

    @action(auto_prefix=True, description="模块1恒温打开（指令类型=38）")
    def thermostatic_module1_open(self, temperature: int = 37, time_minutes: int = 60) -> dict:
        logger.info("离心管液体处理：模块1恒温打开...")
        self._set_node_or_raise("Tube_ThermostaticModule_Temperature1", temperature)
        self._set_node_or_raise("Tube_ThermostaticModule_Time1", time_minutes)
        timeout = time_minutes * 60 + 60
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE1_OPEN, "模块1恒温打开", timeout=timeout)

    @action(auto_prefix=True, description="模块1恒温关闭（指令类型=39）")
    def thermostatic_module1_close(self) -> dict:
        logger.info("离心管液体处理：模块1恒温关闭...")
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE1_CLOSE, "模块1恒温关闭")

    @action(auto_prefix=True, description="模块2恒温打开（指令类型=40）")
    def thermostatic_module2_open(self, temperature: int = 37, time_minutes: int = 60) -> dict:
        logger.info("离心管液体处理：模块2恒温打开...")
        self._set_node_or_raise("Tube_ThermostaticModule_Temperature2", temperature)
        self._set_node_or_raise("Tube_ThermostaticModule_Time2", time_minutes)
        timeout = time_minutes * 60 + 60
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE2_OPEN, "模块2恒温打开", timeout=timeout)

    @action(auto_prefix=True, description="模块2恒温关闭（指令类型=41）")
    def thermostatic_module2_close(self) -> dict:
        logger.info("离心管液体处理：模块2恒温关闭...")
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE2_CLOSE, "模块2恒温关闭")

    @action(auto_prefix=True, description="模块3恒温打开（指令类型=42）")
    def thermostatic_module3_open(self, temperature: int = 37, time_minutes: int = 60) -> dict:
        logger.info("离心管液体处理：模块3恒温打开...")
        self._set_node_or_raise("Tube_ThermostaticModule_Temperature3", temperature)
        self._set_node_or_raise("Tube_ThermostaticModule_Time3", time_minutes)
        timeout = time_minutes * 60 + 60
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE3_OPEN, "模块3恒温打开", timeout=timeout)

    @action(auto_prefix=True, description="模块3恒温关闭（指令类型=43）")
    def thermostatic_module3_close(self) -> dict:
        logger.info("离心管液体处理：模块3恒温关闭...")
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE3_CLOSE, "模块3恒温关闭")

    @action(auto_prefix=True, description="模块4恒温打开（指令类型=44）")
    def thermostatic_module4_open(self, temperature: int = 37, time_minutes: int = 60) -> dict:
        logger.info("离心管液体处理：模块4恒温打开...")
        self._set_node_or_raise("Tube_ThermostaticModule_Temperature4", temperature)
        self._set_node_or_raise("Tube_ThermostaticModule_Time4", time_minutes)
        timeout = time_minutes * 60 + 60
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE4_OPEN, "模块4恒温打开", timeout=timeout)

    @action(auto_prefix=True, description="模块4恒温关闭（指令类型=45）")
    def thermostatic_module4_close(self) -> dict:
        logger.info("离心管液体处理：模块4恒温关闭...")
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE4_CLOSE, "模块4恒温关闭")

    @action(auto_prefix=True, description="模块4震荡打开（指令类型=46）")
    def thermostatic_module4_shake_open(
        self,
        temperature: int = 25,
        time_minutes: int = 1,
        shaking_speed: int = 300,
        shaking_time: int = 20
    ) -> dict:
        logger.info("离心管液体处理：模块4震荡打开...")
        self._set_node_or_raise("Tube_ThermostaticModule_Temperature4", temperature)
        self._set_node_or_raise("Tube_ThermostaticModule_Time4", time_minutes)
        self._set_node_or_raise("Tube_ThermostaticModule4_Shaking_Speed", shaking_speed)
        self._set_node_or_raise("Tube_ThermostaticModule4_Shaking_Time", shaking_time)
        timeout = max(time_minutes, shaking_time) * 60 + 60
        return self._trigger_and_wait(
            TubeCommand.THERMO_MODULE4_SHAKE_OPEN, "模块4震荡打开", timeout=timeout,
        )

    @action(auto_prefix=True, description="模块4震荡关闭（指令类型=47）")
    def thermostatic_module4_shake_close(self) -> dict:
        logger.info("离心管液体处理：模块4震荡关闭...")
        return self._trigger_and_wait(TubeCommand.THERMO_MODULE4_SHAKE_CLOSE, "模块4震荡关闭")

    # ==================== 单点调试（轴点动 1-4） ====================

    @action(auto_prefix=True, description="单点调试：X向左（指令类型=1）")
    def jog_x_left(self) -> dict:
        logger.info("离心管液体处理：单点调试 X向左...")
        self._set_node_or_raise("Tube_XPosSet", 3095)
        self._set_node_or_raise("Tube_XSpeed", 500)
        return self._trigger_and_wait(TubeCommand.X_LEFT, "X向左")

    @action(auto_prefix=True, description="单点调试：X向右（指令类型=2）")
    def jog_x_right(self) -> dict:
        logger.info("离心管液体处理：单点调试 X向右...")
        self._set_node_or_raise("Tube_XPosSet", 0)
        self._set_node_or_raise("Tube_XSpeed", 500)
        return self._trigger_and_wait(TubeCommand.X_RIGHT, "X向右")

    @action(auto_prefix=True, description="单点调试：Y向前（指令类型=3）")
    def jog_y_forward(self) -> dict:
        logger.info("离心管液体处理：单点调试 Y向前...")
        self._set_node_or_raise("Tube_YPosSet", -2090)
        self._set_node_or_raise("Tube_YSpeed", 500)
        return self._trigger_and_wait(TubeCommand.Y_FORWARD, "Y向前")

    @action(auto_prefix=True, description="单点调试：Y向后（指令类型=4）")
    def jog_y_backward(self) -> dict:
        logger.info("离心管液体处理：单点调试 Y向后...")
        self._set_node_or_raise("Tube_YPosSet", 0)
        self._set_node_or_raise("Tube_YSpeed", 500)
        return self._trigger_and_wait(TubeCommand.Y_BACKWARD, "Y向后")

    # ==================== 内部逻辑（参照 centrifuge 写法） ====================

    @not_action
    def _write_pipette_option_params(
        self,
        ch8_blow: int = 0,
        ch1_blow: int = 0,
        ch8_reverse_aspirate: int = 0,
        ch1_reverse_aspirate: int = 0,
    ) -> None:
        """1.3.4 吹样/反向吸液写参（默认 0，与测试流程 yaml 一致）"""
        self._set_node_or_raise("Tube_Ch8Blow", ch8_blow)
        self._set_node_or_raise("Tube_Ch1Blow", ch1_blow)
        self._set_node_or_raise("Tube_Ch8ReverseAspirate", ch8_reverse_aspirate)
        self._set_node_or_raise("Tube_Ch1ReverseAspirate", ch1_reverse_aspirate)

    @not_action
    def _set_node_or_raise(self, node_name: str, value: int) -> None:
        if not self._opc_write(node_name, int(value)):
            raise ValueError(
                f"写入 {node_name}={value} 失败（OPC 连接可能已断开或 PLC 无此节点），请重启后重试"
            )

    @not_action
    def _write_lid_gripper_params(self) -> None:
        """小夹爪开/关盖共用参数（写死）"""
        self._set_node_or_raise("Tube_SmallGripperOpenLidStart", 1390)
        self._set_node_or_raise("Tube_SmallGripperOpenLidEnd", 1330)
        self._set_node_or_raise("Tube_SmallGripperOpenLidSpeed", 40)
        self._set_node_or_raise("Tube_SmallGripperOpenLid_RotationSpeed", 300)
        self._set_node_or_raise("Tube_SmallGripperCloseLidStart", 1330)
        self._set_node_or_raise("Tube_SmallGripperCloseLidEnd", 1390)
        self._set_node_or_raise("Tube_SmallGripperCloseLidSpeed", 40)
        self._set_node_or_raise("Tube_SmallGripperCloseLid_RotationSpeed", 300)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 180.0) -> dict:
        """下发指令类型并触发，等待 CompleteFB=1 后清理命令（同 rotary_stack，不等待 Complete 清零）。"""
        with self._command_lock:
            self._set_node_or_raise("Tube_CmdType", int(cmd_type))
            self._set_node_or_raise("Tube_CmdTrig", 1)
            try:
                if not self._wait_complete_value(
                    expected=1,
                    timeout=timeout,
                    description=f"{description}完成",
                ):
                    raise ValueError(f"{description}失败，动作未完成")
            finally:
                trigger_cleared = self._opc_write("Tube_CmdTrig", 0)
                command_cleared = self._opc_write("Tube_CmdType", 0)
                trigger_value = self._opc_read("Tube_CmdTrig", force_read=True)
                command_value = self._opc_read("Tube_CmdType", force_read=True)
                logger.info(
                    f"离心管液体处理命令清理：CmdTrig={trigger_value!r}，CmdType={command_value!r}"
                )

            if (
                not trigger_cleared
                or not command_cleared
                or trigger_value != 0
                or command_value != 0
            ):
                raise ValueError(
                    "动作已完成，但命令清零失败："
                    f"Tube_CmdTrig={trigger_value!r}, Tube_CmdType={command_value!r}"
                )

            logger.info(f"{description}完成")
            self._log_status(f"{description}后")
            return {"success": True, "message": f"{description}完成"}

    @not_action
    def _wait_complete_value(
        self,
        expected: int,
        timeout: float,
        interval: float = 0.05,
        description: str = "",
    ) -> bool:
        start = time.monotonic()
        read_fail_streak = 0
        while time.monotonic() - start < timeout:
            value = self._opc_read("Tube_CompleteFB", force_read=True)
            if value is None:
                read_fail_streak += 1
                if read_fail_streak >= 3:
                    logger.error(
                        f"✗ {description}中止：Tube_CompleteFB 连续读取失败，OPC 连接已断开，请退出并重启脚本"
                    )
                    return False
            else:
                read_fail_streak = 0
                if value == expected:
                    logger.info(f"✓ {description}（Tube_CompleteFB={value}）")
                    return True
            time.sleep(interval)
        value = self._opc_read("Tube_CompleteFB", force_read=True)
        logger.error(
            f"✗ {description}超时（等待 Tube_CompleteFB={expected}，当前={value!r}）"
        )
        return False

    # ==================== 整体测试流程 ====================

    @not_action
    def run_test_flow(self) -> dict:
        """依次执行 step0~step18 全部动作"""
        logger.info("离心管液体处理：开始整体测试流程...")
        self.ch8_load()
        self.ch8_aspirate()
        self.ch8_dispense()
        self.ch8_mix()
        self.ch8_unload()
        self.big_gripper_pick()
        self.big_gripper_place()
        self.ultrasound_mix()
        self.big_gripper_pick_2()
        self.big_gripper_place_2()
        self.small_gripper_open_lid()
        self.small_gripper_place()
        self.ch1_load()
        self.ch1_aspirate()
        self.ch1_dispense()
        self.ch1_unload()
        self.small_gripper_pick()
        self.small_gripper_close_lid()
        self.reset()
        logger.info("离心管液体处理：整体测试流程完成")
        return {"success": True, "message": "离心管液体处理测试流程完成"}

    # ==================== 状态读取 ====================

    @not_action
    def get_status(self) -> dict:
        return {
            "X": self.get_node_value("Tube_XPosFB"),
            "Y": self.get_node_value("Tube_YPosFB"),
            "Ch8Z": self.get_node_value("Tube_Z1PosFB"),
            "Ch1Z": self.get_node_value("Tube_Z3PosFB"),
            "complete": self.get_node_value("Tube_CompleteFB"),
        }

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        s = self.get_status()
        logger.info(
            f"{prefix}: X={s['X']} Y={s['Y']} Ch8Z={s['Ch8Z']} Ch1Z={s['Ch1Z']} "
            f"Complete={s['complete']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    TUBE_URL = "opc.tcp://192.168.6.6:4840"

    tube = CentrifugeTubeLiquidHandlingDevice(
        url=TUBE_URL,
        xlsx_path=DEFAULT_XLSX_PATH,
        use_subscription=False,
    )
    time.sleep(2)
    logger.info(f"连通性测试: {tube.get_status()}")

    while True:
        print("请选择操作：")
        print("0  读取状态（连通性测试）")
        print("1  8通道装载")
        print("2  8通道吸液")
        print("3  8通道放液")
        print("4  8通道混匀")
        print("5  8通道卸载")
        print("6  大夹爪抓取")
        print("7  大夹爪放置")
        print("8  超声波混匀")
        print("9  大夹爪抓取(2)")
        print("10 大夹爪放置(2)")
        print("11 小夹爪开盖")
        print("12 小夹爪放置")
        print("13 单通道装载")
        print("14 单通道吸液")
        print("15 单通道放液")
        print("16 单通道卸载")
        print("17 小夹爪抓取")
        print("18 小夹爪关盖")
        print("19 复位")
        print("20 超声波混匀立即停止")
        print("--- 单点调试（轴点动 1-4） ---")
        print("31 X向左")
        print("32 X向右")
        print("33 Y向前")
        print("34 Y向后")
        print("--- 恒温模块 (CmdType 38-47) ---")
        print("38 模块1恒温打开")
        print("39 模块1恒温关闭")
        print("40 模块2恒温打开")
        print("41 模块2恒温关闭")
        print("42 模块3恒温打开")
        print("43 模块3恒温关闭")
        print("44 模块4恒温打开")
        print("45 模块4恒温关闭")
        print("46 模块4震荡打开")
        print("47 模块4震荡关闭")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "0":
            print(tube.get_status())
        elif choice == "1":
            tube.ch8_load()
        elif choice == "2":
            tube.ch8_aspirate()
        elif choice == "3":
            tube.ch8_dispense()
        elif choice == "4":
            tube.ch8_mix()
        elif choice == "5":
            tube.ch8_unload()
        elif choice == "6":
            tube.big_gripper_pick()
        elif choice == "7":
            tube.big_gripper_place()
        elif choice == "8":
            tube.ultrasound_mix()
        elif choice == "9":
            tube.big_gripper_pick_2()
        elif choice == "10":
            tube.big_gripper_place_2()
        elif choice == "11":
            tube.small_gripper_open_lid()
        elif choice == "12":
            tube.small_gripper_place()
        elif choice == "13":
            tube.ch1_load()
        elif choice == "14":
            tube.ch1_aspirate()
        elif choice == "15":
            tube.ch1_dispense()
        elif choice == "16":
            tube.ch1_unload()
        elif choice == "17":
            tube.small_gripper_pick()
        elif choice == "18":
            tube.small_gripper_close_lid()
        elif choice == "19":
            tube.reset()
        elif choice == "20":
            tube.ultrasound_stop()
        elif choice == "31":
            tube.jog_x_left()
        elif choice == "32":
            tube.jog_x_right()
        elif choice == "33":
            tube.jog_y_forward()
        elif choice == "34":
            tube.jog_y_backward()
        elif choice == "38":
            temp = int(input("temperature [37]: ").strip() or "37")
            t_min = int(input("time_minutes [60]: ").strip() or "60")
            tube.thermostatic_module1_open(temperature=temp, time_minutes=t_min)
        elif choice == "39":
            tube.thermostatic_module1_close()
        elif choice == "40":
            temp = int(input("temperature [37]: ").strip() or "37")
            t_min = int(input("time_minutes [60]: ").strip() or "60")
            tube.thermostatic_module2_open(temperature=temp, time_minutes=t_min)
        elif choice == "41":
            tube.thermostatic_module2_close()
        elif choice == "42":
            temp = int(input("temperature [37]: ").strip() or "37")
            t_min = int(input("time_minutes [60]: ").strip() or "60")
            tube.thermostatic_module3_open(temperature=temp, time_minutes=t_min)
        elif choice == "43":
            tube.thermostatic_module3_close()
        elif choice == "44":
            temp = int(input("temperature [37]: ").strip() or "37")
            t_min = int(input("time_minutes [60]: ").strip() or "60")
            tube.thermostatic_module4_open(temperature=temp, time_minutes=t_min)
        elif choice == "45":
            tube.thermostatic_module4_close()
        elif choice == "46":
            temp = int(input("temperature [37]: ").strip() or "37")
            t_min = int(input("time_minutes [60]: ").strip() or "60")
            shake_speed = int(input("shaking_speed [300]: ").strip() or "300")
            shake_time = int(input("shaking_time [5]: ").strip() or "5")
            tube.thermostatic_module4_shake_open(
                temperature=temp,
                time_minutes=t_min,
                shaking_speed=shake_speed,
                shaking_time=shake_time,
            )
        elif choice == "47":
            tube.thermostatic_module4_shake_close()
        elif choice == "98":
            tube.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    tube.disconnect()
    print("退出程序。")
