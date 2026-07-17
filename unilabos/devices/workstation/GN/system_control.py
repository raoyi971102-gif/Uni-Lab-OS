"""
系统总控 设备驱动（总复位 / 总停止）

根据 opcua_gn1.3.3.csv 中「总复位」「停止」相关节点定义：
    System_ResetTrig         复位触发
    System_ResetCompleteFB   复位完成反馈
    System_StopTrig          停止触发

该模块为系统级控制，作用于整台设备的所有工站，不隶属任何单一模块。
"""

import os
import time

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")

RESET_TRIG_NODE = "System_ResetTrig"
RESET_COMPLETE_NODE = "System_ResetCompleteFB"
STOP_TRIG_NODE = "System_StopTrig"


@device(
    id="gn_system_control",
    display_name="系统总控",
    category=["workstation"],
    description="GN 系统总控：总停止 (System_StopTrig) 与总复位 (System_ResetTrig)，OPC UA 控制",
    icon="",
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

    # ==================== 总停止 ====================

    @action(auto_prefix=True, description="总停止：向 System_StopTrig 写 1 停止全部动作")
    def stop_all(self) -> dict:
        """总停止：立即停止整台设备的所有工站动作。

        向全局停止触发节点 System_StopTrig 写 1。
        """
        logger.warning("触发系统总停止：System_StopTrig = 1")
        ok = self.set_node_value(STOP_TRIG_NODE, 1)
        if not ok:
            raise ValueError("总停止失败：写入 System_StopTrig=1 未成功")
        return {"success": True, "message": "系统总停止已触发 (System_StopTrig=1)"}

    @action(auto_prefix=True, description="解除总停止：向 System_StopTrig 写 0")
    def clear_stop(self) -> dict:
        """解除总停止：向 System_StopTrig 写 0，复位停止信号。"""
        logger.info("解除系统总停止：System_StopTrig = 0")
        ok = self.set_node_value(STOP_TRIG_NODE, 0)
        if not ok:
            raise ValueError("解除总停止失败：写入 System_StopTrig=0 未成功")
        return {"success": True, "message": "系统总停止已解除 (System_StopTrig=0)"}

    # ==================== 总复位 ====================

    @action(auto_prefix=True, description="总复位：触发 System_ResetTrig 并等待复位完成")
    def reset_all(self, wait: bool = True, timeout: float = 300.0) -> dict:
        """总复位：使整台设备的所有工站回到初始状态。

        向 System_ResetTrig 写 1 触发，等待 System_ResetCompleteFB 变为非 0，
        随后将触发写回 0。

        Args:
            wait: 是否等待复位完成反馈
            timeout: 等待复位完成的超时时间（秒）
        """
        logger.info("触发系统总复位：System_ResetTrig = 1")
        if not self.set_node_value(RESET_TRIG_NODE, 1):
            raise ValueError("总复位失败：写入 System_ResetTrig=1 未成功")

        if not wait:
            return {"success": True, "message": "系统总复位已触发（不等待完成）"}

        ok = self._wait_reset_complete(timeout=timeout)
        self.set_node_value(RESET_TRIG_NODE, 0)
        if not ok:
            raise ValueError(f"总复位超时（{timeout}s）：System_ResetCompleteFB 未变为完成")
        return {"success": True, "message": "系统总复位完成"}

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

    # ==================== 状态读取 ====================

    @not_action
    def get_reset_complete(self) -> int:
        return self.get_node_value(RESET_COMPLETE_NODE, force_read=True)

    @not_action
    def get_stop_state(self) -> int:
        return self.get_node_value(STOP_TRIG_NODE, force_read=True)


if __name__ == "__main__":
    SYSTEM_URL = "opc.tcp://192.168.6.6:4840"

    ctrl = SystemControlDevice(url=SYSTEM_URL, csv_path=DEFAULT_CSV_PATH)

    # 总停止
    ctrl.stop_all()
    # 解除停止
    ctrl.clear_stop()
    # 总复位
    ctrl.reset_all(wait=True)

    ctrl.disconnect()
