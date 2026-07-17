"""
七个液冷模块 设备驱动

根据 opcua_gn1.3.3.csv 中「七个液冷模块」(前缀 Liquid_Cooling_ / Cooling_) 定义，
继承 OPC UA 通讯基类，实现指令触发/等待完成的动作函数。

指令类型 (Cooling_CmdType)：
    1=温度设置 2=液冷模块开启 3=液冷模块关闭

七个液冷通道：
    9320 模块：1, 2
    离心管液体处理模块：1, 2, 3, 4, 5
"""

import os
import time
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")


class CoolingCommand(int, Enum):
    """液冷模块指令类型"""
    SET_TEMPERATURE = 1
    TURN_ON = 2
    TURN_OFF = 3


# 通道名 -> (温度设定节点, 温度反馈节点)
COOLING_CHANNELS = {
    "9320_1": ("Liquid_Cooling_9320_Temperature_1_SET", "Liquid_Cooling_9320_1_FB"),
    "9320_2": ("Liquid_Cooling_9320_Temperature_2_SET", "Liquid_Cooling_9320_2_FB"),
    "tube_1": ("Liquid_Cooling_CentrifugeTube_Temperature_1_SET", "Liquid_Cooling_CentrifugeTube_1_FB"),
    "tube_2": ("Liquid_Cooling_CentrifugeTube_Temperature_2_SET", "Liquid_Cooling_CentrifugeTube_2_FB"),
    "tube_3": ("Liquid_Cooling_CentrifugeTube_Temperature_3_SET", "Liquid_Cooling_CentrifugeTube_3_FB"),
    "tube_4": ("Liquid_Cooling_CentrifugeTube_Temperature_4_SET", "Liquid_Cooling_CentrifugeTube_4_FB"),
    "tube_5": ("Liquid_Cooling_CentrifugeTube_Temperature_5_SET", "Liquid_Cooling_CentrifugeTube_5_FB"),
}


@device(
    id="gn_liquid_cooling",
    display_name="七个液冷模块",
    category=["workstation"],
    description="GN 七个液冷模块：2 路 9320 + 5 路离心管液冷通道温度设置与开关，OPC UA 控制",
    icon="",
)
class LiquidCoolingDevice(OpcUaClientWithSubscription):
    """液冷模块设备类（OPC 前缀 Liquid_Cooling_ / Cooling_）"""

    CMD_TYPE_NODE = "Cooling_CmdType"
    CMD_TRIG_NODE = "Cooling_CmdTrig"
    COMPLETE_NODE = "Liquid_Cooling_Complete_FB"

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

    @not_action
    def _wait_complete(self, timeout: float = 120.0, interval: float = 0.5, description: str = "") -> bool:
        desc = description or self.COMPLETE_NODE
        start = time.time()
        while True:
            value = self.get_node_value(self.COMPLETE_NODE, force_read=True)
            if value:
                logger.info(f"✓ {desc} 完成 (CompleteFB={value})")
                return True
            if time.time() - start >= timeout:
                logger.error(f"✗ 等待 {desc} 完成超时（{timeout}s，当前={value!r}）")
                return False
            time.sleep(interval)

    @not_action
    def _run(self, cmd_type, description: str = "", setpoints: dict = None, timeout: float = 120.0) -> dict:
        logger.info(f"执行液冷模块动作: {description} (cmd={int(cmd_type)})")
        if setpoints:
            for node, val in setpoints.items():
                if val is not None:
                    self.set_node_value(node, val)
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        ok = self._wait_complete(timeout=timeout, description=description)
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        if not ok:
            raise ValueError(f"{description} 执行失败或超时")
        return {"success": True, "message": f"{description} 完成"}

    # ==================== 动作函数 ====================

    @action(auto_prefix=True, description="设置指定液冷通道温度")
    def set_temperature(self, channel: str, temperature: int) -> dict:
        """设置液冷通道温度。

        Args:
            channel: 通道名，取值 9320_1 / 9320_2 / tube_1 ... tube_5
            temperature: 目标温度
        """
        if channel not in COOLING_CHANNELS:
            raise ValueError(f"未知通道 {channel}，可选: {list(COOLING_CHANNELS.keys())}")
        set_node, _ = COOLING_CHANNELS[channel]
        return self._run(CoolingCommand.SET_TEMPERATURE, f"设置 {channel} 温度={temperature}",
                         {set_node: temperature})

    @action(auto_prefix=True, description="开启液冷模块")
    def turn_on(self) -> dict:
        return self._run(CoolingCommand.TURN_ON, "液冷模块开启")

    @action(auto_prefix=True, description="关闭液冷模块")
    def turn_off(self) -> dict:
        return self._run(CoolingCommand.TURN_OFF, "液冷模块关闭")

    @action(auto_prefix=True, description="通用指令：按 Cooling_CmdType 执行任意指令")
    def execute_command(self, cmd_type: int, timeout: float = 120.0) -> dict:
        return self._run(int(cmd_type), f"指令{cmd_type}", timeout=timeout)

    # ==================== 状态读取 ====================

    @not_action
    def get_temperatures(self) -> dict:
        result = {}
        for channel, (_, fb_node) in COOLING_CHANNELS.items():
            result[channel] = self.get_node_value(fb_node, force_read=True)
        return result
