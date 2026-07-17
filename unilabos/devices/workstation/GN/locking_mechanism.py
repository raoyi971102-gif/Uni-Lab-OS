"""
锁紧模块 设备驱动

参照 AI4C.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 opcua_gn1.3.3.csv 中「锁紧模块」(前缀 Lock_)。
各动作点位根据「锁紧模块测试流程.yaml」写死。

指令类型 (Lock_CmdType)：
    1=X向左  2=X向右  3=Y向左  4=Y向右
    5=Z1向左 6=Z1向右 7=Z2向左 8=Z2向右
    9=夹爪夹取 10=夹爪放置 11=电批拧紧 12=电批拧松
    13=夹爪夹紧 14=夹爪松开 16=复位
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
    JAW_PICK = 9        # 夹爪夹取 (GripperTake)
    JAW_PLACE = 10      # 夹爪放置 (GripperPut)
    SCREW_TIGHTEN = 11  # 电批拧紧 (ScrewdriverTighten)
    SCREW_LOOSEN = 12   # 电批拧松 (ScrewdriverRelease)
    JAW_CLAMP = 13
    JAW_RELEASE = 14
    RESET = 16


@device(
    id="gn_locking_mechanism",
    display_name="锁紧模块",
    category=["workstation"],
    description="GN 锁紧模块：按测试流程完成 夹取/放置耗材、夹取/放置盖板、取螺丝/拧螺丝，OPC UA 控制",
    icon="",
)
class LockingMechanismDevice(OpcUaClientWithSubscription):
    """锁紧模块设备类（OPC 前缀 Lock_）"""

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
        """初始化锁紧模块设备

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

    # ==================== 动作函数（点位写死，来自锁紧模块测试流程 yaml） ====================

    @action(auto_prefix=True, description="1.夹取耗材")
    def grip_consumable(self) -> dict:
        """夹取耗材（yaml step0，指令类型=夹爪夹取）"""
        logger.info("锁紧模块：夹取耗材...")
        # 运行位置
        self.set_node_value("Lock_XPosSet", 3700)
        self.set_node_value("Lock_YPosSet", 930)
        self.set_node_value("Lock_Z1PosSet", 0)
        self.set_node_value("Lock_Z2PosSet", 1105)
        # 运行速度
        self.set_node_value("Lock_XSpeed", 500)
        self.set_node_value("Lock_YSpeed", 500)
        self.set_node_value("Lock_Z1Speed", 500)
        self.set_node_value("Lock_Z2Speed", 500)
        # 夹爪位置与力度
        self.set_node_value("Lock_JawPosition", 30.0)
        self.set_node_value("Lock_JawForce", 0.1)
        return self._trigger_and_wait(LockCommand.JAW_PICK, "夹取耗材")

    @action(auto_prefix=True, description="2.放置耗材")
    def place_consumable(self) -> dict:
        """放置耗材（yaml step1，指令类型=夹爪放置）"""
        logger.info("锁紧模块：放置耗材...")
        self.set_node_value("Lock_XPosSet", 760)
        self.set_node_value("Lock_YPosSet", 780)
        self.set_node_value("Lock_Z1PosSet", 0)
        self.set_node_value("Lock_Z2PosSet", 1150)
        self.set_node_value("Lock_XSpeed", 500)
        self.set_node_value("Lock_YSpeed", 500)
        self.set_node_value("Lock_Z1Speed", 500)
        self.set_node_value("Lock_Z2Speed", 500)
        self.set_node_value("Lock_JawPosition", 11.0)
        self.set_node_value("Lock_JawForce", 0.1)
        return self._trigger_and_wait(LockCommand.JAW_PLACE, "放置耗材")

    @action(auto_prefix=True, description="3.夹取盖板")
    def grip_cover_plate(self) -> dict:
        """夹取盖板（yaml step2，指令类型=夹爪夹取）"""
        logger.info("锁紧模块：夹取盖板...")
        self.set_node_value("Lock_XPosSet", 2290)
        self.set_node_value("Lock_YPosSet", 980)
        self.set_node_value("Lock_Z1PosSet", 0)
        self.set_node_value("Lock_Z2PosSet", 875)
        self.set_node_value("Lock_XSpeed", 500)
        self.set_node_value("Lock_YSpeed", 500)
        self.set_node_value("Lock_Z1Speed", 500)
        self.set_node_value("Lock_Z2Speed", 500)
        self.set_node_value("Lock_JawPosition", 65.0)
        self.set_node_value("Lock_JawForce", 0.1)
        return self._trigger_and_wait(LockCommand.JAW_PICK, "夹取盖板")

    @action(auto_prefix=True, description="4.放置盖板")
    def place_cover_plate(self) -> dict:
        """放置盖板（yaml step3，指令类型=夹爪放置）"""
        logger.info("锁紧模块：放置盖板...")
        self.set_node_value("Lock_XPosSet", 760)
        self.set_node_value("Lock_YPosSet", 740)
        self.set_node_value("Lock_Z1PosSet", 0)
        self.set_node_value("Lock_Z2PosSet", 760)
        self.set_node_value("Lock_XSpeed", 500)
        self.set_node_value("Lock_YSpeed", 500)
        self.set_node_value("Lock_Z1Speed", 500)
        self.set_node_value("Lock_Z2Speed", 500)
        self.set_node_value("Lock_JawPosition", 65.0)
        self.set_node_value("Lock_JawForce", 0.1)
        return self._trigger_and_wait(LockCommand.JAW_PLACE, "放置盖板")

    @action(auto_prefix=True, description="5.取螺丝（拧松）")
    def loosen_screw(self) -> dict:
        """取螺丝/拧松（yaml step4，指令类型=电批拧松）"""
        logger.info("锁紧模块：取螺丝（拧松）...")
        self.set_node_value("Lock_XPosSet", 1086)
        self.set_node_value("Lock_YPosSet", 430)
        self.set_node_value("Lock_Z1PosSet", 1070)
        self.set_node_value("Lock_Z2PosSet", 0)
        # self.set_node_value("Lock_XPosSet", 1270)
        # self.set_node_value("Lock_YPosSet", 2030)
        # self.set_node_value("Lock_Z1PosSet", 1065)
        # self.set_node_value("Lock_Z2PosSet", 0)
        self.set_node_value("Lock_XSpeed", 500)
        self.set_node_value("Lock_YSpeed", 500)
        self.set_node_value("Lock_Z1Speed", 500)
        self.set_node_value("Lock_Z2Speed", 500)
        self.set_node_value("Lock_JawPosition", 0.0)
        self.set_node_value("Lock_JawForce", 0.0)
        return self._trigger_and_wait(LockCommand.SCREW_LOOSEN, "取螺丝（拧松）")

    @action(auto_prefix=True, description="6.拧螺丝（拧紧）")
    def tighten_screw(self) -> dict:
        """拧螺丝/拧紧（yaml step5，指令类型=电批拧紧）"""
        logger.info("锁紧模块：拧螺丝（拧紧）...")
        # self.set_node_value("Lock_XPosSet", 1086)
        # self.set_node_value("Lock_YPosSet", 430)
        # self.set_node_value("Lock_Z1PosSet", 1070)
        # self.set_node_value("Lock_Z2PosSet", 0)
        self.set_node_value("Lock_XPosSet", 1270)
        self.set_node_value("Lock_YPosSet", 2030)
        self.set_node_value("Lock_Z1PosSet", 1065)
        self.set_node_value("Lock_Z2PosSet", 0)
        self.set_node_value("Lock_XSpeed", 500)
        self.set_node_value("Lock_YSpeed", 500)
        self.set_node_value("Lock_Z1Speed", 500)
        self.set_node_value("Lock_Z2Speed", 500)
        self.set_node_value("Lock_JawPosition", 0.0)
        self.set_node_value("Lock_JawForce", 0.0)
        return self._trigger_and_wait(LockCommand.SCREW_TIGHTEN, "拧螺丝（拧紧）")

    @action(auto_prefix=True, description="锁紧模块复位")
    def reset(self) -> dict:
        """锁紧模块复位（指令类型=复位）"""
        logger.info("锁紧模块：复位...")
        return self._trigger_and_wait(LockCommand.RESET, "复位")

    # ==================== 内部触发/等待逻辑（参照 AI4C 写法） ====================

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        """下发指令类型并触发，等待完成后复位触发。

        - 设置 Lock_CmdType（指令类型）
        - 设置 Lock_CmdTrig=1（指令触发）
        - 等待 Lock_CompleteFB 变为非 0（完成）
        - 复位 Lock_CmdTrig=0
        - 等待 Lock_CompleteFB 变回 0（完成复位）
        """
        self.set_node_value("Lock_CmdType", int(cmd_type))   # 指令类型
        self.set_node_value("Lock_CmdTrig", 1)               # 指令触发
        if self._wait_until_true("Lock_CompleteFB", timeout=timeout, description=f"{description}完成"):
            self.set_node_value("Lock_CmdTrig", 0)           # 复位触发
            if self._wait_until_false("Lock_CompleteFB", description=f"{description}完成复位"):
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
        """按锁紧模块测试流程 yaml 依次执行全部步骤"""
        logger.info("锁紧模块：开始整体测试流程...")
        self.grip_consumable()      # 1.夹取耗材
        self.place_consumable()     # 2.放置耗材
        self.grip_cover_plate()     # 3.夹取盖板
        self.place_cover_plate()    # 4.放置盖板
        self.loosen_screw()         # 5.取螺丝（拧松）
        self.tighten_screw()        # 6.拧螺丝（拧紧）
        logger.info("锁紧模块：整体测试流程完成")
        return {"success": True, "message": "锁紧模块测试流程完成"}

    # ==================== 状态读取 ====================

    @not_action
    def get_positions(self) -> dict:
        """读取当前 X/Y/Z1/Z2 位置反馈"""
        return {
            "X": self.get_node_value("Lock_XPosFB"),
            "Y": self.get_node_value("Lock_YPosFB"),
            "Z1": self.get_node_value("Lock_Z1PosFB"),
            "Z2": self.get_node_value("Lock_Z2PosFB"),
        }

    @not_action
    def _log_positions(self, prefix: str = "位置反馈") -> None:
        """将位置反馈写入日志"""
        pos = self.get_positions()
        complete = self.get_node_value("Lock_CompleteFB", force_read=True)
        logger.info(f"{prefix}: X={pos['X']} Y={pos['Y']} Z1={pos['Z1']} Z2={pos['Z2']} 完成={complete}")


if __name__ == "__main__":
    # 调试用法：连接 GN 锁紧模块 OPC UA 服务器并执行测试流程
    logging.getLogger("unilabos").setLevel(logging.INFO)

    LOCKING_MECHANISM_URL = "opc.tcp://192.168.6.6:4840"
    POSITION_LOG_INTERVAL = 15.0  # 位置反馈日志间隔（秒）

    locking_mechanism = LockingMechanismDevice(
        url=LOCKING_MECHANISM_URL,
        csv_path=DEFAULT_CSV_PATH,
    )

    time.sleep(2)

    # 后台定时将位置反馈写入日志，便于实时查看
    position_log_running = True

    def _position_log_worker():
        while position_log_running:
            try:
                locking_mechanism._log_positions("实时位置")
            except Exception as e:
                logger.warning(f"位置反馈日志异常: {e}")
            time.sleep(POSITION_LOG_INTERVAL)

    position_log_thread = threading.Thread(target=_position_log_worker, daemon=True, name="LockPositionLog")
    position_log_thread.start()
    logger.info(f"已启动位置反馈实时日志（间隔 {POSITION_LOG_INTERVAL}s）")

    # 命令行菜单
    while True:
        print("请选择操作：")
        print("1 夹取耗材")
        print("2 放置耗材")
        print("3 夹取盖板")
        print("4 放置盖板")
        print("5 取螺丝（拧松）")
        print("6 拧螺丝（拧紧）")
        print("7 复位")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "1":
            locking_mechanism.grip_consumable()
        elif choice == "2":
            locking_mechanism.place_consumable()
        elif choice == "3":
            locking_mechanism.grip_cover_plate()
        elif choice == "4":
            locking_mechanism.place_cover_plate()
        elif choice == "5":
            locking_mechanism.loosen_screw()
        elif choice == "6":
            locking_mechanism.tighten_screw()
        elif choice == "7":
            locking_mechanism.reset()
        elif choice == "98":
            locking_mechanism.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    # 停止位置日志线程
    position_log_running = False
    position_log_thread.join(timeout=POSITION_LOG_INTERVAL + 1)

    # 断开连接
    locking_mechanism.disconnect()
    print("退出程序。")
