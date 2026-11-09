"""偶像档案注册表（hiro.strategies.registry）和 cycle 引擎读档案：值和重构前的模块常量一致，能力位替代 == VEIL_IDOL。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from hiro.strategies import registry as R
from hiro.strategies import schema

pytestmark = pytest.mark.synthetic
VEIL = "i_card-skin-hmsz-3-016"


def test_veil_profile_valid_and_capabilities():
    p = R.idol_profile(VEIL)
    assert schema.check(R.public(p), "idol-profile") == []
    assert R.engine(VEIL) == "cycle" and R.label(VEIL) == "[VEIL] 秦谷美玲"
    assert all(R.has(VEIL, c) for c in ("needs_ledger", "fp_balance", "burst_auto_route"))
    assert R.engine("i_card-skin-unknown") == "default" and not R.has("i_card-skin-unknown", "fp_balance")
    with pytest.raises(ValueError, match="cycle"):
        R.require_engine("i_card-skin-unknown", "cycle", "此考试入口")


def test_veil_engine_constants_unchanged():
    """重构前写死在 hiro.brain.veil 的值（2026-11-08）。改档案会改这些值；这里守住随仓库档案 = 原常量。"""
    import hiro.brain.veil as V

    assert (V.ENTERTAINER, V.DRIVE, V.TEACH, V.REBOUND) == ("エンターテイナー+", "オーバードライブ+", "教えてあげる+", "巻き返し+")
    assert V.CORE == frozenset((V.ENTERTAINER, V.DRIVE, V.TEACH, V.REBOUND))
    assert (V.BOOST, V.SODA, V.WHEY, V.EXTRA_TURN) == ("ブーストエキス", "センブリソーダ", "初星ホエイプロテイン", "初星湯")
    assert V.ORDERS == {2: (V.DRIVE, V.REBOUND, V.TEACH, V.ENTERTAINER), 1: (V.DRIVE, V.TEACH, V.REBOUND, V.ENTERTAINER)}
    assert V.LIMITS == {"r1": 700_000, "r2": 1_400_000} and V.HAND_MAX == 5 and V.PLAN_MAX == 6 and V.MINOR_LOSS == 7
    assert (V.FULL_POWER_AT, V.FP_RESERVE_AT, V.IDOL_CARD, V.STAR) == (10, 3, "アイドルになります", "星々を見下ろす一番星")
    assert V.DEFAULT_ON == frozenset({"HP_GUARD", "RELEASE_GAIN", "DRAW_LAST", "DRIVE_FP", "FP_CAP", "DRAW_ALL", "LAST_TURN",
                                      "CYCLE_SODA", "STUCK_SODA", "FP_RESERVE", "CLEAR_FIRST", "MINOR_HP", "HP_NET",
                                      "PITEM_FLOWER", "ENCHANT_ENGINE"})
    assert len(V.STRATEGY_FLAGS) == 27 and V.PLAY_LATER == {"ひと呼吸"}
    assert list(V.STANCE_LIMITED) == ["ガタゴトすやすや", "一心不乱"]


def test_in_stance_matches_old_conditions():
    from hiro.brain.veil import STANCE_LIMITED, WARM, in_stance

    for cur in ("温存", "のんびり", "強気", "全力", None, "指針なし"):
        assert in_stance(cur, STANCE_LIMITED["ガタゴトすやすや"]["stance"]) == (cur in WARM)
        assert in_stance(cur, STANCE_LIMITED["一心不乱"]["stance"]) == (cur == "強気")
        assert in_stance(cur, STANCE_LIMITED["ガタゴトすやすや"]["unlock_from"]) == (cur not in WARM)
        assert in_stance(cur, STANCE_LIMITED["一心不乱"]["unlock_from"]) == (cur in WARM)


def test_user_override_merges_over_bundled(tmp_path):
    d = tmp_path / "strategies.none" / "idols"
    d.mkdir(parents=True)
    (d / f"{VEIL}.json").write_text(json.dumps({"idol": VEIL, "limits": {"r1": 800000}}), encoding="utf-8")
    p = R.idol_profile(VEIL)
    assert p["limits"] == {"r1": 800000, "r2": 1400000} and p["engine"] == "cycle"
    assert len(p["_sources"]) == 2
    (d / f"{VEIL}.json").write_text(json.dumps({"idol": VEIL, "engine": "magic"}), encoding="utf-8")
    from hiro.strategies.profiles import ProfileError

    with pytest.raises(ProfileError):
        R.idol_profile(VEIL)


def test_fp_balance_follows_capability_not_idol_id():
    import batch_prep as P

    assert P.fp_enabled({"preflight": {"idol": VEIL}})[0]
    assert P.fp_enabled({"preflight": {"idol": "i_card-skin-other"}}) == (False, None)
    cfg = P.fp_cfg(VEIL)
    assert cfg == {"high": 7, "low": 3, "skip": ("オーバードライブ",), "idol": "アイドルになります"}


def test_snapshot_carries_idol_profile():
    from hiro.strategies import profiles

    snap = profiles.snapshot(VEIL, None)
    assert snap["idol_profile"]["engine"] == "cycle" and "_sources" not in snap["idol_profile"]
    assert any(layer["layer"] == "idol" for layer in snap["layers"]) is False  # VEIL 档案没有 memory_defaults
