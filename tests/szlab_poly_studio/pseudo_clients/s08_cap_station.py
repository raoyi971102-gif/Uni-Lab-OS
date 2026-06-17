from __future__ import annotations

from typing import Any, Sequence

from unilabos.devices.workstation.szlab_poly_studio.s08_cap_station import (
    CAP_CACHE_LENGTH,
    UNIT_VARIABLES,
    _cap_cache_element_name,
)


class SzlabS08CapStationPseudoPlcClient:
    """模拟 szlab_poly_plc 对 S08 开盖工位的最小 OPC 读写行为。"""

    def __init__(
        self,
        *,
        station_ready: bool = True,
        allow_liquid: bool = True,
        allow_solid: bool = True,
        process_complete: bool = False,
    ) -> None:
        self.values: dict[str, Any] = {
            "S08原点信号": station_ready,
            "S081_1允许加工": allow_liquid,
            "S081_2允许加工": allow_solid,
            "S081_1工艺任务": 0,
            "S081_2工艺任务": 0,
            "S081_1参数写入完成": False,
            "S081_2参数写入完成": False,
            "S081_1加工完成": process_complete,
            "S081_2加工完成": process_complete,
            "S082瓶盖暂存位": 0,
        }
        for slot in range(1, 6):
            for index in range(CAP_CACHE_LENGTH):
                self.values[_cap_cache_element_name(slot, index)] = 0
        self.writes: list[tuple[str, Any]] = []
        self._completion_armed: dict[str, bool] = {
            UNIT_VARIABLES[1]["process_complete"]: False,
            UNIT_VARIABLES[2]["process_complete"]: False,
        }

    def read(self, name: str) -> Any:
        params_written_nodes = {
            UNIT_VARIABLES[1]["params_written"],
            UNIT_VARIABLES[2]["params_written"],
        }
        for complete_node, params_node in (
            (UNIT_VARIABLES[1]["process_complete"], UNIT_VARIABLES[1]["params_written"]),
            (UNIT_VARIABLES[2]["process_complete"], UNIT_VARIABLES[2]["params_written"]),
        ):
            if name == complete_node:
                if any(item == (params_node, True) for item in self.writes):
                    if self._completion_armed[complete_node]:
                        return True
                    self._completion_armed[complete_node] = True
                    return False
                return False
        return self.values.get(name, 0)

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value
        self.writes.append((name, value))

    def wait_equal(
        self,
        name: str,
        expected: Any,
        timeout: float = 300.0,
        interval: float = 0.2,
    ) -> bool:
        del timeout, interval
        return self.read(name) == expected

    def seed_slot_sample_id(self, slot: int, sample_id: Sequence[int]) -> None:
        for index, value in enumerate(sample_id):
            self.values[_cap_cache_element_name(slot, index)] = int(value)
