"""
固体加样 设备驱动

参照 centrifuge.py / locking_mechanism.py 写法，继承 OPC UA 通讯基类，实现具体的设备动作函数。
节点变量来自 opcua_gn1.3.3.csv 中「固体加样」(前缀 Solid_)。
各动作点位根据「固体加样测试流程.yaml」写死。

指令类型 (Solid_CmdType)：
    1=X向左 2=X向右 3=Y向里 4=Y向外
    5=夹爪Z向上 6=夹爪Z向下 7=D开门 8=D关门
    9=取料筒时Y轴向里 10=取料筒时Y轴向外 11=加料
    12=夹爪夹料 13=夹爪放料 14=天枰去皮 15=天枰称重
    16=料筒Z向上 17=料筒Z向下 18=放料筒时Y轴向里 19=放料筒时Y轴向外
    20=复位 21=夹爪夹紧 22=夹爪松开 23=xyz回原点

YAML 字段 → CSV 节点映射：
    XPos          → Solid_XPosSet
    YPos          → Solid_YPosSet
    MaterialZPos  → Solid_MaterialZPosSet
    GripperZPos   → Solid_GripperZPosSet
    DoorPos       → Solid_DoorPosSet
    VoluneWeight  → Solid_VoluneWeightSet
    XSpeed        → Solid_XSpeed
    YSpeed        → Solid_YSpeed
    MaterialZSpeed→ Solid_MaterialZSpeed
    GripperZSpeed → Solid_GripperZSpeed
    DoorSpeed     → Solid_DoorSpeed
    SolidFeedCmd  → Solid_CmdType（GripperTake=12, GripperPut=13, ...）
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


class SolidCommand(int, Enum):
    """固体加样指令类型 (Solid_CmdType)"""
    X_LEFT = 1
    X_RIGHT = 2
    Y_IN = 3
    Y_OUT = 4
    GRIPPER_Z_UP = 5
    GRIPPER_Z_DOWN = 6
    DOOR_OPEN = 7
    DOOR_CLOSE = 8
    TAKE_CYLINDER_Y_IN = 9
    TAKE_CYLINDER_Y_OUT = 10       # TakeMaterialYBackward
    DISPENSE = 11                  # SolidFeed
    GRIPPER_PICK = 12              # GripperTake
    GRIPPER_PLACE = 13             # GripperPut
    BALANCE_TARE = 14
    BALANCE_WEIGH = 15
    CYLINDER_Z_UP = 16
    CYLINDER_Z_DOWN = 17
    PLACE_CYLINDER_Y_IN = 18       # PutMaterialYForward
    PLACE_CYLINDER_Y_OUT = 19
    RESET = 20
    GRIPPER_CLAMP = 21
    GRIPPER_RELEASE = 22
    HOME_XYZ = 23


@device(
    id="gn_solid_weighing",
    display_name="固体加样",
    category=["workstation"],
    description="GN 固体加样：按测试流程完成 夹取/加样/放回，OPC UA 控制",
    icon="",
)
class SolidWeighingDevice(OpcUaClientWithSubscription):
    """固体加样设备类（OPC 前缀 Solid_）"""

    CMD_TYPE_NODE = "Solid_CmdType"
    CMD_TRIG_NODE = "Solid_CmdTrig"
    COMPLETE_NODE = "Solid_CompleteFB"

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
        """初始化固体加样设备

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

    # ==================== 动作函数（点位写死，来自固体加样测试流程 yaml） ====================

    @action(auto_prefix=True, description="1.夹爪夹取（料架）")
    def gripper_take_from_rack(self) -> dict:
        """夹爪夹取（yaml step0 GripperTake，指令类型=12）"""
        logger.info("固体加样：夹爪夹取（料架）...")
        self._apply_setpoints(
            x_pos=20, y_pos=2100, material_z_pos=0, gripper_z_pos=2200,
            door_pos=0, volune_weight=0,
            x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
        )
        return self._trigger_and_wait(SolidCommand.GRIPPER_PICK, "夹爪夹取（料架）")

    @action(auto_prefix=True, description="2.夹爪放置（加样工位）")
    def gripper_put_to_station(self) -> dict:
        """夹爪放置（yaml step1 GripperPut，指令类型=13）"""
        logger.info("固体加样：夹爪放置（加样工位）...")
        self._apply_setpoints(
            x_pos=-1730, y_pos=1540, material_z_pos=0, gripper_z_pos=1130,
            door_pos=0, volune_weight=0,
            x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
        )
        return self._trigger_and_wait(SolidCommand.GRIPPER_PLACE, "夹爪放置（加样工位）")


    # TODO: 这里后面需要实现指定位置的取料筒
    @action(auto_prefix=True, description="3.取料筒后向外")
    def take_material_y_backward(self) -> dict:
        """取料筒后向外（yaml step2 TakeMaterialYBackward，指令类型=10）"""
        logger.info("固体加样：取料筒后向外...")
        self._apply_setpoints(
            x_pos=-1900, y_pos=2220, material_z_pos=235000, gripper_z_pos=0,
            door_pos=0, volune_weight=0,
            x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=0,
        )
        return self._trigger_and_wait(SolidCommand.TAKE_CYLINDER_Y_OUT, "取料筒后向外")

    @action(auto_prefix=True, description="4.料筒加样")
    def solid_feed(self, amount: int = 30, timeout: float = 600.0) -> dict:
        """料筒加样（yaml step3 SolidFeed，指令类型=11）"""
        logger.info(f"固体加样：料筒加样（目标量={amount}）...")
        self._apply_setpoints(
            x_pos=-300, y_pos=700, material_z_pos=40000, gripper_z_pos=0,
            door_pos=3700, volune_weight=amount,
            x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=150,
        )
        return self._trigger_and_wait(SolidCommand.DISPENSE, "料筒加样", timeout=timeout)

    @action(auto_prefix=True, description="5.放料筒向里")
    def put_material_y_forward(self) -> dict:
        """放料筒向里（yaml step4 PutMaterialYForward，指令类型=18）"""
        logger.info("固体加样：放料筒向里...")
        self._apply_setpoints(
            x_pos=-1900, y_pos=2220, material_z_pos=235000, gripper_z_pos=0,
            door_pos=0, volune_weight=0,
            x_speed=500, y_speed=500, material_z_speed=0, gripper_z_speed=0, door_speed=0,
        )
        return self._trigger_and_wait(SolidCommand.PLACE_CYLINDER_Y_IN, "放料筒向里")

    @action(auto_prefix=True, description="6.夹爪夹取（加样工位）")
    def gripper_take_from_station(self) -> dict:
        """夹爪夹取（yaml step5 GripperTake，指令类型=12）"""
        logger.info("固体加样：夹爪夹取（加样工位）...")
        self._apply_setpoints(
            x_pos=-1730, y_pos=1540, material_z_pos=0, gripper_z_pos=1130,
            door_pos=0, volune_weight=0,
            x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
        )
        return self._trigger_and_wait(SolidCommand.GRIPPER_PICK, "夹爪夹取（加样工位）")

    @action(auto_prefix=True, description="7.夹爪放置（料架）")
    def gripper_put_to_rack(self) -> dict:
        """夹爪放置（yaml step6 GripperPut，指令类型=13）"""
        logger.info("固体加样：夹爪放置（料架）...")
        self._apply_setpoints(
            x_pos=20, y_pos=2100, material_z_pos=0, gripper_z_pos=2180,
            door_pos=0, volune_weight=0,
            x_speed=300, y_speed=300, material_z_speed=0, gripper_z_speed=300, door_speed=0,
        )
        return self._trigger_and_wait(SolidCommand.GRIPPER_PLACE, "夹爪放置（料架）")

    @action(auto_prefix=True, description="8.固体加样复位")
    def reset(self) -> dict:
        """固体加样复位（yaml step7 Reset，指令类型=20）"""
        logger.info("固体加样：复位...")
        self._apply_setpoints(
            x_pos=0, y_pos=0, material_z_pos=0, gripper_z_pos=0,
            door_pos=0, volune_weight=0,
            x_speed=0, y_speed=0, material_z_speed=0, gripper_z_speed=0, door_speed=0,
        )
        return self._trigger_and_wait(SolidCommand.RESET, "复位")

    # ==================== 单点调试（指令类型 1-4 / 14-15，便于现场点动） ====================

    @action(auto_prefix=True, description="单点调试：X向左（指令类型=1）")
    def jog_x_left(self) -> dict:
        logger.info("固体加样：单点调试 X向左...")
        self.set_node_value("Solid_XPosSet", 20)
        self.set_node_value("Solid_XSpeed", 300)
        return self._trigger_and_wait(SolidCommand.X_LEFT, "X向左")

    @action(auto_prefix=True, description="单点调试：X向右（指令类型=2）")
    def jog_x_right(self) -> dict:
        logger.info("固体加样：单点调试 X向右...")
        self.set_node_value("Solid_XPosSet", -1730)
        self.set_node_value("Solid_XSpeed", 300)
        return self._trigger_and_wait(SolidCommand.X_RIGHT, "X向右")

    @action(auto_prefix=True, description="单点调试：Y向里（指令类型=3）")
    def jog_y_in(self) -> dict:
        logger.info("固体加样：单点调试 Y向里...")
        self.set_node_value("Solid_YPosSet", 700)
        self.set_node_value("Solid_YSpeed", 300)
        return self._trigger_and_wait(SolidCommand.Y_IN, "Y向里")

    @action(auto_prefix=True, description="单点调试：Y向外（指令类型=4）")
    def jog_y_out(self) -> dict:
        logger.info("固体加样：单点调试 Y向外...")
        self.set_node_value("Solid_YPosSet", 2220)
        self.set_node_value("Solid_YSpeed", 300)
        return self._trigger_and_wait(SolidCommand.Y_OUT, "Y向外")

    @action(auto_prefix=True, description="天平去皮")
    def balance_tare(self) -> dict:
        return self._trigger_and_wait(SolidCommand.BALANCE_TARE, "天枰去皮")

    @action(auto_prefix=True, description="天平称重")
    def balance_weigh(self) -> dict:
        ret = self._trigger_and_wait(SolidCommand.BALANCE_WEIGH, "天枰称重")
        weight = self.get_node_value("Solid_WeightFB", force_read=True)
        ret["weight"] = weight
        ret["message"] = f"称重完成，重量={weight}"
        return ret

    @action(auto_prefix=True, description="通用指令：按 Solid_CmdType 执行任意指令")
    def execute_command(self, cmd_type: int, timeout: float = 180.0) -> dict:
        return self._trigger_and_wait(int(cmd_type), f"指令{cmd_type}", timeout=timeout)

    # ==================== 内部触发/等待逻辑（参照 centrifuge 写法） ====================

    @not_action
    def _apply_setpoints(
        self,
        x_pos: int,
        y_pos: int,
        material_z_pos: int,
        gripper_z_pos: int,
        door_pos: int,
        volune_weight: int,
        x_speed: int,
        y_speed: int,
        material_z_speed: int,
        gripper_z_speed: int,
        door_speed: int,
    ) -> None:
        """写入 yaml 中的运行位置与速度参数"""
        self.set_node_value("Solid_XPosSet", x_pos)
        self.set_node_value("Solid_YPosSet", y_pos)
        self.set_node_value("Solid_MaterialZPosSet", material_z_pos)
        self.set_node_value("Solid_GripperZPosSet", gripper_z_pos)
        self.set_node_value("Solid_DoorPosSet", door_pos)
        self.set_node_value("Solid_VoluneWeightSet", volune_weight)
        self.set_node_value("Solid_XSpeed", x_speed)
        self.set_node_value("Solid_YSpeed", y_speed)
        self.set_node_value("Solid_MaterialZSpeed", material_z_speed)
        self.set_node_value("Solid_GripperZSpeed", gripper_z_speed)
        self.set_node_value("Solid_DoorSpeed", door_speed)

    @not_action
    def _trigger_and_wait(self, cmd_type, description: str, timeout: float = 180.0) -> dict:
        """下发指令类型并触发，等待完成后复位触发。

        - 设置 Solid_CmdType（指令类型）
        - 设置 Solid_CmdTrig=1（指令触发）
        - 等待 Solid_CompleteFB 变为非 0（完成）
        - 复位 Solid_CmdTrig=0
        - 等待 Solid_CompleteFB 变回 0（完成复位）
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
    def _wait_until_true(self, node_name: str, timeout: float = 180.0,
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
    def _wait_until_false(self, node_name: str, timeout: float = 180.0,
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
        """按固体加样测试流程 yaml 依次执行全部步骤"""
        logger.info("固体加样：开始整体测试流程...")
        self.gripper_take_from_rack()    # 1.夹爪夹取（料架）
        self.gripper_put_to_station()    # 2.夹爪放置（加样工位）
        self.take_material_y_backward()  # 3.取料筒后向外
        self.solid_feed()                # 4.料筒加样
        self.put_material_y_forward()    # 5.放料筒向里
        self.gripper_take_from_station() # 6.夹爪夹取（加样工位）
        self.gripper_put_to_rack()       # 7.夹爪放置（料架）
        self.reset()                     # 8.复位
        logger.info("固体加样：整体测试流程完成")
        return {"success": True, "message": "固体加样测试流程完成"}

    # ==================== 状态读取 ====================

    @not_action
    def get_weight(self) -> int:
        return self.get_node_value("Solid_WeightFB", force_read=True)

    @not_action
    def get_positions(self) -> dict:
        """读取当前 X/Y/Z/门 位置反馈"""
        return {
            "X": self.get_node_value("Solid_XPosFB"),
            "Y": self.get_node_value("Solid_YPosFB"),
            "MaterialZ": self.get_node_value("Solid_MaterialZPosFB"),
            "GripperZ": self.get_node_value("Solid_GripperZPosFB"),
            "Door": self.get_node_value("Solid_DoorPosFB"),
        }

    @not_action
    def get_status(self) -> dict:
        """读取位置与重量反馈"""
        status = self.get_positions()
        status["weight"] = self.get_weight()
        status["complete"] = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        """将位置/重量反馈写入日志"""
        status = self.get_status()
        logger.info(
            f"{prefix}: X={status['X']} Y={status['Y']} "
            f"MaterialZ={status['MaterialZ']} GripperZ={status['GripperZ']} "
            f"Door={status['Door']} 重量={status['weight']} 完成={status['complete']}"
        )


if __name__ == "__main__":
    # 调试用法：连接 GN 固体加样 OPC UA 服务器并执行测试流程
    logging.getLogger("unilabos").setLevel(logging.INFO)

    SOLID_FEED_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0  # 状态反馈日志间隔（秒）

    solid = SolidWeighingDevice(
        url=SOLID_FEED_URL,
        csv_path=DEFAULT_CSV_PATH,
    )

    time.sleep(2)

    # 连通性测试：读取当前状态反馈
    logger.info(f"固体加样连通性测试: {solid.get_status()}")

    # 后台定时将状态反馈写入日志，便于实时查看
    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                solid._log_status("实时状态")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    status_log_thread = threading.Thread(
        target=_status_log_worker, daemon=True, name="SolidWeighingStatusLog"
    )
    status_log_thread.start()
    logger.info(f"已启动状态反馈实时日志（间隔 {STATUS_LOG_INTERVAL}s）")

    # 命令行菜单
    while True:
        print("请选择操作：")
        print("0  读取状态（连通性测试）")
        print("1  夹爪夹取（料架）")
        print("2  夹爪放置（加样工位）")
        print("3  取料筒后向外")
        print("4  料筒加样")
        print("5  放料筒向里")
        print("6  夹爪夹取（加样工位）")
        print("7  夹爪放置（料架）")
        print("8  复位")
        print("--- 单点调试 ---")
        print("11 X向左")
        print("12 X向右")
        print("13 Y向里")
        print("14 Y向外")
        print("15 天平去皮")
        print("16 天平称重")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        elif choice == "0":
            print(f"当前状态: {solid.get_status()}")
        elif choice == "1":
            solid.gripper_take_from_rack()
        elif choice == "2":
            solid.gripper_put_to_station()
        elif choice == "3":
            solid.take_material_y_backward()
        elif choice == "4":
            solid.solid_feed()
        elif choice == "5":
            solid.put_material_y_forward()
        elif choice == "6":
            solid.gripper_take_from_station()
        elif choice == "7":
            solid.gripper_put_to_rack()
        elif choice == "8":
            solid.reset()
        elif choice == "11":
            solid.jog_x_left()
        elif choice == "12":
            solid.jog_x_right()
        elif choice == "13":
            solid.jog_y_in()
        elif choice == "14":
            solid.jog_y_out()
        elif choice == "15":
            solid.balance_tare()
        elif choice == "16":
            solid.balance_weigh()
        elif choice == "98":
            solid.run_test_flow()
        else:
            print("无效的操作序号，请重新输入。")

    # 停止状态日志线程
    status_log_running = False
    status_log_thread.join(timeout=STATUS_LOG_INTERVAL + 1)

    # 断开连接
    solid.disconnect()
    print("退出程序。")
