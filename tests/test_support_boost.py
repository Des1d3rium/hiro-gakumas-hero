"""支援卡的考试内临时强化：头像计数（hiro.vision.support_boost）和按总档位换效果（veil_exam.apply_boosts）。"""

import numpy as np
import pytest

from hiro.vision.support_boost import count_boosts


def _head(img, cx, y, top):
    """在卡 (cx, y) 的头像位置画一个白边 + 红框的方块（top：相对 y 的顶边）。"""
    x = cx + 38
    img[y + top - 4 : y + top + 48, x - 4 : x + 48] = (255, 255, 255)  # 白边（4px，叠放时把上一个的边框隔开）
    img[y + top : y + top + 44, x : x + 44] = (60, 60, 230)  # 红框
    img[y + top + 4 : y + top + 40, x + 4 : x + 40] = (120, 160, 90)  # 头像内容


def test_count_boosts_synthetic():
    img = np.full((1280, 720, 3), 200, np.uint8)
    centers = [(144, 1085), (361, 1085), (577, 1085)]
    _head(img, 144, 1085, -145)
    _head(img, 577, 1085, -145)
    _head(img, 577, 1085, -111)  # 叠第二个
    assert count_boosts(img, centers) == [1, 0, 2]
    five = [(114, 1085), (237, 1085), (361, 1085), (484, 1085), (608, 1085)]
    assert count_boosts(img, five)[:4] == [None] * 4  # 5 张：被盖住的不猜


def test_apply_boosts_uses_ledger_level_plus_boost():
    from hiro.brain.cards import CardView
    from hiro.produce.veil_exam import apply_boosts

    try:
        from hiro.vision.cards import card_version

        pride = card_version("p_card-03-men-2_066", 0)
    except Exception:  # noqa: BLE001
        pride = None
    if pride is None:
        pytest.skip("没有主数据")
    c = CardView(0, pride.name, pride._id, 0, True, "mental", 0, 0, None, "")
    raw = [{"slot": 0, "plus": None, "boost": 1}]
    hand, changed = apply_boosts([c], raw, {"プライド+": 1})
    assert hand[0].name == "プライド+" and hand[0].upgrade == 2 and changed[0]["level"] == 2
    assert apply_boosts([c], [{"slot": 0, "plus": 2, "boost": 1}], {"プライド+": 1})[1] == []  # 卡名看得见：画面已按总档位
    assert apply_boosts([c], raw, {"プライド": 1, "プライド+": 1})[1] == []  # 账本两档都有：分不出


def _catalog():
    try:
        from hiro.vision.cards import card_version

        return card_version("p_card-03-men-2_066", 1) is not None
    except Exception:  # noqa: BLE001
        return False


def test_boost_matters_only_for_decision_fields():
    if not _catalog():
        pytest.skip("没有主数据")
    from hiro.produce.veil_exam import boost_matters
    from hiro.state.context import catalog

    cid = lambda n: catalog().db.execute("select id from ProduceCard where name=?", (n,)).fetchone()[0]  # noqa: E731
    assert not boost_matters(cid("プライド+"), 1)  # ++ 和 + 效果一样
    assert not boost_matters(cid("エンターテイナー+"), 1)  # 只多得分
    assert boost_matters(cid("ハイテンション+"), 1)  # 元気 变
    assert not boost_matters(cid("眠気"), 0)  # 没有强化版


def test_probe_boosts_reads_once_per_turn_and_skips_cycle(monkeypatch):
    if not _catalog():
        pytest.skip("没有主数据")
    from hiro.brain.cards import card_view
    from hiro.brain.state import ExamMemory
    from hiro.produce import veil_exam as V
    from hiro.state.context import catalog
    from hiro.vision.cards import card_version

    def view(slot, name):
        cid, up = catalog().db.execute("select id, upgradeCount from ProduceCard where name=?", (name,)).fetchone()
        return card_view(slot, card_version(cid, up), available=True)

    taps = []
    src = V.VeilScreen({"ハイテンション+": 1, "眠気": 1, "巻き返し+": 1, "達成感": 1, "第一印象+": 1}, [],
                       probe=lambda frame, c: taps.append(c.name) or "元気+15元気増加無効2ターン消費体力減少4ターンレッスン中1回")
    hand = [view(0, "ハイテンション+"), view(1, "眠気"), view(2, "巻き返し+"), view(3, "達成感"), view(4, "第一印象+")]
    state = type("S", (), {"hand": hand})()
    mem = ExamMemory("r1", turn=3)
    raw = [{"slot": i, "plus": None, "boost": None} for i in range(4)] + [{"slot": 4, "plus": 1, "boost": 0}]
    assert src.probe_boosts(state, mem, None, raw) == "veil_boost_probe" and taps == ["ハイテンション+"]
    raw = [{"slot": i, "plus": None, "boost": None} for i in range(4)] + [{"slot": 4, "plus": 1, "boost": 0}]
    assert src.probe_boosts(state, mem, None, raw) is None and raw[0]["boost"] == 1 and len(taps) == 1  # 本回合沿用


def test_probe_level_from_full_card_name():
    """点一次选中后读到的完整卡名（PROBE_NAME 行）按「+」个数定档，不再比说明文字（使用者 2026-11-09）。"""
    from hiro.produce.veil_exam import PROBE_NAME, level_from_text

    cid = "p_card-03-act-2_071"  # トレーニングの成果
    assert level_from_text(cid, f"{PROBE_NAME}トレーニングの成果++\n更試験・ステージ中1回", 1) == 2
    assert level_from_text(cid, f"{PROBE_NAME}トレーニングの成果＋＋＋\n", 1) == 3
    assert level_from_text(cid, f"{PROBE_NAME}トレーニングの成果\n", 1) is None  # 比账本档位还低：不认，退回比说明文字（这里没有）
