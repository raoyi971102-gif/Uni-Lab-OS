"""SZLab virtual mixer workstation devices."""

from typing import TYPE_CHECKING

__all__ = [
    "SzlabMixerStirrerDevice",
    "SzlabMixerPumpDevice",
]

if TYPE_CHECKING:
    from unilabos.devices.workstation.szlab_mixer.pump import SzlabMixerPumpDevice
    from unilabos.devices.workstation.szlab_mixer.stirrer import SzlabMixerStirrerDevice


def __getattr__(name: str):
    if name == "SzlabMixerPumpDevice":
        from unilabos.devices.workstation.szlab_mixer.pump import SzlabMixerPumpDevice

        return SzlabMixerPumpDevice
    if name == "SzlabMixerStirrerDevice":
        from unilabos.devices.workstation.szlab_mixer.stirrer import SzlabMixerStirrerDevice

        return SzlabMixerStirrerDevice
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
