"""原厂 IVI-C 示波器/电源适配器。仅使用已登记型号，不提供虚拟设备。"""
from __future__ import annotations

import ctypes as C
import json
import math
import os
from pathlib import Path
import re
import threading
import warnings
from typing import Any

S, I, B, D, T = C.c_uint32, C.c_int32, C.c_uint16, C.c_double, C.c_char_p
P = C.POINTER
CATALOG = json.loads(Path(__file__).with_name('models.json').read_text(encoding='utf-8'))
MODEL_ATTRIBUTE = 1050512  # IVI-3.1 属性基址与 IVI-3.2 的型号属性偏移。


def normalized(model: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', model.upper())


def model_info(family: str, model: str, interface: str) -> dict[str, Any]:
    entry = next((x for x in CATALOG if x['id'] == family), None)
    if not entry or entry['interface'] != interface:
        raise ValueError('驱动族或接口不匹配')
    if normalized(model) not in {normalized(x) for x in entry['models']}:
        raise ValueError('型号未列入原厂支持清单，禁止自动推断')
    return entry


def finite(value: float, positive: bool = False) -> float:
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('参数必须为有限数；正值参数必须大于零')
    return value


def bounded_int(value: int, upper: int = 2147483647) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise ValueError(f'整数参数必须在 1 至 {upper} 之间')
    return value


class IviCDevice:
    """加载用户已安装的原厂 IVI-C DLL；位数须与 Python 一致。"""
    IVI_CLASS = ''

    def __init__(self, resource: str, model: str, family: str, dll_path: str, prefix: str):
        entry = model_info(family, model, 'IVI-C')
        if entry['ivi_class'] != self.IVI_CLASS:
            raise ValueError('设备类别不匹配')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', prefix):
            raise ValueError('prefix 必须来自所安装驱动的 C 头文件')
        self.model, self.family, self.prefix = model, family, prefix
        self._lock = threading.RLock()
        self._session = S(0)
        self._closed = True
        self._dll = self._load_library(dll_path)
        # 在连接硬件前验证接口完整性。
        self._bind('close', [S])
        self._bind('GetAttributeViString', [S, T, I, I, P(C.c_char)])
        self._bind('error_message', [S, I, P(C.c_char)])
        self._bind_operations()
        init = self._bind('InitWithOptions', [T, B, B, T, P(S)])
        try:
            # 不复位、不使用模拟；由原厂驱动执行 ID 查询和量程检查。
            self._check(init(resource.encode('ascii'), 1, 0,
                             b'Simulate=0,RangeCheck=1,QueryInstrStatus=1,Cache=0',
                             C.byref(self._session)), 'InitWithOptions')
            self._closed = False
            buf = C.create_string_buffer(1024)
            self._call('GetAttributeViString', b'', MODEL_ATTRIBUTE, len(buf), buf)
            self.instrument_model = buf.value.decode('ascii', errors='replace').strip()
            if normalized(self.instrument_model) != normalized(model):
                raise ValueError(f'实际型号 {self.instrument_model} 与配置型号 {model} 不一致')
        except BaseException:
            if self._session.value:
                self._dll_close(self._session)
            self._closed = True
            raise

    @staticmethod
    def _load_library(path: str) -> Any:
        p = Path(path)
        if os.name != 'nt' or not p.is_absolute() or not p.is_file():
            raise RuntimeError('需要 Windows 和原厂 IVI-C DLL 的有效绝对路径')
        return C.WinDLL(str(p))

    def _bind(self, name: str, args: list[Any]) -> Any:
        try:
            fn = getattr(self._dll, self.prefix + '_' + name)
        except AttributeError as exc:
            raise RuntimeError(f'原厂 DLL 缺少 {self.prefix}_{name}；请检查接口、版本和前缀') from exc
        fn.restype, fn.argtypes = I, args
        setattr(self, '_dll_' + name, fn)
        return fn

    def _bind_operations(self) -> None:
        raise NotImplementedError

    def _check(self, status: int, operation: str) -> None:
        if status < 0:
            buf = C.create_string_buffer(1024)
            self._dll_error_message(self._session, status, buf)
            raise RuntimeError(f'{operation} 失败 ({status}): {buf.value.decode(errors="replace")}')
        if status > 0:
            warnings.warn(f'{operation} 返回原厂警告 {status}', RuntimeWarning, stacklevel=2)

    def _call(self, name: str, *args: Any) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError('设备会话已关闭')
            self._check(getattr(self, '_dll_' + name)(self._session, *args), name)

    def close(self) -> None:
        """释放会话；不擅自改变设备输出状态。"""
        with self._lock:
            if not self._closed:
                self._call('close')
                self._closed = True


class IviCScope(IviCDevice):
    IVI_CLASS = 'IviScope'

    def _bind_operations(self) -> None:
        self._bind('ConfigureChannel', [S, T, D, D, I, D, B])
        self._bind('ConfigureAcquisitionRecord', [S, D, I, D])
        self._bind('ReadWaveform', [S, T, I, I, P(D), P(I), P(D), P(D)])
        self._bind('Abort', [S])

    def configure_channel(self, channel: str, range_volts: float, offset_volts: float = 0,
                          coupling: str = 'DC', probe_attenuation: float = 1,
                          enabled: bool = True) -> None:
        """设置通道满量程、偏置、耦合与探头倍率。"""
        self._call('ConfigureChannel', channel.encode('ascii'), finite(range_volts, True),
                   finite(offset_volts), {'AC': 0, 'DC': 1, 'GND': 2}[coupling],
                   finite(probe_attenuation, True), bool(enabled))

    def configure_record(self, seconds: float, points: int, start_seconds: float = 0) -> None:
        self._call('ConfigureAcquisitionRecord', finite(seconds, True), bounded_int(points, 10000000),
                   finite(start_seconds))

    def read_waveform(self, channel: str, points: int = 10000, timeout_ms: int = 5000) -> dict:
        """触发采样，返回电压数组及相对触发点的时间轴参数。"""
        points = bounded_int(points, 10000000)
        timeout_ms = bounded_int(timeout_ms)
        data, actual, initial, interval = (D * points)(), I(), D(), D()
        self._call('ReadWaveform', channel.encode('ascii'), points, timeout_ms, data,
                   C.byref(actual), C.byref(initial), C.byref(interval))
        if not 0 <= actual.value <= points:
            raise RuntimeError('原厂驱动返回的采样点数超出缓冲区')
        return {'volts': list(data[:actual.value]), 'initial_seconds': initial.value,
                'interval_seconds': interval.value}

    def abort(self) -> None:
        self._call('Abort')


class IviCPowerSupply(IviCDevice):
    IVI_CLASS = 'IviDCPwr'

    def _bind_operations(self) -> None:
        self._bind('ConfigureVoltageLevel', [S, T, D])
        self._bind('ConfigureCurrentLimit', [S, T, I, D])
        self._bind('ConfigureOutputEnabled', [S, T, B])
        self._bind('Measure', [S, T, I, P(D)])

    def configure_output(self, channel: str, voltage: float, current_limit: float,
                         trip_on_limit: bool = True) -> None:
        """先关闭该通道，再配置限流与电压；启用输出须另行调用。"""
        v, limit = finite(voltage), finite(current_limit, True)
        ch = channel.encode('ascii')
        with self._lock:
            self._call('ConfigureOutputEnabled', ch, 0)
            self._call('ConfigureCurrentLimit', ch, int(bool(trip_on_limit)), limit)
            self._call('ConfigureVoltageLevel', ch, v)

    def set_output_enabled(self, channel: str, enabled: bool) -> None:
        self._call('ConfigureOutputEnabled', channel.encode('ascii'), bool(enabled))

    def measure(self, channel: str, quantity: str = 'voltage') -> float:
        value = D()
        self._call('Measure', channel.encode('ascii'), {'voltage': 1, 'current': 0}[quantity], C.byref(value))
        return value.value


class IviCFunctionGenerator(IviCDevice):
    IVI_CLASS = 'IviFgen'

    def _bind_operations(self) -> None:
        self._bind('ConfigureOutputMode', [S, I])
        self._bind('ConfigureStandardWaveform', [S, T, I, D, D, D, D])
        self._bind('ConfigureOutputEnabled', [S, T, B])
        self._bind('InitiateGeneration', [S])
        self._bind('AbortGeneration', [S])

    def configure_waveform(self, channel: str, frequency_hz: float, amplitude_vpp: float,
                           offset_volts: float = 0, waveform: str = 'sine', phase_degrees: float = 0) -> None:
        """关闭所选输出后设置标准波形；切换模式可能同时影响其他通道。"""
        f, a, offset, phase = finite(frequency_hz, True), finite(amplitude_vpp, True), finite(offset_volts), finite(phase_degrees)
        shape = {'sine': 1, 'square': 2, 'triangle': 3, 'ramp_up': 4, 'ramp_down': 5}[waveform]
        ch = channel.encode('ascii')
        with self._lock:
            self._call('ConfigureOutputEnabled', ch, 0)
            self._call('AbortGeneration')
            self._call('ConfigureOutputMode', 0)
            self._call('ConfigureStandardWaveform', ch, shape, a, offset, f, phase)

    def set_output_enabled(self, channel: str, enabled: bool) -> None:
        self._call('ConfigureOutputEnabled', channel.encode('ascii'), bool(enabled))

    def initiate(self) -> None:
        self._call('InitiateGeneration')

    def abort(self) -> None:
        self._call('AbortGeneration')
