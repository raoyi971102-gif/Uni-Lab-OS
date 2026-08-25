import importlib
from pathlib import Path

import openpyxl
import pytest

from unilabos.registry.decorators import get_action_meta


xuse_module = importlib.import_module("unilabos.devices.workstation.XUSE.XUSE")
XUSEDevice = xuse_module.XUSEDevice


def _write_recipe(path: Path, powder_name: str, weight: float = 99.0) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "单值参数"
    sheet.append(["参数名", "参数值", "数据类型"])
    sheet.append(["粉末名称", powder_name, "STRING"])
    sheet.append(["加样_重量", weight, "FLOAT"])
    workbook.save(path)


def _write_plan(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "7"
    sheet.append(["加粉名称", "对应重量"])
    sheet.append(["Powder-A", 1.25])
    sheet.append(["Powder-B", 2.5])
    workbook.save(path)


def test_ball_mill_can_number_handles_form_a_chain():
    action_names = [
        "pick_can_from_can_rack",
        "place_empty_can_to_open_can_position",
        "pick_empty_can_from_open_can_position",
        "place_can_to_add_powder_position",
        "add_powder_multiple_times",
    ]

    for index, action_name in enumerate(action_names):
        handles = get_action_meta(getattr(XUSEDevice, action_name))["handles"]
        if index:
            input_handle = handles["input"][0]
            assert input_handle["data_type"] == "xuse_ball_mill_can_number"
            assert input_handle["data_key"] == "can_number"
            assert input_handle["data_source"] == "handle"
        output_handle = handles["output"][0]
        assert output_handle["data_type"] == "xuse_ball_mill_can_number"
        assert output_handle["data_key"] == "can_number"
        assert output_handle["data_source"] == "executor"


def test_multi_powder_plan_uses_sheet_order_and_recipe_overrides(tmp_path, monkeypatch):
    fake_repo = tmp_path / "repo"
    fake_xuse_file = fake_repo / "unilabos" / "devices" / "workstation" / "XUSE" / "XUSE.py"
    params_dir = fake_repo / "powder_params"
    params_dir.mkdir(parents=True)
    plan_file = tmp_path / "plan.xlsx"
    _write_plan(plan_file)
    _write_recipe(params_dir / "Powder-A.xlsx", "Powder-A")
    # 验证文件名不匹配时，可以使用旧参数文件内部的“粉末名称”匹配。
    _write_recipe(params_dir / "opaque-name.xlsx", "Powder-B")
    monkeypatch.setattr(xuse_module, "__file__", str(fake_xuse_file))

    class FakeDevice(XUSEDevice):
        def __init__(self):
            self.param_calls = []
            self.add_calls = []

        def set_add_powder_params(self, **kwargs):
            self.param_calls.append(kwargs)
            return {"success": True, "data": {"written": 2}}

        def add_powder(self, check_can_occupied=True):
            self.add_calls.append(check_can_occupied)
            return {"success": True}

    device = FakeDevice()
    result = device.add_powder_multiple_times(
        can_number=7,
        plan_file=str(plan_file),
        powder_params_dir="powder_params",
        record_dir="records",
        check_can_occupied=False,
    )

    assert result["success"] is True
    assert result["can_number"] == 7
    assert result["data"]["completed"] == 2
    assert [call["powder_name_override"] for call in device.param_calls] == [
        "Powder-A",
        "Powder-B",
    ]
    assert [call["weight_override"] for call in device.param_calls] == [1.25, 2.5]
    assert Path(device.param_calls[0]["param_file"]).name == "Powder-A.xlsx"
    assert Path(device.param_calls[1]["param_file"]).name == "opaque-name.xlsx"
    assert device.add_calls == [False, False]


@pytest.mark.parametrize("configured_dir", ["", "_DEFAULT_POWDER_PARAMS_DIR"])
def test_multi_powder_plan_uses_default_directory(
    tmp_path, monkeypatch, configured_dir
):
    fake_repo = tmp_path / "repo"
    fake_xuse_file = fake_repo / "unilabos" / "devices" / "workstation" / "XUSE" / "XUSE.py"
    params_dir = fake_xuse_file.parent / "powder_params"
    params_dir.mkdir(parents=True)
    plan_file = tmp_path / "plan.xlsx"
    _write_plan(plan_file)
    _write_recipe(params_dir / "Powder-A.xlsx", "Powder-A")
    _write_recipe(params_dir / "Powder-B.xlsx", "Powder-B")
    monkeypatch.setattr(xuse_module, "__file__", str(fake_xuse_file))

    class FakeDevice(XUSEDevice):
        def __init__(self):
            self.param_calls = []

        def set_add_powder_params(self, **kwargs):
            self.param_calls.append(kwargs)
            return {"success": True, "data": {"written": 2}}

        def add_powder(self, check_can_occupied=True):
            return {"success": True}

    device = FakeDevice()
    result = device.add_powder_multiple_times(
        can_number=7,
        plan_file=f'"{plan_file}"',
        powder_params_dir=configured_dir,
        record_dir=f'"{tmp_path / "records"}"',
        check_can_occupied=False,
    )

    assert result["success"] is True
    assert Path(result["data"]["powder_params_dir"]) == Path(
        XUSEDevice._DEFAULT_POWDER_PARAMS_DIR
    )
    assert len(device.param_calls) == 2


def test_multi_powder_plan_accepts_absolute_parameter_directory(tmp_path, monkeypatch):
    fake_repo = tmp_path / "repo"
    fake_xuse_file = fake_repo / "unilabos" / "devices" / "workstation" / "XUSE" / "XUSE.py"
    params_dir = tmp_path / "external-powder-params"
    params_dir.mkdir()
    plan_file = tmp_path / "plan.xlsx"
    _write_plan(plan_file)
    _write_recipe(params_dir / "Powder-A.xlsx", "Powder-A")
    _write_recipe(params_dir / "Powder-B.xlsx", "Powder-B")
    monkeypatch.setattr(xuse_module, "__file__", str(fake_xuse_file))

    class FakeDevice(XUSEDevice):
        def __init__(self):
            self.param_calls = []

        def set_add_powder_params(self, **kwargs):
            self.param_calls.append(kwargs)
            return {"success": True, "data": {"written": 2}}

        def add_powder(self, check_can_occupied=True):
            return {"success": True}

    device = FakeDevice()
    result = device.add_powder_multiple_times(
        can_number=7,
        plan_file=str(plan_file),
        powder_params_dir=f'"{params_dir}"',
        check_can_occupied=False,
    )

    assert result["success"] is True
    assert Path(result["data"]["powder_params_dir"]) == params_dir
    assert {Path(call["param_file"]).parent for call in device.param_calls} == {
        params_dir
    }


def test_set_add_powder_params_applies_name_and_weight_overrides(tmp_path):
    recipe = tmp_path / "recipe.xlsx"
    _write_recipe(recipe, "old-name", 99.0)

    class FakeDevice(XUSEDevice):
        def __init__(self):
            self.writes = []

        def _wait_until_true(self, *args, **kwargs):
            return True

        def set_node_value(self, name, value):
            self.writes.append((name, value))
            return True

        def _send_param_handshake(self, *args, **kwargs):
            return True

        def _dump_add_powder_snapshot(self, **kwargs):
            return "record.xlsx"

    device = FakeDevice()
    result = device.set_add_powder_params(
        param_file=str(recipe),
        check_can_occupied=False,
        powder_name_override="new-name",
        weight_override=1.75,
    )

    assert result["success"] is True
    assert ("粉末名称", "new-name") in device.writes
    assert ("加样_重量", 1.75) in device.writes
    assert ("粉末名称", "old-name") not in device.writes
    assert ("加样_重量", 99.0) not in device.writes
