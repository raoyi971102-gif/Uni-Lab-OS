"""通过已安装的 IVI-COM 标准 IIviScope 接口控制泰克示波器。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
from typing import Any, Callable

from .driver import bounded_int, finite, model_info, normalized


class IviComScope:
    """COM 对象及其调用始终在同一个专用线程，避免跨线程接口失效。"""

    def __init__(self, resource: str, model: str, family: str,
                 prog_id: str, scope_type_library: str, driver_type_library: str):
        model_info(family, model, 'IVI-COM')
        self.model, self.family = model, family
        self._lock = threading.RLock()
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ivi-com')
        try:
            self._pool.submit(self._open, resource, model, prog_id, scope_type_library, driver_type_library).result()
        except BaseException:
            self._closed = True
            self._pool.shutdown(wait=True)
            raise

    @staticmethod
    def _create(prog_id: str, library: str, driver_library: str) -> tuple[Any, Any, Callable[[], None]]:
        if os.name != 'nt' or any(not Path(p).is_absolute() or not Path(p).is_file() for p in (library, driver_library)):
            raise RuntimeError('需要 Windows 和已安装 IviScopeTypeLib 类型库的绝对路径')
        # 延迟导入；扫描注册表时无需安装 SDK，也不会连接设备。
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        try:
            module = comtypes.client.GetModule(library)
            driver_module = comtypes.client.GetModule(driver_library)
            obj = comtypes.client.CreateObject(prog_id, interface=module.IIviScope)
            return obj, obj.QueryInterface(driver_module.IIviDriver), comtypes.CoUninitialize
        except BaseException:
            comtypes.CoUninitialize()
            raise

    def _open(self, resource: str, model: str, prog_id: str, library: str, driver_library: str) -> None:
        self._scope, self._driver, self._uninitialize = self._create(prog_id, library, driver_library)
        initialized = False
        try:
            self._driver.Initialize(resource, True, False,
                                   'Simulate=0,RangeCheck=1,QueryInstrStatus=1,Cache=0')
            initialized = True
            self.instrument_model = self._driver.Identity.InstrumentModel
            if normalized(self.instrument_model) != normalized(model):
                raise ValueError(f'实际型号 {self.instrument_model} 与配置型号 {model} 不一致')
        except BaseException:
            try:
                if initialized:
                    self._driver.Close()
            finally:
                self._scope = None
                self._driver = None
                self._uninitialize()
            raise

    def _run(self, operation: Callable[[], Any]) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError('设备会话已关闭')
            return self._pool.submit(operation).result()

    def configure_channel(self, channel: str, range_volts: float, offset_volts: float = 0,
                          coupling: str = 'DC', probe_attenuation: float = 1,
                          enabled: bool = True) -> None:
        args = (finite(range_volts, True), finite(offset_volts),
                {'AC': 0, 'DC': 1, 'GND': 2}[coupling], finite(probe_attenuation, True), bool(enabled))
        self._run(lambda: self._scope.Channels.Item(channel).Configure(*args))

    def configure_record(self, seconds: float, points: int, start_seconds: float = 0) -> None:
        args = (finite(seconds, True), bounded_int(points, 10000000), finite(start_seconds))
        self._run(lambda: self._scope.Acquisition.ConfigureRecord(*args))

    def read_waveform(self, channel: str, timeout_ms: int = 5000) -> dict:
        timeout_ms = bounded_int(timeout_ms)
        def read() -> dict:
            # comtypes 从标准类型库生成 [in,out] 参数；空数组请求原厂分配。
            volts, initial, interval = self._scope.Measurements.Item(channel).ReadWaveform(
                timeout_ms, (), 0.0, 0.0)
            return {'volts': list(volts), 'initial_seconds': float(initial),
                    'interval_seconds': float(interval)}
        return self._run(read)

    def abort(self) -> None:
        self._run(lambda: self._scope.Measurements.Abort())

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            def finish() -> None:
                try:
                    self._driver.Close()
                finally:
                    self._scope = None
                    self._driver = None
                    self._uninitialize()
            try:
                self._pool.submit(finish).result()
            finally:
                self._closed = True
                self._pool.shutdown(wait=True)
