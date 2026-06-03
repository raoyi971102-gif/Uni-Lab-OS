"""
XUSE 厦大固态实验设备驱动
继承自 OPC UA 通讯基类，实现具体的设备动作函数
"""

import json
import time
import traceback
from typing import Optional
import os
import threading

# 导入日志类
from unilabos.utils.log import logger
import logging

# 导入通讯基类
from unilabos.devices.workstation.XUSE.base_opcua_client import OpcUaClientWithSubscription

# 导入常量定义
from unilabos.devices.workstation.XUSE.XUSE_CONSTS import RoboticArmTargetPosition_1, RoboticArmPickPlaceCode_1
from unilabos.devices.workstation.XUSE.XUSE_CONSTS import RoboticArmPickPlaceCode_2, RoboticArmTargetPosition_3
from unilabos.devices.workstation.XUSE.XUSE_CONSTS import RoboticArmPickPlaceCode_3
from unilabos.devices.workstation.XUSE.XUSE_CONSTS import OpenCanActionCode, SieveActionCode, ScrapePowderActionCode
from unilabos.devices.workstation.XUSE.XUSE_CONSTS import SmallCrucibleDischargePosition, LargeCrucibleFeedPosition

# 定义 XUSE 设备通信类
# 包含三个机械臂，一个罐架区，一个加珠区，一个开罐区，一个刮粉区，一个过筛区，一个加粉区，一个球磨区，一个马弗炉区，一个出料区
class XUSEDevice(OpcUaClientWithSubscription):
    """
    XUSE 设备类
    继承自 OpcUaClientWithSubscription，实现具体的设备动作函数
    """
    
    def __init__(
        self, 
        url: str, 
        csv_path: str = None, 
        username: str = None, 
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        """
        初始化 XUSE 设备
        
        参数:
            url: OPC UA 服务器地址
            csv_path: 节点配置 CSV 文件路径
            username: OPC UA 用户名
            password: OPC UA 密码
            use_subscription: 是否启用订阅模式
            cache_timeout: 缓存超时时间（秒）
            subscription_interval: 订阅发布间隔（毫秒）
        """
        # 调用父类构造函数
        super().__init__(
            url=url,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs
        )

        # 如果提供了 CSV 路径，则直接加载节点
        if csv_path:
            self.load_nodes_from_csv(csv_path)

    # 初始化工站
    def trigger_init(self, **kwargs) -> dict:
        """
        初始化函数（人工确认节点：云端确认通过后才会执行）：
        - 停止 3 个机械臂触发
        - 触发工站初始化
        - 等待初始化完成

        参数:
            **kwargs: 用于接收云端人工确认透传过来的 timeout_seconds、assignee_user_ids 等
                     字段（仅 UI / 后端使用，PLC 初始化逻辑本身用不到）

        Returns:
            dict: 包含 success 和 message
        """
        logger.info("停止机械臂触发...")
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)
        self.set_node_value("Robotic_Arm_Action_Trigger_3", False)

        logger.info("进行初始化...")
        self.set_node_value("Station_Initialize", True) 
        time.sleep(1.0)
        if self._wait_until_true("Station_Initialize_Complete", description="初始化工站"):
            logger.info("初始化工站成功")
            self.set_node_value("Station_Initialize", False)
            return {
                "success": True,
                "message": "初始化工站成功",
            }
        else:
            logger.error("初始化工站失败")
            self.set_node_value("Station_Initialize", False)
            raise ValueError("初始化工站失败")
    
    def is_robotic_arm_idle(self, arm_id: int) -> bool:
        """
        检查机械臂是否空闲
        
        参数:
            arm_id: 机械臂ID,1,2,3
        
        Returns:
            bool: 如果机械臂空闲，返回True，否则返回False
        """
        if arm_id not in [1, 2, 3]:
            raise ValueError("机械臂ID必须为1,2,3")
        return self.get_node_value(f"Robotic_Arm_Idle_{arm_id}")
    
    def is_open_can_upper_lid_occupied(self) -> bool:
        """
        检查开罐上盖是否占位
        
        Returns:
            bool: 如果开罐上盖占位，返回True，否则返回False
        """
        return self.get_node_value("Open_Can_Upper_Lid_Occupied")
    
    def is_open_can_body_occupied(self) -> bool:
        """
        检查开罐主体是否占位
        
        Returns:
            bool: 如果开罐主体占位，返回True，否则返回False
        """
        return self.get_node_value("Open_Can_Body_Occupied")
    
    def is_add_sample_occupied(self) -> bool:
        """
        检查加样是否占位
        
        Returns:
            bool: 如果加样占位，返回True，否则返回False
        """
        return self.get_node_value("Add_Sample_Occupied")
    
    def is_add_bead_occupied(self) -> bool:
        """
        检查加珠是否占位
        
        Returns:
            bool: 如果加珠占位，返回True，否则返回False
        """
        return self.get_node_value("Add_Bead_Occupied")
    
    def is_ball_mill_occupied(self, mill_position: int) -> bool:
        """
        检查球磨区是否占位
        
        参数:
            mill_position: 球磨区位置
        
        Returns:
            bool: 如果球磨区占位，返回True，否则返回False
        """
        return self.get_node_value(f"Ball_Mill_Occupied_{mill_position}")
    
    def is_sieve_can_occupied(self) -> bool:
        """
        检查过筛区球磨罐是否占位
        
        Returns:
            bool: 如果过筛区球磨罐占位，返回True，否则返回False
        """
        return self.get_node_value("Sieve_Can_Occupied")
    
    def is_sieve_crucible_occupied(self) -> bool:
        """
        检查过筛区小坩埚是否占位
        
        Returns:
            bool: 如果过筛区小坩埚占位，返回True，否则返回False
        """
        return self.get_node_value("Sieve_Crucible_Occupied")
    
    def is_sieve_funnel_occupied(self) -> bool:
        """
        检查过筛区漏斗是否占位
        
        Returns:
            bool: 如果过筛区漏斗占位，返回True，否则返回False
        """
        return self.get_node_value("Sieve_Funnel_Occupied")
    
    def is_scrape_occupied(self) -> bool:
        """
        检查刮粉区是否占位
        
        Returns:
            bool: 如果刮粉区占位，返回True，否则返回False
        """
        return self.get_node_value("Scrape_Powder_Occupied")
    
    def is_small_crucible_discharge_occupied(self, position: int) -> bool:
        """
        检查小坩埚出料位指定位置是否占位
        
        参数:
            position: 小坩埚出料位编号，1-4
        
        Returns:
            bool: 如果该位置占位，返回True，否则返回False
        """
        if position not in [1, 2, 3, 4]:
            raise ValueError(f"小坩埚出料位编号必须在 1-4 范围内，当前值: {position}")
        return self.get_node_value(f"Small_Crucible_Discharge_Occupied_{position}")
    
    def get_small_crucible_discharge_current_position(self) -> int:
        """
        获取小坩埚出料当前位置
        
        Returns:
            int: 小坩埚出料当前位置
        """
        return self.get_node_value("Small_Crucible_Discharge_Current_Position")
    
    def get_large_crucible_feed_current_position(self) -> int:
        """
        获取大坩埚入料当前位置
        
        Returns:
            int: 大坩埚入料当前位置
        """
        return self.get_node_value("Large_Crucible_Feed_Current_Position")
    
    def is_muffle_furnace_occupied(self, muffle_furnace_position: int) -> bool:
        """
        检查马弗炉是否占位
        
        参数:
            muffle_furnace_position: 马弗炉位置
        
        Returns:
            bool: 如果马弗炉占位，返回True，否则返回False
        """
        return self.get_node_value(f"Muffle_Furnace_Occupied_{muffle_furnace_position}")
    
    def is_upper_product_rack_occupied(self) -> bool:
        """
        检查上成品架是否占位
        
        Returns:
            bool: 如果上成品架占位，返回True，否则返回False
        """
        return self.get_node_value("Upper_Product_Rack_Occupied")
    
    def is_lower_product_rack_occupied(self) -> bool:
        """
        检查下成品架是否占位
        
        Returns:
            bool: 如果下成品架占位，返回True，否则返回False
        """
        return self.get_node_value("Lower_Product_Rack_Occupied")
    
    def pick_can_from_can_rack(self, rack_position: int) -> dict:
        """
        从罐架区取球磨罐
        - 检查机械臂1是否空闲
        - 设置从罐架位置rack_position处抓取球磨罐
        - 等待从罐架抓取球磨罐完成
        - 返回成功
        
        参数:
            rack_position: 罐架区位置
        
        Returns:
            dict: 包含 success 和 message
        """
        logger.info(f"从罐架区取球磨罐，位置：{rack_position}")
        MIN_RACK_POSITION = 1
        MAX_RACK_POSITION = RoboticArmPickPlaceCode_1.PICK_CAN_RACK_END - RoboticArmPickPlaceCode_1.PICK_CAN_RACK_START + 1
        if rack_position < MIN_RACK_POSITION or rack_position > MAX_RACK_POSITION:
            error_msg = "罐架位置错误"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")

        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.CAN_RACK_POSITION) # 设置机械臂目标位置为罐架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.PICK_CAN_RACK_START + rack_position - 1) # 设置罐架位置
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="从罐架抓取球磨罐完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="从罐架抓取球磨罐完成"): # 等待完成状态复位
                logger.info("从罐架区取球磨罐完成")
                return {
                    "success": True,
                    "message": "从罐架区取球磨罐完成",
                }
            else:
                error_msg = "从罐架区取球磨罐失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "从罐架区取球磨罐失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    def place_empty_can_to_open_can_position(self) -> dict:
        """
        将空罐放置到开盖区
        - 检查机械臂1是否空闲
        - 检查开罐是否占位
        - 设置将开盖罐磨罐放置到开盖区
        - 等待将开盖罐磨罐放置到开盖区完成
        - 返回成功
        """
        logger.info("将空球磨罐放置到开盖区...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")

        if self.is_open_can_upper_lid_occupied() or self.is_open_can_body_occupied():
            error_msg = "开罐上盖或主体占位，无法放置"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开盖区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.OPEN_CAN_NO_POWDER_PLACE_EMPTY_CAN) # 设置开盖区放空罐
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="将球磨罐放置到开盖区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="将球磨罐放置到开盖区完成"): # 等待完成状态复位
                logger.info("将球磨罐放置到开盖区完成")
                return {
                    "success": True,
                    "message": "将球磨罐放置到开盖区完成",
                }
            else:
                error_msg = "将球磨罐放置到开盖区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "将球磨罐放置到开盖区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    def open_can_lid(self) -> dict:
        """
        打开罐上盖
        - 检查罐体占位
        - 检查盖子非占位
        - 设置打开开罐上盖
        - 等待请求执行
        - 等待打开开罐上盖完成
        - 返回成功
        """
        logger.info("打开罐上盖...")
        if not self.is_open_can_body_occupied():
            error_msg = "开罐主体未占位，无法打开"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if self.is_open_can_upper_lid_occupied():
            error_msg = "开罐上盖已占位，无法打开"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self._wait_until_true("Open_Can_Request_Process", description="打开上盖请求"):
            logger.info("接收到打开上盖请求")
            self.set_node_value("Open_Can_Action_Control_Code", OpenCanActionCode.OPEN_CAN_LID) # 设置打开开罐上盖
            self.set_node_value("Open_Can_Start_Process", True) # 开始加工
            if self._wait_until_true("Open_Can_Process_Complete", description="打开上盖完成"):
                logger.info("打开上盖完成")
                self.set_node_value("Open_Can_Action_Control_Code", OpenCanActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Open_Can_Start_Process", False) # 复位加工
                return {
                    "success": True,
                    "message": "打开上盖完成",
                }
            else:
                logger.error("打开上盖失败，动作超时")
                self.set_node_value("Open_Can_Action_Control_Code", OpenCanActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Open_Can_Start_Process", False) # 复位加工
                raise ValueError("打开上盖失败，动作超时")
        else:
            error_msg = "打开上盖失败，未收到开盖请求"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        
    def pick_empty_can_from_open_can_position(self) -> dict:
        """
        从开盖区抓取空罐
        - 检查机械臂1是否空闲
        - 检查开罐是否占位
        - 设置从开盖区抓取球磨罐
        - 等待从开盖区抓取空罐完成
        - 返回成功
        """
        logger.info("从开盖区抓取空罐...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if not self.is_open_can_body_occupied():
            error_msg = "开罐主体未占位，无法抓取"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开盖区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.OPEN_CAN_NO_POWDER_PICK_BASE) # 设置开盖区取底座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="从开盖区抓取球磨罐完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="从开盖区抓取球磨罐完成"): # 等待完成状态复位
                logger.info("从开盖区抓取球磨罐完成")
                return {
                    "success": True,
                    "message": "从开盖区抓取球磨罐完成",
                }
            else:
                error_msg = "从开盖区抓取球磨罐失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "从开盖区抓取球磨罐失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def place_can_to_add_powder_position(self) -> dict:
        """
        将罐体放置到加粉区
        - 检查机械臂1是否空闲
        - 检查加样是否占位
        - 等待放置到加粉区完成
        - 返回成功
        """
        logger.info("将罐体放置到加粉区...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if self.is_add_sample_occupied():
            error_msg = "加样占位，无法放置"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.ADD_POWDER_POSITION) # 设置机械臂目标位置为加粉区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.ADD_POWDER_PLACE_BASE) # 设置加粉区放底座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="将罐体放置到加粉区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="将罐体放置到加粉区完成"): # 等待完成状态复位
                logger.info("将罐体放置到加粉区完成")
                return {
                    "success": True,
                    "message": "将罐体放置到加粉区完成",
                }
            else:
                error_msg = "将罐体放置到加粉区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "将罐体放置到加粉区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    def add_powder(self) -> dict:
        """
        加粉
        - 检查加样是否占位
        - 等待加粉完成
        - 返回成功
        """
        logger.info("加粉...")     
        if not self.is_add_sample_occupied():
            error_msg = "没有罐体，无法加粉"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info("to do: 有罐体，开始加粉...")

        if self._wait_until_true("Add_Sample_Request_Process", description="加样请求加工"):
            logger.info("接收到加样请求加工")
            self.set_node_value("Add_Sample_Start_Process", True) # 开始加工
            if self._wait_until_true("Add_Sample_Process_Complete", description="加样加工完成"):
                logger.info("加样加工完成")
                self.set_node_value("Add_Sample_Start_Process", False) # 复位加工
                return {
                    "success": True,
                    "message": "加样加工完成",
                }
            else:
                logger.error("加样加工失败，动作超时")
                self.set_node_value("Add_Sample_Start_Process", False) # 复位加工
                raise ValueError("加样加工失败，动作超时")
        else:
            error_msg = "加样失败，未收到加样请求"
            logger.error(error_msg)
            raise ValueError(error_msg)

    
    def pick_can_from_add_powder_position(self) -> dict:
        """
        从加粉区取罐体
        - 检查机械臂1是否空闲
        - 检查加样是否占位
        - 等待取罐体完成
        - 返回成功
        """
        logger.info("从加粉区取罐体...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if not self.is_add_sample_occupied():
            error_msg = "加样未占位，无法取罐体"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.ADD_POWDER_POSITION) # 设置机械臂目标位置为加粉区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.ADD_POWDER_PICK_BASE) # 设置加粉区取底座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="从加粉区取罐体完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="从加粉区取罐体完成"): # 等待完成状态复位
                logger.info("从加粉区取罐体完成")
                return {
                    "success": True,
                    "message": "从加粉区取罐体完成",
                }
            else:
                error_msg = "从加粉区取罐体失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "从加粉区取罐体失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_can_to_add_bead_position(self) -> dict:
        """
        将罐体放置到加珠区
        - 检查机械臂1是否空闲
        - 检查加珠是否占位
        - 等待放置到加珠区完成
        - 返回成功
        """
        logger.info("将罐体放置到加珠区...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if self.is_add_bead_occupied():
            error_msg = "加珠未占位，无法放置罐体"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.ADD_BEAD_POSITION) # 设置机械臂目标位置为加珠区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.ADD_BEAD_PLACE_BASE) # 设置加珠区放底座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="将罐体放置到加珠区成功"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="将罐体放置到加珠区成功"): # 等待完成状态复位
                logger.info("将罐体放置到加珠区成功")
                return {
                    "success": True,
                    "message": "将罐体放置到加珠区成功",
                }
            else:
                error_msg = "将罐体放置到加珠区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "将罐体放置到加珠区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def add_bead(self) -> dict:
        """
        进行加珠操作
        - 检查加珠是否占位
        - 等待加珠完成
        - 返回成功
        """
        logger.info("加珠...")
        if not self.is_add_bead_occupied():
            error_msg = "加珠未占位，无法加珠"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self._wait_until_true("Add_Bead_Request_Process", description="加珠请求加工"):
            logger.info("接收到加珠请求加工")
            self.set_node_value("Add_Bead_Start_Process", True) # 设置加珠开始
            if self._wait_until_true("Add_Bead_Process_Complete", description="等待加珠完成"):
                logger.info("加珠完成")
                self.set_node_value("Add_Bead_Start_Process", False) # 复位加珠开始
                return {
                    "success": True,
                    "message": "加珠完成",
                }
            else:
                logger.error("加珠失败")
                self.set_node_value("Add_Bead_Start_Process", False) # 复位加珠开始
                raise ValueError("加珠失败，完成复位超时")
        else:
            error_msg = "加珠失败，未收到加珠请求"
            logger.error(error_msg)
            raise ValueError(error_msg)

    
    def pick_can_from_add_bead_position(self) -> dict:
        """
        从加珠区取罐体
        - 检查机械臂1是否空闲
        - 检查加珠是否占位
        - 等待取罐体完成
        - 返回成功
        """
        logger.info("从加珠区取罐体...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if not self.is_add_bead_occupied():
            error_msg = "加珠未占位，无法取罐体"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.ADD_BEAD_POSITION) # 设置机械臂目标位置为加珠区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.ADD_BEAD_PICK_BASE) # 设置加珠区取底座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="从加珠区取罐体成功"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="从加珠区取罐体成功"): # 等待完成状态复位
                logger.info("从加珠区取罐体成功")
                return {
                    "success": True,
                    "message": "从加珠区取罐体成功",
                }
            else:
                error_msg = "从加珠区取罐体失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "从加珠区取罐体失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
    

    def place_can_with_powder_and_bead_to_open_can_position(self) -> dict:
        """
        将带有粉珠的球磨罐放置到开盖区
        - 检查机械臂1是否空闲
        - 检查开罐是否占位
        - 设置将开盖罐磨罐放置到开盖区
        - 等待将开盖罐磨罐放置到开盖区完成
        - 返回成功
        """
        logger.info("将带有粉珠的球磨罐放置到开盖区...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")

        if self.is_open_can_body_occupied():
            error_msg = "开罐主体占位，无法放置"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开盖区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.OPEN_CAN_WITH_POWDER_PLACE_BASE) # 设置开盖区放底座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="将球磨罐放置到开盖区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="将球磨罐放置到开盖区完成"): # 等待完成状态复位
                logger.info("将球磨罐放置到开盖区完成")
                return {
                    "success": True,
                    "message": "将球磨罐放置到开盖区完成",
                }
            else:
                error_msg = "将球磨罐放置到开盖区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "将球磨罐放置到开盖区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def close_can_lid(self) -> dict:
        """
        关闭罐上盖
        - 检查罐体占位
        - 检查盖子占位
        - 设置关闭罐上盖
        - 等待请求执行
        - 等待关闭罐上盖完成
        - 返回成功
        """
        logger.info("关闭罐上盖...")
        if not self.is_open_can_body_occupied():
            error_msg = "开罐主体未占位，无法关盖"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if not self.is_open_can_upper_lid_occupied():
            error_msg = "开罐上盖未占位，无法关盖"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self._wait_until_true("Open_Can_Request_Process", description="关闭上盖请求"):
            logger.info("接收到关闭上盖请求")
            self.set_node_value("Open_Can_Action_Control_Code", OpenCanActionCode.CLOSE_CAN_LID) # 设置关闭罐上盖
            self.set_node_value("Open_Can_Start_Process", True) # 开始加工
            if self._wait_until_true("Open_Can_Process_Complete", description="关闭上盖完成"):
                logger.info("关闭上盖完成")
                self.set_node_value("Open_Can_Action_Control_Code", OpenCanActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Open_Can_Start_Process", False) # 复位加工
                return {
                    "success": True,
                    "message": "关闭上盖完成",
                }
            else:
                logger.error("关闭上盖失败")
                self.set_node_value("Open_Can_Action_Control_Code", OpenCanActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Open_Can_Start_Process", False) # 复位加工
                raise ValueError("关闭上盖失败，完成复位超时")
        else:
            error_msg = "关闭上盖失败，未收到关盖请求"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def pick_can_with_powder_and_bead_from_open_can_position(self) -> dict:
        """
        从开盖区抓取带有粉珠的球磨罐
        - 检查机械臂1是否空闲
        - 检查开罐是否占位
        - 设置从开盖区抓取球磨罐
        - 等待从开盖区抓取空罐完成
        - 返回成功
        """
        logger.info("从开盖区抓取带有粉珠的球磨罐...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if not self.is_open_can_body_occupied():
            error_msg = "开罐主体未占位，无法抓取"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开盖区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.OPEN_CAN_WITH_POWDER_PICK_FULL_CAN) # 设置开盖区取满罐
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description="从开盖区抓取球磨罐完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description="从开盖区抓取球磨罐完成"): # 等待完成状态复位
                logger.info("从开盖区抓取球磨罐完成")
                return {
                    "success": True,
                    "message": "从开盖区抓取球磨罐完成",
                }
            else:
                error_msg = "从开盖区抓取球磨罐失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "从开盖区抓取球磨罐失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def place_can_to_ball_mill(self, mill_position: int) -> dict:
        """
        将罐体放置到球磨区
        - 检查机械臂1是否空闲
        - 检查球磨区是否占位
        - 设置将开盖罐磨罐放置到球磨区
        - 等待放置罐体完成
        - 返回成功
        """
        logger.info(f"将开盖罐磨罐放置到球磨区{mill_position}...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"球磨区位置{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self.is_ball_mill_occupied(mill_position):
            error_msg = f"球磨区{mill_position}占位，无法放置"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        mill_position_code = RoboticArmPickPlaceCode_1.BALL_MILL_PLACE_CAN_1 + (mill_position - 1)
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.BALL_MILL_POSITION) # 设置机械臂目标位置为球磨区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", mill_position_code) # 设置球磨区放罐
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"向球磨区{mill_position}放罐完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"向球磨区{mill_position}放罐完成"): # 等待完成状态复位
                logger.info(f"向球磨区{mill_position}放罐完成")
                return {
                    "success": True,
                    "message": f"向球磨区{mill_position}放罐完成",
                }
            else:
                error_msg = f"向球磨区{mill_position}放罐失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"向球磨区{mill_position}放罐失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def ball_mill(self) -> dict:
        """
        进行球磨
        - 检测球磨区4个位置是否都有球磨罐
        - 启动球磨
        - 等待球磨完成
        - 返回成功
        """   
        for mill_position in [1, 2, 3, 4]:
            if not self.is_ball_mill_occupied(mill_position):
                error_msg = f"球磨区位置{mill_position}为空，无法球磨"
                logger.error(error_msg)
                raise ValueError(error_msg)

        if self._wait_until_true("Ball_Mill_Request_Process", description="球磨请求加工"):
            logger.info("收到球磨请求加工")
            self.set_node_value("Ball_Mill_Start_Process", True) # 设置球磨开始
            if self._wait_until_true("Ball_Mill_Process_Complete", description="等待球磨完成"):
                logger.info("球磨完成")
                self.set_node_value("Ball_Mill_Start_Process", False) # 复位球磨开始
                return {
                    "success": True,
                    "message": "球磨完成",
                }
            else:
                self.set_node_value("Ball_Mill_Start_Process", False) # 复位球磨开始
                error_msg = "球磨失败，操作超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = "球磨加工失败，未收到加工请求"
            logger.error(error_msg)
            raise ValueError(error_msg)
    
    
    def pick_can_from_ball_mill(self, mill_position: int) -> dict:
        """
        从球磨区抓取罐体
        - 检查机械臂1是否空闲
        - 检查球磨区是否为空
        - 设置从球磨区抓取罐体
        - 等待抓取罐体完成
        - 返回成功
        """
        logger.info(f"从球磨区{mill_position}抓取罐体...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"球磨区位置{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if not self.is_ball_mill_occupied(mill_position):
            error_msg = f"球磨区位置{mill_position}为空，无法抓取"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        mill_position_code = RoboticArmPickPlaceCode_1.BALL_MILL_PICK_CAN_1 + (mill_position - 1)
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.BALL_MILL_POSITION) # 设置机械臂目标位置为球磨区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", mill_position_code) # 设置球磨区抓取罐 
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"从球磨区位置{mill_position}抓取罐完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"从球磨区位置{mill_position}抓取罐完成"): # 等待完成状态复位
                logger.info(f"从球磨区位置{mill_position}抓取罐完成")
                return {
                    "success": True,
                    "message": f"从球磨区位置{mill_position}抓取罐完成",
                }
            else:
                error_msg = f"从球磨区位置{mill_position}抓取罐失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"从球磨区位置{mill_position}抓取罐失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)

    
    def place_milled_can_to_open_can_position(self, mill_position: int) -> dict:
        """
        将研磨后球磨罐放到开盖区
        - 检查机械臂1是否空闲
        - 检查开盖区是否为空
        - 设置将球磨罐体放罐到开盖区
        - 等待放罐完成
        - 返回成功
        """
        logger.info(f"将研磨后球磨罐{mill_position}放到开盖区...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self.is_open_can_body_occupied():
            error_msg = "开罐主体占位，无法放置"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        pick_place_code = RoboticArmPickPlaceCode_1.OPEN_CAN_AFTER_MILL_PLACE_CAN_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开盖区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置开盖区放罐    
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}放到开盖区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}放到开盖区完成"): # 等待完成状态复位
                logger.info(f"将研磨后球磨罐{mill_position}放到开盖区完成")
                return {
                    "success": True,
                    "message": f"将研磨后球磨罐{mill_position}放到开盖区完成",
                }
            else:
                error_msg = f"将研磨后球磨罐{mill_position}放到开盖区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将研磨后球磨罐{mill_position}放到开盖区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def pick_milled_can_from_open_can_position(self, mill_position: int) -> dict:
        """
        从开盖区抓取研磨后球磨罐
        - 检查机械臂1是否空闲
        - 检查开盖区是否为空
        - 设置将研磨后球磨罐从开盖区位置抓取
        - 等待抓取罐完成
        - 返回成功
        """
        logger.info(f"将研磨后球磨罐{mill_position}从开盖区位置抓取...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if not self.is_open_can_body_occupied():
            error_msg = "开罐主体未占位，无法抓取"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        pick_place_code = RoboticArmPickPlaceCode_1.OPEN_CAN_AFTER_MILL_PICK_BASE_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开盖区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置开盖区取座    
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}从开盖区位置抓取完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}从开盖区位置抓取完成"): # 等待完成状态复位
                logger.info(f"将研磨后球磨罐{mill_position}从开盖区位置抓取完成")
                return {
                    "success": True,
                    "message": f"将研磨后球磨罐{mill_position}从开盖区位置抓取完成",
                }
            else:
                error_msg = f"将研磨后球磨罐{mill_position}从开盖区位置抓取失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将研磨后球磨罐{mill_position}从开盖区位置抓取失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_milled_can_to_sieve_position(self, mill_position: int) -> dict:
        """
        将研磨后球磨罐放到过筛区
        - 检查机械臂1是否空闲
        - 检查过筛区是否为空
        - 设置将研磨后球磨罐放到过筛区位置
        - 等待放到过筛区位置完成
        - 返回成功
        """
        logger.info(f"将研磨后球磨罐{mill_position}放到过筛区...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self.is_sieve_can_occupied():
            error_msg = "过筛区球磨罐占位，无法放罐"
            logger.error(error_msg)
            raise ValueError(error_msg)

        pick_place_code = RoboticArmPickPlaceCode_1.SIEVE_PLACE_BASE_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.SIEVE_POSITION) # 设置机械臂目标位置为过筛区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置过筛区放座    
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}放到过筛区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}放到过筛区完成"): # 等待完成状态复位
                logger.info(f"将研磨后球磨罐{mill_position}放到过筛区完成")
                return {
                    "success": True,
                    "message": f"将研磨后球磨罐{mill_position}放到过筛区完成",
                }
            else:
                error_msg = f"将研磨后球磨罐{mill_position}放到过筛区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将研磨后球磨罐{mill_position}放到过筛区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def sieve(self) -> dict:
        """
        过筛
        - 检查过筛占位
        - 设置过筛动作代码
        - 等待请求执行
        - 等待过筛完成
        - 返回成功
        """
        logger.info("过筛...")
        if not self.is_sieve_can_occupied():
            error_msg = "过筛区球磨罐没有占位，无法过筛"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if self._wait_until_true("Sieve_Request_Process", description="过筛请求"):
            logger.info("接收到过筛请求")
            self.set_node_value("Sieve_Action_Control_Code", SieveActionCode.SIEVE) # 设置过筛动作代码
            self.set_node_value("Sieve_Start_Process", True) # 设置过筛开始
            if self._wait_until_true("Sieve_Process_Complete", description="过筛完成"):
                logger.info("过筛完成")
                self.set_node_value("Sieve_Action_Control_Code", SieveActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Sieve_Start_Process", False) # 复位过筛开始
                return {
                    "success": True,
                    "message": "过筛完成",
                }
            else:
                logger.error("过筛失败，操作未完成")
                self.set_node_value("Sieve_Action_Control_Code", SieveActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Sieve_Start_Process", False) # 复位过筛开始
                raise ValueError("过筛失败，操作未完成")
        else:
            error_msg = "过筛失败，未收到过筛请求"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def pick_milled_can_from_sieve_position(self, mill_position: int) -> dict:
        """
        从过筛区抓取研磨后球磨罐
        - 检查机械臂1是否空闲
        - 检查过筛区是否占位
        - 设置将研磨后球磨罐从过筛区位置抓取
        - 等待从过筛区位置抓取完成
        - 返回成功
        """
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if not self.is_sieve_can_occupied():
            error_msg = "过筛区球磨罐没有占位，无法从过筛区抓取球磨罐"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        pick_place_code = RoboticArmPickPlaceCode_1.SIEVE_PICK_BASE_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.SIEVE_POSITION) # 设置机械臂目标位置为过筛区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置过筛区取座       
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"从过筛区抓取研磨后球磨罐{mill_position}完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"从过筛区抓取研磨后球磨罐{mill_position}完成"): # 等待完成状态复位
                logger.info(f"从过筛区抓取研磨后球磨罐{mill_position}完成")
                return {
                    "success": True,
                    "message": f"从过筛区抓取研磨后球磨罐{mill_position}完成",
                }
            else:
                error_msg = f"从过筛区抓取研磨后球磨罐{mill_position}失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"从过筛区抓取研磨后球磨罐{mill_position}失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_milled_can_to_scrape_position(self, mill_position: int) -> dict:
        """
        将研磨后球磨罐放到刮粉区
        - 检查机械臂1是否空闲
        - 检查刮粉区是否占位
        - 设置将研磨后球磨罐放到刮粉区
        - 等待放到刮粉区完成
        - 返回成功
        """
        logger.info(f"将研磨后球磨罐{mill_position}放到刮粉区...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self.is_scrape_occupied():
            error_msg = "刮粉区占位，无法放罐"
            logger.error(error_msg)
            raise ValueError(error_msg)

        pick_place_code = RoboticArmPickPlaceCode_1.SCRAPE_POWDER_PLACE_BASE_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.SCRAPE_POWDER_POSITION) # 设置机械臂目标位置为刮粉区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置刮粉区放座    
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}放到刮粉区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}放到刮粉区完成"): # 等待完成状态复位
                logger.info(f"将研磨后球磨罐{mill_position}放到刮粉区完成")
                return {
                    "success": True,
                    "message": f"将研磨后球磨罐{mill_position}放到刮粉区完成",
                }
            else:
                error_msg = f"将研磨后球磨罐{mill_position}放到刮粉失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将研磨后球磨罐{mill_position}放到刮粉区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def scrape_powder(self) -> dict:
        """
        刮粉
        - 检查刮粉区占位
        - 设置刮粉区动作代码
        - 等待请求执行
        - 等待刮粉区完成
        - 返回成功
        """
        logger.info("刮粉区...")
        if not self.is_scrape_occupied():
            error_msg = "刮粉区没有占位，无法刮粉"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if self._wait_until_true("Scrape_Powder_Request_Process", description="刮粉请求"):
            logger.info("接收到刮粉请求")
            self.set_node_value("Scrape_Powder_Action_Control_Code", ScrapePowderActionCode.SCRAPE_POWDER) # 设置刮粉区动作代码
            self.set_node_value("Scrape_Powder_Start_Process", True) # 设置刮粉开始
            if self._wait_until_true("Scrape_Powder_Process_Complete", description="刮粉完成"):
                logger.info("刮粉完成")
                self.set_node_value("Scrape_Powder_Action_Control_Code", ScrapePowderActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Scrape_Powder_Start_Process", False) # 复位刮粉开始
                return {
                    "success": True,
                    "message": "刮粉完成",
                }
            else:
                logger.error("刮粉失败，操作未完成")
                self.set_node_value("Scrape_Powder_Action_Control_Code", ScrapePowderActionCode.NO_ACTION) # 复位动作
                self.set_node_value("Scrape_Powder_Start_Process", False) # 复位刮粉开始
                raise ValueError("刮粉失败，操作未完成")
        else:
            error_msg = "刮粉失败，未收到刮粉请求"
            logger.error(error_msg)
            raise ValueError(error_msg)
        

    def pick_milled_can_from_scrape_position(self, mill_position: int) -> dict:
        """
        从刮粉区取下研磨后球磨罐
        - 检查机械臂1是否空闲
        - 检查刮粉区是否占位
        - 设置将研磨后球磨罐从刮粉区取下
        - 等待从刮粉区取下完成
        - 返回成功
        """
        logger.info(f"从刮粉区位置取下研磨后球磨罐{mill_position}...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if not self.is_scrape_occupied():
            error_msg = "刮粉区没有占位，无法取下"
            logger.error(error_msg)
            raise ValueError(error_msg)

        pick_place_code = RoboticArmPickPlaceCode_1.SCRAPE_POWDER_PICK_BASE_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.SCRAPE_POWDER_POSITION) # 设置机械臂目标位置为刮粉区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置刮粉区取座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}从刮粉区取下完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将研磨后球磨罐{mill_position}从刮粉区取下完成"): # 等待完成状态复位
                logger.info(f"将研磨后球磨罐{mill_position}从刮粉区取下完成")
                return {
                    "success": True,
                    "message": f"将研磨后球磨罐{mill_position}从刮粉区取下完成",
                }
            else:
                error_msg = f"将研磨后球磨罐{mill_position}从刮粉区取下失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将研磨后球磨罐{mill_position}从刮粉区取下失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def place_sieved_can_to_open_can_position(self, mill_position: int) -> dict:
        """
        将过筛后球磨罐放到开罐区位置
        - 检查机械臂1是否空闲
        - 检查过筛区是否占位
        - 设置将过筛后球磨罐放到开罐区位置
        - 等待放到开罐区位置完成
        - 返回成功
        """
        logger.info(f"将过筛后球磨罐{mill_position}放到开罐区位置...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self.is_open_can_body_occupied():
            error_msg = "开罐区占位，无法放罐"
            logger.error(error_msg)
            raise ValueError(error_msg)

        pick_place_code = RoboticArmPickPlaceCode_1.OPEN_CAN_AFTER_SIEVE_PLACE_BASE_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开罐区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置开罐区放座    
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将过筛后球磨罐{mill_position}放到开罐区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将过筛后球磨罐{mill_position}放到开罐区完成"): # 等待完成状态复位
                logger.info(f"将过筛后球磨罐{mill_position}放到开罐区完成")
                return {
                    "success": True,
                    "message": f"将过筛后球磨罐{mill_position}放到开罐区完成",
                }
            else:
                error_msg = f"将过筛后球磨罐{mill_position}放到开罐区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将过筛后球磨罐{mill_position}放到开罐区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def pick_sieved_can_from_open_can_position(self, mill_position: int) -> dict:
        """
        将过筛后球磨罐从开罐区位置取下
        - 检查机械臂1是否空闲
        - 检查开罐区是否占位
        - 设置将过筛后球磨罐从开罐区位置取下
        - 等待从开罐区位置取下完成
        - 返回成功
        """
        logger.info(f"将过筛后球磨罐{mill_position}从开罐区位置取下...")
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")
        
        if mill_position not in [1, 2, 3, 4]:
            error_msg = f"研磨后球磨罐编号{mill_position}无效"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if not self.is_open_can_body_occupied():
            error_msg = "开罐区没有占位，无法取下"
            logger.error(error_msg)
            raise ValueError(error_msg)

        pick_place_code = RoboticArmPickPlaceCode_1.OPEN_CAN_AFTER_SIEVE_PICK_CAN_1 + (mill_position - 1) * 10
        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.OPEN_CAN_POSITION) # 设置机械臂目标位置为开罐区
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", pick_place_code) # 设置开罐区取座
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将过筛后球磨罐{mill_position}从开罐区取下完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将过筛后球磨罐{mill_position}从开罐区取下完成"): # 等待完成状态复位
                logger.info(f"将过筛后球磨罐{mill_position}从开罐区取下完成")
                return {
                    "success": True,
                    "message": f"将过筛后球磨罐{mill_position}从开罐区取下完成",
                }
            else:
                error_msg = f"将过筛后球磨罐{mill_position}从开罐区取下失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将过筛后球磨罐{mill_position}从开罐区取下失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def place_can_to_can_rack(self, rack_position: int) -> dict:
        """
        将球磨罐放到罐架区
        - 检查机械臂1是否空闲
        - 设置将球磨罐放到罐架位置
        - 等待将球磨罐放到罐架位置完成
        - 返回成功
        
        参数:
            rack_position: 罐架区位置
        
        Returns:
            dict: 包含 success 和 message
        """
        logger.info(f"放到罐架区放，位置：{rack_position}")
        MIN_RACK_POSITION = 1
        MAX_RACK_POSITION = RoboticArmPickPlaceCode_1.PLACE_CAN_RACK_END - RoboticArmPickPlaceCode_1.PLACE_CAN_RACK_START + 1
        if rack_position < MIN_RACK_POSITION or rack_position > MAX_RACK_POSITION:
            error_msg = "罐架位置错误"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self._wait_until_true("Robotic_Arm_Idle_1", description="等待机械臂1空闲")

        self.set_node_value("Robotic_Arm_Target_Position_Code_1", RoboticArmTargetPosition_1.CAN_RACK_POSITION) # 设置机械臂目标位置为罐架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_1", RoboticArmPickPlaceCode_1.PLACE_CAN_RACK_START + rack_position - 1) # 设置罐架位置
        self.set_node_value("Robotic_Arm_Action_Trigger_1", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_1", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_1", description=f"将球磨罐放到罐架位置{rack_position}完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_1", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_1", description=f"将球磨罐放到罐架位置{rack_position}完成"): # 等待完成状态复位
                logger.info(f"将球磨罐放到罐架位置{rack_position}完成")
                return {
                    "success": True,
                    "message": f"将球磨罐放到罐架位置{rack_position}完成",
                }
            else:
                error_msg = f"将球磨罐放到罐架位置{rack_position}失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将球磨罐放到罐架位置{rack_position}失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def pick_small_crucible_from_crucible_rack(self, rack_position: int) -> dict:
        """
        从坩锅架区取小坩埚
        - 检查机械臂2是否空闲
        - 设置取小坩埚位置
        - 等待取小坩埚位置完成
        - 返回成功
        """
        logger.info(f"从坩埚架区取小坩埚，位置：{rack_position}")
        MIN_RACK_POSITION = 1
        MAX_RACK_POSITION = RoboticArmPickPlaceCode_2.PICK_CRUCIBLE_RACK_END - RoboticArmPickPlaceCode_2.PICK_CRUCIBLE_RACK_START + 1
        if rack_position < MIN_RACK_POSITION or rack_position > MAX_RACK_POSITION:
            error_msg = "坩埚位置错误"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PICK_CRUCIBLE_RACK_START + rack_position - 1) # 设置坩埚位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"取小坩埚位置{rack_position}完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"取小坩埚位置{rack_position}完成"): # 等待完成状态复位
                logger.info(f"取小坩埚位置{rack_position}完成")
                return {
                    "success": True,
                    "message": f"取小坩埚位置{rack_position}完成",
                }
            else:
                error_msg = f"取小坩埚位置{rack_position}失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"取小坩埚位置{rack_position}失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def place_small_crucible_to_sieve_position(self) -> dict:
        """
        将小坩埚放到过筛区
        - 检查机械臂2是否空闲
        - 检查过筛是否占位
        - 设置放小坩埚位置
        - 等待放小坩锅位置完成
        - 返回成功
        """
        logger.info(f"将小坩埚放到过筛区")
        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        if self.is_sieve_crucible_occupied():
            error_msg = "过筛区小坩锅已占位"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PLACE_SIEVE_CRUCIBLE) # 设置过筛区位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"放小坩埚到过筛区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"放小坩埚到过筛区完成"): # 等待完成状态复位
                logger.info(f"放小坩锅到过筛区完成")
                return {
                    "success": True,
                    "message": f"放小坩埚到过筛区完成",
                }
            else:
                error_msg = f"放小坩埚到过筛区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"放小坩埚到过筛区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def pick_funnel_from_crucible_rack(self, rack_position: int) -> dict:
        """
        从漏斗架区取漏斗
        - 检查机械臂2是否空闲
        - 设置取漏斗位置
        - 等待取漏斗完成
        - 返回成功
        """
        logger.info(f"从漏斗架区取漏斗，位置：{rack_position}")
        MIN_RACK_POSITION = 1
        MAX_RACK_POSITION = RoboticArmPickPlaceCode_2.PICK_FUNNEL_RACK_END - RoboticArmPickPlaceCode_2.PICK_FUNNEL_RACK_START + 1
        if rack_position < MIN_RACK_POSITION or rack_position > MAX_RACK_POSITION:
            error_msg = "漏斗架位置错误"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PICK_FUNNEL_RACK_START + rack_position - 1) # 设置漏斗架位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"取漏斗位置{rack_position}完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"取漏斗位置{rack_position}完成"): # 等待完成状态复位
                logger.info(f"取漏斗位置{rack_position}完成")
                return {
                    "success": True,
                    "message": f"取漏斗位置{rack_position}完成",
                }
            else:
                error_msg = f"取漏斗位置{rack_position}失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"取漏斗位置{rack_position}失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_funnel_to_sieve_position(self) -> dict:
        """
        将漏斗放到过筛区
        - 检查机械臂2是否空闲
        - 检查过筛是否占位
        - 设置放漏斗位置
        - 等待放漏斗位置完成
        - 返回成功
        """
        logger.info(f"将漏斗放到过筛区")
        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        if self.is_sieve_funnel_occupied():
            error_msg = "过筛区漏斗已占位"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PLACE_SIEVE_FUNNEL) # 设置过筛区位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"放漏斗到过筛区完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"放漏斗到过筛区完成"): # 等待完成状态复位
                logger.info(f"放漏斗到过筛区完成")
                return {
                    "success": True,
                    "message": f"放漏斗到过筛区完成",
                }
            else:
                error_msg = f"放漏斗到过筛区失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"放漏斗到过筛区失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def pick_small_crucible_from_sieve_position(self) -> dict:
        """
        将小坩埚从过筛区取出
        - 检查机械臂2是否空闲
        - 检查过筛是否占位
        - 设置放小坩埚位置
        - 等待放小坩锅位置完成
        - 返回成功
        """
        logger.info(f"将小坩埚从过筛区取出")
        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        if not self.is_sieve_crucible_occupied():
            error_msg = "过筛区小坩埚未占位"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PICK_SIEVE_CRUCIBLE) # 设置过筛区位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"从过筛区取小坩埚完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"从过筛区取小坩埚完成"): # 等待完成状态复位
                logger.info(f"从过筛区取小坩埚完成")
                return {
                    "success": True,
                    "message": f"从过筛区取小坩埚完成",
                }
            else:
                error_msg = f"从过筛区取小坩锅失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"从过筛区取小坩锅失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_small_crucible_to_moving_position(self, moving_position: int)  -> dict:
        """
        将小坩锅放到搬运位置
        - 检查机械臂2是否空闲
        - 检查移动位置是否在放料位
        - 设置放小坩埚位置
        - 等待放小坩埚位置完成
        - 返回成功
        """
        logger.info(f"将小坩埚放到搬运位置 {moving_position}")

        MIN_MOVING_POSITION = 1
        MAX_MOVING_POSITION = RoboticArmPickPlaceCode_2.PLACE_SMALL_CRUCIBLE_4 - RoboticArmPickPlaceCode_2.PLACE_SMALL_CRUCIBLE_1 + 1

        if not (MIN_MOVING_POSITION <= moving_position <= MAX_MOVING_POSITION):
            error_msg = f"搬运位置 {moving_position} 超出范围"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        if self.get_small_crucible_discharge_current_position() != SmallCrucibleDischargePosition.FEEDING:
            error_msg = f"当前小坩锅搬运位置不是放料位，无法放到搬运位置"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PLACE_SMALL_CRUCIBLE_1 + moving_position - 1) # 设置搬运位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"将小坩锅放到搬运位置 {moving_position} 完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"将小坩锅放到搬运位置 {moving_position} 完成"): # 等待完成状态复位
                logger.info(f"将小坩锅放到搬运位置 {moving_position} 完成")
                return {
                    "success": True,
                    "message": f"将小坩锅放到搬运位置 {moving_position} 完成",
                }
            else:
                error_msg = f"将小坩锅放到搬运位置 {moving_position} 失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"将小坩锅放到搬运位置 {moving_position} 失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def pick_funnel_from_sieve_position(self) -> dict:
        """
        将漏斗从过筛区取出
        - 检查机械臂2是否空闲
        - 检查过筛是否占位
        - 设置放漏斗位置
        - 等待放漏斗位置完成
        - 返回成功
        """
        logger.info(f"将漏斗从过筛区取出")
        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        if not self.is_sieve_funnel_occupied():
            error_msg = "过筛区漏斗未占位"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PICK_SIEVE_FUNNEL) # 设置过筛区位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"从过筛区取漏斗完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"从过筛区取漏斗完成"): # 等待完成状态复位
                logger.info(f"从过筛区取漏斗完成")
                return {
                    "success": True,
                    "message": f"从过筛区取漏斗完成",
                }
            else:
                error_msg = f"从过筛区取漏斗失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"从过筛区取漏斗失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_funnel_to_crucible_rack(self, rack_position: int) -> dict:
        """
        将漏斗放到漏斗架
        - 检查机械臂2是否空闲
        - 设置取漏斗位置
        - 等待取漏斗完成
        - 返回成功
        """
        logger.info(f"将漏斗放到漏斗架，位置：{rack_position}")
        MIN_RACK_POSITION = 1
        MAX_RACK_POSITION = RoboticArmPickPlaceCode_2.PLACE_FUNNEL_RACK_END - RoboticArmPickPlaceCode_2.PLACE_FUNNEL_RACK_START + 1
        if rack_position < MIN_RACK_POSITION or rack_position > MAX_RACK_POSITION:
            error_msg = "漏斗架位置错误"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self._wait_until_true("Robotic_Arm_Idle_2", description="等待机械臂2空闲")
        
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_2", RoboticArmPickPlaceCode_2.PLACE_FUNNEL_RACK_START + rack_position - 1) # 设置漏斗架位置
        self.set_node_value("Robotic_Arm_Action_Trigger_2", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_2", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_2", description=f"放漏斗位置{rack_position}完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_2", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_2", description=f"放漏斗位置{rack_position}完成"): # 等待完成状态复位
                logger.info(f"放漏斗位置{rack_position}完成")
                return {
                    "success": True,
                    "message": f"放漏斗位置{rack_position}完成",
                }
            else:
                error_msg = f"放漏斗位置{rack_position}失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"放漏斗位置{rack_position}失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        

    def small_crucible_discharge(self) -> dict:
        """
        小坩锅出料
        - 检查搬运位置在放料位
        - 检查 4 个出料占位都为 True
        - 设置出料操作
        - 等待出料完成
        - 返回成功
        """
        logger.info("小坩埚出料")
        if self.get_small_crucible_discharge_current_position() != SmallCrucibleDischargePosition.FEEDING:
            error_msg = f"当前小坩埚搬运位置不是放料位，无法出料"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        unoccupied = [i for i in (1, 2, 3, 4) if not self.is_small_crucible_discharge_occupied(i)]
        if unoccupied:
            error_msg = f"小坩埚出料占位 {unoccupied} 未占位，无法出料"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Small_Crucible_Discharge_Target_Position_Code", SmallCrucibleDischargePosition.DISCHARGE) # 设置出料位置
        self.set_node_value("Small_Crucible_Discharge_Action_Trigger", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Small_Crucible_Discharge_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Small_Crucible_Discharge_Action_Complete", description=f"小坩埚出料完成"):
            self.set_node_value("Small_Crucible_Discharge_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Small_Crucible_Discharge_Action_Complete", description=f"小坩埚出料完成"): # 等待完成状态复位
                logger.info(f"小坩埚出料完成")
                return {
                    "success": True,
                    "message": f"小坩埚出料完成",
                }
            else:
                error_msg = f"小坩埚出料失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"小坩埚出料失败，操作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def small_crucible_feed(self) -> dict:
        """
        小坩锅上料
        - 设置上料操作
        - 等待上料完成
        - 返回成功
        """
        logger.info("小坩埚上料")
        if self.get_small_crucible_discharge_current_position() != SmallCrucibleDischargePosition.DISCHARGE:
            error_msg = f"当前小坩锅搬运位置不是出料位，无法上料"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Small_Crucible_Discharge_Target_Position_Code", SmallCrucibleDischargePosition.FEEDING) # 设置上料位置
        self.set_node_value("Small_Crucible_Discharge_Action_Trigger", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Small_Crucible_Discharge_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Small_Crucible_Discharge_Action_Complete", description=f"小坩埚上料完成"):
            self.set_node_value("Small_Crucible_Discharge_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Small_Crucible_Discharge_Action_Complete", description=f"小坩埚上料完成"): # 等待完成状态复位
                logger.info(f"小坩埚上料完成，完成复位超时")
                return {
                    "success": True,
                    "message": f"小坩埚出料完成",
                }
            else:
                error_msg = f"小坩埚上料失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"小坩埚上料失败，操作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def large_crucible_discharge(self) -> dict:
        """
        大坩锅搬运位出料
        - 设置出料操作
        - 等待出料完成
        - 返回成功
        """
        logger.info("大坩埚出料")
        if self.get_large_crucible_feed_current_position() != LargeCrucibleFeedPosition.PICKING:
            error_msg = "当前大坩埚搬运位置不是取料位，无法出料"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Large_Crucible_Feed_Target_Position_Code", LargeCrucibleFeedPosition.FEEDING) # 设置出料位置
        self.set_node_value("Large_Crucible_Feed_Action_Trigger", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Large_Crucible_Feed_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Large_Crucible_Feed_Action_Complete", description=f"大坩埚出料完成"):
            self.set_node_value("Large_Crucible_Feed_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Large_Crucible_Feed_Action_Complete", description=f"大坩锅出料完成"): # 等待完成状态复位
                logger.info(f"大坩锅出料完成")
                return {
                    "success": True,
                    "message": f"大坩埚出料完成",
                }
            else:
                error_msg = f"大坩埚出料失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"大坩锅出料失败，操作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def large_crucible_feed(self) -> dict:
        """
        大坩锅搬运位置上料
        - 设置上料操作
        - 等待上料完成
        - 返回成功
        """
        logger.info("大坩埚上料")
        if self.get_large_crucible_feed_current_position() != LargeCrucibleFeedPosition.FEEDING:
            error_msg = f"当前大坩锅搬运位置不是入料位，无法上料"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Large_Crucible_Feed_Target_Position_Code", LargeCrucibleFeedPosition.PICKING) # 设置取料位置
        self.set_node_value("Large_Crucible_Feed_Action_Trigger", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Large_Crucible_Feed_Action_Trigger", True) # 设置动作触发
        if self._wait_until_true("Large_Crucible_Feed_Action_Complete", description=f"大坩锅上料完成"):
            self.set_node_value("Large_Crucible_Feed_Action_Trigger", False) # 复位动作触发
            if self._wait_until_false("Large_Crucible_Feed_Action_Complete", description=f"大坩锅上料完成"): # 等待完成状态复位
                logger.info(f"大坩锅上料完成")
                return {
                    "success": True,
                    "message": f"大坩锅上料完成",
                }
            else:
                error_msg = f"大坩锅上料失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"大坩锅上料失败，操作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def pick_large_crucible_from_moving_position(self) -> dict:
        """
        从搬运区取大坩埚
        - 检查机械臂3是否空闲
        - 检查大坩埚入料是否在取料区
        - 设置取大坩埚
        - 等待取大坩锅完成
        - 返回成功
        """
        logger.info("从搬运区取大坩埚")
        self._wait_until_true("Robotic_Arm_Idle_3", description="等待机械臂3空闲")

        if self.get_large_crucible_feed_current_position() != LargeCrucibleFeedPosition.PICKING:
            error_msg = f"当前大坩埚搬运位置不是取料位，无法取料"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        self.set_node_value("Robotic_Arm_Target_Position_Code_3", RoboticArmTargetPosition_3.LARGE_CRUCIBLE_POSITION) # 设置机械臂目标位置为大坩埚
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_3", RoboticArmPickPlaceCode_3.PICK_FEED_LARGE_CRUCIBLE) # 设置取大坩埚
        self.set_node_value("Robotic_Arm_Action_Trigger_3", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_3", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_3", description=f"取大坩埚完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_3", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_3", description=f"取大坩锅完成"): # 等待完成状态复位
                logger.info(f"取大坩埚完成")
                return {
                    "success": True,
                    "message": f"取大坩埚完成",
                }
            else:
                error_msg = f"取大坩埚失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"取大坩埚失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_large_crucible_to_muffle_furnace(self, muffle_furnace_position: int) -> dict:
        """
        把大坩埚放到马弗炉
        - 检查机械臂3是否空闲
        - 检查马弗炉是否占位
        - 设置放大坩埚
        - 等待放大坩锅完成
        - 返回成功
        """
        logger.info(f"把大坩锅放到马弗炉位置{muffle_furnace_position}")

        MIN_MUFFLE_FURNACE_POSITION = 1
        MAX_MUFFLE_FURNACE_POSITION = 6
        if muffle_furnace_position < MIN_MUFFLE_FURNACE_POSITION or muffle_furnace_position > MAX_MUFFLE_FURNACE_POSITION:
            error_msg = f"马弗炉位置{muffle_furnace_position}不在有效范围内"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self._wait_until_true("Robotic_Arm_Idle_3", description="等待机械臂3空闲")
        
        if self.is_muffle_furnace_occupied(muffle_furnace_position):
            error_msg = f"马弗炉位置{muffle_furnace_position}占位，无法放料"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.set_node_value("Robotic_Arm_Target_Position_Code_3", RoboticArmTargetPosition_3.MUFFLE_FURNACE_1_POSITION + muffle_furnace_position - 1) # 设置机械臂目标位置为马弗炉
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_3", RoboticArmPickPlaceCode_3.PLACE_MUFFLE_FURNACE_1 + muffle_furnace_position - 1) # 设置放马弗炉
        self.set_node_value("Robotic_Arm_Action_Trigger_3", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_3", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_3", description=f"放马弗炉完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_3", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_3", description=f"放马弗炉完成"): # 等待完成状态复位
                logger.info(f"放马弗炉完成")
                return {
                    "success": True,
                    "message": f"放马弗炉完成",
                }
            else:
                error_msg = f"放马弗炉失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"放马弗炉失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def muffle_furnace_sintering(self, muffle_furnace_position: int) -> dict:
        """
        马弗炉烧结
        - 检查马弗炉是否占位
        - 设置开始烧结
        - 等待烧结完成
        - 返回成功
        """
        logger.info(f"开始马弗炉{muffle_furnace_position}烧结")
        MIN_MUFFLE_FURNACE_POSITION = 1
        MAX_MUFFLE_FURNACE_POSITION = 6
        if muffle_furnace_position < MIN_MUFFLE_FURNACE_POSITION or muffle_furnace_position > MAX_MUFFLE_FURNACE_POSITION:
            error_msg = f"马弗炉位置{muffle_furnace_position}不在有效范围内"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if not self.is_muffle_furnace_occupied(muffle_furnace_position):
            error_msg = f"马弗炉位置{muffle_furnace_position}未占位，无法烧结"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if self._wait_until_true(f"Muffle_Furnace_Request_Process_{muffle_furnace_position}", description=f"马弗炉{muffle_furnace_position}开始请求"):
            self.set_node_value(f"Muffle_Furnace_Start_Process_{muffle_furnace_position}", True) # 设置开始烧结
            if self._wait_until_true(f"Muffle_Furnace_Process_Complete_{muffle_furnace_position}", description=f"马弗炉{muffle_furnace_position}烧结完成"):
                logger.info(f"马弗炉{muffle_furnace_position}烧结完成")
                self.set_node_value(f"Muffle_Furnace_Start_Process_{muffle_furnace_position}", False) # 复位动作触发
                return {
                    "success": True,
                    "message": f"马弗炉{muffle_furnace_position}烧结完成",
                }
            else:
                logger.error(f"马弗炉{muffle_furnace_position}烧结失败")
                self.set_node_value(f"Muffle_Furnace_Start_Process_{muffle_furnace_position}", False) # 复位动作触发
                raise ValueError(f"马弗炉{muffle_furnace_position}烧结失败，等待烧结超时")
        else:
            error_msg = f"马弗炉{muffle_furnace_position}烧结失败，未收到请求"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def pick_large_crucible_from_muffle_furnace(self, muffle_furnace_position: int) -> dict:
        """
        从马弗炉取大坩埚
        - 检查机械臂3是否空闲
        - 检查马弗炉是否占位
        - 设置开始取
        - 等待取完成
        - 返回成功
        """
        logger.info(f"从马弗炉{muffle_furnace_position}取大坩埚")

        MIN_MUFFLE_FURNACE_POSITION = 1
        MAX_MUFFLE_FURNACE_POSITION = 6
        if muffle_furnace_position < MIN_MUFFLE_FURNACE_POSITION or muffle_furnace_position > MAX_MUFFLE_FURNACE_POSITION:
            error_msg = f"马弗炉位置{muffle_furnace_position}不在有效范围内"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self._wait_until_true("Robotic_Arm_Idle_3", description="等待机械臂3空闲")
        
        if not self.is_muffle_furnace_occupied(muffle_furnace_position):
            error_msg = f"马弗炉位置{muffle_furnace_position}未占位，无法取大坩埚"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.set_node_value("Robotic_Arm_Target_Position_Code_3", RoboticArmTargetPosition_3.MUFFLE_FURNACE_1_POSITION + muffle_furnace_position - 1) # 设置机械臂目标位置为马弗炉
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_3", RoboticArmPickPlaceCode_3.PICK_MUFFLE_FURNACE_1 + muffle_furnace_position - 1) # 设置放马弗炉
        self.set_node_value("Robotic_Arm_Action_Trigger_3", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_3", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_3", description=f"从马弗炉取大坩埚完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_3", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_3", description=f"从马弗炉取大坩埚完成"): # 等待完成状态复位
                logger.info(f"从马弗炉取大坩埚完成")
                return {
                    "success": True,
                    "message": f"从马弗炉取大坩埚完成",
                }
            else:
                error_msg = f"从马弗炉取大坩埚失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"从马弗炉取大坩埚失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_large_crucible_to_upper_product_rack(self) -> dict:
        """
        放大坩埚到成品出料上位置
        - 检查机械臂3是否空闲
        - 检查成品出料上位置是否占位
        - 设置放成品出料上位置
        - 等待放成品出料上位置完成
        - 返回成功
        """
        logger.info(f"放大坩埚到成品出料上位置")
        self._wait_until_true("Robotic_Arm_Idle_3", description="等待机械臂3空闲")
        
        if self.is_upper_product_rack_occupied():
            error_msg = f"上成品架占位，无法放料"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.set_node_value("Robotic_Arm_Target_Position_Code_3", RoboticArmTargetPosition_3.DISCHARGE_POSITION) # 设置机械臂目标位置为成品出料架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_3", RoboticArmPickPlaceCode_3.PLACE_DISCHARGE_UPPER) # 设置放成品出料上位置
        self.set_node_value("Robotic_Arm_Action_Trigger_3", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_3", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_3", description=f"放成品出料上位置完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_3", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_3", description=f"放成品出料上位置完成"): # 等待完成状态复位
                logger.info(f"放成品出料上位置完成")
                return {
                    "success": True,
                    "message": f"放成品出料上位置完成",
                }
            else:
                error_msg = f"放成品出料上位置失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"放成品出料上位置失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)


    def place_large_crucible_to_lower_product_rack(self) -> dict:
        """
        放大坩埚到成品出料下位置
        - 检查机械臂3是否空闲
        - 检查成品出料下位置是否占位
        - 设置放成品出料下位置
        - 等待放成品出料下位置完成
        - 返回成功
        """
        logger.info(f"放大坩埚到成品出料下位置")
        self._wait_until_true("Robotic_Arm_Idle_3", description="等待机械臂3空闲")
        
        if self.is_lower_product_rack_occupied():
            error_msg = f"下成品架占位，无法放料"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.set_node_value("Robotic_Arm_Target_Position_Code_3", RoboticArmTargetPosition_3.DISCHARGE_POSITION) # 设置机械臂目标位置为成品出料架
        self.set_node_value("Robotic_Arm_Target_Pick_Place_Code_3", RoboticArmPickPlaceCode_3.PLACE_DISCHARGE_LOWER) # 设置放成品出料下位置
        self.set_node_value("Robotic_Arm_Action_Trigger_3", False)  # 上升沿: 先复位
        time.sleep(0.5)
        self.set_node_value("Robotic_Arm_Action_Trigger_3", True) # 设置动作触发
        if self._wait_until_true("Robotic_Arm_Action_Complete_3", description=f"放成品出料下位置完成"):
            self.set_node_value("Robotic_Arm_Action_Trigger_3", False) # 复位动作触发
            if self._wait_until_false("Robotic_Arm_Action_Complete_3", description=f"放成品出料下位置完成"): # 等待完成状态复位
                logger.info(f"放成品出料下位置完成")
                return {
                    "success": True,
                    "message": f"放成品出料下位置完成",
                }
            else:
                error_msg = f"放成品出料下位置失败，完成复位超时"
                logger.error(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"放成品出料下位置失败，机械臂动作未完成"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
    
    def trigger_all_process(self) -> dict:
        logger.info(f"触发所有过程...")
        time.sleep(1)
        logger.info(f"------ 首先进行1-4号位4个罐的加粉加珠球磨流程...")
        for rack_pos in range(1, 4):
            # 从罐架区取球磨罐
            ret = self.pick_can_from_can_rack(rack_pos)
            if not ret["success"]:
                return ret
            
            # 将空罐放到开盖区
            ret = self.place_empty_can_to_open_can_position()
            if not ret["success"]:
                return ret
            
            # 打开罐上盖
            ret = self.open_can_lid()
            if not ret["success"]:
                return ret
            
            # 从开盖区抓取空罐
            ret = self.pick_empty_can_from_open_can_position()
            if not ret["success"]:
                return ret
            
            # 将罐体放置到加粉区
            ret = self.place_can_to_add_powder_position()
            if not ret["success"]:
                return ret
            
            # 加粉
            ret = self.add_powder()
            if not ret["success"]:
                return ret
            
            # 从加粉区取罐体
            ret = self.pick_can_from_add_powder_position()
            if not ret["success"]:
                return ret
            
            # 将罐体放置到加珠区
            ret = self.place_can_to_add_bead_position()
            if not ret["success"]:
                return ret
            
            # 加珠
            ret = self.add_bead()
            if not ret["success"]:
                return ret
            
            # 从加珠区取罐体
            ret = self.pick_can_from_add_bead_position()
            if not ret["success"]:
                return ret
            
            # 将带有粉珠的球磨罐放置到开盖区
            ret = self.place_can_with_powder_and_bead_to_open_can_position()
            if not ret["success"]:
                return ret
            
            # 关盖
            ret = self.close_can_lid()
            if not ret["success"]:
                return ret
            
            # 从开盖区抓取带有粉珠的球磨罐
            ret = self.pick_can_with_powder_and_bead_from_open_can_position()
            if not ret["success"]:
                return ret
            
            # 将罐体放置到球磨区
            ret = self.place_can_to_ball_mill(rack_pos)
            if not ret["success"]:
                return ret
            
        # 球磨
        ret = self.ball_mill()
        if not ret["success"]:
            return ret
            
        logger.info(f"------ 进行4个罐的过筛刮粉流程...")
        # 小坩埚搬运位上料
        self.small_crucible_feed()
        
        for mill_pos in range(1, 4):
            # 从球磨区取罐体
            ret = self.pick_can_from_ball_mill(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将研磨后球磨罐放到开盖区
            ret = self.place_milled_can_to_open_can_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 打开罐上盖
            ret = self.open_can_lid()
            if not ret["success"]:
                return ret
            
            # 从开盖区抓取研磨后球磨罐
            ret = self.pick_milled_can_from_open_can_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将研磨后球磨罐放到过筛区
            ret = self.place_milled_can_to_sieve_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 从坩锅架区取小坩埚
            self.pick_small_crucible_from_crucible_rack(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将小坩埚放到过筛区
            self.place_small_crucible_to_sieve_position()
            if not ret["success"]:
                return ret
            
            # 从漏斗架区取漏斗
            self.pick_funnel_from_crucible_rack(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将漏斗放到过筛区
            self.place_funnel_to_sieve_position()
            if not ret["success"]:
                return ret
            
            # 过筛
            ret = self.sieve()
            if not ret["success"]:
                return ret
            
            # 从过筛区抓取研磨后球磨罐
            self.pick_milled_can_from_sieve_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将研磨后球磨罐放到刮粉区
            ret = self.place_milled_can_to_scrape_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 刮粉
            ret = self.scrape_powder()
            if not ret["success"]:
                return ret
            
            # 从刮粉区位置取下研磨后球磨罐
            ret = self.pick_milled_can_from_scrape_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 再次放到过筛区
            ret = self.place_milled_can_to_sieve_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 再次过筛
            ret = self.sieve()
            if not ret["success"]:
                return ret
            
            # 从过筛区抓取研磨后球磨罐
            self.pick_milled_can_from_sieve_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将过筛后球磨罐放到开罐区位置
            self.place_sieved_can_to_open_can_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 关盖
            ret = self.close_can_lid()
            if not ret["success"]:
                return ret
            
            # 将过筛后球磨罐从开罐区位置取下
            ret = self.pick_sieved_can_from_open_can_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将球磨罐放到罐架区
            ret = self.place_can_to_can_rack(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将小坩埚从过筛区取出
            ret = self.pick_small_crucible_from_sieve_position()
            if not ret["success"]:
                return ret

            # 将小坩锅放到搬运位置
            ret = self.place_small_crucible_to_moving_position(mill_pos)
            if not ret["success"]:
                return ret
            
            # 将漏斗从过筛区取出
            ret = self.pick_funnel_from_sieve_position()
            if not ret["success"]:
                return ret
            
            # 将漏斗放到漏斗架
            ret = self.place_funnel_to_crucible_rack(mill_pos)
            if not ret["success"]:
                return ret
            
        # 小坩埚搬运位出料
        self.small_crucible_discharge()
        
        # 大坩埚出料
        self.large_crucible_discharge()
        
        # 人工操作，把小坩埚取下，并放置在大坩埚中
        logger.info("------ 人工操作，把小坩锅取下，并放置在大坩埚中...")
        time.sleep(30)

        logger.info("------ 进行马弗炉烧结工作...")
        # 大坩埚上料
        self.large_crucible_feed()

        muffle_furnace_pos = 1

        # 从搬运区取大坩埚
        ret = self.pick_large_crucible_from_moving_position()
        if not ret["success"]:
            return ret
        
        # 把大坩埚放到马弗炉
        ret = self.place_large_crucible_to_muffle_furnace(muffle_furnace_pos)
        if not ret["success"]:
            return ret
        
        # 马弗炉烧结
        ret = self.muffle_furnace_sintering(muffle_furnace_pos)
        if not ret["success"]:
            return ret
        
        # 从马弗炉取大坩埚
        ret = self.pick_large_crucible_from_muffle_furnace(muffle_furnace_pos)
        if not ret["success"]:
            return ret
        
        # 放大坩埚到成品出料上位置
        ret = self.place_large_crucible_to_upper_product_rack()
        if not ret["success"]:
            return ret
        
        logger.info("------ 成品已放置在出料位，整体流程结束")

        return {
            "success": True,
            "message": f"整体流程运行完成",
        } 

            
    def _wait_until_true(
        self,
        node_name: str,
        interval: float = 0.2,
        description: str = None
    ) -> bool:
        """等待布尔节点变为 True（无超时，持续轮询直到满足）"""
        desc = description or node_name
        logger.info(f"等待 {desc} 变为 True...")

        while True:
            if self.get_node_value(node_name, use_cache=True):
                logger.info(f"✓ {desc} 已变为 True")
                return True
            time.sleep(interval)

    def _wait_until_false(
        self,
        node_name: str,
        interval: float = 0.2,
        description: str = None
    ) -> bool:
        """等待布尔节点变为 False（无超时，持续轮询直到满足）"""
        desc = description or node_name
        logger.info(f"等待 {desc} 变为 False...")

        while True:
            if not self.get_node_value(node_name, use_cache=True):
                logger.info(f"✓ {desc} 已变为 False")
                return True
            time.sleep(interval)

    def _wait_for_nodes(
        self,
        conditions: dict,  # {node_name: target_value, ...}
        interval: float = 0.2
    ) -> bool:
        """等待多个节点同时满足条件（无超时，持续轮询直到满足）"""
        while True:
            all_met = all(
                self.get_node_value(name, use_cache=True) == target
                for name, target in conditions.items()
            )
            if all_met:
                return True
            time.sleep(interval)


if __name__ == '__main__':
    # 调试用法
    xuseDevice = XUSEDevice(
        url="opc.tcp://192.168.1.10:4840",
        # url="opc.tcp://127.0.0.1:48010",
        csv_path=os.path.dirname(os.path.abspath(__file__)) + "/xuse_variables.csv"
    )

    # 启动心跳
    # xuseDevice.start_heart_beat()

    logger.setLevel(logging.INFO)

    time.sleep(3)

    # 初始化工作站
    # xuseDevice.init_workstation()
    
    # 显示命令行，让用户通过选择序号来完成相应的操作
    # 如果带有参数，则序号和各参数之间均由空格分隔
    while True:
        print("请选择操作：")
        print("0 初始化")
        print("1-1 从罐架区取球磨罐")
        print("1-2 将空罐放到开盖区")
        print("1-3 打开罐上盖")
        print("1-4 从开盖区抓取空罐")
        print("1-5 将罐体放置到加粉区")
        print("1-6 加粉")
        print("1-7 从加粉区取罐体")
        print("1-8 将罐体放置到加珠区")
        print("1-9 加珠")
        print("1-10 从加珠区取罐体")
        print("1-11 将带有粉珠的球磨罐放置到开盖区")
        print("1-12 关盖")
        print("1-13 从开盖区抓取带有粉珠的球磨罐")
        print("1-14 将罐体放置到球磨区")
        print("1-15 球磨")
        print("1-16 从球磨区抓取罐体")
        print("1-17 将研磨后球磨罐放到开盖区")
        print("1-18 从开盖区抓取研磨后球磨罐")
        print("1-19 将研磨后球磨罐放到过筛区")
        print("1-20 过筛")
        print("1-21 从过筛区抓取研磨后球磨罐")
        print("1-22 将研磨后球磨罐放到刮粉区")
        print("1-23 刮粉")
        print("1-24 从刮粉区位置取下研磨后球磨罐")
        print("1-25 将过筛后球磨罐放到开罐区位置")
        print("1-26 将过筛后球磨罐从开罐区位置取下")
        print("1-27 将球磨罐放到罐架区")
        print("2-1 从坩埚架区取小坩埚")
        print("2-2 将小坩埚放到过筛区")
        print("2-3 从漏斗架区取漏斗")
        print("2-4 将漏斗放到过筛区")
        print("2-5 将小坩埚从过筛区取出")
        print("2-6 将小坩埚放到搬运位置")
        print("2-7 将漏斗从过筛区取出")
        print("2-8 将漏斗放到漏斗架")
        print("2-9 小坩埚搬运位出料")
        print("2-10 小坩埚搬运位上料")
        print("3-1 大坩埚搬运位出料")
        print("3-2 大坩埚搬运位置上料")
        print("3-3 从搬运区取大坩埚")
        print("3-4 把大坩埚放到马弗炉")
        print("3-5 马弗炉烧结")
        print("3-6 从马弗炉取大坩埚")
        print("3-7 放大坩埚到成品出料上位置")
        print("3-8 放大坩埚到成品出料下位置")
        print("98 全部流程")
        print("99 退出")
        choice = input("请输入操作序号：")
        if choice == "0":
            xuseDevice.trigger_init()
        elif choice.startswith("1-1 "):
            rack_pos = int(choice.split(" ")[1])
            xuseDevice.pick_can_from_can_rack(rack_pos)
        elif choice.startswith("1-2 "):
            xuseDevice.place_empty_can_to_open_can_position()
        elif choice.startswith("1-3 "):
            xuseDevice.open_can_lid()
        elif choice.startswith("1-4 "):
            xuseDevice.pick_empty_can_from_open_can_position()
        elif choice.startswith("1-5 "):
            xuseDevice.place_can_to_add_powder_position()
        elif choice.startswith("1-6 "):
            xuseDevice.add_powder()
        elif choice.startswith("1-7 "):
            xuseDevice.pick_can_from_add_powder_position()
        elif choice.startswith("1-8 "):
            xuseDevice.place_can_to_add_bead_position()
        elif choice.startswith("1-9 "):
            xuseDevice.add_bead()
        elif choice.startswith("1-10 "):
            xuseDevice.pick_can_from_add_bead_position()
        elif choice.startswith("1-11 "):
            xuseDevice.place_can_with_powder_and_bead_to_open_can_position()
        elif choice.startswith("1-12 "):
            xuseDevice.close_can_lid()
        elif choice.startswith("1-13 "):
            xuseDevice.pick_can_with_powder_and_bead_from_open_can_position()
        elif choice.startswith("1-14 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.place_can_to_ball_mill(mill_pos)
        elif choice.startswith("1-15 "):
            xuseDevice.ball_mill()
        elif choice.startswith("1-16 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.pick_can_from_ball_mill(mill_pos)
        elif choice.startswith("1-17 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.place_milled_can_to_open_can_position(mill_pos)
        elif choice.startswith("1-18 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.pick_milled_can_from_open_can_position(mill_pos)
        elif choice.startswith("1-19 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.place_milled_can_to_sieve_position(mill_pos)
        elif choice.startswith("1-20 "):
            xuseDevice.sieve()
        elif choice.startswith("1-21 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.pick_milled_can_from_sieve_position(mill_pos)
        elif choice.startswith("1-22 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.place_milled_can_to_scrape_position(mill_pos)
        elif choice.startswith("1-23 "):
            xuseDevice.scrape_powder()
        elif choice.startswith("1-24 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.pick_milled_can_from_scrape_position(mill_pos)
        elif choice.startswith("1-25 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.place_sieved_can_to_open_can_position(mill_pos)
        elif choice.startswith("1-26 "):
            mill_pos = int(choice.split(" ")[1])
            xuseDevice.pick_sieved_can_from_open_can_position(mill_pos)
        elif choice.startswith("1-27 "):
            rack_pos = int(choice.split(" ")[1])
            xuseDevice.place_can_to_can_rack(rack_pos)
        

        elif choice.startswith("2-1 "):
            rack_pos = int(choice.split(" ")[1])
            xuseDevice.pick_small_crucible_from_crucible_rack(rack_pos)
        elif choice.startswith("2-2 "):
            xuseDevice.place_small_crucible_to_sieve_position()
        elif choice.startswith("2-3 "):
            rack_pos = int(choice.split(" ")[1])
            xuseDevice.pick_funnel_from_crucible_rack(rack_pos)
        elif choice.startswith("2-4 "):
            xuseDevice.place_funnel_to_sieve_position()
        elif choice.startswith("2-5 "):
            xuseDevice.pick_small_crucible_from_sieve_position()
        elif choice.startswith("2-6 "):
            moving_pos = int(choice.split(" ")[1])
            xuseDevice.place_small_crucible_to_moving_position(moving_pos)
        elif choice.startswith("2-7 "):
            xuseDevice.pick_funnel_from_sieve_position()
        elif choice.startswith("2-8 "):
            rack_pos = int(choice.split(" ")[1])
            xuseDevice.place_funnel_to_crucible_rack(rack_pos)
        elif choice.startswith("2-9 "):
            xuseDevice.small_crucible_discharge()
        elif choice.startswith("2-10 "):
            xuseDevice.small_crucible_feed()


        elif choice.startswith("3-1 "):
            xuseDevice.large_crucible_discharge()
        elif choice.startswith("3-2 "):
            xuseDevice.large_crucible_feed()
        elif choice.startswith("3-3 "):
            xuseDevice.pick_large_crucible_from_moving_position()
        elif choice.startswith("3-4 "):
            furnace_pos = int(choice.split(" ")[1])
            xuseDevice.place_large_crucible_to_muffle_furnace(furnace_pos)
        elif choice.startswith("3-5 "):
            xuseDevice.muffle_furnace_sintering()
        elif choice.startswith("3-6 "):
            furnace_pos = int(choice.split(" ")[1])
            xuseDevice.pick_large_crucible_from_muffle_furnace(furnace_pos)
        elif choice.startswith("3-7 "):
            xuseDevice.place_large_crucible_to_upper_product_rack()
        elif choice.startswith("3-8 "):
            xuseDevice.place_large_crucible_to_lower_product_rack()


        elif choice.startswith("98 "):
            xuseDevice.trigger_all_process()
        elif choice.startswith("99 "):
            break
        else:
            print("无效的操作序号，请重新输入。")

    # 结束心跳
    # xuseDevice.stop_heart_beat()

    # 断开连接
    xuseDevice.disconnect()

    print("退出程序。")