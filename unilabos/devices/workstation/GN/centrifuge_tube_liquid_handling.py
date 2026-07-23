"""
离心管液体处理 设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「离心管液体处理」；节点：opcua_gn1.3.3.csv（前缀 Tube_）。

对外仅暴露 execute_command（Tube_CmdType + 写参）；测试流程预设供本地调试。
ultrasound_stop=True 时脉冲 Tube_UltrasoundSTOP，忽略 cmd_type。
"""

import json
import os
import re
import time
import logging
import threading
from enum import Enum
from typing import Any, Literal, Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

_GN_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV_PATH = os.path.join(_GN_DIR, "opcua_gn1.3.3.csv")
DEFAULT_APPSETTINGS_PATH = os.path.join(_GN_DIR, "appsettings.txt")

BottleType = Literal["large", "small"]

# 开盖/关盖小夹爪参数（与离心管测试流程.yaml 一致）
_LID_OPEN_ANGLE = 540
_LID_OPEN_FORCE = 500
_LID_HANDLE_ANGLE = 540
_LID_HANDLE_FORCE = 300
_LID_CLOSE_ANGLE = -540


def strip_json_comments(text: str) -> str:
    """去掉 appsettings.txt 中的 // 行注释，保留字符串内的 //。"""
    out: list[str] = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_appsettings(path: str = DEFAULT_APPSETTINGS_PATH) -> dict[str, Any]:
    """加载 GN 工站 appsettings.txt（含 PositionInfo / LidSlots）。"""
    raw = open(path, encoding="utf-8").read()
    return json.loads(strip_json_comments(raw))


def resolve_bottle_lid_positions(
    settings: dict[str, Any],
    bottle_index: int,
    bottle_type: BottleType,
) -> tuple[dict[str, int], dict[str, int]]:
    """返回 (瓶身 SmallGripper XYZ, 瓶盖 LidSlot XYZ)。"""
    tube_pos = settings["PositionInfo"]["CentrifugeTubeLiquidHandlingPosition"]
    if bottle_type == "large":
        bottles = tube_pos["LargeBottles"]
        lid_index = bottle_index
    else:
        bottles = tube_pos["SmallBottles"]
        lid_index = bottle_index + 6
    bottle = next(b for b in bottles if int(b["Index"]) == bottle_index)
    lid = next(s for s in tube_pos["LidSlots"] if int(s["Index"]) == lid_index)
    sg = bottle["SmallGripper"]
    xyz = lid["Xyz"]
    bottle_xyz = {"x": int(sg["X"]), "y": int(sg["Y"]), "z": int(sg["Z"])}
    lid_xyz = {"x": int(xyz["X"]), "y": int(xyz["Y"]), "z": int(xyz["Z"])}
    return bottle_xyz, lid_xyz


def resolve_bottle_channel1_position(
    settings: dict[str, Any],
    bottle_index: int,
    bottle_type: BottleType,
) -> dict[str, int]:
    """返回试剂瓶单通道移液位（X/Y/Z），供 8 通道放液等步骤使用。"""
    tube_pos = settings["PositionInfo"]["CentrifugeTubeLiquidHandlingPosition"]
    bottles = (
        tube_pos["LargeBottles"]
        if bottle_type == "large"
        else tube_pos["SmallBottles"]
    )
    bottle = next(b for b in bottles if int(b["Index"]) == bottle_index)
    ch1 = bottle["Channel1"]
    return {"x": int(ch1["X"]), "y": int(ch1["Y"]), "z": int(ch1["Z"])}

# OPC 1.3.3 Tube_CmdType（与 Excel 表头一致）
TUBE_CMD_LABELS = {
    1: "X向左",
    2: "X向右",
    3: "Y向前",
    4: "Y向后",
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
    34: "8通道混匀",
    35: "超声波混匀",
    36: "复位",
    37: "xyz回原点",
}

# 小夹爪开/关盖共用速度参数（不在 execute_command 可选写参中）
_LID_GRIPPER_SPEED_DEFAULTS = dict(
    z2_speed=500, p2_speed=500, z3_speed=500, z4_speed=500,
)

# 8 通道步骤共用速度
_CH8_SPEED_DEFAULTS = dict(
    x_speed=500, y_speed=500, z1_speed=500, p1_speed=500,
    z2_speed=500, p2_speed=500, z3_speed=500, z4_speed=500,
    small_gripper_force=300,
)

# 单通道步骤共用速度
_CH1_SPEED_DEFAULTS = dict(
    x_speed=500, y_speed=500, z1_speed=500, p1_speed=500,
    z2_speed=500, p2_speed=500, z3_speed=500, z4_speed=500,
    small_gripper_force=300,
)


class TubeCommand(int, Enum):
    """离心管液体处理指令类型 (Tube_CmdType)"""

    X_LEFT = 1
    X_RIGHT = 2
    Y_FORWARD = 3
    Y_BACKWARD = 4
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
    CH8_MIX = 34
    ULTRASOUND_MIX = 35
    RESET = 36
    HOME_XYZ = 37


# 离心管液体处理测试流程预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS = [
    ("1.8通道装载", TubeCommand.CH8_LOAD_TIP, dict(
        x_pos=3095, y_pos=-2090, z1_pos=905, p1_pos=500, **_CH8_SPEED_DEFAULTS,
    )),
    ("2.8通道吸液", TubeCommand.CH8_ASPIRATE_LIQUID, dict(
        x_pos=4563, y_pos=-2121, z1_pos=900, p1_pos=300, **_CH8_SPEED_DEFAULTS,
    )),
    ("3.8通道放液", TubeCommand.CH8_DISPENSE_LIQUID, dict(
        x_pos=3093, y_pos=-3181, z1_pos=1330, p1_pos=300, **_CH8_SPEED_DEFAULTS,
    )),
    ("4.8通道混匀", TubeCommand.CH8_MIX, dict(
        x_pos=3093, y_pos=-3181, z1_pos=1330, p1_pos=300,
        mix_counts=5, **_CH8_SPEED_DEFAULTS,
    )),
    ("5.8通道卸载", TubeCommand.CH8_UNLOAD_TIP, dict(
        x_pos=2463, y_pos=-581, z1_pos=1027, p1_pos=500,
        x_speed=500, y_speed=500, z1_speed=500, p1_speed=3000,
        z2_speed=500, p2_speed=500, z3_speed=500, z4_speed=500,
        small_gripper_force=300,
    )),
    ("6.大夹爪抓取", TubeCommand.BIG_GRIPPER_PICK, dict(
        x_pos=2490, y_pos=-3200, z2_pos=1420,
        x_speed=500, y_speed=500, z2_speed=500,
    )),
    ("7.大夹爪放置", TubeCommand.BIG_GRIPPER_PLACE, dict(
        x_pos=7100, y_pos=-3550, z2_pos=1215,
        x_speed=500, y_speed=500, z2_speed=500,
    )),
    ("8.超声波混匀", TubeCommand.ULTRASOUND_MIX, dict(
        m_pos=300, m_speed=300, ultrasound_time=5,
    )),
    ("9.大夹爪抓取(2)", TubeCommand.BIG_GRIPPER_PICK, dict(
        x_pos=7100, y_pos=-3550, z2_pos=1215,
        x_speed=500, y_speed=500, z2_speed=500,
    )),
    ("10.大夹爪放置(2)", TubeCommand.BIG_GRIPPER_PLACE, dict(
        x_pos=2490, y_pos=-3200, z2_pos=1420,
        x_speed=500, y_speed=500, z2_speed=500,
    )),
    ("11.小夹爪开盖", TubeCommand.SMALL_GRIPPER_OPEN_LID, dict(
        x_pos=6670, y_pos=-2300,
        small_gripper_angle=540, small_gripper_force=500,
        **_LID_GRIPPER_SPEED_DEFAULTS,
    )),
    ("12.小夹爪放置", TubeCommand.SMALL_GRIPPER_PLACE, dict(
        x_pos=6330, y_pos=-480, z4_pos=1375,
        small_gripper_angle=540, small_gripper_force=300,
        **_LID_GRIPPER_SPEED_DEFAULTS,
    )),
    ("13.单通道装载", TubeCommand.CH1_LOAD_TIP, dict(
        x_pos=720, y_pos=-850, p2_pos=2000, z3_pos=1030, **_CH1_SPEED_DEFAULTS,
    )),
    ("14.单通道吸液", TubeCommand.CH1_ASPIRATE_LIQUID, dict(
        x_pos=7920, y_pos=-2250, p1_pos=300, p2_pos=2000, z3_pos=1130, **_CH1_SPEED_DEFAULTS,
    )),
    ("15.单通道放液", TubeCommand.CH1_DISPENSE_LIQUID, dict(
        x_pos=3920, y_pos=-2250, p1_pos=300, p2_pos=2000, z3_pos=930, **_CH1_SPEED_DEFAULTS,
    )),
    ("16.单通道卸载", TubeCommand.CH1_UNLOAD_TIP, dict(
        x_pos=20, y_pos=-850, p1_pos=500, p2_pos=2000, z3_pos=1030, **_CH1_SPEED_DEFAULTS,
    )),
    ("17.小夹爪抓取", TubeCommand.SMALL_GRIPPER_PICK, dict(
        x_pos=6330, y_pos=-480, z4_pos=1375,
        small_gripper_angle=540, small_gripper_force=300,
        **_LID_GRIPPER_SPEED_DEFAULTS,
    )),
    ("18.小夹爪关盖", TubeCommand.SMALL_GRIPPER_CLOSE_LID, dict(
        x_pos=6670, y_pos=-2300,
        small_gripper_angle=-540, small_gripper_force=300,
        **_LID_GRIPPER_SPEED_DEFAULTS,
    )),
    ("19.复位", TubeCommand.RESET, dict(
        small_gripper_angle=180, small_gripper_force=100,
    )),
    ("20.xyz回原点", TubeCommand.HOME_XYZ, dict()),
]


_EXECUTE_CMD_DOC = (
    "按 Tube_CmdType 执行 OPC 1.3.3 指令。"
    "1=X左 2=X右 3=Y前 4=Y后 19=8通道装载 20=8通道吸液 21=8通道放液 22=8通道卸载 "
    "23=单通道装载 24=单通道吸液 25=单通道放液 26=单通道卸载 "
    "27=小夹爪开盖 28=小夹爪关盖 29=小夹爪抓取 30=小夹爪放置 "
    "31=大夹爪抓取 32=大夹爪放置 34=8通道混匀 35=超声波混匀 36=复位 37=xyz回原点。"
    "ultrasound_stop=True 时立即停止超声波混匀。"
)


@device(
    id="gn_centrifuge_tube_liquid_handling",
    display_name="离心管液体处理",
    category=["workstation"],
    description="GN 离心管液体处理：OPC UA 1.3.3，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class CentrifugeTubeLiquidHandlingDevice(OpcUaClientWithSubscription):
    """离心管液体处理设备类（OPC 前缀 Tube_）"""

    CMD_TYPE_NODE = "Tube_CmdType"
    CMD_TRIG_NODE = "Tube_CmdTrig"
    COMPLETE_NODE = "Tube_CompleteFB"
    ULTRASOUND_STOP_NODE = "Tube_UltrasoundSTOP"

    _LID_GRIPPER_CMDS = frozenset({
        int(TubeCommand.SMALL_GRIPPER_OPEN_LID),
        int(TubeCommand.SMALL_GRIPPER_CLOSE_LID),
        int(TubeCommand.SMALL_GRIPPER_PICK),
        int(TubeCommand.SMALL_GRIPPER_PLACE),
    })

    def __init__(
        self,
        url: str,
        csv_path: str = DEFAULT_CSV_PATH,
        appsettings_path: str = DEFAULT_APPSETTINGS_PATH,
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
        self.appsettings_path = appsettings_path
        self._appsettings: Optional[dict[str, Any]] = None

    @not_action
    def _get_appsettings(self) -> dict[str, Any]:
        if self._appsettings is None:
            self._appsettings = load_appsettings(self.appsettings_path)
        return self._appsettings

    @not_action
    def _resolve_bottle_lid(
        self, bottle_index: int, bottle_type: BottleType
    ) -> tuple[dict[str, int], dict[str, int]]:
        return resolve_bottle_lid_positions(
            self._get_appsettings(), bottle_index, bottle_type
        )

    @not_action
    def _resolve_ch8_work_position(
        self,
        bottle_index: Optional[int] = None,
        bottle_type: Optional[BottleType] = None,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
    ) -> tuple[int, int, int]:
        """优先显式坐标；否则按 bottle_index + bottle_type 查 appsettings。"""
        if x_pos is not None and y_pos is not None:
            return x_pos, y_pos, z1_pos if z1_pos is not None else 1330
        if bottle_index is not None and bottle_type is not None:
            ch1 = resolve_bottle_channel1_position(
                self._get_appsettings(), bottle_index, bottle_type
            )
            return ch1["x"], ch1["y"], z1_pos if z1_pos is not None else ch1["z"]
        raise ValueError("8 通道步骤需 bottle_index+bottle_type 或 x_pos+y_pos")

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int = 0,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        z3_pos: Optional[int] = None,
        z4_pos: Optional[int] = None,
        p1_pos: Optional[int] = None,
        p2_pos: Optional[int] = None,
        m_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        z4_speed: Optional[int] = None,
        p1_speed: Optional[int] = None,
        p2_speed: Optional[int] = None,
        m_speed: Optional[int] = None,
        small_gripper_force: Optional[int] = None,
        small_gripper_angle: Optional[int] = None,
        mix_counts: Optional[int] = None,
        ultrasound_time: Optional[int] = None,
        ultrasound_stop: bool = False,
        timeout: float = 180.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        if ultrasound_stop:
            return self._pulse_ultrasound_stop()

        setpoints = self._build_setpoints(
            x_pos=x_pos, y_pos=y_pos,
            z1_pos=z1_pos, z2_pos=z2_pos, z3_pos=z3_pos, z4_pos=z4_pos,
            p1_pos=p1_pos, p2_pos=p2_pos, m_pos=m_pos,
            x_speed=x_speed, y_speed=y_speed,
            z1_speed=z1_speed, z2_speed=z2_speed, z3_speed=z3_speed, z4_speed=z4_speed,
            p1_speed=p1_speed, p2_speed=p2_speed, m_speed=m_speed,
            small_gripper_force=small_gripper_force,
            small_gripper_angle=small_gripper_angle,
            mix_counts=mix_counts,
            ultrasound_time=ultrasound_time,
        )
        label = TUBE_CMD_LABELS.get(int(cmd_type), f"CmdType={int(cmd_type)}")
        effective_timeout = timeout
        if int(cmd_type) == int(TubeCommand.ULTRASOUND_MIX) and ultrasound_time is not None:
            effective_timeout = ultrasound_time * 60 + 60
        return self._run(int(cmd_type), label, setpoints, timeout=effective_timeout)

    @action(description="开盖：小夹爪开盖(27) → 瓶盖放至 LidSlots（appsettings 标定坐标）")
    def open_bottle_lid(
        self,
        bottle_index: int = 1,
        bottle_type: BottleType = "large",
        timeout: float = 180.0,
    ) -> dict:
        bottle, lid = self._resolve_bottle_lid(bottle_index, bottle_type)
        logger.info(
            f"开盖: {bottle_type}#{bottle_index} 瓶({bottle}) → 盖槽({lid})"
        )
        open_ret = self._run(
            int(TubeCommand.SMALL_GRIPPER_OPEN_LID),
            f"小夹爪开盖({bottle_type}#{bottle_index})",
            self._build_setpoints(
                x_pos=bottle["x"], y_pos=bottle["y"],
                small_gripper_angle=_LID_OPEN_ANGLE,
                small_gripper_force=_LID_OPEN_FORCE,
                **_LID_GRIPPER_SPEED_DEFAULTS,
            ),
            timeout=timeout,
        )
        place_ret = self._run(
            int(TubeCommand.SMALL_GRIPPER_PLACE),
            f"小夹爪放盖至槽位({bottle_type}#{bottle_index})",
            self._build_setpoints(
                x_pos=lid["x"], y_pos=lid["y"], z4_pos=lid["z"],
                small_gripper_angle=_LID_HANDLE_ANGLE,
                small_gripper_force=_LID_HANDLE_FORCE,
                **_LID_GRIPPER_SPEED_DEFAULTS,
            ),
            timeout=timeout,
        )
        return {"success": True, "open": open_ret, "place_lid": place_ret}

    @action(description="关盖：从 LidSlots 取盖(29) → 小夹爪关盖(28)")
    def close_bottle_lid(
        self,
        bottle_index: int = 1,
        bottle_type: BottleType = "large",
        timeout: float = 180.0,
    ) -> dict:
        bottle, lid = self._resolve_bottle_lid(bottle_index, bottle_type)
        logger.info(
            f"关盖: 盖槽({lid}) → {bottle_type}#{bottle_index} 瓶({bottle})"
        )
        pick_ret = self._run(
            int(TubeCommand.SMALL_GRIPPER_PICK),
            f"小夹爪取盖({bottle_type}#{bottle_index})",
            self._build_setpoints(
                x_pos=lid["x"], y_pos=lid["y"], z4_pos=lid["z"],
                small_gripper_angle=_LID_HANDLE_ANGLE,
                small_gripper_force=_LID_HANDLE_FORCE,
                **_LID_GRIPPER_SPEED_DEFAULTS,
            ),
            timeout=timeout,
        )
        close_ret = self._run(
            int(TubeCommand.SMALL_GRIPPER_CLOSE_LID),
            f"小夹爪关盖({bottle_type}#{bottle_index})",
            self._build_setpoints(
                x_pos=bottle["x"], y_pos=bottle["y"],
                small_gripper_angle=_LID_CLOSE_ANGLE,
                small_gripper_force=_LID_HANDLE_FORCE,
                **_LID_GRIPPER_SPEED_DEFAULTS,
            ),
            timeout=timeout,
        )
        return {"success": True, "pick_lid": pick_ret, "close": close_ret}

    @action(description="8 通道吸液 (cmd 20)；坐标来自 bottle 或显式 x_pos/y_pos")
    def ch8_aspirate(
        self,
        bottle_index: Optional[int] = None,
        bottle_type: Optional[BottleType] = None,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        p1_pos: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        x, y, z1 = self._resolve_ch8_work_position(
            bottle_index, bottle_type, x_pos, y_pos, z1_pos
        )
        return self.execute_command(
            cmd_type=int(TubeCommand.CH8_ASPIRATE_LIQUID),
            x_pos=x, y_pos=y, z1_pos=z1, p1_pos=p1_pos,
            **_CH8_SPEED_DEFAULTS,
            timeout=timeout,
        )

    @action(description="8 通道放液 (cmd 21)；坐标来自 bottle 或显式 x_pos/y_pos")
    def ch8_dispense(
        self,
        bottle_index: Optional[int] = None,
        bottle_type: Optional[BottleType] = None,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        p1_pos: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        x, y, z1 = self._resolve_ch8_work_position(
            bottle_index, bottle_type, x_pos, y_pos, z1_pos
        )
        return self.execute_command(
            cmd_type=int(TubeCommand.CH8_DISPENSE_LIQUID),
            x_pos=x, y_pos=y, z1_pos=z1, p1_pos=p1_pos,
            **_CH8_SPEED_DEFAULTS,
            timeout=timeout,
        )

    @action(description="8 通道吹打混匀 (cmd 34)")
    def ch8_mix(
        self,
        bottle_index: Optional[int] = None,
        bottle_type: Optional[BottleType] = None,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        p1_pos: int = 300,
        mix_counts: int = 10,
        timeout: float = 180.0,
    ) -> dict:
        x, y, z1 = self._resolve_ch8_work_position(
            bottle_index, bottle_type, x_pos, y_pos, z1_pos
        )
        return self.execute_command(
            cmd_type=int(TubeCommand.CH8_MIX),
            x_pos=x, y_pos=y, z1_pos=z1, p1_pos=p1_pos,
            mix_counts=mix_counts,
            **_CH8_SPEED_DEFAULTS,
            timeout=timeout,
        )

    @action(description="超声混匀 (cmd 35)；ultrasound_time=0 时仅作占位返回")
    def ultrasound_mix(
        self,
        ultrasound_time: int = 5,
        m_pos: int = 300,
        m_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        if ultrasound_time == 0:
            return {"success": True, "message": "solution_ready marker"}
        return self.execute_command(
            cmd_type=int(TubeCommand.ULTRASOUND_MIX),
            m_pos=m_pos, m_speed=m_speed, ultrasound_time=ultrasound_time,
            timeout=timeout,
        )

    @not_action
    def _build_setpoints(
        self,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        z3_pos: Optional[int] = None,
        z4_pos: Optional[int] = None,
        p1_pos: Optional[int] = None,
        p2_pos: Optional[int] = None,
        m_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        z4_speed: Optional[int] = None,
        p1_speed: Optional[int] = None,
        p2_speed: Optional[int] = None,
        m_speed: Optional[int] = None,
        small_gripper_force: Optional[int] = None,
        small_gripper_angle: Optional[int] = None,
        mix_counts: Optional[int] = None,
        ultrasound_time: Optional[int] = None,
    ) -> dict:
        mapping = {
            "Tube_XPosSet": x_pos,
            "Tube_YPosSet": y_pos,
            "Tube_Z1PosSet": z1_pos,
            "Tube_Z2PosSet": z2_pos,
            "Tube_Z3PosSet": z3_pos,
            "Tube_Z4PosSet": z4_pos,
            "Tube_P1PosSet": p1_pos,
            "Tube_P2PosSet": p2_pos,
            "Tube_MPosSet": m_pos,
            "Tube_XSpeed": x_speed,
            "Tube_YSpeed": y_speed,
            "Tube_Z1Speed": z1_speed,
            "Tube_Z2Speed": z2_speed,
            "Tube_Z3Speed": z3_speed,
            "Tube_Z4Speed": z4_speed,
            "Tube_P1Speed": p1_speed,
            "Tube_P2Speed": p2_speed,
            "Tube_MSpeed": m_speed,
            "Tube_SmallGripperForce": small_gripper_force,
            "Tube_SmallGripperAngle": small_gripper_angle,
            "Tube_MixCounts": mix_counts,
            "Tube_UltrasoundTime": ultrasound_time,
        }
        return {node: val for node, val in mapping.items() if val is not None}

    @not_action
    def _write_lid_gripper_params(self) -> None:
        """小夹爪开/关盖共用参数（写死）"""
        self.set_node_value("Tube_SmallGripperOpenLidStart", 1390)
        self.set_node_value("Tube_SmallGripperOpenLidEnd", 1330)
        self.set_node_value("Tube_SmallGripperOpenLidSpeed", 40)
        self.set_node_value("Tube_SmallGripperOpenLid_RotationSpeed", 300)
        self.set_node_value("Tube_SmallGripperCloseLidStart", 1330)
        self.set_node_value("Tube_SmallGripperCloseLidEnd", 1390)
        self.set_node_value("Tube_SmallGripperCloseLidSpeed", 40)
        self.set_node_value("Tube_SmallGripperCloseLid_RotationSpeed", 300)

    @not_action
    def _pulse_ultrasound_stop(self) -> dict:
        """超声波混匀立即停止：脉冲写 Tube_UltrasoundSTOP=1 后复位为 0"""
        logger.info("离心管液体处理：超声波混匀立即停止...")
        self.set_node_value(self.ULTRASOUND_STOP_NODE, 1)
        time.sleep(0.2)
        self.set_node_value(self.ULTRASOUND_STOP_NODE, 0)
        logger.info("超声波混匀立即停止命令已下发")
        self._log_status("超声波混匀立即停止后")
        return {"success": True, "message": "超声波混匀立即停止命令已下发"}

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 180.0,
    ) -> dict:
        logger.info(f"离心管液体处理：{description} (CmdType={cmd_type})")
        if cmd_type in self._LID_GRIPPER_CMDS:
            self._write_lid_gripper_params()
        if setpoints:
            for node, value in setpoints.items():
                self.set_node_value(node, value)
        return self._trigger_and_wait(cmd_type, description, timeout=timeout)

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
        """按离心管液体处理测试流程预设依次 execute_command（本地调试用）"""
        logger.info("离心管液体处理：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            preset_args = dict(preset)
            ultrasound_time = preset_args.get("ultrasound_time")
            step_timeout = 180.0
            if int(cmd_type) == int(TubeCommand.ULTRASOUND_MIX) and ultrasound_time is not None:
                step_timeout = ultrasound_time * 60 + 60
            self.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        logger.info("离心管液体处理：整体测试流程完成")
        return {"success": True, "message": "离心管液体处理测试流程完成"}

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
    STATUS_LOG_INTERVAL = 15.0

    dev = CentrifugeTubeLiquidHandlingDevice(
        url=TUBE_URL,
        csv_path=DEFAULT_CSV_PATH,
        use_subscription=False,
    )

    time.sleep(2)
    logger.info(f"连通性测试: {dev.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                dev._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    threading.Thread(target=_status_log_worker, daemon=True, name="TubeLiquidStatusLog").start()

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("21 超声波混匀立即停止")
        print("--- 单点调试 ---")
        print("31 X向左 (CmdType=1)")
        print("32 X向右 (CmdType=2)")
        print("33 Y向前 (CmdType=3)")
        print("34 Y向后 (CmdType=4)")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "98":
            dev.run_test_flow()
        elif choice == "21":
            dev.execute_command(ultrasound_stop=True)
        elif choice == "31":
            dev.execute_command(cmd_type=1, x_pos=3095, x_speed=500)
        elif choice == "32":
            dev.execute_command(cmd_type=2, x_pos=0, x_speed=500)
        elif choice == "33":
            dev.execute_command(cmd_type=3, y_pos=-2090, y_speed=500)
        elif choice == "34":
            dev.execute_command(cmd_type=4, y_pos=0, y_speed=500)
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            preset_args = dict(preset)
            ultrasound_time = preset_args.get("ultrasound_time")
            step_timeout = 180.0
            if int(cmd_type) == int(TubeCommand.ULTRASOUND_MIX) and ultrasound_time is not None:
                step_timeout = ultrasound_time * 60 + 60
            dev.execute_command(
                cmd_type=int(cmd_type), timeout=step_timeout, **preset_args
            )
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    dev.disconnect()
    print("退出程序。")
