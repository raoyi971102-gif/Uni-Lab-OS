# IVI 原厂驱动适配

本目录提供示波器的 IVI-C、IVI-COM 适配，以及电源和函数发生器的 IVI-C 适配。
`models.json` 为逐型号白名单，每组附原厂支持清单链接及版本。
注册表 `unilabos/registry/devices/ivi_family_models.yaml` 对每个型号分别注册，强制绑定型号和驱动族。

状态为**规范兼容**，依据为原厂支持清单、IVI 标准接口及本地实现。未进行实机验证，也未在此环境安装、验证原厂 DLL。
软件契约测试不能代替硬件测试。此实现覆盖基本控制和采集，不表示支持所有厂商扩展功能。

## 安装与配置

1. 从 `models.json` 的原厂链接安装支持所选型号的驱动。不得把 IVI.NET、IVI-COM 文件当作 IVI-C DLL。
2. 安装与 Python 位数匹配的 VISA / 原厂 IO Libraries、IVI Shared Components，以及原厂软件要求的运行库。
3. IVI-C：填写 DLL **绝对路径**、头文件中的函数前缀和 VISA 资源地址。也可使用已配置逻辑名称的对应 IVI-C 类驱动；类驱动需单独安装，Shared Components 并不代替它。
4. IVI-COM：额外安装 `comtypes`，填写原厂已注册 ProgID，以及实际安装的 `IviScopeTypeLib`、`IviDriverTypeLib` 文件路径。所有 COM 操作使用同一个专用线程。
5. 旧版 Agilent546XX 资料仅列出 Windows 2000/XP/Vista 32 位。部署现代 Windows/Python 时须确认新版 SDK 或兼容环境，当前只作协议兼容判断，不能宣称当前机器可直接运行。

IVI-C 示例（大写占位内容须替换为本机实际配置）：

```python
from unilabos.devices.ivi_family.driver import IviCScope

scope = IviCScope(
    resource="YOUR_VISA_RESOURCE", model="UXR0134B", family="infiniium",
    dll_path=r"C:\YOUR_INSTALLED_DRIVER\YOUR_DRIVER.dll", prefix="YOUR_HEADER_PREFIX",
)
try:
    scope.configure_channel("Channel1", range_volts=2.0)
    scope.configure_record(seconds=0.01, points=10000)
    waveform = scope.read_waveform("Channel1", points=10000, timeout_ms=5000)
finally:
    scope.close()
```

通道名由原厂驱动决定；示例中的 `Channel1` 不适用于所有驱动。
初始化启用型号查询和量程检查，禁用模拟、禁用复位；实际型号与配置不一致时关闭会话并报错。
不在白名单中的型号会在加载 SDK 前被拒绝。不会因为字符串相似或同属某个系列自动接纳新型号。

电源 `configure_output` 先关闭所选输出，再设置限流与电压；需要显式调用 `set_output_enabled` 才打开输出。
函数发生器支持标准波形配置、输出开关、启动和中止；模式设置可能影响设备上的其他通道。
`close` 仅释放会话，不保证关闭硬件输出；实验结束时应先显式关闭输出。
模拟会话不对外开放，测试使用注入的假接口，目录不包含虚拟型号。

## 设备图片

注册型号的图片、来源和缺图清单位于 `unilabos/registry/device_images/ivi/`。
35 张图片关联 356 个注册型号，其中 28 个为型号对应图片、328 个使用明确标注的同代系列参考图；其余 343 个注册型号保持缺图。
图片仅用于展示，不增加支持型号或实机验证记录。`gallery.html` 可离线查看。

## 软件与图片检查

从仓库根目录执行：

```text
python -m unittest discover -s tests -p test_ivi_family_adapter.py -v
```

测试包括 ctypes 函数指针 ABI、会话回收、型号不一致、采样返回值、限流顺序、失败时不启用输出、COM 线程约束及全部型号注册路径。

## 标准依据

- [IVI-3.2 固有能力与初始化](https://www.ivifoundation.org/downloads/Architecture%20Specifications/IVI-3.2_Inherent_Capabilities_2017-02-07.pdf)
- [IVI-4.1 示波器](https://www.ivifoundation.org/downloads/Class%20Specifications/IVI-4.1_Scope_2016-10-14.pdf)
- [IVI-4.4 电源](https://www.ivifoundation.org/downloads/Class%20Specifications/IVI-4.4_DCPwr_2015-09-24.pdf)
- [IVI-4.3 函数发生器](https://www.ivifoundation.org/downloads/Class%20Specifications/IVI-4.3_Fgen_2016-10-14.pdf)
