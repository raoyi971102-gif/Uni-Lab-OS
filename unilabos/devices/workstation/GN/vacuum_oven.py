"""
真空烘箱 设备驱动
真空烘箱密码：7701
温度报警：130°C

参照 centrifuge.py / locking_mechanism.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 opcua_gn1.3.3.csv 中「真空烘箱」(前缀 VacuumOven_)。

指令类型 (VacuumOven_CmdType)：
    1=启动 2=复位 100=开门 101=关门

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
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")


class VacuumOvenCommand(int, Enum):
    """真空烘箱指令类型 (VacuumOven_CmdType)"""
    START = 1
    RESET = 2
    OPEN_DOOR = 100
    CLOSE_DOOR = 101


@device(
    id="gn_vacuum_oven",
    display_name="真空烘箱",
    category=["workstation"],
    description="GN 真空烘箱：多段温度/时间/真空上下限程序设置后启动，OPC UA 控制",
    icon="",
)
class VacuumOvenDevice(OpcUaClientWithSubscription):
    """真空烘箱设备类（OPC 前缀 VacuumOven_）"""

    CMD_TYPE_NODE = "VacuumOven_CmdType"
    CMD_TRIG_NODE = "VacuumOven_CmdTrig"
    COMPLETE_NODE = "VacuumOven_CompleteFB"
    MAX_SEGMENTS = 6

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
        """初始化真空烘箱设备

        参数:
            url: OPC UA 服务器地址
            csv_path: 节点配置 CSV 文件路径
            username / password: OPC UA 登录凭据
            use_subscription: 是否启用订阅模式
            cache_timeout: 缓存超时时间（秒）
            subscription_interval: 订阅发布间隔（毫秒）
        """
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

    # ==================== 动作函数 ====================

    @action(auto_prefix=True, description="设置单段程序（segment=1-6）")
    def set_segment(self, segment: int, temperature: int, minutes: int,
                    vacuum_high: Optional[int] = None, vacuum_low: Optional[int] = None) -> dict:
        """设置第 segment 段的温度、时间及真空控制上下限"""
        if segment < 1 or segment > self.MAX_SEGMENTS:
            raise ValueError(f"段号必须在 1-{self.MAX_SEGMENTS} 之间")
        self.set_node_value(f"VacuumOven_TempSet_{segment}", temperature)
        self.set_node_value(f"VacuumOven_TimeSet_{segment}", minutes)
        if vacuum_high is not None:
            self.set_node_value(f"VacuumOven_VacuumHigh_{segment}", vacuum_high)
        if vacuum_low is not None:
            self.set_node_value(f"VacuumOven_VacuumLow_{segment}", vacuum_low)
        logger.info(
            f"真空烘箱第 {segment} 段程序已设置: {temperature}℃/{minutes}min "
            f"真空上限={vacuum_high} 真空下限={vacuum_low}"
        )
        return {"success": True, "message": f"第 {segment} 段程序已设置"}

    @action(auto_prefix=True, description="启动真空烘箱（可选先下发第一段程序）")
    def start(self, temperature: Optional[int] = None, minutes: Optional[int] = None,
              vacuum_high: Optional[int] = None, vacuum_low: Optional[int] = None,
              wait: bool = True) -> dict:
        """启动真空烘箱。若提供温度/时间则同时写入第 1 段程序。

        Args:
            temperature: 第 1 段运行温度（可选）
            minutes: 第 1 段运行时间（分钟，可选）
            vacuum_high/vacuum_low: 第 1 段真空控制上下限（可选）
            wait: 是否等待完成反馈；长时间程序可设为 False 仅下发启动
        """
        logger.info("真空烘箱：启动...")
        if temperature is not None:
            self.set_node_value("VacuumOven_TempSet_1", temperature)
        if minutes is not None:
            self.set_node_value("VacuumOven_TimeSet_1", minutes)
        if vacuum_high is not None:
            self.set_node_value("VacuumOven_VacuumHigh_1", vacuum_high)
        if vacuum_low is not None:
            self.set_node_value("VacuumOven_VacuumLow_1", vacuum_low)

        if not wait:
            self.set_node_value(self.CMD_TYPE_NODE, int(VacuumOvenCommand.START))
            self.set_node_value(self.CMD_TRIG_NODE, 1)
            logger.info("真空烘箱启动指令已下发（不等待完成）")
            self._log_status("启动指令下发后")
            return {"success": True, "message": "真空烘箱已启动（不等待完成）"}

        timeout = (minutes or 0) * 60.0 + 600.0
        return self._trigger_and_wait(VacuumOvenCommand.START, "启动真空烘箱", timeout=timeout)

    @action(auto_prefix=True, description="真空烘箱复位")
    def reset(self) -> dict:
        """真空烘箱复位（指令类型=2）"""
        logger.info("真空烘箱：复位...")
        return self._trigger_and_wait(VacuumOvenCommand.RESET, "复位")

    @action(auto_prefix=True, description="真空烘箱开门")
    def open_door(self) -> dict:
        """开门（指令类型=100，直接触发）"""
        logger.info("真空烘箱：开门...")
        return self._trigger_and_wait(VacuumOvenCommand.OPEN_DOOR, "开门")

    @action(auto_prefix=True, description="真空烘箱关门")
    def close_door(self) -> dict:
        """关门（指令类型=101，直接触发）"""
        logger.info("真空烘箱：关门...")
        return self._trigger_and_wait(VacuumOvenCommand.CLOSE_DOOR, "关门")

    @action(auto_prefix=True, description="通用指令：按 VacuumOven_CmdType 执行任意指令")
    def execute_command(self, cmd_type: int, timeout: float = 120.0) -> dict:
        return self._trigger_and_wait(int(cmd_type), f"指令{cmd_type}", timeout=timeout)

    # ==================== 内部触发/等待逻辑（参照 centrifuge 写法） ====================

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        """下发指令类型并触发，等待完成后复位触发。

        - 设置 VacuumOven_CmdType（指令类型）
        - 设置 VacuumOven_CmdTrig=1（指令触发）
        - 等待 VacuumOven_CompleteFB 变为非 0（完成）
        - 复位 VacuumOven_CmdTrig=0
        - 等待 VacuumOven_CompleteFB 变回 0（完成复位）
        """
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        if self._wait_until_true(self.COMPLETE_NODE, timeout=timeout, description=f"{description}完成"):
            self.set_node_value(self.CMD_TRIG_NODE, 0)
            if self._wait_until_false(self.COMPLETE_NODE, description=f"{description}完成复位"):
                logger.info(f"{description}完成")
                self._log_status(f"{description}后")
                return {"success": True, "message": f"{description}完成"}
            raise ValueError(f"{description}失败，完成复位超时")
        raise ValueError(f"{description}失败，动作未完成")

    @not_action
    def _wait_until_true(self, node_name: str, timeout: float = 120.0,
                         interval: float = 0.2, description: str = None) -> bool:
        """等待节点变为非 0 / True（强制从服务器读取，避免订阅缓存过期）"""
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
        """等待节点变为 0 / False（强制从服务器读取，避免订阅缓存过期）"""
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
        """连通调试：设置第 1 段程序 → 启动（不等待）→ 读取状态 → 复位"""
        logger.info("真空烘箱：开始连通测试流程...")
        self.set_segment(segment=1, temperature=80, minutes=1, vacuum_high=-80, vacuum_low=-90)
        self.start(temperature=80, minutes=1, vacuum_high=-80, vacuum_low=-90, wait=False)
        time.sleep(2)
        status = self.get_status()
        logger.info(f"启动后状态: {status}")
        self.reset()
        logger.info("真空烘箱：连通测试流程完成")
        return {"success": True, "message": "真空烘箱连通测试完成", "status": status}

    # ==================== 状态读取 ====================

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
        """将状态反馈写入日志"""
        status = self.get_status()
        logger.info(
            f"{prefix}: 温度={status['temperature']}℃ "
            f"时间={status['time']}min 气压={status['pressure']} 完成={status['complete']}"
        )


if __name__ == "__main__":
    # 调试用法：连接 GN 真空烘箱 OPC UA 服务器并执行连通测试
    logging.getLogger("unilabos").setLevel(logging.INFO)

    VACUUM_OVEN_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0  # 状态反馈日志间隔（秒）

    oven = VacuumOvenDevice(
        url=VACUUM_OVEN_URL,
        csv_path=DEFAULT_CSV_PATH,
    )

    time.sleep(2)

    # 连通性测试：读取当前状态反馈
    logger.info(f"真空烘箱连通性测试: {oven.get_status()}")

    # 后台定时将状态反馈写入日志，便于实时查看
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

    # 命令行菜单
    while True:
        print("请选择操作：")
        print("0  读取状态（连通性测试）")
        print("1  设置第1段程序（80℃/1min，真空 -80~-90）")
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
            oven.set_segment(segment=1, temperature=25, minutes=1, vacuum_high=-95, vacuum_low=-98)
        elif choice == "2":
            oven.start(temperature=25, minutes=1, vacuum_high=-95, vacuum_low=-98, wait=True)
        elif choice == "3":
            oven.start(temperature=25, minutes=1, vacuum_high=-95, vacuum_low=-98, wait=False)
        elif choice == "4":
            oven.reset()
        elif choice == "5":
            oven.open_door()
        elif choice == "6":
            oven.close_door()
        elif choice == "98":
            oven.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    # 停止状态日志线程
    status_log_running = False
    status_log_thread.join(timeout=STATUS_LOG_INTERVAL + 1)

    # 断开连接
    oven.disconnect()
    print("退出程序。")
