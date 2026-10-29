# 偶氮反应工作站驱动

微流控系统：两个蠕动泵将两种液体经 Y 形接头泵入同一管路，流经控温装置反应后，由光谱仪在线表征。

## 系统组成

| 设备 | 注册表 ID | 通信 | 说明 |
| --- | --- | --- | --- |
| RS485 串口 | `azo_rs485_serial` | COM, 38400 8N1 | 泵与温控共享，实例 id 必须以 `serial_` 开头 |
| 蠕动泵 | `azo.peristaltic_pump` | Modbus 站号 5 / 6 | 对外流速 mL/min，内部换算为步进电机 rpm |
| 温控器 | `azo.temperature_controller` | Modbus 站号 1 | TEC107/115，温度 /100000 ℃，输出使能 0x1100 |
| 光谱仪 | `azo.spectrometer` | IdeaOptics USB | 独享 USB，使用包内 SDK |
| 工作站 | `azo_workstation` | 编排子设备 | 不直接占有串口 |

```
泵A(站号5) ─┐
            ├─ RS485 (serial_485) ─ COM
泵B(站号6) ─┤
温控(站号1) ─┘

光谱仪 ── IdeaOptics USB Device
```

## 流速换算

对外动作 `set_speed(flow_rate)` 使用 **mL/min**。内部：

```
rpm = flow_rate / volume_per_rev
```

若配置了 `flow_to_rpm_ratio`，则优先使用 `rpm = flow_rate * flow_to_rpm_ratio`。

标定后把实测 `volume_per_rev`（mL/rev）写入图文件泵节点 config。当前默认 `1.0`，即 1 mL/min = 1 rpm。

## 启动

```bash
unilab --graph unilabos/test/experiments/azo_workstation.json --backend ros
```

图文件中请按实际环境修改 `serial_485.config.port`。无硬件联调时，可给各子设备 config 增加 `"simulate": true`（串口模拟时不会打开 COM 口）。

主工作流动作：`run_azo_reaction(flow_rate_a, flow_rate_b, temperature, duration, spectrum_interval)`。

## 单设备调试

不启动 Uni-Lab 时，可在本目录运行：

```bash
python test_pump.py COM10 38400
python test_temperature.py COM10 38400
python test_spectrometer.py
```
