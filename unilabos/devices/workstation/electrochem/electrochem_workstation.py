"""
电化学合成工作站
Electrochem Workstation

集成来宇(LaiYu)移液站和GreenLab电反应仪的电化学合成工作站。
通过 WorkstationBase 管理 Deck 上的物料，并通过代理模式控制子设备。
"""

import time
from typing import Any, Dict, List, Optional

from pylabrobot.resources import Deck

from unilabos.devices.workstation.workstation_base import WorkstationBase, WorkflowStatus, WorkflowInfo
from unilabos.utils.log import logger


class ElectrochemWorkstation(WorkstationBase):
    """电化学合成工作站

    集成设备：
    - 来宇(LaiYu) 移液站：负责液体转移、加样等操作
    - GreenLab 电反应仪：6通道电化学反应控制（恒压/恒流、搅拌）
    - 多孔架：存放多孔板、深孔板、试剂瓶等耗材

    设备通过 graph 文件中的 children 定义关联，workstation 以代理模式
    （proxy:device_id）调用子设备的 ROS2 action。
    """

    def __init__(
        self,
        deck: Optional[Deck] = None,
        *args,
        **kwargs,
    ):
        super().__init__(deck=deck, *args, **kwargs)

        self.supported_workflows = {
            "electrochem_reaction": WorkflowInfo(
                name="electrochem_reaction",
                description="电化学反应工作流：移液加样 → 启动电反应 → 监测状态",
                estimated_duration=3600.0,
                required_materials=["反应液", "电解质溶液"],
                output_product="电化学反应产物",
                parameters_schema={
                    "channel": {"type": "integer", "description": "反应通道(1-6)", "minimum": 1, "maximum": 6},
                    "mode": {"type": "integer", "description": "模式(0=恒压,1=恒流)", "enum": [0, 1]},
                    "voltage": {"type": "number", "description": "电压(V)", "default": 0.0},
                    "current": {"type": "number", "description": "电流(mA)", "default": 0.0},
                    "stirrer_speed": {"type": "integer", "description": "搅拌转速(rpm)", "default": 500},
                    "duration": {"type": "number", "description": "反应时长(秒)", "default": 3600},
                },
            ),
        }

    # ============ 移液操作（代理到来宇移液站） ============

    def transfer_liquid(
        self,
        source: str,
        target: str,
        volume: float,
        **kwargs,
    ) -> Dict[str, Any]:
        """液体转移

        Args:
            source: 源位置（Deck 上的资源名称）
            target: 目标位置
            volume: 转移体积 (μL)

        Returns:
            操作结果
        """
        logger.info(f"电化学工作站: 液体转移 {source} → {target}, 体积={volume}μL")
        try:
            result = self.call_device_method(
                "add_liquid",
                reagent_sources=[source],
                targets=[target],
                dis_vols=[volume],
                **kwargs,
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"液体转移失败: {e}")
            return {"success": False, "error": str(e)}

    # ============ 电反应控制（代理到 GreenLab） ============

    def start_reaction(
        self,
        channel: int,
        mode: int = 0,
        voltage: float = 0.0,
        current: float = 0.0,
        stirrer_speed: int = 500,
    ) -> Dict[str, Any]:
        """启动电化学反应

        Args:
            channel: 通道号 (1-6)
            mode: 输出模式 (0=恒压, 1=恒流)
            voltage: 电压 (V)，恒压模式有效
            current: 电流 (mA)，恒流模式有效
            stirrer_speed: 搅拌转速 (rpm)，0 表示不搅拌

        Returns:
            操作结果
        """
        logger.info(
            f"电化学工作站: 启动反应 ch={channel}, mode={'恒压' if mode == 0 else '恒流'}, "
            f"V={voltage}, I={current}, stir={stirrer_speed}"
        )
        try:
            result = self.call_device_method(
                "start_reaction",
                channel=channel,
                mode=mode,
                voltage=voltage,
                current=current,
                stirrer_speed=stirrer_speed,
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"启动反应失败: {e}")
            return {"success": False, "error": str(e)}

    def stop_reaction(self, channel: int, stop_stirrer: bool = True) -> Dict[str, Any]:
        """停止电化学反应

        Args:
            channel: 通道号 (1-6)
            stop_stirrer: 是否同时停止搅拌
        """
        logger.info(f"电化学工作站: 停止反应 ch={channel}")
        try:
            result = self.call_device_method(
                "stop_reaction",
                channel=channel,
                stop_stirrer=stop_stirrer,
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"停止反应失败: {e}")
            return {"success": False, "error": str(e)}

    def get_channel_status(self, channel: int) -> Dict[str, Any]:
        """获取通道状态"""
        try:
            return self.call_device_method("get_channel_status", channel=channel)
        except Exception as e:
            logger.error(f"获取通道状态失败: {e}")
            return {"error": str(e)}

    def get_all_channels_status(self) -> List[Dict[str, Any]]:
        """获取所有通道状态"""
        try:
            return self.call_device_method("get_all_channels_status")
        except Exception as e:
            logger.error(f"获取所有通道状态失败: {e}")
            return []

    def emergency_stop(self) -> bool:
        """紧急停止所有通道"""
        logger.warning("电化学工作站: 紧急停止！")
        try:
            return self.call_device_method("emergency_stop")
        except Exception as e:
            logger.error(f"紧急停止失败: {e}")
            return False

    # ============ 工作流实现 ============

    def _execute_workflow_impl(self, workflow_name: str, parameters: Dict[str, Any]) -> bool:
        """执行工作流的具体实现"""
        if workflow_name == "electrochem_reaction":
            return self._run_electrochem_reaction(parameters)
        else:
            logger.error(f"不支持的工作流: {workflow_name}")
            return False

    def _stop_workflow_impl(self, emergency: bool = False) -> bool:
        """停止工作流的具体实现"""
        if emergency:
            return self.emergency_stop()

        try:
            for ch in range(1, 7):
                self.stop_reaction(ch, stop_stirrer=True)
            return True
        except Exception as e:
            logger.error(f"停止工作流失败: {e}")
            return False

    def _run_electrochem_reaction(self, params: Dict[str, Any]) -> bool:
        """运行电化学反应工作流"""
        channel = params.get("channel", 1)
        mode = params.get("mode", 0)
        voltage = params.get("voltage", 0.0)
        current = params.get("current", 0.0)
        stirrer_speed = params.get("stirrer_speed", 500)

        result = self.start_reaction(
            channel=channel,
            mode=mode,
            voltage=voltage,
            current=current,
            stirrer_speed=stirrer_speed,
        )
        return result.get("success", False)
