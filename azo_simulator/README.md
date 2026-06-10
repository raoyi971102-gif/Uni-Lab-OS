# Azo 工作站模拟器

这个目录提供偶氮反应工作站的离线模拟设备，用于不连接真实硬件时调试流程、光谱保存和设备状态。

当前模拟内容：

- `SimulatedPeristalticPump`：模拟泵 A / 泵 B，接口对齐 `PeristalticPump`。
- `SimulatedTemperatureController`：模拟温控器升温、降温和读温，接口对齐 `TemperatureController`。
- `SimulatedSpectrometer`：模拟 IdeaOptics 光谱仪连接、采集和保存数据，接口对齐 `SpectrometerDriver`。
- `SimulatedAzoWorkstation`：组合以上设备，模拟整站偶氮反应流程。
- `SimulatedRS485Bus`：模拟当前泵和温控驱动使用的 Modbus RTU 二进制帧，可注入真实驱动做低层通信调试。

## 快速运行整站模拟

在仓库根目录执行：

```bash
python -m azo_simulator.run_demo --duration 5 --spectrum-interval 1
```

指定保存目录：

```bash
python -m azo_simulator.run_demo --data-dir azo_simulator_data
```

运行后会生成模拟光谱 CSV 和实验 summary JSON。

## Python 中使用

```python
from azo_simulator import SimulatedAzoWorkstation

workstation = SimulatedAzoWorkstation(data_save_dir="azo_simulator_data")
workstation.run_azo_reaction(
    flow_rate_a=2.0,
    flow_rate_b=1.5,
    temperature=60.0,
    duration=10.0,
    spectrum_interval=2.0,
)

print(workstation.get_workstation_status())
```

## 注入真实底层驱动测试 Modbus

如果只想测试真实 `PeristalticPump` / `TemperatureController` 的命令构造和响应解析，可以使用 `SimulatedRS485Bus`：

```python
from azo_simulator import SimulatedRS485Bus
from unilabos.devices.workstation.azo.peristaltic_pump import PeristalticPump
from unilabos.devices.workstation.azo.temperature_controller import TemperatureController

bus = SimulatedRS485Bus()

pump_a = PeristalticPump("pump_a", modbus_address=5)
pump_a.serial_write = bus.write
pump_a.serial_read = bus.read

temp = TemperatureController("temp_controller", modbus_address=1)
temp.serial_write = bus.write
temp.serial_read = bus.read

pump_a.set_flow_rate(2.0)
temp.start_temperature_control(60.0)
print(temp.read_actual_temperature())
print(bus.snapshot())
```

## 设计约束

- 不修改 `unilabos/devices/workstation/azo` 下的真实驱动。
- 不依赖真实串口、ROS2、pythonnet 或光谱仪 DLL。
- 使用标准库实现，方便 CI 和本地快速调试。
