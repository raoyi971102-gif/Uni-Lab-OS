"""
堆栈（旋转堆栈）设备驱动

根据 opcua_gn1.3.3.csv 中「堆栈」(前缀 Stack_) 定义，
继承 OPC UA 通讯基类，实现指令触发/等待完成的动作函数。

指令类型 (Stack_CmdType)：
    1=堆栈左旋转 2=堆栈右旋转 3=相机Z向上 4=相机Z向下
    5=堆栈旋转至目标位置 6=相机移动至目标位置并检测有无 7=复位
"""

import os
import time
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.devices.workstation.AI4C.base_opcua_client import OpcUaClientWithSubscription

DEFAULT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opcua_gn1.3.3.csv")


class StackCommand(int, Enum):
    """旋转堆栈指令类型"""
    ROTATE_LEFT = 1
    ROTATE_RIGHT = 2
    CAMERA_Z_UP = 3
    CAMERA_Z_DOWN = 4
    ROTATE_TO_TARGET = 5
    CAMERA_DETECT = 6
    RESET = 7


@device(
    id="gn_rotary_stack",
    display_name="旋转堆栈",
    category=["workstation"],
    description="GN 旋转堆栈：堆栈旋转 + 相机 Z 升降 + 物料有无检测，OPC UA 控制",
    icon="",
)
class RotaryStackDevice(OpcUaClientWithSubscription):
    """旋转堆栈设备类（OPC 前缀 Stack_）"""

    CMD_TYPE_NODE = "Stack_CmdType"
    CMD_TRIG_NODE = "Stack_CmdTrig"
    COMPLETE_NODE = "Stack_CompleteFB"

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

    @not_action
    def _wait_complete(self, timeout: float = 120.0, interval: float = 0.2, description: str = "") -> bool:
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
    def _run(self, cmd_type, description: str = "", setpoints: dict = None, timeout: float = 120.0) -> dict:
        logger.info(f"执行旋转堆栈动作: {description} (cmd={int(cmd_type)})")
        if setpoints:
            for node, val in setpoints.items():
                if val is not None:
                    self.set_node_value(node, val)
        self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type))
        self.set_node_value(self.CMD_TRIG_NODE, 1)
        ok = self._wait_complete(timeout=timeout, description=description)
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        if not ok:
            raise ValueError(f"{description} 执行失败或超时")
        return {"success": True, "message": f"{description} 完成"}

    # ==================== 动作函数 ====================

    @action(auto_prefix=True, description="堆栈左旋转")
    def rotate_left(self, speed: Optional[int] = None) -> dict:
        return self._run(StackCommand.ROTATE_LEFT, "堆栈左旋转", {"Stack_RSpeed": speed})

    @action(auto_prefix=True, description="堆栈右旋转")
    def rotate_right(self, speed: Optional[int] = None) -> dict:
        return self._run(StackCommand.ROTATE_RIGHT, "堆栈右旋转", {"Stack_RSpeed": speed})

    @action(auto_prefix=True, description="相机 Z 向上")
    def camera_z_up(self, position: Optional[int] = None, speed: Optional[int] = None) -> dict:
        return self._run(StackCommand.CAMERA_Z_UP, "相机Z向上",
                         {"Stack_ZPosSet": position, "Stack_ZSpeed": speed})

    @action(auto_prefix=True, description="相机 Z 向下")
    def camera_z_down(self, position: Optional[int] = None, speed: Optional[int] = None) -> dict:
        return self._run(StackCommand.CAMERA_Z_DOWN, "相机Z向下",
                         {"Stack_ZPosSet": position, "Stack_ZSpeed": speed})

    @action(auto_prefix=True, description="堆栈旋转至目标位置")
    def rotate_to_target(self, position: int, speed: Optional[int] = None) -> dict:
        return self._run(StackCommand.ROTATE_TO_TARGET, "堆栈旋转至目标位置",
                         {"Stack_RPosSet": position, "Stack_RSpeed": speed})

    @action(auto_prefix=True, description="相机移动至目标位置并检测有无（返回检测结果）")
    def camera_detect(self, position: int, speed: Optional[int] = None) -> dict:
        ret = self._run(StackCommand.CAMERA_DETECT, "相机移动至目标位置并检测有无",
                        {"Stack_ZPosSet": position, "Stack_ZSpeed": speed})
        result = self.get_node_value("Stack_DetectResult", force_read=True)
        ret["detect_result"] = result
        ret["message"] = f"检测完成，结果={result}"
        return ret

    @action(auto_prefix=True, description="旋转堆栈复位")
    def reset(self) -> dict:
        return self._run(StackCommand.RESET, "复位")

    @action(auto_prefix=True, description="通用指令：按 Stack_CmdType 执行任意指令")
    def execute_command(self, cmd_type: int, timeout: float = 120.0) -> dict:
        return self._run(int(cmd_type), f"指令{cmd_type}", timeout=timeout)

    @not_action
    def get_detect_result(self) -> int:
        return self.get_node_value("Stack_DetectResult", force_read=True)
