"""
快换模块 设备驱动

参照 AI4C.py / locking_mechanism.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 opcua_gn1.3.3.csv 中「快换」(前缀 QuickChange_)。
各动作点位根据「快换模块测试流程.yaml」写死。

指令类型 (QuickChange_CmdType)：
    1=X向左  2=X向右  3=Z1向左 4=Z1向右
    5=Z2向左 6=Z2向右 7=推轴向左 8=推轴向右
    9=Z3向左 10=Z3向右 11=物料顶出 12=物料放置
    13=磁力搅拌运行 14=复位

YAML 字段 → CSV 节点映射：
    XPos          → QuickChange_XPosSet
    Z1Pos         → QuickChange_TopZPosSet（顶料Z）
    Z2Pos         → QuickChange_TakeZPosSet（接料Z）
    PushBoardPos  → QuickChange_PushPosSet（推轴）
    Z3Pos         → QuickChange_PushZPosSet（压料Z）
    XSpeed        → QuickChange_XSpeed
    Z1Speed       → QuickChange_Z1Speed
    Z2Speed       → QuickChange_Z2Speed
    PushBoardSpeed→ QuickChange_PushSpeed
    Z3Speed       → QuickChange_Z3Speed
    RPM/Temp/Time → QuickChange_StirRPM/StirTemp/StirTime
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


class QuickChangeCommand(int, Enum):
    """快换模块指令类型 (QuickChange_CmdType)"""
    X_LEFT = 1
    X_RIGHT = 2
    Z1_LEFT = 3
    Z1_RIGHT = 4
    Z2_LEFT = 5
    Z2_RIGHT = 6
    PUSH_LEFT = 7
    PUSH_RIGHT = 8
    Z3_LEFT = 9
    Z3_RIGHT = 10
    EJECT_MATERIAL = 11   # 物料顶出
    PLACE_MATERIAL = 12   # 物料放置
    STIR_RUN = 13         # 磁力搅拌运行
    RESET = 14            # 复位


@device(
    id="gn_quick_carrier_exchange",
    display_name="快换模块",
    category=["workstation"],
    description="GN 快换模块：按测试流程完成 物料顶出/物料放置，OPC UA 控制",
    icon="",
)
class QuickCarrierExchangeDevice(OpcUaClientWithSubscription):
    """快换模块设备类（OPC 前缀 QuickChange_）"""

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
        """初始化快换模块设备

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

    # ==================== 动作函数（点位写死，来自快换模块测试流程 yaml） ====================

    @action(auto_prefix=True, description="1.物料顶出")
    def eject_material(self) -> dict:
        """物料顶出（yaml step0 MaterialPushOut，指令类型=11）"""
        logger.info("快换模块：物料顶出...")
        # 运行位置
        self.set_node_value("QuickChange_XPosSet", 0)
        self.set_node_value("QuickChange_TopZPosSet", -830)    # Z1Pos
        self.set_node_value("QuickChange_TakeZPosSet", 1800)   # Z2Pos
        self.set_node_value("QuickChange_PushPosSet", 240)     # PushBoardPos
        self.set_node_value("QuickChange_PushZPosSet", 0)      # Z3Pos
        # 运行速度
        self.set_node_value("QuickChange_XSpeed", 300)
        self.set_node_value("QuickChange_Z1Speed", 100)
        self.set_node_value("QuickChange_Z2Speed", 100)
        self.set_node_value("QuickChange_PushSpeed", 50)
        self.set_node_value("QuickChange_Z3Speed", 0)
        # 搅拌参数（yaml 为 0）
        self._apply_stir_setpoints(0, 0, 0)
        return self._trigger_and_wait(QuickChangeCommand.EJECT_MATERIAL, "物料顶出")

    @action(auto_prefix=True, description="2.物料放置")
    def place_material(self) -> dict:
        """物料放置（yaml step1 MaterialPlace，指令类型=12）"""
        logger.info("快换模块：物料放置...")
        # 运行位置
        self.set_node_value("QuickChange_XPosSet", 1810)
        self.set_node_value("QuickChange_TopZPosSet", 0)      # Z1Pos
        self.set_node_value("QuickChange_TakeZPosSet", 1600)   # Z2Pos
        self.set_node_value("QuickChange_PushPosSet", 240)      # PushBoardPos
        self.set_node_value("QuickChange_PushZPosSet", 2100)   # Z3Pos
        # 运行速度
        self.set_node_value("QuickChange_XSpeed", 300)
        self.set_node_value("QuickChange_Z1Speed", 100)
        self.set_node_value("QuickChange_Z2Speed", 100)
        self.set_node_value("QuickChange_PushSpeed", 50)
        self.set_node_value("QuickChange_Z3Speed", 100)
        # 搅拌参数（yaml 为 0）
        self._apply_stir_setpoints(0, 0, 0)
        return self._trigger_and_wait(QuickChangeCommand.PLACE_MATERIAL, "物料放置")

    @action(auto_prefix=True, description="磁力搅拌运行")
    def run_magnetic_stir(self, rpm: int = 300, temp: int = 25, time_minutes: int = 1) -> dict:
        """磁力搅拌运行（指令类型=13）

        参数对应 OPC 节点：
            QuickChange_StirRPM  转速
            QuickChange_StirTemp 温度
            QuickChange_StirTime 时间（分钟）
        """
        logger.info(
            f"快换模块：磁力搅拌运行 RPM={rpm} Temp={temp} Time={time_minutes}min..."
        )
        self._apply_stir_setpoints(rpm, temp, time_minutes)
        timeout = time_minutes * 60 + 60
        return self._trigger_and_wait(
            QuickChangeCommand.STIR_RUN, "磁力搅拌运行", timeout=timeout
        )

    @action(auto_prefix=True, description="快换模块复位")
    def reset(self) -> dict:
        """快换模块复位（指令类型=14）"""
        logger.info("快换模块：复位...")
        return self._trigger_and_wait(QuickChangeCommand.RESET, "复位")

    # ==================== 内部触发/等待逻辑（参照 locking_mechanism 写法） ====================

    @not_action
    def _apply_stir_setpoints(self, rpm: int, temp: int, time_minutes: int) -> None:
        """写入磁力搅拌三参数：转速 / 温度 / 时间(分)"""
        self.set_node_value("QuickChange_StirRPM", rpm)
        self.set_node_value("QuickChange_StirTemp", temp)
        self.set_node_value("QuickChange_StirTime", time_minutes)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        """下发指令类型并触发，等待完成后复位触发。

        - 设置 QuickChange_CmdType（指令类型）
        - 设置 QuickChange_CmdTrig=1（指令触发）
        - 等待 QuickChange_CompleteFB 变为非 0（完成）
        - 复位 QuickChange_CmdTrig=0
        - 等待 QuickChange_CompleteFB 变回 0（完成复位）
        """
        self.set_node_value("QuickChange_CmdType", int(cmd_type))   # 指令类型
        self.set_node_value("QuickChange_CmdTrig", 1)               # 指令触发
        if self._wait_until_true("QuickChange_CompleteFB", timeout=timeout, description=f"{description}完成"):
            self.set_node_value("QuickChange_CmdTrig", 0)           # 复位触发
            if self._wait_until_false("QuickChange_CompleteFB", description=f"{description}完成复位"):
                logger.info(f"{description}完成")
                self._log_positions(f"{description}后")
                return {"success": True, "message": f"{description}完成"}
            else:
                raise ValueError(f"{description}失败，完成复位超时")
        else:
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
        """按快换模块测试流程 yaml 依次执行全部步骤"""
        logger.info("快换模块：开始整体测试流程...")
        self.eject_material()    # 1.物料顶出
        self.place_material()    # 2.物料放置
        logger.info("快换模块：整体测试流程完成")
        return {"success": True, "message": "快换模块测试流程完成"}

    # ==================== 状态读取 ====================

    @not_action
    def get_positions(self) -> dict:
        """读取当前位置反馈"""
        return {
            "X": self.get_node_value("QuickChange_XPosFB"),
            "Z1": self.get_node_value("QuickChange_Z1PosFB"),
            "Z2": self.get_node_value("QuickChange_Z2PosFB"),
            "Z3": self.get_node_value("QuickChange_Z3PosFB"),
            "Push": self.get_node_value("QuickChange_PushPosFB"),
        }

    @not_action
    def _log_positions(self, prefix: str = "位置反馈") -> None:
        """将位置反馈写入日志"""
        pos = self.get_positions()
        complete = self.get_node_value("QuickChange_CompleteFB", force_read=True)
        logger.info(
            f"{prefix}: X={pos['X']} Z1={pos['Z1']} Z2={pos['Z2']} "
            f"Z3={pos['Z3']} Push={pos['Push']} 完成={complete}"
        )


if __name__ == "__main__":
    # 调试用法：连接 GN 快换模块 OPC UA 服务器并执行测试流程
    logging.getLogger("unilabos").setLevel(logging.INFO)

    QUICK_CHANGE_URL = "opc.tcp://192.168.6.6:4840"
    POSITION_LOG_INTERVAL = 15.0  # 位置反馈日志间隔（秒）

    quick_change = QuickCarrierExchangeDevice(
        url=QUICK_CHANGE_URL,
        csv_path=DEFAULT_CSV_PATH,
    )

    time.sleep(2)

    # 后台定时将位置反馈写入日志，便于实时查看
    position_log_running = True

    def _position_log_worker():
        while position_log_running:
            try:
                quick_change._log_positions("实时位置")
            except Exception as e:
                logger.warning(f"位置反馈日志异常: {e}")
            time.sleep(POSITION_LOG_INTERVAL)

    position_log_thread = threading.Thread(
        target=_position_log_worker, daemon=True, name="QuickChangePositionLog"
    )
    position_log_thread.start()
    logger.info(f"已启动位置反馈实时日志（间隔 {POSITION_LOG_INTERVAL}s）")

    # 命令行菜单
    while True:
        print("请选择操作：")
        print("1 物料顶出")
        print("2 物料放置")
        print("3 复位")
        print("4 磁力搅拌运行（输入 RPM/温度/时间）")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "1":
            quick_change.eject_material()
        elif choice == "2":
            quick_change.place_material()
        elif choice == "3":
            quick_change.reset()
        elif choice == "4":
            rpm = int(input("转速 RPM [100]: ").strip() or "100")
            temp = int(input("温度 [25]: ").strip() or "25")
            time_minutes = int(input("时间(分) [1]: ").strip() or "1")
            quick_change.run_magnetic_stir(rpm=rpm, temp=temp, time_minutes=time_minutes)
        elif choice == "98":
            quick_change.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    # 停止位置日志线程
    position_log_running = False
    position_log_thread.join(timeout=POSITION_LOG_INTERVAL + 1)

    # 断开连接
    quick_change.disconnect()
    print("退出程序。")
