"""
离心机 设备驱动

参照 AI4C.py / locking_mechanism.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 opcua_gn1.3.3.csv 中「离心机」(前缀 Centrifuge_)。
各动作点位根据「离心机模块测试流程.yaml」写死。

指令类型 (Centrifuge_CmdType)：
    1=Y向左 2=Y向右 3=Z向左 4=Z向右
    5=放入物料 6=运行离心机 7=取出物料 8=复位
    9=夹爪张开 10=夹爪夹紧

YAML 字段 → CSV 节点映射：
    YPos   → Centrifuge_YPosSet
    Z1Pos  → Centrifuge_ZPosSet（台面Z）
    Z2Pos  → Centrifuge_InnerZPosSet（离心机内Z）
    RPM    → Centrifuge_RPM
    Time   → Centrifuge_Time
    YSpeed → Centrifuge_YSpeed
    ZSpeed → Centrifuge_ZSpeed
    PlateNo→ Centrifuge_PlateNo
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


class CentrifugeCommand(int, Enum):
    """离心机指令类型 (Centrifuge_CmdType)"""
    Y_LEFT = 1
    Y_RIGHT = 2
    Z_LEFT = 3
    Z_RIGHT = 4
    LOAD_MATERIAL = 5     # 放入物料 (PutMaterial)
    RUN = 6               # 运行离心机 (RunCentrifuge)
    UNLOAD_MATERIAL = 7   # 取出物料 (TakeMaterial)
    RESET = 8
    JAW_OPEN = 9
    JAW_CLAMP = 10


@device(
    id="gn_centrifuge",
    display_name="离心机",
    category=["workstation"],
    description="GN 离心机：按测试流程完成 放入物料/离心运行/取出物料，OPC UA 控制",
    icon="",
)
class CentrifugeDevice(OpcUaClientWithSubscription):
    """离心机设备类（OPC 前缀 Centrifuge_）"""

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
        """初始化离心机设备

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

    # ==================== 动作函数（点位写死，来自离心机模块测试流程 yaml） ====================

    @action(auto_prefix=True, description="1.放入物料")
    def put_material(self) -> dict:
        """放入物料（yaml step0 PutMaterial，指令类型=5）"""
        logger.info("离心机：放入物料...")
        self.set_node_value("Centrifuge_YPosSet", -1700)
        self.set_node_value("Centrifuge_ZPosSet", 1000)        # Z1Pos 台面Z
        self.set_node_value("Centrifuge_InnerZPosSet", 3450)   # Z2Pos 离心机内Z
        self.set_node_value("Centrifuge_RPM", 0)
        self.set_node_value("Centrifuge_Time", 0)
        self.set_node_value("Centrifuge_YSpeed", 300)
        self.set_node_value("Centrifuge_ZSpeed", 300)
        self.set_node_value("Centrifuge_PlateNo", 2)
        return self._trigger_and_wait(CentrifugeCommand.LOAD_MATERIAL, "放入物料")

    @action(auto_prefix=True, description="2.离心机启动")
    def run_centrifuge(self) -> dict:
        """离心机启动（yaml step1 RunCentrifuge，指令类型=6）"""
        logger.info("离心机：启动运行...")
        self.set_node_value("Centrifuge_YPosSet", 0)
        self.set_node_value("Centrifuge_ZPosSet", 0)
        self.set_node_value("Centrifuge_InnerZPosSet", 0)
        self.set_node_value("Centrifuge_RPM", 1000)
        self.set_node_value("Centrifuge_Time", 1)
        self.set_node_value("Centrifuge_YSpeed", 0)
        self.set_node_value("Centrifuge_ZSpeed", 0)
        self.set_node_value("Centrifuge_PlateNo", 2)
        timeout = 1 * 60.0 + 180.0
        return self._trigger_and_wait(CentrifugeCommand.RUN, "离心机启动", timeout=timeout)

    @action(auto_prefix=True, description="3.取出物料")
    def take_material(self) -> dict:
        """取出物料（yaml step2 TakeMaterial，指令类型=7）"""
        logger.info("离心机：取出物料...")
        self.set_node_value("Centrifuge_YPosSet", -1700)
        self.set_node_value("Centrifuge_ZPosSet", 100)
        self.set_node_value("Centrifuge_InnerZPosSet", 3450)
        self.set_node_value("Centrifuge_RPM", 0)
        self.set_node_value("Centrifuge_Time", 0)
        self.set_node_value("Centrifuge_YSpeed", 300)
        self.set_node_value("Centrifuge_ZSpeed", 300)
        self.set_node_value("Centrifuge_PlateNo", 2)
        return self._trigger_and_wait(CentrifugeCommand.UNLOAD_MATERIAL, "取出物料")

    @action(auto_prefix=True, description="离心机复位")
    def reset(self) -> dict:
        """离心机复位（指令类型=8）"""
        logger.info("离心机：复位...")
        return self._trigger_and_wait(CentrifugeCommand.RESET, "复位")

    # ==================== 单点调试（指令类型 1-4，点位写死便于现场点动） ====================

    @action(auto_prefix=True, description="单点调试：Y向左（指令类型=1）")
    def jog_y_left(self) -> dict:
        """Y 轴向左点动（指令类型=1）"""
        logger.info("离心机：单点调试 Y向左...")
        self.set_node_value("Centrifuge_YPosSet", -1700)
        self.set_node_value("Centrifuge_YSpeed", 300)
        return self._trigger_and_wait(CentrifugeCommand.Y_LEFT, "Y向左")

    @action(auto_prefix=True, description="单点调试：Y向右（指令类型=2）")
    def jog_y_right(self) -> dict:
        """Y 轴向右点动（指令类型=2）"""
        logger.info("离心机：单点调试 Y向右...")
        self.set_node_value("Centrifuge_YPosSet", 0)
        self.set_node_value("Centrifuge_YSpeed", 300)
        return self._trigger_and_wait(CentrifugeCommand.Y_RIGHT, "Y向右")

    @action(auto_prefix=True, description="单点调试：Z向左（指令类型=3）")
    def jog_z_left(self) -> dict:
        """Z 轴向左点动（指令类型=3，写 Centrifuge_ZPosSet 台面Z）"""
        logger.info("离心机：单点调试 Z向左...")
        self.set_node_value("Centrifuge_ZPosSet", 300)
        self.set_node_value("Centrifuge_ZSpeed", 300)
        return self._trigger_and_wait(CentrifugeCommand.Z_LEFT, "Z向左")

    @action(auto_prefix=True, description="单点调试：Z向右（指令类型=4）")
    def jog_z_right(self) -> dict:
        """Z 轴向右点动（指令类型=4，写 Centrifuge_ZPosSet 台面Z）"""
        logger.info("离心机：单点调试 Z向右...")
        self.set_node_value("Centrifuge_ZPosSet", -300)
        self.set_node_value("Centrifuge_ZSpeed", 300)
        return self._trigger_and_wait(CentrifugeCommand.Z_RIGHT, "Z向右")

    # ==================== 内部触发/等待逻辑（参照 locking_mechanism 写法） ====================

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 120.0) -> dict:
        """下发指令类型并触发，等待完成后复位触发。

        - 设置 Centrifuge_CmdType（指令类型）
        - 设置 Centrifuge_CmdTrig=1（指令触发）
        - 等待 Centrifuge_CompleteFB 变为非 0（完成）
        - 复位 Centrifuge_CmdTrig=0
        - 等待 Centrifuge_CompleteFB 变回 0（完成复位）
        """
        self.set_node_value("Centrifuge_CmdType", int(cmd_type))   # 指令类型
        self.set_node_value("Centrifuge_CmdTrig", 1)               # 指令触发
        if self._wait_until_true("Centrifuge_CompleteFB", timeout=timeout, description=f"{description}完成"):
            self.set_node_value("Centrifuge_CmdTrig", 0)           # 复位触发
            if self._wait_until_false("Centrifuge_CompleteFB", description=f"{description}完成复位"):
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
        """按离心机模块测试流程 yaml 依次执行全部步骤"""
        logger.info("离心机：开始整体测试流程...")
        self.put_material()      # 1.放入物料
        self.run_centrifuge()    # 2.离心机启动
        self.take_material()     # 3.取出物料
        logger.info("离心机：整体测试流程完成")
        return {"success": True, "message": "离心机测试流程完成"}

    # ==================== 状态读取 ====================

    @not_action
    def get_positions(self) -> dict:
        """读取当前 Y/Z 位置反馈"""
        return {
            "Y": self.get_node_value("Centrifuge_YPosFB"),
            "Z": self.get_node_value("Centrifuge_ZPosFB"),
        }

    @not_action
    def _log_positions(self, prefix: str = "位置反馈") -> None:
        """将位置反馈写入日志"""
        pos = self.get_positions()
        complete = self.get_node_value("Centrifuge_CompleteFB", force_read=True)
        logger.info(f"{prefix}: Y={pos['Y']} Z={pos['Z']} 完成={complete}")


if __name__ == "__main__":
    # 调试用法：连接 GN 离心机 OPC UA 服务器并执行测试流程
    logging.getLogger("unilabos").setLevel(logging.INFO)

    CENTRIFUGE_URL = "opc.tcp://192.168.6.6:4840"
    POSITION_LOG_INTERVAL = 15.0  # 位置反馈日志间隔（秒）

    centrifuge = CentrifugeDevice(
        url=CENTRIFUGE_URL,
        csv_path=DEFAULT_CSV_PATH,
    )

    time.sleep(2)

    # 后台定时将位置反馈写入日志，便于实时查看
    position_log_running = True

    def _position_log_worker():
        while position_log_running:
            try:
                centrifuge._log_positions("实时位置")
            except Exception as e:
                logger.warning(f"位置反馈日志异常: {e}")
            time.sleep(POSITION_LOG_INTERVAL)

    position_log_thread = threading.Thread(
        target=_position_log_worker, daemon=True, name="CentrifugePositionLog"
    )
    position_log_thread.start()
    logger.info(f"已启动位置反馈实时日志（间隔 {POSITION_LOG_INTERVAL}s）")

    # 命令行菜单
    while True:
        print("请选择操作：")
        print("1 放入物料")
        print("2 离心机启动")
        print("3 取出物料")
        print("4 复位")
        print("--- 单点调试（指令类型 1-4） ---")
        print("11 Y向左")
        print("12 Y向右")
        print("13 Z向左")
        print("14 Z向右")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "1":
            centrifuge.put_material()
        elif choice == "2":
            centrifuge.run_centrifuge()
        elif choice == "3":
            centrifuge.take_material()
        elif choice == "4":
            centrifuge.reset()
        elif choice == "11":
            centrifuge.jog_y_left()
        elif choice == "12":
            centrifuge.jog_y_right()
        elif choice == "13":
            centrifuge.jog_z_left()
        elif choice == "14":
            centrifuge.jog_z_right()
        elif choice == "98":
            centrifuge.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    # 停止位置日志线程
    position_log_running = False
    position_log_thread.join(timeout=POSITION_LOG_INTERVAL + 1)

    # 断开连接
    centrifuge.disconnect()
    print("退出程序。")
