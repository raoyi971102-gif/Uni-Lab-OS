"""S08 测试共用 fixture，供 CI 与 local 手动测试复用。"""

from __future__ import annotations

from tests.szlab_poly_studio.pseudo_clients.s08_cap_station import PseudoSzlabS08OpcUaClient
from tests.szlab_poly_studio.s08_driver_loader import load_s08_cap_station_module

_s08_module = load_s08_cap_station_module()
NODE_PARAMS_WRITTEN = _s08_module.NODE_PARAMS_WRITTEN
NODE_PROCESS_SELECT = _s08_module.NODE_PROCESS_SELECT
NODE_PROCESS_COMPLETE = _s08_module.NODE_PROCESS_COMPLETE
NODE_STATION_STATUS = _s08_module.NODE_STATION_STATUS
S08ProcessType = _s08_module.S08ProcessType
SZLabS08CapStationDevice = _s08_module.SZLabS08CapStationDevice
_cap_cache_element_name = _s08_module._cap_cache_element_name
s08_module = _s08_module

SAMPLE_A = [101, 102, 103]
SAMPLE_B = [201, 202, 203]


def make_s08_device(
    client: PseudoSzlabS08OpcUaClient | None = None,
    *,
    timeout: float = 1.0,
    require_station_status: bool = False,
    validate_cap_constraints: bool = False,
) -> tuple[SZLabS08CapStationDevice, PseudoSzlabS08OpcUaClient]:
    pseudo = client or PseudoSzlabS08OpcUaClient()
    device = SZLabS08CapStationDevice(
        url="opc.tcp://127.0.0.1:0/unused",
        timeout=timeout,
        opcua_client=pseudo,
    )
    if require_station_status:
        device.require_station_status = True
    if validate_cap_constraints:
        device.validate_cap_constraints = True
    return device, pseudo
