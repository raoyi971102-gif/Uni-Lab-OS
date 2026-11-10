"""中析辅助功能字符串：反向吸液 / 吹样 必须与官方软件导出一致。"""

from unilabos.devices.liquid_handling.prcxi.prcxi import (
    _prcxi_format_assist_fun,
    _prcxi_imbibing_assist_funs,
    _prcxi_tapping_assist_fun,
)


def test_reverse_aspirate_matches_official_export() -> None:
    assert _prcxi_format_assist_fun("反向吸液", 5) == " 反向吸液(5ul)"
    assert _prcxi_format_assist_fun("反向吸液", 5.0) == " 反向吸液(5ul)"
    assert _prcxi_format_assist_fun("反向吸液", 50) == " 反向吸液(50ul)"


def test_blowout_matches_official_export() -> None:
    assert _prcxi_format_assist_fun("吹样", 5.0) == " 吹样(5ul)"
    assert _prcxi_format_assist_fun("吹样", 1) == " 吹样(1ul)"


def test_hover_matches_official_export() -> None:
    assert _prcxi_format_assist_fun("悬停", 10, "s") == " 悬停(10s)"
    assert _prcxi_format_assist_fun("悬停", 3.0, "s") == " 悬停(3s)"
    assert _prcxi_format_assist_fun("悬停", 60, "s") == " 悬停(60s)"


def test_imbibing_packs_hover_before_reverse_aspirate() -> None:
    """官方同时开悬停+反向吸液时 AssistA=悬停、AssistB=反向吸液。"""
    assert _prcxi_imbibing_assist_funs(hover_s=10, reverse_ul=20) == (
        " 悬停(10s)",
        " 反向吸液(20ul)",
        "",
        "",
        "",
    )
    assert _prcxi_imbibing_assist_funs(reverse_ul=20)[0] == " 反向吸液(20ul)"
    assert _prcxi_imbibing_assist_funs(hover_s=10)[0] == " 悬停(10s)"
    assert _prcxi_imbibing_assist_funs()[0] == ""


def test_tapping_override_allows_blowout_larger_than_aspirated_air() -> None:
    """吹样独立下发，可以大于 PLR op 上的反向吸液气垫。"""
    assert _prcxi_tapping_assist_fun(20.0, override=True, override_ul=25) == " 吹样(25ul)"
    assert _prcxi_tapping_assist_fun(20.0, override=True, override_ul=None) == " 吹样(5ul)"
    assert _prcxi_tapping_assist_fun(20.0) == " 吹样(20ul)"
    assert _prcxi_tapping_assist_fun(None) == " 吹样(5ul)"
