"""P 道具通用加载器（hiro.brain.pitems）：主数据 → 触发器 / 牌搜索 / 计数。没有主数据时跳过。"""

import pytest

from hiro.brain import pitems as P

FLOWER = "pitem_03-3-303-1"  # 夜空を従える花+


@pytest.fixture(scope="module")
def flower():
    try:
        item = P.load_pitem(FLOWER)
    except Exception:  # noqa: BLE001
        item = None
    if item is None:
        pytest.skip("没有主数据")
    return item


def test_flower_compiles(flower):
    assert flower.name == "夜空を従える花+" and not flower.missing
    (en,) = flower.play_counters
    assert en.trigger.play_interval == 3
    (cond,) = en.trigger.conditions
    assert cond.negate and cond.field.endswith("NoStance")
    assert {e.type.rsplit("_", 1)[-1] for e in en.effects} >= {"ExamOverPreservation", "ExamBlock"}


def test_flower_counts_concentration_cards(flower):
    """数「带強気效果组的牌」：強気下再打強気牌也算；没有指针（指針なし / 指針解除）不算；客制化「強気追加」后算。"""
    trig = flower.play_counters[0].trigger
    drive = P.card_profile_by_name("オーバードライブ+")
    rebound = P.card_profile_by_name("巻き返し+")
    assert trig.counts_play(drive, "強気") is True
    assert trig.counts_play(drive, "温存") is True
    assert trig.counts_play(drive, None) is False
    assert trig.counts_play(drive, "指針解除") is False
    assert trig.counts_play(rebound, "温存") is False
    assert trig.counts_play(P.card_profile_by_name("インフルエンサー+"), "温存") is False
    assert trig.counts_play(P.card_profile_by_name("インフルエンサー+", ("強気追加",)), "温存") is True
    assert trig.counts_play(None, "強気") is None  # 牌画像未知：不知道


def test_play_counter_remaining(flower):
    """remaining = 画面道具旁「N回」：开局 3，数到 3 张触发后回到 3。"""
    ctr = P.play_counter(flower)
    drive = P.card_profile_by_name("オーバードライブ+")
    assert ctr.remaining == 3
    assert ctr.play(drive, None) is False and ctr.remaining == 3  # 指針なし 不计
    assert ctr.play(drive, "温存") is False and ctr.remaining == 2
    assert ctr.play(drive, "強気") is False and ctr.remaining == 1
    assert ctr.play(drive, "強気") is True and ctr.remaining == 3
    assert ctr.play(None, "強気") is None and ctr.remaining is None


def test_all_pitems_load():
    """主数据里每个 P 道具都能编译，没有引用断链。"""
    try:
        from hiro.state.catalog import catalog

        ids = [r[0] for r in catalog().db.execute("SELECT id FROM ProduceItem")]
    except Exception:  # noqa: BLE001
        pytest.skip("没有主数据")
    broken = [i for i in ids if (p := P.load_pitem(i)) is None or p.missing or any(e.missing for e in p.enchants)]
    assert not broken


def _view(name, upgrade=None, added=()):
    """主数据里的真牌 → CardView（带结构化效果；added = 客制化追加的效果 id）。"""
    from hiro.brain import card_fx
    from hiro.brain.cards import CardView, description_text, parse_tags
    from hiro.state.catalog import catalog

    row = catalog().db.execute("SELECT id, upgradeCount, produceDescriptions FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    up = row[1] if upgrade is None else upgrade
    text = description_text(row[2])
    fx = card_fx.card_fx(row[0], row[1])
    if added:
        fx = card_fx.add_effects(fx, list(added))
    return CardView(0, name, row[0], up, True, "active", 0, 0, None, text, parse_tags(name, text), fx=fx)


def test_view_profile_customs_and_temp_upgrade(flower):
    """CardView：客制化「強気追加」从 fx 里多出来的效果认出；考试里的临时强化（+2）退到主数据有的那一级。"""
    infl = _view("インフルエンサー+")
    assert P.flower_counts(infl, "温存") is False
    assert P.flower_counts(_view("インフルエンサー+", added=["e_effect-exam_concentration-0001"]), "温存") is True
    assert P.flower_counts(_view("オーバードライブ+", upgrade=3), "強気") is True


def test_veil_after_play_uses_loader(flower, monkeypatch):
    """after_play：指針なし 打強気牌不计数；強気下打強気牌计数；计数到 1 触发のんびり、回到 3。=0 退回手写规则。"""
    from hiro.brain.veil import NEUTRAL, after_play

    drive = _view("オーバードライブ+")
    assert after_play(NEUTRAL, 3, drive) == ("強気", 3)
    assert after_play("強気", 2, drive) == ("強気", 1)
    assert after_play("強気", 1, drive) == ("のんびり", 3)
    assert after_play("温存", 2, _view("巻き返し+")) == ("温存", 2)
    infl = _view("インフルエンサー+", added=["e_effect-exam_concentration-0001"])
    assert after_play("温存", 2, infl)[1] == 1
    monkeypatch.setenv("VEIL_PITEM_FLOWER", "0")
    assert after_play("強気", 2, drive) == ("強気", 1)


def test_enchant_engine_star_and_flower(flower):
    """一番星（回合开始非温存切のんびり，3 次）和夜空触发的效果都由主数据执行。"""
    from hiro.brain.veil import after_play, start_turn

    en, count = P.card_start_turn_enchant("星々を見下ろす一番星")
    assert count == 3
    assert start_turn("強気", 3) == ("のんびり", 2)
    assert start_turn("温存", 3) == ("温存", 3)
    assert start_turn("のんびり", 2) == ("のんびり", 2)
    assert start_turn("強気", 0) == ("強気", 0)
    out = P.flower_trigger_outcome("強気")
    assert out.stance == "のんびり" and out.genki == 3
    assert P.flower_trigger_outcome("全力").stance is None  # 全力 下切不了姿态
    assert after_play("全力", 1, _view("オーバードライブ+")) == ("全力", 3)
    from hiro.produce.veil_exam import star_count

    assert star_count(_view("星々を見下ろす一番星")) == 3
