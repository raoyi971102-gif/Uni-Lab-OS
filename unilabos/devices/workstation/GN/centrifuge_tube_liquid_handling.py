"""
离心管液体处理 设备驱动

参照 centrifuge.py / locking_mechanism.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 opcua_gn1.3.3.csv 中「离心管液体处理」(前缀 Tube_)。
各动作点位根据「离心管测试流程.yaml」写死。

指令类型 (Tube_CmdType)：
    19=8通道装载 20=8通道吸液 21=8通道放液 22=8通道卸载 34=8通道混匀
    31=大夹爪抓取 32=大夹爪放置 35=超声波混匀
    27=小夹爪开盖 30=小夹爪放置 29=小夹爪抓取 28=小夹爪关盖
    23=单通道装载 24=单通道吸液 25=单通道放液 26=单通道卸载
    36=复位 37=xyz回原点

YAML 字段 → CSV 节点映射：
    XPos/YPos           → Tube_XPosSet / Tube_YPosSet
    Ch8ZPos/Ch8LiquidPos → Tube_Z1PosSet / Tube_P1PosSet
    BigGripperZPos      → Tube_Z2PosSet
    Ch1LiquidPos/Ch1ZPos → Tube_P2PosSet / Tube_Z3PosSet
    SmallGripperZPos    → Tube_Z4PosSet
    MagneticRackPos     → Tube_MPosSet
    *Speed              → Tube_XSpeed / Tube_YSpeed / Tube_Z1Speed ...
    Ch8BlowValue 等     → Tube_Ch8Blow / Tube_Ch1Blow / Tube_Ch8ReverseAspirate ...
    LidAngle/LidForce   → Tube_SmallGripperAngle / Tube_SmallGripperForce
    MixCount            → Tube_MixCounts
    UltraSoundTime      → Tube_UltrasoundTime
    Tube_UltrasoundSTOP → 超声波混匀立即停止（脉冲写 1 后复位 0，不走 CmdType）
    SmallGripperOpenLid_* / CloseLid_* → Tube_SmallGripperOpenLid* / CloseLid*
"""

import os
import time
import logging
import threading
from enum import Enum

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")


class TubeCommand(int, Enum):
    """离心管液体处理指令类型 (Tube_CmdType)"""
    X_LEFT = 1
    X_RIGHT = 2
    Y_FORWARD = 3
    Y_BACKWARD = 4
    CH8_Z_UP = 5
    CH8_Z_DOWN = 6
    CH8_ASPIRATE = 7
    CH8_DISPENSE = 8
    BIG_GRIPPER_Z_UP = 9
    BIG_GRIPPER_Z_DOWN = 10
    CH1_ASPIRATE = 11
    CH1_DISPENSE = 12
    CH1_UP = 13
    CH1_DOWN = 14
    SMALL_GRIPPER_UP = 15
    SMALL_GRIPPER_DOWN = 16
    MAGNET_AXIS_UP = 17
    MAGNET_AXIS_DOWN = 18
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


@device(
    id="gn_centrifuge_tube_liquid_handling",
    display_name="离心管液体处理",
    category=["workstation"],
    description="GN 离心管液体处理：按测试流程完成分样/移液/开关盖，OPC UA 控制",
    icon="",
)
class CentrifugeTubeLiquidHandlingDevice(OpcUaClientWithSubscription):
    """离心管液体处理设备类（OPC 前缀 Tube_）"""

    ULTRASOUND_STOP_NODE = "Tube_UltrasoundSTOP"

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

    # ==================== 动作函数（点位写死，来自离心管测试流程 yaml） ====================

    @action(auto_prefix=True, description="1.8通道装载")
    def step0_ch8_load(self) -> dict:
        """8通道装载（yaml step0 Chanel8Load，指令类型=19）"""
        logger.info("离心管液体处理：8通道装载...")
        self._apply_setpoints(
            y_pos=-2090, x_pos=3095, ch8_z_pos=905, ch8_liquid_pos=500,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=500,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH8_LOAD_TIP, "8通道装载")

    @action(auto_prefix=True, description="2.8通道吸液")
    def step1_ch8_aspirate(self) -> dict:
        """8通道吸液（yaml step1 Chanel8Aspirate，指令类型=20）"""
        logger.info("离心管液体处理：8通道吸液...")
        self._apply_setpoints(
            y_pos=-2121, x_pos=4563, ch8_z_pos=900, ch8_liquid_pos=300,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=500,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH8_ASPIRATE_LIQUID, "8通道吸液")

    @action(auto_prefix=True, description="3.8通道放液")
    def step2_ch8_dispense(self) -> dict:
        """8通道放液（yaml step2 Chanel8Dispense，指令类型=21）"""
        logger.info("离心管液体处理：8通道放液...")
        self._apply_setpoints(
            y_pos=-3181, x_pos=3093, ch8_z_pos=1330, ch8_liquid_pos=300,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=500,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH8_DISPENSE_LIQUID, "8通道放液")

    @action(auto_prefix=True, description="4.8通道混匀")
    def step3_ch8_mix(self) -> dict:
        """8通道混匀（yaml step3 Chanel8Mix，指令类型=34）"""
        logger.info("离心管液体处理：8通道混匀...")
        self._apply_setpoints(
            y_pos=-3181, x_pos=3093, ch8_z_pos=1330, ch8_liquid_pos=300,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=500,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, mix_count=5, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH8_MIX, "8通道混匀")

    @action(auto_prefix=True, description="5.8通道卸载")
    def step4_ch8_unload(self) -> dict:
        """8通道卸载（yaml step4 Chanel8Unload，指令类型=22）"""
        logger.info("离心管液体处理：8通道卸载...")
        self._apply_setpoints(
            y_pos=-581, x_pos=2463, ch8_z_pos=1027, ch8_liquid_pos=500,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=3000,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH8_UNLOAD_TIP, "8通道卸载")

    @action(auto_prefix=True, description="6.大夹爪抓取")
    def step5_big_gripper_pick(self) -> dict:
        """大夹爪抓取（yaml step5 BigGripperClamp，指令类型=31）"""
        logger.info("离心管液体处理：大夹爪抓取...")
        self._apply_setpoints(
            y_pos=-3200, x_pos=2490, big_gripper_z_pos=1420,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
        )
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PICK, "大夹爪抓取")

    @action(auto_prefix=True, description="7.大夹爪放置")
    def step6_big_gripper_place(self) -> dict:
        """大夹爪放置（yaml step6 BigGripperPlace，指令类型=32）"""
        logger.info("离心管液体处理：大夹爪放置...")
        self._apply_setpoints(
            y_pos=-3550, x_pos=7100, big_gripper_z_pos=1215,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
        )
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PLACE, "大夹爪放置")

    @action(auto_prefix=True, description="8.超声波混匀")
    def step7_ultrasound_mix(self) -> dict:
        """超声波混匀（yaml step7 UltraSound，指令类型=35）"""
        logger.info("离心管液体处理：超声波混匀...")
        ultrasound_time = 5
        self._apply_setpoints(
            magnetic_rack_pos=300, magnetic_rack_speed=300,
            ultrasound_time=ultrasound_time,
        )
        timeout = ultrasound_time * 60 + 60
        return self._trigger_and_wait(
            TubeCommand.ULTRASOUND_MIX, "超声波混匀", timeout=timeout
        )

    @action(auto_prefix=True, description="9.大夹爪抓取(2)")
    def step8_big_gripper_pick(self) -> dict:
        """大夹爪抓取(2)（yaml step8 BigGripperClamp，指令类型=31）"""
        logger.info("离心管液体处理：大夹爪抓取(2)...")
        self._apply_setpoints(
            y_pos=-3550, x_pos=7100, big_gripper_z_pos=1215,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
        )
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PICK, "大夹爪抓取(2)")

    @action(auto_prefix=True, description="10.大夹爪放置(2)")
    def step9_big_gripper_place(self) -> dict:
        """大夹爪放置(2)（yaml step9 BigGripperPlace，指令类型=32）"""
        logger.info("离心管液体处理：大夹爪放置(2)...")
        self._apply_setpoints(
            y_pos=-3200, x_pos=2490, big_gripper_z_pos=1420,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
        )
        return self._trigger_and_wait(TubeCommand.BIG_GRIPPER_PLACE, "大夹爪放置(2)")

    @action(auto_prefix=True, description="11.小夹爪开盖")
    def step10_small_gripper_open_lid(self) -> dict:
        """小夹爪开盖（yaml step10 SmallGripperOpenLid，指令类型=27）"""
        logger.info("离心管液体处理：小夹爪开盖...")
        self._apply_setpoints(
            y_pos=-2300, x_pos=6670,
            open_lid_start=1390, open_lid_end=1330, open_lid_speed=40,
            open_lid_rotation_speed=300, close_lid_start=1330, close_lid_end=1390,
            close_lid_speed=40, close_lid_rotation_speed=300,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
            ch1_liquid_speed=500, ch1_z_speed=500, small_gripper_z_speed=500,
            lid_angle=540, lid_force=500,
        )
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_OPEN_LID, "小夹爪开盖")

    @action(auto_prefix=True, description="12.小夹爪放置")
    def step11_small_gripper_place(self) -> dict:
        """小夹爪放置（yaml step11 SmallGripperPlace，指令类型=30）"""
        logger.info("离心管液体处理：小夹爪放置...")
        self._apply_setpoints(
            y_pos=-480, x_pos=6330, small_gripper_z_pos=1375,
            open_lid_start=1390, open_lid_end=1330, open_lid_speed=40,
            open_lid_rotation_speed=300, close_lid_start=1330, close_lid_end=1390,
            close_lid_speed=40, close_lid_rotation_speed=300,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
            ch1_liquid_speed=500, ch1_z_speed=500, small_gripper_z_speed=500,
            lid_angle=540, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_PLACE, "小夹爪放置")

    @action(auto_prefix=True, description="13.单通道装载")
    def step12_ch1_load(self) -> dict:
        """单通道装载（yaml step12 Chanel1Load，指令类型=23）"""
        logger.info("离心管液体处理：单通道装载...")
        self._apply_setpoints(
            y_pos=-850, x_pos=720, ch1_liquid_pos=2000, ch1_z_pos=1030,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
            ch1_liquid_speed=500, ch1_z_speed=500, small_gripper_z_speed=500,
            lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH1_LOAD_TIP, "单通道装载")

    @action(auto_prefix=True, description="14.单通道吸液")
    def step13_ch1_aspirate(self) -> dict:
        """单通道吸液（yaml step13 Chanel1Aspirate，指令类型=24）"""
        logger.info("离心管液体处理：单通道吸液...")
        self._apply_setpoints(
            y_pos=-2250, x_pos=7920, ch8_liquid_pos=300,
            ch1_liquid_pos=2000, ch1_z_pos=1130,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=500,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH1_ASPIRATE_LIQUID, "单通道吸液")

    @action(auto_prefix=True, description="15.单通道放液")
    def step14_ch1_dispense(self) -> dict:
        """单通道放液（yaml step14 Chanel1Dispense，指令类型=25）"""
        logger.info("离心管液体处理：单通道放液...")
        self._apply_setpoints(
            y_pos=-2250, x_pos=3920, ch8_liquid_pos=300,
            ch1_liquid_pos=2000, ch1_z_pos=930,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=500,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH1_DISPENSE_LIQUID, "单通道放液")

    @action(auto_prefix=True, description="16.单通道卸载")
    def step15_ch1_unload(self) -> dict:
        """单通道卸载（yaml step15 Chanel1Unload，指令类型=26）"""
        logger.info("离心管液体处理：单通道卸载...")
        self._apply_setpoints(
            y_pos=-850, x_pos=20, ch8_liquid_pos=500,
            ch1_liquid_pos=2000, ch1_z_pos=1030,
            x_speed=500, y_speed=500, ch8_z_speed=500, ch8_liquid_speed=500,
            big_gripper_z_speed=500, ch1_liquid_speed=500, ch1_z_speed=500,
            small_gripper_z_speed=500, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.CH1_UNLOAD_TIP, "单通道卸载")

    @action(auto_prefix=True, description="17.小夹爪抓取")
    def step16_small_gripper_pick(self) -> dict:
        """小夹爪抓取（yaml step16 SmallGripperClamp，指令类型=29）"""
        logger.info("离心管液体处理：小夹爪抓取...")
        self._apply_setpoints(
            y_pos=-480, x_pos=6330, small_gripper_z_pos=1375,
            open_lid_start=1390, open_lid_end=1330, open_lid_speed=40,
            open_lid_rotation_speed=300, close_lid_start=1330, close_lid_end=1390,
            close_lid_speed=40, close_lid_rotation_speed=300,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
            ch1_liquid_speed=500, ch1_z_speed=500, small_gripper_z_speed=500,
            lid_angle=540, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_PICK, "小夹爪抓取")

    @action(auto_prefix=True, description="18.小夹爪关盖")
    def step17_small_gripper_close_lid(self) -> dict:
        """小夹爪关盖（yaml step17 SmallGripperCloseLid，指令类型=28）"""
        logger.info("离心管液体处理：小夹爪关盖...")
        self._apply_setpoints(
            y_pos=-2300, x_pos=6670,
            open_lid_start=1390, open_lid_end=1330, open_lid_speed=40,
            open_lid_rotation_speed=300, close_lid_start=1330, close_lid_end=1390,
            close_lid_speed=40, close_lid_rotation_speed=300,
            x_speed=500, y_speed=500, big_gripper_z_speed=500,
            ch1_liquid_speed=500, ch1_z_speed=500, small_gripper_z_speed=500,
            lid_angle=-540, lid_force=300,
        )
        return self._trigger_and_wait(TubeCommand.SMALL_GRIPPER_CLOSE_LID, "小夹爪关盖")

    @action(auto_prefix=True, description="19.离心管液体处理复位")
    def step18_reset(self) -> dict:
        """离心管液体处理复位（yaml step18 centrifugetubeliquidreset，指令类型=36）"""
        logger.info("离心管液体处理：复位...")
        self._apply_setpoints(lid_angle=180, lid_force=100)
        return self._trigger_and_wait(TubeCommand.RESET, "复位")

    @action(auto_prefix=True, description="20.超声波混匀立即停止")
    def ultrasound_stop(self) -> dict:
        """超声波混匀立即停止：脉冲写 Tube_UltrasoundSTOP=1 后复位为 0"""
        logger.info("离心管液体处理：超声波混匀立即停止...")
        self.set_node_value(self.ULTRASOUND_STOP_NODE, 1)
        time.sleep(0.2)
        self.set_node_value(self.ULTRASOUND_STOP_NODE, 0)
        logger.info("超声波混匀立即停止命令已下发")
        self._log_status("超声波混匀立即停止后")
        return {"success": True, "message": "超声波混匀立即停止命令已下发"}

    @action(auto_prefix=True, description="XYZ 回原点")
    def home_xyz(self) -> dict:
        return self._trigger_and_wait(TubeCommand.HOME_XYZ, "xyz回原点")

    # ==================== 单点调试（轴点动 1-4） ====================

    @action(auto_prefix=True, description="单点调试：X向左（指令类型=1）")
    def jog_x_left(self) -> dict:
        logger.info("离心管液体处理：单点调试 X向左...")
        self.set_node_value("Tube_XPosSet", 3095)
        self.set_node_value("Tube_XSpeed", 500)
        return self._trigger_and_wait(TubeCommand.X_LEFT, "X向左")

    @action(auto_prefix=True, description="单点调试：X向右（指令类型=2）")
    def jog_x_right(self) -> dict:
        logger.info("离心管液体处理：单点调试 X向右...")
        self.set_node_value("Tube_XPosSet", 0)
        self.set_node_value("Tube_XSpeed", 500)
        return self._trigger_and_wait(TubeCommand.X_RIGHT, "X向右")

    @action(auto_prefix=True, description="单点调试：Y向前（指令类型=3）")
    def jog_y_forward(self) -> dict:
        logger.info("离心管液体处理：单点调试 Y向前...")
        self.set_node_value("Tube_YPosSet", -2090)
        self.set_node_value("Tube_YSpeed", 500)
        return self._trigger_and_wait(TubeCommand.Y_FORWARD, "Y向前")

    @action(auto_prefix=True, description="单点调试：Y向后（指令类型=4）")
    def jog_y_backward(self) -> dict:
        logger.info("离心管液体处理：单点调试 Y向后...")
        self.set_node_value("Tube_YPosSet", 0)
        self.set_node_value("Tube_YSpeed", 500)
        return self._trigger_and_wait(TubeCommand.Y_BACKWARD, "Y向后")

    # ==================== 内部触发/等待逻辑（参照 centrifuge 写法） ====================

    @not_action
    def _apply_setpoints(
        self,
        x_pos: int = 0,
        y_pos: int = 0,
        ch8_z_pos: int = 0,
        ch8_liquid_pos: int = 0,
        big_gripper_z_pos: int = 0,
        ch1_liquid_pos: int = 0,
        ch1_z_pos: int = 0,
        small_gripper_z_pos: int = 0,
        x_speed: int = 0,
        y_speed: int = 0,
        ch8_z_speed: int = 0,
        ch8_liquid_speed: int = 0,
        big_gripper_z_speed: int = 0,
        ch1_liquid_speed: int = 0,
        ch1_z_speed: int = 0,
        small_gripper_z_speed: int = 0,
        ch8_blow: int = 0,
        ch1_blow: int = 0,
        ch8_reverse_aspirate: int = 0,
        ch1_reverse_aspirate: int = 0,
        magnetic_rack_pos: int = 0,
        magnetic_rack_speed: int = 0,
        ultrasound_time: int = 0,
        mix_count: int = 0,
        lid_angle: int = 0,
        lid_force: int = 0,
        open_lid_start: int = 0,
        open_lid_end: int = 0,
        open_lid_speed: int = 0,
        open_lid_rotation_speed: int = 0,
        close_lid_start: int = 0,
        close_lid_end: int = 0,
        close_lid_speed: int = 0,
        close_lid_rotation_speed: int = 0,
    ) -> None:
        """写入运行位置/速度/搅拌/开盖等 setpoint 节点"""
        self.set_node_value("Tube_XPosSet", x_pos)
        self.set_node_value("Tube_YPosSet", y_pos)
        self.set_node_value("Tube_Z1PosSet", ch8_z_pos)
        self.set_node_value("Tube_P1PosSet", ch8_liquid_pos)
        self.set_node_value("Tube_Z2PosSet", big_gripper_z_pos)
        self.set_node_value("Tube_P2PosSet", ch1_liquid_pos)
        self.set_node_value("Tube_Z3PosSet", ch1_z_pos)
        self.set_node_value("Tube_Z4PosSet", small_gripper_z_pos)
        self.set_node_value("Tube_XSpeed", x_speed)
        self.set_node_value("Tube_YSpeed", y_speed)
        self.set_node_value("Tube_Z1Speed", ch8_z_speed)
        self.set_node_value("Tube_P1Speed", ch8_liquid_speed)
        self.set_node_value("Tube_Z2Speed", big_gripper_z_speed)
        self.set_node_value("Tube_P2Speed", ch1_liquid_speed)
        self.set_node_value("Tube_Z3Speed", ch1_z_speed)
        self.set_node_value("Tube_Z4Speed", small_gripper_z_speed)
        self.set_node_value("Tube_Ch8Blow", ch8_blow)
        self.set_node_value("Tube_Ch1Blow", ch1_blow)
        self.set_node_value("Tube_Ch8ReverseAspirate", ch8_reverse_aspirate)
        self.set_node_value("Tube_Ch1ReverseAspirate", ch1_reverse_aspirate)
        self.set_node_value("Tube_MPosSet", magnetic_rack_pos)
        self.set_node_value("Tube_MSpeed", magnetic_rack_speed)
        self.set_node_value("Tube_UltrasoundTime", ultrasound_time)
        self.set_node_value("Tube_MixCounts", mix_count)
        self.set_node_value("Tube_SmallGripperAngle", lid_angle)
        self.set_node_value("Tube_SmallGripperForce", lid_force)
        self.set_node_value("Tube_SmallGripperOpenLidStart", open_lid_start)
        self.set_node_value("Tube_SmallGripperOpenLidEnd", open_lid_end)
        self.set_node_value("Tube_SmallGripperOpenLidSpeed", open_lid_speed)
        self.set_node_value("Tube_SmallGripperOpenLid_RotationSpeed", open_lid_rotation_speed)
        self.set_node_value("Tube_SmallGripperCloseLidStart", close_lid_start)
        self.set_node_value("Tube_SmallGripperCloseLidEnd", close_lid_end)
        self.set_node_value("Tube_SmallGripperCloseLidSpeed", close_lid_speed)
        self.set_node_value("Tube_SmallGripperCloseLid_RotationSpeed", close_lid_rotation_speed)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 180.0) -> dict:
        """下发指令类型并触发，等待完成后复位触发。"""
        self.set_node_value("Tube_CmdType", int(cmd_type))
        self.set_node_value("Tube_CmdTrig", 1)
        if self._wait_until_true(
            "Tube_CompleteFB", timeout=timeout, description=f"{description}完成"
        ):
            self.set_node_value("Tube_CmdTrig", 0)
            if self._wait_until_false(
                "Tube_CompleteFB", description=f"{description}完成复位"
            ):
                logger.info(f"{description}完成")
                self._log_status(f"{description}后")
                return {"success": True, "message": f"{description}完成"}
            raise ValueError(f"{description}失败，完成复位超时")
        raise ValueError(f"{description}失败，动作未完成")

    @not_action
    def _wait_until_true(self, node_name: str, timeout: float = 180.0,
                         interval: float = 0.2, description: str = None) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc} 变为完成（轮询节点: {node_name}）...")
        start = time.time()
        while True:
            value = self.get_node_value(node_name, force_read=True)
            if value:
                logger.info(f"✓ {desc}（节点 [{node_name}]={value}）")
                return True
            if time.time() - start >= timeout:
                logger.error(f"✗ 等待 {desc} 超时（{timeout}秒，节点 [{node_name}] 仍为 {value!r}）")
                return False
            time.sleep(interval)

    @not_action
    def _wait_until_false(self, node_name: str, timeout: float = 120.0,
                          interval: float = 0.2, description: str = None) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc} 复位（轮询节点: {node_name}）...")
        start = time.time()
        while True:
            value = self.get_node_value(node_name, force_read=True)
            if not value:
                logger.info(f"✓ {desc}（节点 [{node_name}]={value}）")
                return True
            if time.time() - start >= timeout:
                logger.error(f"✗ 等待 {desc} 超时（{timeout}秒，节点 [{node_name}] 仍为 {value!r}）")
                return False
            time.sleep(interval)

    # ==================== 整体测试流程 ====================

    @not_action
    def run_test_flow(self) -> dict:
        """按离心管测试流程 yaml 依次执行全部步骤"""
        logger.info("离心管液体处理：开始整体测试流程...")
        self.step0_ch8_load()
        self.step1_ch8_aspirate()
        self.step2_ch8_dispense()
        self.step3_ch8_mix()
        self.step4_ch8_unload()
        self.step5_big_gripper_pick()
        self.step6_big_gripper_place()
        self.step7_ultrasound_mix()
        self.step8_big_gripper_pick()
        self.step9_big_gripper_place()
        self.step10_small_gripper_open_lid()
        self.step11_small_gripper_place()
        self.step12_ch1_load()
        self.step13_ch1_aspirate()
        self.step14_ch1_dispense()
        self.step15_ch1_unload()
        self.step16_small_gripper_pick()
        self.step17_small_gripper_close_lid()
        self.step18_reset()
        logger.info("离心管液体处理：整体测试流程完成")
        return {"success": True, "message": "离心管液体处理测试流程完成"}

    # ==================== 状态读取 ====================

    @not_action
    def get_status(self) -> dict:
        status = {
            "X": self.get_node_value("Tube_XPosFB"),
            "Y": self.get_node_value("Tube_YPosFB"),
            "Ch8Z": self.get_node_value("Tube_Z1PosFB"),
            "Ch1Z": self.get_node_value("Tube_Z3PosFB"),
            "complete": self.get_node_value("Tube_CompleteFB"),
        }
        for i in range(1, 6):
            status[f"cooling_{i}"] = self.get_node_value(
                f"Liquid_Cooling_CentrifugeTube_{i}_FB"
            )
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        s = self.get_status()
        cooling = " ".join(
            f"C{i}={s.get(f'cooling_{i}')}" for i in range(1, 6) if s.get(f"cooling_{i}") is not None
        )
        logger.info(
            f"{prefix}: X={s['X']} Y={s['Y']} Ch8Z={s['Ch8Z']} Ch1Z={s['Ch1Z']} "
            f"Complete={s['complete']}" + (f" {cooling}" if cooling else "")
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    TUBE_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    tube = CentrifugeTubeLiquidHandlingDevice(
        url=TUBE_URL,
        csv_path=DEFAULT_CSV_PATH,
        use_subscription=False,
    )

    time.sleep(2)
    init_status = tube.get_status()
    logger.info(f"连通性测试: {init_status}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                tube._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    status_log_thread = threading.Thread(
        target=_status_log_worker, daemon=True, name="TubeLiquidStatusLog"
    )
    status_log_thread.start()
    logger.info(f"已启动离心管液体处理状态日志（间隔 {STATUS_LOG_INTERVAL}s，无订阅）")

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
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "0":
            print(tube.get_status())
        elif choice == "1":
            tube.step0_ch8_load()
        elif choice == "2":
            tube.step1_ch8_aspirate()
        elif choice == "3":
            tube.step2_ch8_dispense()
        elif choice == "4":
            tube.step3_ch8_mix()
        elif choice == "5":
            tube.step4_ch8_unload()
        elif choice == "6":
            tube.step5_big_gripper_pick()
        elif choice == "7":
            tube.step6_big_gripper_place()
        elif choice == "8":
            tube.step7_ultrasound_mix()
        elif choice == "9":
            tube.step8_big_gripper_pick()
        elif choice == "10":
            tube.step9_big_gripper_place()
        elif choice == "11":
            tube.step10_small_gripper_open_lid()
        elif choice == "12":
            tube.step11_small_gripper_place()
        elif choice == "13":
            tube.step12_ch1_load()
        elif choice == "14":
            tube.step13_ch1_aspirate()
        elif choice == "15":
            tube.step14_ch1_dispense()
        elif choice == "16":
            tube.step15_ch1_unload()
        elif choice == "17":
            tube.step16_small_gripper_pick()
        elif choice == "18":
            tube.step17_small_gripper_close_lid()
        elif choice == "19":
            tube.step18_reset()
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
        elif choice == "98":
            tube.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    status_log_thread.join(timeout=STATUS_LOG_INTERVAL + 1)
    tube.disconnect()
    print("退出程序。")
