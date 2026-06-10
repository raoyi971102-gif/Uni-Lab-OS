# 偶氮反应工作站驱动

## 概述

偶氮反应工作站是一个微流控系统，用于在线监测偶氮反应过程。系统通过两个蠕动泵将两种液体混合，经过温控装置反应后，使用光谱仪进行实时表征。

## 系统组成

### 硬件设备

1. **蠕动泵 A & B** (`peristaltic_pump.py`)
   - 通过步进电机驱动器（Modbus协议）控制
   - 支持流速到转速的自动换算
   - Modbus地址：5（泵A）、6（泵B）
   - 通信：RS485串口（共享）

2. **温控器** (`temperature_controller.py`)
   - TEC107/115型号温控器
   - 支持温度设定和实时读取
   - Modbus地址：1
   - 通信：RS485串口（共享）

3. **光谱仪** (`spectrometer.py`)
   - Ideaoptics光谱仪（基于CyUSB）
   - 支持积分时间、平均次数设置
   - 支持CSV/JSON格式数据导出
   - 通信：IdeaOptics USB Device 驱动 + `Ideaoptics.USB.SDK.dll`

### 通信架构

```
┌─────────────────────────────────────────┐
│         偶氮反应工作站                    │
│                                         │
│  ┌──────────┐  ┌──────────┐            │
│  │  泵A     │  │  泵B     │            │
│  │ (地址5)  │  │ (地址6)  │            │
│  └────┬─────┘  └────┬─────┘            │
│       │             │                   │
│       └──────┬──────┘                   │
│              │                          │
│       ┌──────┴──────┐                   │
│       │  温控器     │                   │
│       │  (地址1)    │                   │
│       └──────┬──────┘                   │
│              │                          │
│       ┌──────┴──────┐                   │
│       │ RS485串口   │                   │
│       │  (COM3)     │                   │
│       └─────────────┘                   │
│                                         │
│       ┌─────────────┐                   │
│       │  光谱仪     │                   │
│       │IdeaOptics USB│                  │
│       │ Device + SDK│                   │
│       └─────────────┘                   │
└─────────────────────────────────────────┘
```

## 文件结构

```
unilabos/devices/workstation/azo/
├── __init__.py                      # 包初始化
├── azo_workstation.py               # 主工作站驱动
├── azo_raw_serial.py                # 偶氮专用 RS485 二进制串口设备
├── peristaltic_pump.py              # 蠕动泵驱动
├── temperature_controller.py        # 温控器驱动
├── spectrometer.py                  # 光谱仪驱动
├── 光谱仪/                          # 光谱仪SDK和示例
│   └── Python4CyUSB/
│       ├── Python4CyUSB.py
│       └── dlls/
│           └── Ideaoptics.USB.SDK.dll
└── README.md                        # 本文档

unilabos/test/experiments/
└── azo_workstation.json             # 示例Graph配置
```

## 使用方法

### 1. 配置设备拓扑

编辑 `azo_workstation.json`，配置RS485串口和设备参数；光谱仪不配置COM口，由SDK自动枚举USB设备：

```json
{
  "nodes": [
    {
      "id": "azo_workstation_1",
      "type": "device",
      "class": "azo_workstation",
      "config": {
        "protocol_type": [],
        "pump_a_address": 5,
        "pump_b_address": 6,
        "pump_a_flow_ratio": 1.0,  // TODO: 根据实际泵参数调整
        "pump_b_flow_ratio": 1.0,
        "temp_controller_address": 1,
        "data_save_dir": "./azo_experiment_data"
      }
    },
    {
      "id": "serial_485",
      "type": "device",
      "class": "azo_raw_serial",
      "parent": "azo_workstation_1",
      "config": {
        "port": "COM6",
        "baudrate": 38400,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 1.0
      }
    }
  ]
}
```

### 2. 启动工作站

```bash
# 启动工作站
unilab --graph unilabos/test/experiments/azo_workstation.json --backend ros

# 测试模式（模拟硬件）
unilab --graph unilabos/test/experiments/azo_workstation.json --test_mode
```

### 3. 运行工作流

通过ROS2 Action或API调用工作流：

```python
# 工作流参数
parameters = {
    "flow_rate_a": 2.0,        # 泵A流速 (ml/min)
    "flow_rate_b": 1.5,        # 泵B流速 (ml/min)
    "temperature": 60.0,       # 反应温度 (°C)
    "duration": 3600.0,        # 反应时长 (秒)
    "spectrum_interval": 10.0  # 光谱采集间隔 (秒)
}

# 启动工作流
workstation.start_workflow("azo_reaction", parameters)
```

### 4. 数据输出

光谱数据自动保存到指定目录：

```
azo_experiment_data/
├── azo_20260527_143022_20260527_143025.csv  # 光谱数据（CSV格式）
├── azo_20260527_143022_20260527_143035.csv
├── ...
└── azo_20260527_143022_summary.json         # 实验总结
```

## 重要配置项

### 流速到转速的换算

在 `peristaltic_pump.py` 中，需要根据实际泵的参数调整换算系数：

```python
def flow_rate_to_rpm(self, flow_rate: float) -> int:
    """将流速转换为转速
    
    TODO: 根据实际泵的参数调整换算公式
    当前使用简单的线性关系: rpm = flow_rate * ratio
    
    实际可能需要考虑：
    - 泵管内径
    - 泵头滚轮数量
    - 非线性修正
    """
    rpm = int(flow_rate * self.flow_to_rpm_ratio)
    return rpm
```

**需要确定的参数：**
- 泵管内径（mm）
- 泵头滚轮数量
- 每转输送体积（ml/rev）

**换算公式示例：**
```
流速 (ml/min) = 转速 (rpm) × 每转体积 (ml/rev)
转速 (rpm) = 流速 (ml/min) / 每转体积 (ml/rev)
```

### 串口通信注入

在 `azo_workstation.py` 的 `post_init` 方法中，会从 `serial_485` 子设备注入原始二进制串口读写函数：

```python
def post_init(self, ros_node) -> None:
    super().post_init(ros_node)
    
    # 注入RS485串口通信函数（泵和温控器共享）
    if "serial_485" in self._children:
        serial_485 = self._children["serial_485"]
        self.pump_a.serial_write = serial_485.write
        self.pump_a.serial_read = serial_485.read
        self.pump_b.serial_write = serial_485.write
        self.pump_b.serial_read = serial_485.read
        self.temperature_controller.serial_write = serial_485.write
        self.temperature_controller.serial_read = serial_485.read
```

## 工作流程

1. **初始化阶段**
   - 连接所有设备
   - 检查设备状态
   - 初始化光谱仪

2. **反应阶段**
   - 设置目标温度
   - 等待温度稳定
   - 启动两个蠕动泵
   - 按设定间隔采集光谱数据

3. **数据采集**
   - 每次采集包含：
     - 波长-强度数据
     - 时间戳
     - 实际温度
     - 流速参数
   - 自动保存为CSV文件

4. **结束阶段**
   - 停止泵
   - 停止加热
   - 保存实验总结

## 待完善功能

### 必须完善的部分

1. **流速换算公式** (`peristaltic_pump.py`)
   - 需要实际测量泵的流速-转速关系
   - 建议进行标定实验

2. **串口通信注入** (`azo_workstation.py`)
   - 根据实际的子设备ID修改
   - 确保串口设备正确配置

3. **温度稳定判断** (`azo_workstation.py`)
   - 当前使用简单的延时
   - 建议实现PID控制或温度稳定判断逻辑

### 可选增强功能

1. **数据处理**
   - 实时光谱分析
   - 反应进度监测
   - 异常检测

2. **控制优化**
   - PID温度控制
   - 流速闭环控制
   - 自适应采集间隔

3. **可视化**
   - 实时光谱曲线
   - 温度曲线
   - 流速曲线

## 故障排查

### 光谱仪连接失败

1. 检查USB连接
2. 确认DLL路径正确
3. 检查pythonnet是否安装：`pip install pythonnet`
4. 查看日志中的详细错误信息

### 泵或温控器无响应

1. 检查RS485串口连接（COM口、波特率）
2. 确认Modbus地址正确
3. 使用串口调试工具测试通信
4. 检查CRC校验是否正确

### 数据保存失败

1. 检查保存目录权限
2. 确认磁盘空间充足
3. 查看日志中的错误信息

## 联系方式

如有问题，请查看：
- 项目文档：`docs/developer_guide/examples/workstation_architecture.md`
- 设备驱动示例：`unilabos/devices/workstation/electrochem/`
- 提交Issue：https://github.com/your-repo/issues

## 更新日志

- **2026-05-27**: 初始版本
  - 实现基础驱动框架
  - 支持偶氮反应工作流
  - 支持光谱数据采集和保存
