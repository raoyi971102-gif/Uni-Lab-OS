from __future__ import annotations

from typing import Any, Sequence

from unilabos.devices.workstation.szlab_poly_studio.s08_cap_station import (
    CAP_CACHE_LENGTH,
    NODE_PARAMS_WRITTEN,
    NODE_PROCESS_COMPLETE,
    NODE_PROCESS_SELECT,
    _cap_cache_element_name,
)


class SzlabS08CapStationPseudoPlcClient:
    """模拟 szlab_poly_plc 对 S08 开盖工位（新版协议）的最小 OPC 读写行为。"""

    def __init__(
        self,
        *,
        station_ready: bool = True,
        allow_process: bool = True,
    ) -> None:
        self.values: dict[str, Any] = {
            "S08原点信号": station_ready,
            "S08允许加工": allow_process,
            "S08工艺选择": 0,
            "S08参数写入完成": False,
            "S08工艺完成": 0,
            "S082瓶盖暂存位": 0,
        }
        for slot in range(1, 6):
            for index in range(CAP_CACHE_LENGTH):
                self.values[_cap_cache_element_name(slot, index)] = 0
        self.writes: list[tuple[str, Any]] = []

    def read(self, name: str) -> Any:
        if name == NODE_PROCESS_COMPLETE:
            if self.values.get(NODE_PARAMS_WRITTEN):
                return int(self.values.get(NODE_PROCESS_SELECT, 0))
            return 0
        return self.values.get(name, 0)

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value
        self.writes.append((name, value))

    def seed_slot_sample_id(self, slot: int, sample_id: Sequence[int]) -> None:
        for index, value in enumerate(sample_id):
            self.values[_cap_cache_element_name(slot, index)] = int(value)
