"""
常规烘箱 设备驱动

协议：opcua_gn1.3.3.csv「常规烘箱」(前缀 Oven_)。
对外仅暴露 execute_command（Oven_CmdType + 写参）。

指令类型 (Oven_CmdType)：
    1=启动 2=复位/停止

运行状态 (Oven_Running_Status, INT16)：
    0=停止 1=运行

互锁（下发前校验）：
    - CmdType=1（启动）仅当 Running_Status=0 时允许；触发后应变为 1
    - CmdType=2（复位/停止）仅当 Running_Status=1 时允许；触发后应变为 0

程序完成（wait=True 时等待）：
    - 以 Oven_CompleteFB 为准，超时 = 设定运行时间 + 缓冲
    - 不用 Running_Status→0 判断烘干结束（到温即变 0，会提前结束）
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

RUNNING_STOPPED = 0
RUNNING_ACTIVE = 1

# OPC 1.3.3 Oven_CmdType（与 Excel 表头一致）
OVEN_CMD_LABELS = {
    1: "启动",
    2: "复位/停止",
}


class OvenCommand(int, Enum):
    """常规烘箱指令类型"""

    START = 1
    RESET = 2


_EXECUTE_CMD_DOC = (
    "按 Oven_CmdType 执行 OPC 1.3.3 指令。"
    "1=启动（需 temperature/hours/minutes 写参） 2=复位/停止。"
    "启动 wait=True 时等待 CompleteFB；timeout 默认 program_timeout=设定时长+300。"
)


@device(
    id="gn_standard_oven",
    display_name="常规烘箱",
    category=["workstation"],
    description="GN 常规烘箱：设置温度与运行时间后启动烘干，OPC UA 控制，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class StandardOvenDevice(OpcUaClientWithSubscription):
    """常规烘箱设备类（OPC 前缀 Oven_）"""

    CMD_TYPE_NODE = "Oven_CmdType"
    CMD_TRIG_NODE = "Oven_CmdTrig"
    COMPLETE_NODE = "Oven_CompleteFB"
    RUNNING_STATUS_NODE = "Oven_Running_Status"

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
        temperature: Optional[int] = None,
        hours: int = 0,
        minutes: int = 0,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> dict:
        """唯一注册动作：写参 → CmdType → CmdTrig → 互锁/等待 CompleteFB。"""
        cmd = int(cmd_type)
        label = OVEN_CMD_LABELS.get(cmd, f"CmdType={cmd}")

        setpoints = self._build_setpoints(
            cmd_type=cmd,
            temperature=temperature,
            hours=hours,
            minutes=minutes,
        )

        if cmd == OvenCommand.START:
            program_timeout = (
                timeout if timeout is not None else (hours * 3600 + minutes * 60) + 300.0
            )
            ack_timeout = 60.0
        else:
            program_timeout = 120.0
            ack_timeout = timeout if timeout is not None else 60.0

        return self._run(
            cmd,
            label,
            setpoints,
            wait=wait,
            program_timeout=program_timeout,
            ack_timeout=ack_timeout,
        )

    @not_action
    def _build_setpoints(
        self,
        cmd_type: int,
        temperature: Optional[int] = None,
        hours: int = 0,
        minutes: int = 0,
    ) -> dict:
        if int(cmd_type) != OvenCommand.START:
            return {}
        if temperature is None:
            raise ValueError("启动(CmdType=1) 需指定 temperature")
        return {
            "Oven_TempSet": temperature,
            "Oven_TimeHourSet": hours,
            "Oven_TimeMinuteSet": minutes,
        }

    @not_action
    def get_running_status(self) -> int:
        """读取运行状态：0=停止，1=运行"""
        return int(self.get_node_value(self.RUNNING_STATUS_NODE, force_read=True) or 0)

    @not_action
    def _required_running_status(self, cmd_type) -> int:
        if int(cmd_type) == OvenCommand.START:
            return RUNNING_STOPPED
        if int(cmd_type) == OvenCommand.RESET:
            return RUNNING_ACTIVE
        raise ValueError(f"未知指令类型 {cmd_type}")

    @not_action
    def _expected_running_status_after_ack(self, cmd_type) -> int:
        if int(cmd_type) == OvenCommand.START:
            return RUNNING_ACTIVE
        if int(cmd_type) == OvenCommand.RESET:
            return RUNNING_STOPPED
        raise ValueError(f"未知指令类型 {cmd_type}")

    @not_action
    def _check_running_status_allowed(self, cmd_type, description: str) -> None:
        required = self._required_running_status(cmd_type)
        current = self.get_running_status()
        if current != required:
            state_desc = "运行" if current == RUNNING_ACTIVE else "停止"
            req_desc = "停止" if required == RUNNING_STOPPED else "运行"
            raise ValueError(
                f"{description} 被拒绝：当前{state_desc}(Running_Status={current})，"
                f"需{req_desc}(Running_Status={required}) 才能执行 CmdType={int(cmd_type)}"
            )
        logger.info(f"互锁通过: Running_Status={current}，CmdType={int(cmd_type)}")

    @not_action
    def _wait_running_status(
        self,
        expected: int,
        timeout: float = 60.0,
        interval: float = 0.5,
        description: str = "",
    ) -> bool:
        state_label = "运行" if expected == RUNNING_ACTIVE else "停止"
        logger.info(f"等待 Running_Status→{expected}（{state_label}，{description}）...")
        start = time.time()
        while time.time() - start < timeout:
            current = self.get_running_status()
            if current == expected:
                logger.info(f"✓ Running_Status={current}（{state_label}）")
                return True
            time.sleep(interval)
        logger.error(f"✗ Running_Status 等待超时（当前={self.get_running_status()}，期望={expected}）")
        return False

    @not_action
    def _wait_until_true(
        self,
        node_name: str,
        timeout: float = 120.0,
        interval: float = 0.5,
        description: str = None,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc} 完成（轮询 {node_name}）...")
        start = time.time()
        while time.time() - start < timeout:
            value = self.get_node_value(node_name, force_read=True)
            if value:
                logger.info(f"✓ {desc}（{node_name}={value}）")
                return True
            time.sleep(interval)
        logger.error(f"✗ 等待 {desc} 超时（{timeout}s，{node_name}={value!r}）")
        return False

    @not_action
    def _wait_until_false(
        self,
        node_name: str,
        timeout: float = 120.0,
        interval: float = 0.5,
        description: str = None,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc} 复位（轮询 {node_name}）...")
        start = time.time()
        while time.time() - start < timeout:
            value = self.get_node_value(node_name, force_read=True)
            if not value:
                logger.info(f"✓ {desc}（{node_name}={value}）")
                return True
            time.sleep(interval)
        logger.error(f"✗ 等待 {desc} 复位超时（{timeout}s，{node_name}={value!r}）")
        return False

    @not_action
    def _trigger_and_wait_complete(self, description: str, timeout: float) -> None:
        """等待 CompleteFB 完成并复位触发"""
        if not self._wait_until_true(
            self.COMPLETE_NODE, timeout=timeout, description=f"{description}程序"
        ):
            raise ValueError(f"{description} 失败，CompleteFB 未完成")
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        if not self._wait_until_false(
            self.COMPLETE_NODE, timeout=60.0, description=f"{description}CompleteFB复位"
        ):
            raise ValueError(f"{description} 失败，CompleteFB 未复位")

    @not_action
    def _run(
        self,
        cmd_type,
        description: str = "",
        setpoints: dict = None,
        wait: bool = True,
        program_timeout: float = 120.0,
        ack_timeout: float = 60.0,
    ) -> dict:
        cmd = int(cmd_type)
        self._check_running_status_allowed(cmd, description)

        logger.info(f"执行常规烘箱: {description} (cmd={cmd})")
        if setpoints:
            for node, val in setpoints.items():
                if val is not None:
                    self.set_node_value(node, val)

        self.set_node_value(self.CMD_TRIG_NODE, 0)
        time.sleep(0.1)
        self.set_node_value(self.CMD_TYPE_NODE, cmd)
        self.set_node_value(self.CMD_TRIG_NODE, 1)

        if not wait:
            logger.info(f"{description} 已下发（不等待 CompleteFB）")
            return {
                "success": True,
                "message": f"{description} 已下发",
                "cmd_type": cmd,
                **self.get_status(),
            }

        expected_ack = self._expected_running_status_after_ack(cmd)

        if cmd == OvenCommand.START:
            if not self._wait_running_status(
                expected_ack, timeout=ack_timeout, description="启动确认"
            ):
                raise ValueError(f"{description} 失败，Running_Status 未变为运行")
            self._trigger_and_wait_complete(description, timeout=program_timeout)
        else:
            self._trigger_and_wait_complete(description, timeout=ack_timeout)
            if not self._wait_running_status(
                expected_ack, timeout=ack_timeout, description="停止确认"
            ):
                raise ValueError(f"{description} 失败，Running_Status 未变为停止")

        logger.info(f"{description} 完成")
        self._log_status(f"{description}后")
        return {
            "success": True,
            "message": f"{description} 完成",
            "cmd_type": cmd,
            **self.get_status(),
        }

    @not_action
    def get_temperature(self) -> int:
        return int(self.get_node_value("Oven_TempFB", force_read=True) or 0)

    @not_action
    def get_status(self) -> dict:
        running = self.get_running_status()
        return {
            "temperature": self.get_temperature(),
            "running_status": running,
            "running": running == RUNNING_ACTIVE,
            "complete": int(self.get_node_value(self.COMPLETE_NODE, force_read=True) or 0),
        }

    @not_action
    def _log_status(self, prefix: str = "常规烘箱状态") -> None:
        s = self.get_status()
        run_label = "运行" if s["running"] else "停止"
        logger.info(
            f"{prefix}: 温度={s['temperature']}℃ {run_label}(Running_Status={s['running_status']}) "
            f"CompleteFB={s['complete']}"
        )

    @not_action
    def run_test_flow(self, temperature: int = 80, hours: int = 0, minutes: int = 1) -> dict:
        """连通测试：启动（等待程序结束）→ 复位/停止"""
        logger.info("常规烘箱：开始测试流程...")
        self.execute_command(
            cmd_type=int(OvenCommand.START),
            temperature=temperature,
            hours=hours,
            minutes=minutes,
            wait=True,
        )
        self.execute_command(cmd_type=int(OvenCommand.RESET))
        logger.info("常规烘箱：测试流程完成")
        return {"success": True, "message": "常规烘箱测试流程完成"}


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    OVEN_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    oven = StandardOvenDevice(
        url=OVEN_URL,
        csv_path=DEFAULT_CSV_PATH,
        use_subscription=False,
    )

    time.sleep(2)
    logger.info(f"连通性测试: {oven.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                oven._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    status_log_thread = threading.Thread(
        target=_status_log_worker, daemon=True, name="StandardOvenStatusLog"
    )
    status_log_thread.start()
    logger.info(f"已启动常规烘箱状态日志（间隔 {STATUS_LOG_INTERVAL}s，无订阅）")

    while True:
        print("请选择操作：")
        print("0  读取状态")
        print("1  启动（等待程序结束）")
        print("2  启动（不等待）")
        print("3  复位/停止")
        print("98 测试流程（启动→停止）")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "0":
            print(oven.get_status())
        elif choice == "1":
            oven.execute_command(cmd_type=1, temperature=25, hours=0, minutes=1, wait=True)
        elif choice == "2":
            oven.execute_command(cmd_type=1, temperature=25, hours=0, minutes=1, wait=False)
        elif choice == "3":
            oven.execute_command(cmd_type=2)
        elif choice == "98":
            oven.run_test_flow(temperature=80, hours=0, minutes=1)
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    status_log_thread.join(timeout=STATUS_LOG_INTERVAL + 1)
    oven.disconnect()
    print("退出程序。")
