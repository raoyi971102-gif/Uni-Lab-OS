from pathlib import Path
from importlib.util import find_spec

import pytest

import unilabos.devices.workstation.szlab_poly_studio as szlab_poly_studio


def test_szlab_poly_studio_imports_from_device_workstation_package():
    assert "SZLabPolyPLCDevice" in szlab_poly_studio.__all__
    assert "S1Workstation" in szlab_poly_studio.__all__
    assert "SZLabPolyStudioDeck" in szlab_poly_studio.__all__
    assert "SzlabMixerPumpDevice" in szlab_poly_studio.__all__
    assert "SzlabMixerPhotoShottingDevice" in szlab_poly_studio.__all__


def test_szlab_poly_studio_default_csv_resolves_inside_workstation_package():
    if find_spec("pylabrobot") is None:
        pytest.skip("pylabrobot 未安装，跳过依赖 PLC 模块导入的布局检查")

    from unilabos.devices.workstation.szlab_poly_studio.plc import _resolve_csv_path

    csv_path = Path(_resolve_csv_path(None))

    assert csv_path.name == "szlab_plc_0610.csv"
    assert csv_path.parent.parts[-4:] == (
        "unilabos",
        "devices",
        "workstation",
        "szlab_poly_studio",
    )
    assert csv_path.exists()


def test_szlab_poly_studio_latest_csv_supports_utf16_encoding():
    if find_spec("pylabrobot") is None:
        pytest.skip("pylabrobot 未安装，跳过依赖 PLC 模块导入的 CSV 检查")

    from unilabos.devices.workstation.szlab_poly_studio.plc import load_variable_names_from_csv

    csv_path = Path("unilabos/devices/workstation/szlab_poly_studio/szlab_plc_0623.csv")

    names = load_variable_names_from_csv(str(csv_path))

    assert "S05加工完成" in names
    assert "S05拍照结果" in names


def test_temporary_top_level_workstation_package_path_is_removed():
    removed_module_path = ".".join(
        ["unilabos", "workstation", "szlab_poly_studio"]
    )

    with pytest.raises(ModuleNotFoundError):
        __import__(removed_module_path)
