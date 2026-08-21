# 偶氮工站设备调试指南

独立脚本，不需要启动 Uni-Lab。泵和温控会实例化正式驱动，并手动把 `serial_485.transact` 绑到设备上。

```bash
cd unilabos/devices/workstation/azo
python test_pump.py COM10 38400
python test_temperature.py COM10 38400
python test_spectrometer.py
python test_spectrometer.py --simulate
```

## 通信参数

- RS485：38400, 8N1
- 泵 A 站号 5，泵 B 站号 6，寄存器 PA-53（地址 53），int16 rpm
- 温控站号 1：目标温度 0x1000、实际温度 0x1002，int32，单位 /100000 ℃；输出使能 0x1100（uint16，用功能码 0x10 写入 1 个寄存器，1=开始控温，0=停止控温）
- 光谱仪：设备管理器中为 `IdeaOptics USB Device`，需要 pythonnet 与包内 `光谱仪/Python4CyUSB/dlls/Ideaoptics.USB.SDK.dll`
