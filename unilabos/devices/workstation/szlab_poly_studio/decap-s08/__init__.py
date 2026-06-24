"""SZLab Poly Studio S08 cap station device."""

from importlib import import_module

SZLabS08CapStationDevice = import_module(__name__ + ".s08_cap_station").SZLabS08CapStationDevice

__all__ = ["SZLabS08CapStationDevice"]
