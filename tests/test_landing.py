"""出牌落地核对按卡图认牌（hiro.exec.landing，HIRO_LANDING_ART）。合成手牌身份，不读图。"""

from types import SimpleNamespace as NS

import pytest

from hiro.exec import landing as L

X5 = [115, 235, 365, 485, 605]


def hand(ids, xs=None, ys=None, layout="ok"):
    xs = xs or X5[: len(ids)] if len(ids) == 5 else (xs or [100 * i + 150 for i in range(len(ids))])
    return L.HandIds(list(ids), list(xs), list(ys or [1000] * len(ids)), layout)


def test_swallowed_tap_same_hand_is_not_played():
    """codex-20261109-1-p R2 T11 / 1-j R2 T6 / 1-g：点被吞，手牌一张没变 → 没打出（不看元気等数字有没有变）。"""
    h = hand(["acc", "men", "ido", "OD", "act"])
    assert L.classify(h, hand(["acc", "men", "ido", "OD", "act"]), 3)[0] == "none"


def test_played_and_drew_into_same_slot():
    """codex-20261109-1-l R1 T1：インフルエンサー+ 打出后抽到 アンダンテ 补在同一格，位置不变 → 目标牌少了一张 = 打出。"""
    assert L.classify(hand(["men66", "infl"]), hand(["men66", "andante"]), 1)[0] == "played"


def test_duplicate_names_counted():
    assert L.classify(hand(["mk", "mk", "x"]), hand(["mk", "x"]), 0)[0] == "played"
    assert L.classify(hand(["mk", "mk", "x"]), hand(["mk", "mk", "x"]), 1)[0] == "none"


def test_last_card_played_hand_empty():
    assert L.classify(hand(["sleep"]), L.HandIds([], [], [], "empty"), 0)[0] == "played"
    assert L.classify(hand(["a", "b"]), L.HandIds([], [], [], "empty"), 0)[0] == "moving"


def test_unclear_falls_back():
    assert L.classify(hand([None, "b"]), hand(["a", "b"]), 0)[0] == "unclear"  # 目标认不出
    assert L.classify(hand(["a", "b"]), hand(["a", None]), 0)[0] == "unclear"
    assert L.classify(hand(["a", "b"]), hand(["a", "c"]), 0)[0] == "unclear"  # 手牌变了、目标没少
    assert L.classify(hand(["a", "b"]), hand(["a", "b"], layout="shifted"), 0)[0] == "moving"


def test_lifted_target_is_selected_not_played():
    h0 = hand(["a", "b", "c"])
    h1 = hand(["a", "b", "c"], ys=[1000, 970, 1000], layout="partial")
    state, why = L.classify(h0, h1, 1)
    assert state == "none" and "抬起" in why


def test_settle_waits_until_two_equal_reads():
    reads = iter([hand(["a", "b"], layout="shifted"), hand(["a"]), hand(["a"])])
    t = [0.0]
    state, why, _ = L.settle(lambda: None, hand(["a", "b"]), 1, read=lambda img: next(reads),
                             sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert state == "played"


def test_play_not_landed_uses_art_verdict(monkeypatch):
    """开关打开时以卡图判定为准：none → 返回没打出；played → None，不再看签名 / 像素。"""
    import numpy as np

    pytest.importorskip("kaa")
    import hiro.exec.exam as ex

    img = np.zeros((1280, 720, 3), np.uint8)
    monkeypatch.setenv("HIRO_LANDING_ART", "1")
    monkeypatch.setattr(ex, "device", NS(screenshot=lambda: img, click=lambda *a: None))
    monkeypatch.setattr(ex, "sleep", lambda s: None)
    monkeypatch.setattr(ex, "_select_at", lambda im, x: False)
    monkeypatch.setitem(ex.PLAY_RESULT, "stuck", False)
    monkeypatch.setitem(ex.PLAY_RESULT, "confirmed", True)
    monkeypatch.setitem(ex.PLAY_RESULT, "name_visible", False)
    monkeypatch.setattr(L, "read_ids", lambda im: hand(["a", "b", "c", "OD", "e"]))
    monkeypatch.setattr(L, "settle", lambda shoot, h0, slot, **k: ("none", "手牌和点之前一样", img))
    assert "没打出" in ex._play_not_landed(NS(img=img), 485)
    monkeypatch.setattr(L, "settle", lambda shoot, h0, slot, **k: ("played", "目标牌少了一张", img))
    assert ex._play_not_landed(NS(img=img), 485) is None


def test_tap_probe_refuses_when_a_card_is_lifted(monkeypatch):
    """codex-20261109-1-r R2 T3：目标牌已被抬起（选中）但 SELECT 没认出，点按读档位等于确认打出。开关打开时先看首字母框高度：
    有牌被抬起就只点空白取消选中，不点牌。"""
    import sys
    import types

    pytest.importorskip("kaa")
    import hiro.exec.exam as X
    from hiro.produce import veil_exam as V

    clicks = []
    dev = types.SimpleNamespace(screenshot=lambda: "img", click=lambda x, y: clicks.append((x, y)))
    monkeypatch.setitem(sys.modules, "kotonebot", types.SimpleNamespace(device=dev))
    monkeypatch.setenv("HIRO_LANDING_ART", "1")
    monkeypatch.setattr(X, "_selects", lambda img: [])
    monkeypatch.setattr(V.time, "sleep", lambda s: None)
    monkeypatch.setattr(L, "read_ids", lambda img: L.HandIds(["a", "b", "c"], [145, 365, 575], [1000, 970, 1000], "ok"))
    frame = NS(cards=[NS(rect=NS(center=NS(x=x, y=1010))) for x in (145, 365, 575)])
    assert V.tap_probe(frame, NS(slot=1, name="b")) is None
    assert clicks == [X.DESELECT_TAP]
