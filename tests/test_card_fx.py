"""技能卡结构化效果（hiro.brain.card_fx）：主数据 playEffects → 姿态 / 全力値 / 追加行动 / 抽牌 / 使用条件，
和 VEIL 记账用的函数（destination、fp_effect…）走结构化的那条路（使用者 2026-11-09「以结构化为准」）。"""

import pytest

from hiro.brain import card_fx


def _master(card_id, upgrade):
    from hiro.brain.cards import _MasterCards, card_view

    try:
        m = _MasterCards().get((card_id, upgrade))
    except Exception:  # noqa: BLE001
        m = None
    if m is None:
        pytest.skip("没有主数据")
    return card_view(0, m, available=True)


def test_overdrive():
    from hiro.brain import veil as V

    c = _master("p_card-03-act-2_102", 1)  # オーバードライブ+
    fx = c.fx
    assert (fx["stance"], fx["next_stance"], fx["fp_gain"], fx["extra_actions"], fx["makes_sleep"]) == ("強気", "温存", 2, 1, True)
    assert V.destination(c) == "強気" and V.next_stance(c) == "温存" and V.fp_delta(c) == (2, 0)
    assert c.tags.extra_actions == 1 and c.tags.makes_sleep


def test_condition_and_cost():
    from hiro.produce.veil_exam import play_condition

    isshin = _master("p_card-03-men-3_061", 1)  # 一心不乱+：強気の場合、使用可
    assert play_condition(isshin)["kind"] == "stance" and play_condition(isshin)["value"] == "強気"
    tough = _master("p_card-03-men-2_074", 0)  # タフネス：温存になった回数が2回以上
    assert (play_condition(tough)["kind"], play_condition(tough)["value"]) == ("warm", 2)
    idol = _master("p_card-03-men-3_058", 1)  # アイドルになります+：全力値消費3
    assert idol.fx["fp_cost"] == 3 and idol.fx["stance"] == "温存" and idol.fx["once"]


def test_timer_and_enchant_next_stance():
    teach = _master("p_card-03-ido-3_177", 1)  # 教えてあげる+：次のターン、スキルカードを2枚引く
    assert teach.fx["next_draws"] == 2 and teach.fx["draws"] == 0 and teach.tags.draws == 2
    star = _master("p_card-03-ido-100_047", 0)  # 一番星：带条件的回合开始附魔不算预约姿态
    assert star.fx["next_stance"] is None


def test_custom_effect_add_updates_fx():
    from hiro.brain import veil as V
    from hiro.produce.veil_exam import apply_customs

    inf = _master("p_card-03-men-2_080", 1)  # インフルエンサー+：本身不切姿态
    assert V.destination(inf) is None
    (got,) = apply_customs([inf], [{"card": "インフルエンサー+", "item": "強気追加"}])
    assert got.fx["stance"] == "強気" and V.destination(got) == "強気"


def test_disabled_falls_back_to_text(monkeypatch):
    from hiro.brain import veil as V

    c = _master("p_card-03-act-2_102", 1)
    monkeypatch.setenv("HIRO_CARD_FX", "0")
    assert card_fx.of(c) is None and V.destination(c) == "強気"  # 文字读法
