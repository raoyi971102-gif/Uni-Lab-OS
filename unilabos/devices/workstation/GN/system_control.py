"""
系统总控 设备驱动（总复位 / 总停止）

协议：opcua_gn1.3.3.csv「总复位」「停止」相关节点。
对外仅暴露 execute_command（伪 CmdType，无 OPC CmdType 节点）。

伪指令类型：
    1=总停止(stop_all)  2=解除总停止(clear_stop)  3=总复位(reset_all)

该模块为系统级控制，作用于整台设备的所有工站，不隶属任何单一模块。
"""

import os
import time
import logging

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

RESET_TRIG_NODE = "System_ResetTrig"
RESET_COMPLETE_NODE = "System_ResetCompleteFB"
STOP_TRIG_NODE = "System_StopTrig"

# 伪 CmdType（无 OPC CmdType 节点，仅软件层路由）
SYSTEM_CMD_LABELS = {
    1: "总停止",
    2: "解除总停止",
    3: "总复位",
}


class SystemCommand(int):
    """系统总控伪指令类型"""

    STOP_ALL = 1
    CLEAR_STOP = 2
    RESET_ALL = 3


_EXECUTE_CMD_DOC = (
    "按伪 CmdType 执行系统总控指令（无 OPC CmdType 节点）。"
    "1=总停止(System_StopTrig=1) "
    "2=解除总停止(System_StopTrig=0) "
    "3=总复位(System_ResetTrig=1，wait=True 时等待 ResetCompleteFB)。"
)


@device(
    id="gn_system_control",
    display_name="系统总控",
    category=["workstation"],
    description="GN 系统总控：总停止/解除总停止/总复位，OPC UA 控制，仅 execute_command 通用入口",
    icon="",
    version="2.0.0",
)
class SystemControlDevice(OpcUaClientWithSubscription):
    """系统总控设备类（总复位 / 总停止）"""

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
        wait: bool = True,
        timeout: float = 300.0,
    ) -> dict:
        """唯一注册动作：按伪 CmdType 路由至 stop_all / clear_stop / reset_all。"""
        cmd = int(cmd_type)
        label = SYSTEM_CMD_LABELS.get(cmd, f"CmdType={cmd}")

        if cmd == SystemCommand.STOP_ALL:
            return self._stop_all(label)
        if cmd == SystemCommand.CLEAR_STOP:
            return self._clear_stop(label)
        if cmd == SystemCommand.RESET_ALL:
            return self._reset_all(label, wait=wait, timeout=timeout)

        raise ValueError(f"未知系统总控指令 CmdType={cmd}，支持 1/2/3")

    @action(description="人工准备耗材（工作流占位，不触发硬件）")
    def manual_prepare(self, timeout: float = 10.0) -> dict:
        logger.info("工作流占位：人工准备耗材")
        return {"success": True, "message": "manual_prepare", "timeout": timeout}

    @action(description="工作流完成（占位，不触发硬件）")
    def workflow_complete(self, timeout: float = 10.0) -> dict:
        logger.info("工作流占位：流程完成")
        return {"success": True, "message": "workflow_complete", "timeout": timeout}

    @not_action
    def _stop_all(self, description: str) -> dict:
        """总停止：向 System_StopTrig 写 1"""
        logger.warning(f"触发{description}：System_StopTrig = 1")
        ok = self.set_node_value(STOP_TRIG_NODE, 1)
        if not ok:
            raise ValueError("总停止失败：写入 System_StopTrig=1 未成功")
        return {
            "success": True,
            "message": "系统总停止已触发 (System_StopTrig=1)",
            "cmd_type": SystemCommand.STOP_ALL,
        }

    @not_action
    def _clear_stop(self, description: str) -> dict:
        """解除总停止：向 System_StopTrig 写 0"""
        logger.info(f"触发{description}：System_StopTrig = 0")
        ok = self.set_node_value(STOP_TRIG_NODE, 0)
        if not ok:
            raise ValueError("解除总停止失败：写入 System_StopTrig=0 未成功")
        return {
            "success": True,
            "message": "系统总停止已解除 (System_StopTrig=0)",
            "cmd_type": SystemCommand.CLEAR_STOP,
        }

    @not_action
    def _reset_all(self, description: str, wait: bool = True, timeout: float = 300.0) -> dict:
        """总复位：触发 System_ResetTrig 并可选等待复位完成"""
        logger.info(f"触发{description}：System_ResetTrig = 1")
        if not self.set_node_value(RESET_TRIG_NODE, 1):
            raise ValueError("总复位失败：写入 System_ResetTrig=1 未成功")

        if not wait:
            return {
                "success": True,
                "message": "系统总复位已触发（不等待完成）",
                "cmd_type": SystemCommand.RESET_ALL,
            }

        ok = self._wait_reset_complete(timeout=timeout)
        self.set_node_value(RESET_TRIG_NODE, 0)
        if not ok:
            raise ValueError(f"总复位超时（{timeout}s）：System_ResetCompleteFB 未变为完成")
        return {
            "success": True,
            "message": "系统总复位完成",
            "cmd_type": SystemCommand.RESET_ALL,
        }

    @not_action
    def _wait_reset_complete(self, timeout: float = 300.0, interval: float = 0.5) -> bool:
        """等待复位完成反馈 System_ResetCompleteFB 变为非 0"""
        start = time.time()
        while True:
            value = self.get_node_value(RESET_COMPLETE_NODE, force_read=True)
            if value:
                logger.info(f"✓ 系统总复位完成 (System_ResetCompleteFB={value})")
                return True
            if time.time() - start >= timeout:
                logger.error(f"✗ 等待系统总复位完成超时（{timeout}s，当前={value!r}）")
                return False
            time.sleep(interval)

    @not_action
    def get_reset_complete(self) -> int:
        return self.get_node_value(RESET_COMPLETE_NODE, force_read=True)

    @not_action
    def get_stop_state(self) -> int:
        return self.get_node_value(STOP_TRIG_NODE, force_read=True)


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    SYSTEM_URL = "opc.tcp://192.168.6.6:4840"

    ctrl = SystemControlDevice(url=SYSTEM_URL, csv_path=DEFAULT_CSV_PATH)

    ctrl.execute_command(cmd_type=1)
    ctrl.execute_command(cmd_type=2)
    ctrl.execute_command(cmd_type=3, wait=True)

    ctrl.disconnect()
