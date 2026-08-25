"""PRCXI 复用枪头动作测试（``one_channel_reuse_tip`` / ``eight_channels_reuse_tips``）。

覆盖：
  - 一副枪头跑完全程：``pick_up`` 只在首轮、``drop`` 只在末轮；
  - 每轮都从同一个 source 吸液，速率固定 30；
  - ``vols`` 长度规则（1 广播 / 逐轮指定 / 其余报错）；
  - 轴选择：按 ``pip_setting`` 选单通道轴（左）与 8 通道轴（右），未配置时回退硬编码；
  - 体积超轴量程 / 超枪头量程 → 报错（不做拆分）；
  - 8 通道 targets 整列校验（8 的倍数、同板、同列、A→H 顺序）；
  - 中途失败触发 ``_cleanup_after_failed_transfer``。

依赖 PRCXI 完整 import 链，环境无 PLR 时 ``skipif`` 跳过（与
``test_prcxi_pip_setting.py`` 的环境兼容策略一致）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from unilabos.devices.liquid_handling.prcxi.flatten_utils import normalize_pip_setting

try:
    from pylabrobot.resources import Coordinate

    from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300Handler
    from unilabos.devices.liquid_handling.prcxi.prcxi_labware import PRCXI_300ul_Tips

    _PLR_AVAILABLE = True
    _PLR_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - 环境相关
    Coordinate = None  # type: ignore[assignment, misc]
    PRCXI9300Handler = None  # type: ignore[assignment, misc]
    PRCXI_300ul_Tips = None  # type: ignore[assignment, misc]
    _PLR_AVAILABLE = False
    _PLR_IMPORT_ERROR = exc


_skip_if_no_plr = pytest.mark.skipif(
    not _PLR_AVAILABLE,
    reason=f"pylabrobot not importable in this env: {_PLR_IMPORT_ERROR!r}",
)

# 实机 9320 配置（unilabos/test/experiments/prcxi_9320_slim.json）：左 300µL/单通道、右 1000µL/8 通道。
PIP_9320 = {"left": {"vol": 300, "channels": 1}, "right": {"vol": 1000, "channels": 8}}
LEFT_CHANNELS = [0]
RIGHT_CHANNELS = [8, 9, 10, 11, 12, 13, 14, 15]


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


@dataclass
class _DummyWell:
    name: str
    parent: Optional[Any] = None

    def get_size_x(self) -> float:
        return 9.0

    def get_size_z(self) -> float:
        return 10.0


@dataclass
class _DummyPlate:
    """96 板替身：``children`` 按列优先（A1..H1, A2..H2, ...），``num_items_y=8``。"""

    name: str
    num_columns: int = 2
    num_items_y: int = 8
    parent: Optional[Any] = None
    children: List[_DummyWell] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.children:
            return
        rows = "ABCDEFGH"[: self.num_items_y]
        for col in range(1, self.num_columns + 1):
            for row in rows:
                self.children.append(_DummyWell(f"{row}{col}", parent=self))

    def column(self, col: int) -> List[_DummyWell]:
        """第 ``col`` 列（1-based）的 A~H。"""
        return self.children[(col - 1) * self.num_items_y : col * self.num_items_y]


@dataclass
class _FakeApiClient:
    sent: List[Any] = field(default_factory=list)

    def update_pipetting_position(self, matrix_id: str, positions: List[Any]) -> None:
        self.sent.append((matrix_id, positions))


@dataclass
class _FakeBackend:
    matrix_id: str = "matrix-1"
    api_client: _FakeApiClient = field(default_factory=_FakeApiClient)
    _active_axis: Optional[str] = None


def _make_fake_prcxi(*, pip_setting: Any = PIP_9320) -> Any:
    """构造跳过 ``__init__`` 的 handler：只保留复用枪头动作依赖的属性，其余 stub 掉。"""
    inst: Any = PRCXI9300Handler.__new__(PRCXI9300Handler)
    inst._first_transfer_done = True
    inst.step_mode = False
    inst.pip_setting = normalize_pip_setting(pip_setting)
    inst.tip_height = 0
    # no_matrix_id=False → _sync_pipetting_positions 只刷新 tip_height，不发位置同步
    inst.no_matrix_id = False
    inst._unilabos_backend = _FakeBackend()
    inst.x_increase = -0.003636
    inst.right_2_left = Coordinate(22, -1, 12)

    async def _identity_resolve(resources: Any) -> Any:
        return list(resources or [])

    inst._resolve_to_plr_resources = _identity_resolve  # type: ignore[assignment]
    inst._attach_resources_to_deck_if_needed = lambda *a, **kw: None  # type: ignore[assignment]
    # 枪头池初始化依赖 deck 上的真实资源扫描，与本用例关注点（轮次编排）无关
    inst.set_tiprack = lambda *a, **kw: None  # type: ignore[assignment]
    return inst


class _RoundCapture:
    """替换 ``_transfer_base_method``，记录每轮入参。"""

    def __init__(self, fail_at: Optional[int] = None) -> None:
        self.rounds: List[Dict[str, Any]] = []
        self._fail_at = fail_at

    async def __call__(self, **kwargs: Any) -> None:
        self.rounds.append(kwargs)
        if self._fail_at is not None and len(self.rounds) - 1 == self._fail_at:
            raise RuntimeError("boom mid round")


def _install_capture(prcxi: Any, fail_at: Optional[int] = None) -> _RoundCapture:
    capture = _RoundCapture(fail_at=fail_at)
    prcxi._transfer_base_method = capture  # type: ignore[assignment]
    return capture


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _stub_resource_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个动作返回 ``TransferLiquidReturn`` 时会把资源序列化；替身孔位不是真 PLR 资源，
    这里把序列化替换为孔名列表（本文件关注轮次编排，不关注序列化细节）。"""
    if not _PLR_AVAILABLE:
        return
    from unilabos.devices.liquid_handling.prcxi import prcxi as prcxi_mod

    class _NameDump:
        def __init__(self, resources: Any) -> None:
            self._resources = list(resources)

        def dump(self) -> List[str]:
            return [getattr(r, "name", "?") for r in self._resources]

    monkeypatch.setattr(
        prcxi_mod.ResourceTreeSet,
        "from_plr_resources",
        classmethod(lambda cls, resources, known_newly_created=False: _NameDump(resources)),
    )


def _tip_rack() -> Any:
    """真实 PRCXI 300µL tip 盒（枪头量程 300µL，用于量程校验用例）。"""
    return PRCXI_300ul_Tips("tr")


# ---------------------------------------------------------------------------
# one_channel_reuse_tip
# ---------------------------------------------------------------------------


@_skip_if_no_plr
class TestOneChannelReuseTip:
    def test_single_tip_across_all_targets(self) -> None:
        """4 个目标孔 → 4 轮；只有首轮取枪头、只有末轮丢枪头。"""
        prcxi = _make_fake_prcxi()
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))
        targets = plate.children[:4]

        _run(prcxi.one_channel_reuse_tip([source], targets, [_tip_rack()], vols=20.0))

        assert len(cap.rounds) == 4
        assert [r["pick_up"] for r in cap.rounds] == [True, False, False, False]
        assert [r["drop"] for r in cap.rounds] == [False, False, False, True]
        # 每轮都从同一个 source 吸液，目标按传入顺序逐个走
        assert all(r["sources"] == [source] for r in cap.rounds)
        assert [r["targets"] for r in cap.rounds] == [[t] for t in targets]

    def test_left_axis_and_fixed_flow_rate(self) -> None:
        """单通道动作选单通道轴（本配置为左轴 [0]），吸放速率固定 30。"""
        prcxi = _make_fake_prcxi()
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        _run(prcxi.one_channel_reuse_tip([source], plate.children[:2], [_tip_rack()], vols=[15.0]))

        for r in cap.rounds:
            assert r["use_channels"] == LEFT_CHANNELS
            assert r["asp_flow_rates"] == [30.0]
            assert r["dis_flow_rates"] == [30.0]

    def test_single_vol_broadcasts(self) -> None:
        """vols 只给 1 个值 → 所有目标孔共用该体积。"""
        prcxi = _make_fake_prcxi()
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        _run(prcxi.one_channel_reuse_tip([source], plate.children[:3], [_tip_rack()], vols=[25.0]))

        assert [r["asp_vols"] for r in cap.rounds] == [[25.0]] * 3
        assert [r["dis_vols"] for r in cap.rounds] == [[25.0]] * 3

    def test_per_target_vols(self) -> None:
        """vols 长度 == 目标孔数量 → 逐孔体积。"""
        prcxi = _make_fake_prcxi()
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        _run(
            prcxi.one_channel_reuse_tip(
                [source], plate.children[:3], [_tip_rack()], vols=[10.0, 20.0, 30.0]
            )
        )

        assert [r["asp_vols"] for r in cap.rounds] == [[10.0], [20.0], [30.0]]

    def test_vols_length_mismatch_raises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        with pytest.raises(ValueError, match="vols 长度"):
            _run(
                prcxi.one_channel_reuse_tip(
                    [source], plate.children[:3], [_tip_rack()], vols=[10.0, 20.0]
                )
            )

    def test_multiple_sources_raises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        src_plate = _DummyPlate("p_src")
        plate = _DummyPlate("p_tgt")

        with pytest.raises(ValueError, match="只接受 1 个 source"):
            _run(
                prcxi.one_channel_reuse_tip(
                    src_plate.children[:2], plate.children[:2], [_tip_rack()], vols=10.0
                )
            )

    def test_volume_over_axis_range_raises(self) -> None:
        """只配了左轴 300µL 时 500µL 无轴可用 → 报错，不拆分成多轮。"""
        prcxi = _make_fake_prcxi(pip_setting={"left": {"vol": 300, "channels": 1}})
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        with pytest.raises(ValueError, match="超过所有 1 通道轴的量程"):
            _run(prcxi.one_channel_reuse_tip([source], plate.children[:2], [_tip_rack()], vols=500.0))

    def test_large_vol_falls_back_to_multi_channel_axis_single_channel(self) -> None:
        """左轴（单通道 300µL）装不下时，退到右轴用 1 个通道（右轴 1000µL）。"""
        prcxi = _make_fake_prcxi()
        assert prcxi._select_reuse_tip_channels(1, 500.0, "one_channel_reuse_tip") == [8]

    def test_volume_over_tip_range_raises(self) -> None:
        """轴量程够（左 1000µL）但超过 300µL 枪头量程 → 报错。"""
        prcxi = _make_fake_prcxi(pip_setting={"left": {"vol": 1000, "channels": 1}})
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        with pytest.raises(ValueError, match="超过枪头量程"):
            _run(prcxi.one_channel_reuse_tip([source], plate.children[:2], [_tip_rack()], vols=500.0))

    def test_fallback_left_axis_without_pip_setting(self) -> None:
        """未配置 pip_setting → 回退左轴 [0]。"""
        prcxi = _make_fake_prcxi(pip_setting=None)
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        _run(prcxi.one_channel_reuse_tip([source], plate.children[:2], [_tip_rack()], vols=20.0))

        assert all(r["use_channels"] == LEFT_CHANNELS for r in cap.rounds)

    def test_error_triggers_cleanup_and_reraises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi, fail_at=1)
        spy = {"n": 0}

        async def _cleanup() -> None:
            spy["n"] += 1

        prcxi._cleanup_after_failed_transfer = _cleanup  # type: ignore[assignment]
        plate = _DummyPlate("p_tgt")
        source = _DummyWell("S0", parent=_DummyPlate("p_src"))

        with pytest.raises(RuntimeError, match="boom mid round"):
            _run(prcxi.one_channel_reuse_tip([source], plate.children[:3], [_tip_rack()], vols=10.0))

        assert spy["n"] == 1


# ---------------------------------------------------------------------------
# eight_channels_reuse_tips
# ---------------------------------------------------------------------------


@_skip_if_no_plr
class TestEightChannelsReuseTips:
    def test_two_columns_one_tip_column(self) -> None:
        """16 个目标孔（2 整列）→ 2 轮；只有首轮取整列枪头、只有末轮丢。"""
        prcxi = _make_fake_prcxi()
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=2)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        _run(prcxi.eight_channels_reuse_tips([source], plate.children, [_tip_rack()], vols=50.0))

        assert len(cap.rounds) == 2
        assert [r["pick_up"] for r in cap.rounds] == [True, False]
        assert [r["drop"] for r in cap.rounds] == [False, True]
        assert cap.rounds[0]["targets"] == plate.column(1)
        assert cap.rounds[1]["targets"] == plate.column(2)

    def test_right_axis_and_broadcast_source(self) -> None:
        """8 通道动作选 8 通道轴（本配置为右轴 [8..15]）；单一 source 广播到 8 个通道。"""
        prcxi = _make_fake_prcxi()
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=1)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        _run(prcxi.eight_channels_reuse_tips([source], plate.children, [_tip_rack()], vols=[60.0]))

        round0 = cap.rounds[0]
        assert round0["use_channels"] == RIGHT_CHANNELS
        assert round0["sources"] == [source] * 8
        assert round0["asp_vols"] == [60.0] * 8
        assert round0["dis_vols"] == [60.0] * 8
        assert round0["asp_flow_rates"] == [30.0] * 8
        # 8 通道插同一个源孔：PRCXI 不用 offsets，用 custom 跳过 PLR 几何校验
        assert round0["spread"] == "custom"

    def test_per_column_vols(self) -> None:
        """vols 长度 == 列数 → 逐列体积。"""
        prcxi = _make_fake_prcxi()
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=3)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        _run(
            prcxi.eight_channels_reuse_tips(
                [source], plate.children, [_tip_rack()], vols=[10.0, 20.0, 30.0]
            )
        )

        assert [r["asp_vols"][0] for r in cap.rounds] == [10.0, 20.0, 30.0]
        assert all(len(set(r["asp_vols"])) == 1 for r in cap.rounds)

    def test_vols_length_mismatch_raises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=3)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        with pytest.raises(ValueError, match="vols 长度"):
            _run(
                prcxi.eight_channels_reuse_tips(
                    [source], plate.children, [_tip_rack()], vols=[10.0, 20.0]
                )
            )

    def test_targets_not_multiple_of_eight_raises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=2)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        with pytest.raises(ValueError, match="整列形式"):
            _run(
                prcxi.eight_channels_reuse_tips(
                    [source], plate.children[:10], [_tip_rack()], vols=50.0
                )
            )

    def test_column_across_two_plates_raises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        plate_a = _DummyPlate("p_a", num_columns=1)
        plate_b = _DummyPlate("p_b", num_columns=1)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))
        mixed = plate_a.children[:7] + plate_b.children[:1]

        with pytest.raises(ValueError, match="必须来自同一块板"):
            _run(prcxi.eight_channels_reuse_tips([source], mixed, [_tip_rack()], vols=50.0))

    def test_out_of_order_column_raises(self) -> None:
        """同板 8 个孔但不是同一列的 A→H 顺序 → 报错。"""
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=2)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))
        shuffled = list(reversed(plate.column(1)))

        with pytest.raises(ValueError, match="A→H 顺序"):
            _run(prcxi.eight_channels_reuse_tips([source], shuffled, [_tip_rack()], vols=50.0))

    def test_cross_column_group_raises(self) -> None:
        """跨列拼出的 8 个孔（4+4）→ 报错。"""
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=2)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))
        cross = plate.column(1)[:4] + plate.column(2)[:4]

        with pytest.raises(ValueError, match="A→H 顺序"):
            _run(prcxi.eight_channels_reuse_tips([source], cross, [_tip_rack()], vols=50.0))

    def test_plate_without_eight_rows_raises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi)
        rack = _DummyPlate("tube_rack", num_columns=2, num_items_y=4)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        with pytest.raises(ValueError, match="num_items_y"):
            _run(prcxi.eight_channels_reuse_tips([source], rack.children, [_tip_rack()], vols=50.0))

    def test_no_eight_channel_axis_raises(self) -> None:
        """pip_setting 里没有 8 通道轴 → 报错（不退化成单通道串行）。"""
        prcxi = _make_fake_prcxi(pip_setting={"left": {"vol": 300, "channels": 1}})
        _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=1)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        with pytest.raises(ValueError, match="没有并行度 >= 8 的轴"):
            _run(prcxi.eight_channels_reuse_tips([source], plate.children, [_tip_rack()], vols=50.0))

    def test_fallback_right_axis_without_pip_setting(self) -> None:
        prcxi = _make_fake_prcxi(pip_setting=None)
        cap = _install_capture(prcxi)
        plate = _DummyPlate("p_tgt", num_columns=1)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        _run(prcxi.eight_channels_reuse_tips([source], plate.children, [_tip_rack()], vols=50.0))

        assert cap.rounds[0]["use_channels"] == RIGHT_CHANNELS

    def test_error_triggers_cleanup_and_reraises(self) -> None:
        prcxi = _make_fake_prcxi()
        _install_capture(prcxi, fail_at=0)
        spy = {"n": 0}

        async def _cleanup() -> None:
            spy["n"] += 1

        prcxi._cleanup_after_failed_transfer = _cleanup  # type: ignore[assignment]
        plate = _DummyPlate("p_tgt", num_columns=1)
        source = _DummyWell("trough", parent=_DummyPlate("p_src", num_columns=1))

        with pytest.raises(RuntimeError, match="boom mid round"):
            _run(prcxi.eight_channels_reuse_tips([source], plate.children, [_tip_rack()], vols=50.0))

        assert spy["n"] == 1


# ---------------------------------------------------------------------------
# 注册表暴露
# ---------------------------------------------------------------------------


class TestRegistryExposure:
    """两个动作必须在 liquid_handler.prcxi 的 action_value_mappings 中按原名暴露。"""

    @staticmethod
    def _prcxi_actions() -> Dict[str, Any]:
        from pathlib import Path

        import yaml

        yaml_path = (
            Path(__file__).resolve().parents[3]
            / "unilabos"
            / "registry"
            / "devices"
            / "liquid_handler.yaml"
        )
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return data["liquid_handler.prcxi"]["class"]["action_value_mappings"]

    @pytest.mark.parametrize(
        "action_name", ["one_channel_reuse_tip", "eight_channels_reuse_tips"]
    )
    def test_action_registered(self, action_name: str) -> None:
        actions = self._prcxi_actions()
        assert action_name in actions, f"{action_name} 未在注册表中暴露"
        entry = actions[action_name]
        assert entry["type"] == "UniLabJsonCommandAsync"
        goal_props = entry["schema"]["properties"]["goal"]["properties"]
        assert {"sources", "targets", "tip_racks", "vols"} <= set(goal_props)
        # 三个资源入参需要 placeholder + handle，前端才能连线选物料
        assert entry["placeholder_keys"] == {
            "sources": "unilabos_resources",
            "targets": "unilabos_resources",
            "tip_racks": "unilabos_resources",
        }
        handle_keys = {h["data_key"] for h in entry["handles"]["input"]}
        assert handle_keys == {"sources", "targets", "tip_racks"}

    @pytest.mark.parametrize(
        "action_name", ["one_channel_reuse_tip", "eight_channels_reuse_tips"]
    )
    def test_rate_params_not_exposed(self, action_name: str) -> None:
        """asp_rates / dis_rates / asp_vols / dis_vols 不对外暴露（速率固定 30、体积走 vols）。"""
        goal_props = self._prcxi_actions()[action_name]["schema"]["properties"]["goal"]["properties"]
        for hidden in ("asp_flow_rates", "dis_flow_rates", "asp_vols", "dis_vols", "use_channels"):
            assert hidden not in goal_props
