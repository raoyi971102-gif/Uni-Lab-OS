from enum import Enum

# 定义机械臂1目标位置的枚举
class RoboticArmTargetPosition_1(int, Enum):
    """
    机械臂1目标位置的枚举
    """
    # 罐架区_取放位
    CAN_RACK_POSITION = 1
    # 加珠区_取放位
    ADD_BEAD_POSITION = 2
    # 开罐区_取放位
    OPEN_CAN_POSITION = 3
    # 刮粉区_取放位
    SCRAPE_POWDER_POSITION = 4
    # 过筛区_取放位
    SIEVE_POSITION = 5
    # 加粉区_取放位
    ADD_POWDER_POSITION = 6
    # 球磨区_取放位
    BALL_MILL_POSITION = 7

class RoboticArmPickPlaceCode_1(int, Enum):
    """
    机械臂1取放代码的枚举
    """
    # 罐架区_取罐起始
    PICK_CAN_RACK_START = 1
    # 罐架区_取罐结束
    PICK_CAN_RACK_END = 32

    # 开盖区_无粉珠_放空罐
    OPEN_CAN_NO_POWDER_PLACE_EMPTY_CAN = 40
    # 开盖区_无粉珠_取底座
    OPEN_CAN_NO_POWDER_PICK_BASE = 41
    
    # 加粉区_放底座
    ADD_POWDER_PLACE_BASE = 42
    # 加粉区_取底座
    ADD_POWDER_PICK_BASE = 43
    
    # 加珠区_放底座
    ADD_BEAD_PLACE_BASE = 44
    # 加珠区_取底座
    ADD_BEAD_PICK_BASE = 45
    
    # 开盖区_有粉珠_放底座
    OPEN_CAN_WITH_POWDER_PLACE_BASE = 46
    # 开盖区_有粉珠_取满罐
    OPEN_CAN_WITH_POWDER_PICK_FULL_CAN = 47

    # 球磨区_放罐1
    BALL_MILL_PLACE_CAN_1 = 50
    # 球磨区_放罐2
    BALL_MILL_PLACE_CAN_2 = 51
    # 球磨区_放罐3
    BALL_MILL_PLACE_CAN_3 = 52
    # 球磨区_放罐4
    BALL_MILL_PLACE_CAN_4 = 53
    # 球磨区_取罐1
    BALL_MILL_PICK_CAN_1 = 54
    # 球磨区_取罐2
    BALL_MILL_PICK_CAN_2 = 55
    # 球磨区_取罐3
    BALL_MILL_PICK_CAN_3 = 56
    # 球磨区_取罐4
    BALL_MILL_PICK_CAN_4 = 57

    # 开盖区_研磨后_放罐1
    OPEN_CAN_AFTER_MILL_PLACE_CAN_1 = 60
    # 开盖区_研磨后_取座1
    OPEN_CAN_AFTER_MILL_PICK_BASE_1 = 61
    # 过筛区_放底座1
    SIEVE_PLACE_BASE_1 = 62
    # 过筛区_取底座1
    SIEVE_PICK_BASE_1 = 63
    # 刮粉区_放底座1
    SCRAPE_POWDER_PLACE_BASE_1 = 64
    # 刮粉区_取底座1
    SCRAPE_POWDER_PICK_BASE_1 = 65
    # 开盖区_过筛完毕_放座1
    OPEN_CAN_AFTER_SIEVE_PLACE_BASE_1 = 66
    # 开盖区_过筛完毕_取罐1
    OPEN_CAN_AFTER_SIEVE_PICK_CAN_1 = 67

    # 开盖区_研磨后_放罐2
    OPEN_CAN_AFTER_MILL_PLACE_CAN_2 = 70
    # 开盖区_研磨后_取座2
    OPEN_CAN_AFTER_MILL_PICK_BASE_2 = 71
    # 过筛区_放底座2
    SIEVE_PLACE_BASE_2 = 72
    # 过筛区_取底座2
    SIEVE_PICK_BASE_2 = 73
    # 刮粉区_放底座2
    SCRAPE_POWDER_PLACE_BASE_2 = 74
    # 刮粉区_取底座2
    SCRAPE_POWDER_PICK_BASE_2 = 75
    # 开盖区_过筛完毕_放座2
    OPEN_CAN_AFTER_SIEVE_PLACE_BASE_2 = 76
    # 开盖区_过筛完毕_取罐2
    OPEN_CAN_AFTER_SIEVE_PICK_CAN_2 = 77

    # 开盖区_研磨后_放罐3
    OPEN_CAN_AFTER_MILL_PLACE_CAN_3 = 80
    # 开盖区_研磨后_取座3
    OPEN_CAN_AFTER_MILL_PICK_BASE_3 = 81
    # 过筛区_放底座3
    SIEVE_PLACE_BASE_3 = 82
    # 过筛区_取底座3
    SIEVE_PICK_BASE_3 = 83
    # 刮粉区_放底座3
    SCRAPE_POWDER_PLACE_BASE_3 = 84
    # 刮粉区_取底座3
    SCRAPE_POWDER_PICK_BASE_3 = 85
    # 开盖区_过筛完毕_放座3
    OPEN_CAN_AFTER_SIEVE_PLACE_BASE_3 = 86
    # 开盖区_过筛完毕_取罐3
    OPEN_CAN_AFTER_SIEVE_PICK_CAN_3 = 87

    # 开盖区_研磨后_放罐4
    OPEN_CAN_AFTER_MILL_PLACE_CAN_4 = 90
    # 开盖区_研磨后_取座4
    OPEN_CAN_AFTER_MILL_PICK_BASE_4 = 91
    # 过筛区_放底座4
    SIEVE_PLACE_BASE_4 = 92
    # 过筛区_取底座4
    SIEVE_PICK_BASE_4 = 93
    # 刮粉区_放底座4
    SCRAPE_POWDER_PLACE_BASE_4 = 94
    # 刮粉区_取底座4
    SCRAPE_POWDER_PICK_BASE_4 = 95
    # 开盖区_过筛完毕_放座4
    OPEN_CAN_AFTER_SIEVE_PLACE_BASE_4 = 96
    # 开盖区_过筛完毕_取罐4
    OPEN_CAN_AFTER_SIEVE_PICK_CAN_4 = 97

    # 罐架区_放罐起始
    PLACE_CAN_RACK_START = 101
    # 罐架区_放罐结束
    PLACE_CAN_RACK_END = 132

class RoboticArmPickPlaceCode_2(int, Enum):
    """
    机械臂2取放代码的枚举
    """
    # 坩埚架区_取坩埚起始
    PICK_CRUCIBLE_RACK_START = 1
    # 坩锅架区_取锅结束
    PICK_CRUCIBLE_RACK_END = 20

    # 坩埚架区_放漏斗起始
    PLACE_FUNNEL_RACK_START = 21
    # 坩锅架区_放漏斗结束
    PLACE_FUNNEL_RACK_END = 28

    # 坩埚架区_取漏斗起始
    PICK_FUNNEL_RACK_START = 31
    # 坩锅架区_取漏斗结束
    PICK_FUNNEL_RACK_END = 38

    # 过筛区_取坩埚
    PICK_SIEVE_CRUCIBLE = 41
    # 过筛区_放坩埚
    PLACE_SIEVE_CRUCIBLE = 42
    # 过筛区_取漏斗
    PICK_SIEVE_FUNNEL = 43
    # 过筛区_放漏斗
    PLACE_SIEVE_FUNNEL = 44

    # 搬运区_放坩埚1
    PLACE_SMALL_CRUCIBLE_1 = 47
    # 搬运区_放坩埚2
    PLACE_SMALL_CRUCIBLE_2 = 48
    # 搬运区_放坩埚3
    PLACE_SMALL_CRUCIBLE_3 = 49
    # 搬运区_放坩埚4
    PLACE_SMALL_CRUCIBLE_4 = 50


# 定义机械臂3目标位置的枚举
class RoboticArmTargetPosition_3(int, Enum):
    """
    机械臂3目标位置的枚举
    """
    # 大坩埚_取放位
    LARGE_CRUCIBLE_POSITION = 1
    # 马弗炉1_取放位
    MUFFLE_FURNACE_1_POSITION = 2
    # 马弗炉2_取放位
    MUFFLE_FURNACE_2_POSITION = 3
    # 马弗炉3_取放位
    MUFFLE_FURNACE_3_POSITION = 4
    # 马弗炉4_取放位
    MUFFLE_FURNACE_4_POSITION = 5
    # 马弗炉5_取放位
    MUFFLE_FURNACE_5_POSITION = 6
    # 马弗炉6_取放位
    MUFFLE_FURNACE_6_POSITION = 7
    # 出料_取放位
    DISCHARGE_POSITION = 8

class RoboticArmPickPlaceCode_3(int, Enum):
    """
    机械臂3取放代码的枚举
    """
    #进料取_取大坩埚
    PICK_FEED_LARGE_CRUCIBLE = 1
    #马弗炉区_放1号
    PLACE_MUFFLE_FURNACE_1 = 2
    #马弗炉区_放2号
    PLACE_MUFFLE_FURNACE_2 = 3
    #马弗炉区_放3号
    PLACE_MUFFLE_FURNACE_3 = 4
    #马弗炉区_放4号
    PLACE_MUFFLE_FURNACE_4 = 5
    #马弗炉区_放5号
    PLACE_MUFFLE_FURNACE_5 = 6
    #马弗炉区_放6号
    PLACE_MUFFLE_FURNACE_6 = 7
    #马弗炉区_取1号
    PICK_MUFFLE_FURNACE_1 = 8
    #马弗炉区_取2号
    PICK_MUFFLE_FURNACE_2 = 9
    #马弗炉区_取3号
    PICK_MUFFLE_FURNACE_3 = 10
    #马弗炉区_取4号
    PICK_MUFFLE_FURNACE_4 = 11
    #马弗炉区_取5号
    PICK_MUFFLE_FURNACE_5 = 12
    #马弗炉区_取6号
    PICK_MUFFLE_FURNACE_6 = 13
    #出料区_放上层
    PLACE_DISCHARGE_UPPER = 14
    #出料区_放下层
    PLACE_DISCHARGE_LOWER = 15

class OpenCanActionCode(int, Enum):
    """
    打开罐上盖动作代码的枚举
    """
    # 没动作
    NO_ACTION = 0
    # 打开罐上盖
    OPEN_CAN_LID = 1
    # 关闭罐上盖
    CLOSE_CAN_LID = 2


class SieveActionCode(int, Enum):
    """
    过筛动作代码的枚举
    """
    # 没动作
    NO_ACTION = 0
    # 过筛
    SIEVE = 1


class ScrapePowderActionCode(int, Enum):
    """
    刮粉区动作代码的枚举
    """
    # 没动作
    NO_ACTION = 0
    # 刮粉区
    SCRAPE_POWDER = 1


class SmallCrucibleDischargePosition(int, Enum):
    """
    小坩埚出料位置的枚举
    """
    # 放料位
    FEEDING = 1
    # 出料位
    DISCHARGE = 2


class LargeCrucibleFeedPosition(int, Enum):
    """
    大坩埚入料位置的枚举
    """
    # 入料位
    FEEDING = 1
    # 取料位
    PICKING = 2