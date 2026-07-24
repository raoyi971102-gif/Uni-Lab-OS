"""
七个液冷模块 设备驱动

协议：OPC_UA协议1.3.3(2).xlsx「七个液冷模块」；节点：opcua_gn1.3.3.csv（前缀 Liquid_Cooling_ / Cooling_）。

对外仅暴露 execute_command（Cooling_CmdType + 写参）；测试流程预设供本地调试。

指令类型 (Cooling_CmdType)：
    1=温度设置 2=液冷模块开启 3=液冷模块关闭

七个液冷通道：
    9320 模块：1, 2
    离心管液体处理模块：1, 2, 3, 4, 5
"""

import os
import time
import logging
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_opcua_device import GnOpcUaDevice

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

# OPC 1.3.3 Cooling_CmdType（与 Excel 表头一致）
COOLING_CMD_LABELS = {
    1: "温度设置",
    2: "液冷模块开启",
    3: "液冷模块关闭",
}


class CoolingCommand(int, Enum):
    """液冷模块指令类型 (Cooling_CmdType)"""

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

# 液冷模块测试流程预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS: list = []


_EXECUTE_CMD_DOC = (
    "按 Cooling_CmdType 执行 OPC 1.3.3 指令。"
    "1=温度设置 2=液冷模块开启 3=液冷模块关闭。"
    "cmd_type=1 时 channel 必填（9320_1/9320_2/tube_1…tube_5），temperature 为目标温度。"
)


@device(
    id="gn_liquid_cooling",
    display_name="七个液冷模块",
    category=["workstation"],
    description="GN 七个液冷模块：OPC UA 1.3.3，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class LiquidCoolingDevice(GnOpcUaDevice):
    """液冷模块设备类（OPC 前缀 Liquid_Cooling_ / Cooling_）"""

    CMD_TYPE_NODE = "Cooling_CmdType"
    CMD_TRIG_NODE = "Cooling_CmdTrig"
    COMPLETE_NODE = "Liquid_Cooling_Complete_FB"

    def __init__(
        self,
        url: Optional[str] = None,
        plc_device_id: Optional[str] = None,
        csv_path: str = DEFAULT_CSV_PATH,
        username: str = None,
        password: str = None,
        use_subscription: bool = False,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        super().__init__(
            url=url,
            plc_device_id=plc_device_id,
            csv_path=csv_path,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs,
        )

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        channel: Optional[str] = None,
        temperature: Optional[int] = None,
        timeout: float = 120.0,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB（不等待 CompleteFB 复位）。"""
        cmd = int(cmd_type)
        if cmd == int(CoolingCommand.SET_TEMPERATURE):
            if not channel:
                raise ValueError(f"cmd_type=1 时 channel 必填，可选: {list(COOLING_CHANNELS.keys())}")
            if temperature is None:
                raise ValueError("cmd_type=1 时 temperature 必填")
        setpoints = self._build_setpoints(channel=channel, temperature=temperature)
        label = COOLING_CMD_LABELS.get(cmd, f"CmdType={cmd}")
        if channel and cmd == int(CoolingCommand.SET_TEMPERATURE):
            label = f"设置 {channel} 温度={temperature}"
        return self._run(cmd, label, setpoints, timeout=timeout)

    @not_action
    def _build_setpoints(
        self,
        channel: Optional[str] = None,
        temperature: Optional[int] = None,
    ) -> dict:
        if channel is None or temperature is None:
            return {}
        if channel not in COOLING_CHANNELS:
            raise ValueError(f"未知通道 {channel}，可选: {list(COOLING_CHANNELS.keys())}")
        set_node, _ = COOLING_CHANNELS[channel]
        return {set_node: temperature}

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 120.0,
    ) -> dict:
        logger.info(f"液冷模块：{description} (CmdType={cmd_type})")
        if setpoints:
            for node, value in setpoints.items():
                self.set_node_value(node, value)
        return self._trigger_and_wait(cmd_type, description, timeout=timeout)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        if not self._wait_complete(timeout=timeout, description=description):
            self.set_node_value(self.CMD_TRIG_NODE, 0)
            raise ValueError(f"{description} 执行失败或超时")
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        logger.info(f"{description} 完成")
        return {
            "success": True,
            "message": f"{description} 完成",
            "cmd_type": int(cmd_type),
        }

    @not_action
    def _wait_complete(
        self,
        timeout: float = 120.0,
        interval: float = 0.5,
        description: str = "",
    ) -> bool:
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
    def run_test_flow(self) -> dict:
        """按液冷模块测试预设依次 execute_command（本地调试用）"""
        logger.info("液冷模块：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            label = COOLING_CMD_LABELS.get(int(cmd_type), str(cmd_type))
            setpoints = self._build_setpoints(**preset) if preset else {}
            self._run(int(cmd_type), f"{step_name}/{label}", setpoints)
        logger.info("液冷模块：整体测试流程完成")
        return {"success": True, "message": "液冷模块测试流程完成"}

    @not_action
    def get_temperatures(self) -> dict:
        result = {}
        for channel, (_, fb_node) in COOLING_CHANNELS.items():
            result[channel] = self.get_node_value(fb_node, force_read=True)
        return result


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    COOLING_URL = "opc.tcp://192.168.6.6:4840"

    dev = LiquidCoolingDevice(url=COOLING_URL, csv_path=DEFAULT_CSV_PATH)
    time.sleep(2)

    while True:
        print("请选择操作：")
        print("1 设置温度（输入 channel / temperature）")
        print("2 液冷模块开启")
        print("3 液冷模块关闭")
        print(f"可选通道: {', '.join(COOLING_CHANNELS.keys())}")
        if TEST_FLOW_PRESETS:
            print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入 CmdType 序号：").strip()
        if choice == "99":
            break
        if choice == "98" and TEST_FLOW_PRESETS:
            dev.run_test_flow()
        elif choice == "1":
            channel = input("channel: ").strip()
            temperature = int(input("temperature: ").strip())
            dev.execute_command(cmd_type=1, channel=channel, temperature=temperature)
        elif choice == "2":
            dev.execute_command(cmd_type=2)
        elif choice == "3":
            dev.execute_command(cmd_type=3)
        else:
            print("无效的操作序号，请重新输入。")

    dev.disconnect()
    print("退出程序。")
