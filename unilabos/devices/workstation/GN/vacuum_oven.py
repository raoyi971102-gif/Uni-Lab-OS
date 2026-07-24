"""
unilab -g unilabos\devices\workstation\GN\GN_station.json --ak b19d8b2f-8067-4821-a163-7bf15abb0b2e --sk 3f620bb2-7687-44cc-949f-a1fbdf043d48 --upload_registry --addr https://leap-lab.bohrium.com/api/v1 --disable_browser    

真空烘箱密码：7701
温度报警：130°C

协议：opcua_gn1.3.3.csv「真空烘箱」(前缀 VacuumOven_)。
对外仅暴露 execute_command（VacuumOven_CmdType + 写参）。

指令类型 (VacuumOven_CmdType)：
    1=启动 2=复位 100=开门 101=关门

气压=4 表示0.004mPa，意味着没有真空度
支持最多 6 段温度/时间/真空上下限程序。
"""

import os
import time
import logging
import threading
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.GN.gn_opcua_device import GnOpcUaDevice

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

# OPC 1.3.3 VacuumOven_CmdType（与 Excel 表头一致）
VACUUM_CMD_LABELS = {
    1: "启动",
    2: "复位",
    100: "开门",
    101: "关门",
}


class VacuumOvenCommand(int, Enum):
    """真空烘箱指令类型 (VacuumOven_CmdType)"""

    START = 1
    RESET = 2
    OPEN_DOOR = 100
    CLOSE_DOOR = 101


_EXECUTE_CMD_DOC = (
    "按 VacuumOven_CmdType 执行 OPC 1.3.3 指令。"
    "1=启动 2=复位 100=开门 101=关门。"
    "启动时可写 segment(1-6)/temperature/minutes/vacuum_high/vacuum_low；"
    "未指定 segment 但提供 temperature 时默认写第 1 段。"
    "启动 wait=False 仅触发不等待；wait=True 时 timeout 默认 minutes*60+600。"
)


@device(
    id="gn_vacuum_oven",
    display_name="真空烘箱",
    category=["workstation"],
    description="GN 真空烘箱：多段温度/时间/真空上下限程序设置后启动，OPC UA 控制，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class VacuumOvenDevice(GnOpcUaDevice):
    """真空烘箱设备类（OPC 前缀 VacuumOven_）"""

    CMD_TYPE_NODE = "VacuumOven_CmdType"
    CMD_TRIG_NODE = "VacuumOven_CmdTrig"
    COMPLETE_NODE = "VacuumOven_CompleteFB"
    MAX_SEGMENTS = 6

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
        segment: Optional[int] = None,
        temperature: Optional[int] = None,
        minutes: Optional[int] = None,
        vacuum_high: Optional[int] = None,
        vacuum_low: Optional[int] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        cmd = int(cmd_type)
        label = VACUUM_CMD_LABELS.get(cmd, f"CmdType={cmd}")

        setpoints = self._build_setpoints(
            cmd_type=cmd,
            segment=segment,
            temperature=temperature,
            minutes=minutes,
            vacuum_high=vacuum_high,
            vacuum_low=vacuum_low,
        )

        if cmd == VacuumOvenCommand.START and not wait:
            logger.info(f"真空烘箱：{label}（不等待完成）")
            if setpoints:
                for node, value in setpoints.items():
                    self.set_node_value(node, value)
            self.set_node_value(self.CMD_TYPE_NODE, cmd)
            self.set_node_value(self.CMD_TRIG_NODE, 1)
            self._log_status(f"{label}指令下发后")
            return {
                "success": True,
                "message": f"真空烘箱{label}（不等待完成）",
                "cmd_type": cmd,
            }

        effective_timeout = timeout
        if effective_timeout is None and cmd == VacuumOvenCommand.START:
            effective_timeout = (minutes or 0) * 60.0 + 600.0
        if effective_timeout is None:
            effective_timeout = 120.0

        return self._run(cmd, label, setpoints, timeout=effective_timeout)

    @not_action
    def _build_setpoints(
        self,
        cmd_type: int,
        segment: Optional[int] = None,
        temperature: Optional[int] = None,
        minutes: Optional[int] = None,
        vacuum_high: Optional[int] = None,
        vacuum_low: Optional[int] = None,
    ) -> dict:
        """构建写参节点字典；segment 指定时写对应段，未指定但提供 temperature 时写第 1 段。"""
        has_values = any(v is not None for v in (temperature, minutes, vacuum_high, vacuum_low))
        if not has_values:
            return {}

        effective_segment = segment
        if effective_segment is None and temperature is not None:
            effective_segment = 1
        if effective_segment is None:
            return {}

        if effective_segment < 1 or effective_segment > self.MAX_SEGMENTS:
            raise ValueError(f"段号必须在 1-{self.MAX_SEGMENTS} 之间")

        mapping = {
            f"VacuumOven_TempSet_{effective_segment}": temperature,
            f"VacuumOven_TimeSet_{effective_segment}": minutes,
            f"VacuumOven_VacuumHigh_{effective_segment}": vacuum_high,
            f"VacuumOven_VacuumLow_{effective_segment}": vacuum_low,
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
        logger.info(f"真空烘箱：{description} (CmdType={cmd_type})")
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
        """连通调试：设置第 1 段程序 → 启动（不等待）→ 读取状态 → 复位"""
        logger.info("真空烘箱：开始连通测试流程...")
        self.execute_command(
            cmd_type=int(VacuumOvenCommand.START),
            segment=1,
            temperature=80,
            minutes=1,
            vacuum_high=-80,
            vacuum_low=-90,
            wait=False,
        )
        time.sleep(2)
        status = self.get_status()
        logger.info(f"启动后状态: {status}")
        self.execute_command(cmd_type=int(VacuumOvenCommand.RESET))
        logger.info("真空烘箱：连通测试流程完成")
        return {"success": True, "message": "真空烘箱连通测试完成", "status": status}

    @not_action
    def get_status(self) -> dict:
        """读取温度/时间/气压反馈及完成标志"""
        return {
            "temperature": self.get_node_value("VacuumOven_TempFB", force_read=True),
            "time": self.get_node_value("VacuumOven_TimeFB", force_read=True),
            "pressure": self.get_node_value("VacuumOven_PressureFB", force_read=True),
            "complete": self.get_node_value(self.COMPLETE_NODE, force_read=True),
        }

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: 温度={status['temperature']}℃ "
            f"时间={status['time']}min 气压={status['pressure']} 完成={status['complete']}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    VACUUM_OVEN_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    oven = VacuumOvenDevice(url=VACUUM_OVEN_URL, csv_path=DEFAULT_CSV_PATH)
    time.sleep(2)

    logger.info(f"真空烘箱连通性测试: {oven.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                oven._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    status_log_thread = threading.Thread(
        target=_status_log_worker, daemon=True, name="VacuumOvenStatusLog"
    )
    status_log_thread.start()
    logger.info(f"已启动状态反馈实时日志（间隔 {STATUS_LOG_INTERVAL}s）")

    while True:
        print("请选择操作：")
        print("0  读取状态（连通性测试）")
        print("1  设置第1段程序")
        print("2  启动（等待完成）")
        print("3  启动（不等待，仅下发指令）")
        print("4  复位")
        print("5  开门（CmdType=100）")
        print("6  关门（CmdType=101）")
        print("98 连通测试流程（设程序→启动→读状态→复位）")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "0":
            status = oven.get_status()
            print(f"当前状态: {status}")
        elif choice == "1":
            # 仅写第 1 段程序参数，不触发 CmdTrig
            for node, value in oven._build_setpoints(
                cmd_type=1,
                segment=1,
                temperature=25,
                minutes=1,
                vacuum_high=-95,
                vacuum_low=-98,
            ).items():
                oven.set_node_value(node, value)
            logger.info("第 1 段程序参数已写入（未触发启动）")
        elif choice == "2":
            oven.execute_command(
                cmd_type=1,
                temperature=25,
                minutes=1,
                vacuum_high=-95,
                vacuum_low=-98,
                wait=True,
            )
        elif choice == "3":
            oven.execute_command(
                cmd_type=1,
                temperature=25,
                minutes=1,
                vacuum_high=-95,
                vacuum_low=-98,
                wait=False,
            )
        elif choice == "4":
            oven.execute_command(cmd_type=2)
        elif choice == "5":
            oven.execute_command(cmd_type=100)
        elif choice == "6":
            oven.execute_command(cmd_type=101)
        elif choice == "98":
            oven.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    status_log_thread.join(timeout=STATUS_LOG_INTERVAL + 1)
    oven.disconnect()
    print("退出程序。")
