"""考试牌堆记账三层（hiro.brain.piles，VEIL_PILE_ENGINE）：暗抽、矛盾倒推「山札已空」、存疑的牌出局、牌库一览重置。"""

from collections import Counter

import pytest

from hiro.brain import piles as P

pytestmark = pytest.mark.synthetic


def _start(cards, hand):
    data = {"veil_deck": dict(Counter(cards))}
    slots = list(hand) + [None] * (5 - len(hand))
    P.observe(data, 1, slots, Counter(cards) - Counter(hand))
    return data


def _play(data, name, *, draw=0, lost=False):
    """模拟 VeilBrain._commit：手里拿掉、非一次性进捨札、登记应抽张数。"""
    piles = data["veil_piles"]
    piles["hand"][piles["hand"].index(name)] = None
    if lost:
        data["veil_deck"][name] -= 1
    else:
        piles["grave"][name] = piles["grave"].get(name, 0) + 1
    P.expect(piles, P.Moves(draw=draw))


def _see(data, turn, hand):
    slots = list(hand) + [None] * (5 - len(hand))
    outside = Counter({k: v for k, v in data["veil_deck"].items() if v > 0}) - Counter(hand)
    return P.observe(data, turn, slots, outside)


def test_hidden_draw_becomes_ghost_and_resolves_on_shuffle():
    """应该抽的牌回合结束都没出现（回合末被丢）：记暗抽，不当矛盾；洗牌时随捨札回到山札，账对上。"""
    data = _start("ABCDEFT", "AB")
    _play(data, "A", draw=1)  # 抽 1 张，但回合马上结束、这张直接进捨札
    v = _see(data, 2, "CDE")
    assert data["veil_piles"]["ghost"] == 1 and not v["unlocated"]
    _play(data, "C", draw=2)  # 山札账 {F, T} 实际只剩 1 张：抽 2 → 洗牌
    v = _see(data, 2, "DEFA")
    assert not v["unlocated"] and not v["doubt"] and data["veil_piles"]["ghost"] == 0
    assert Counter(v["deck"]) + Counter(v["grave"]) == Counter("BCT")
    assert not data["veil_piles"]["need_view"]


def test_contradiction_marks_rest_doubtful_and_then_gone():
    """codex-20261109-1 R2：账面山札剩 T，画面却抽到捨札的牌——倒推山札已空，T 存疑；洗回山札后又抽空一轮都没出现 → 不在牌库。"""
    data = _start("ABCDT", "AB")
    _play(data, "A")
    _play(data, "B")
    v = _see(data, 2, "CD")  # 回合开始抽 2（山札账 {C, D, T}）
    assert Counter(v["deck"]) == Counter("T")
    _play(data, "C", draw=1)
    v = _see(data, 2, "DA")  # 应抽到 T，实际抽到捨札的 A：T 其实不在山札
    assert v["doubt"] == ["T"] and data["veil_piles"]["need_view"]
    assert not v["unlocated"]
    _play(data, "D")
    v = _see(data, 3, "BC")  # 新山札 {T, B, C}（实际只有 B, C）
    assert "T" in v["deck"]
    _play(data, "B", draw=1)
    v = _see(data, 3, "CD")  # 山札只剩存疑的 T，却洗牌抽到 D → T 出局
    assert "T" not in v["deck"] + v["grave"] + v["doubt"] and data["veil_deck"]["T"] == 0
    assert data["veil_gone"] == {"T": 1}


def test_small_deck_ignores_doubt():
    from hiro.brain.veil import CORE, small_deck

    class S:
        hand = []
        piles = {"deck": sorted(CORE) + ["眠気"], "grave": [], "unlocated": [], "doubt": ["トレーニングの成果+"],
                 "once": []}

    S.piles["deck"].append("トレーニングの成果+")
    assert small_deck(S)


def test_apply_view_resets_piles():
    data = _start("ABCDT", "AB")
    _play(data, "A")
    data["veil_piles"]["need_view"] = True
    diff = P.apply_view(data, {"手札": ["B"], "山札": ["C", "D"], "捨札": ["A"], "除外": ["T"]})
    assert diff is not None and data["veil_gone"] == {"T": 1}
    v = P._view(data["veil_piles"])
    assert Counter(v["deck"]) == Counter("CD") and v["grave"] == ["A"] and not data["veil_piles"]["need_view"]


def test_moves_from_master():
    assert P.moves_of_ability("draw").draw == 1 and P.moves_of_ability("sleep").draw == 0
    soda = P.moves_of_drink("センブリソーダ")
    if soda == P.Moves():
        pytest.skip("没有主数据")
    assert soda.draw == 2


def test_probe_that_changes_hand_requests_pile_view(monkeypatch):
    """codex-20261109-1-c R1 T2：点按读档位后 トレーニングの成果+ 从手里没了（这一下把它打出去了），没记账成了幽灵牌。
    点按前后手牌签名变了 → 牌堆层标 need_view、清掉本回合已看过，下一帧开牌库一览重置。"""
    import sys
    import types

    from hiro.exec import exam as X
    from hiro.produce.veil_exam import VeilScreen

    shots = iter(["before", "after"])
    monkeypatch.setitem(sys.modules, "kotonebot", types.SimpleNamespace(device=types.SimpleNamespace(screenshot=lambda: next(shots))))
    sigs = {"before": ((8, 42, 30), ((1, 2), (3, 4))), "after": ((8, 40, 30), ((1, 2), (3, 4)))}
    monkeypatch.setattr(X, "_lite_sig", lambda img: sigs[img])
    screen = VeilScreen({}, set(), probe=lambda frame, card: "卡名:トレーニングの成果+")
    logs = []
    screen._log = lambda kind, **kw: logs.append(kind)

    class M:
        turn = 2
        strategy_data = {"veil_piles": {"need_view": False}, "veil_pile_viewed": 2}

    class C:
        name, slot = "トレーニングの成果+", 1

    assert screen._probe(None, C, M) == "卡名:トレーニングの成果+"
    assert M.strategy_data["veil_piles"]["need_view"] and "veil_pile_viewed" not in M.strategy_data
    assert logs == ["veil_probe_changed"]


def test_apply_view_maps_temporary_upgrade_to_ledger_name():
    """codex-20261109-1-r R2 T3：一览里是「アンダンテ+」（支援卡临时强化），账本是「アンダンテ」→ 按账本名记，不改强化；
    原来记成 アンダンテ+，T4 抽到「アンダンテ」对不上又白开一次一览、trust 2 坏步。账本里真有「+」版的照旧。"""
    assert P._to_book("アンダンテ+", {"アンダンテ": 1}) == "アンダンテ"
    assert P._to_book("巻き返し++", {"巻き返し+": 1, "巻き返し": 1}) == "巻き返し+"
    assert P._to_book("教えてあげる+", {"教えてあげる+": 1}) == "教えてあげる+"
    assert P._to_book("新卡", {"アンダンテ": 1}) == "新卡"
    data = _start("ABCDT", "AB")
    data["veil_deck"] = {"A": 1, "B": 1, "C": 1, "D": 1, "T": 1}
    _play(data, "A")
    P.apply_view(data, {"手札": ["B"], "山札": ["C+", "D"], "捨札": ["A"], "除外": ["T"]})
    assert Counter(P._view(data["veil_piles"])["deck"]) == Counter("CD")


def test_probe_with_art_ids_ignores_hp_change(monkeypatch):
    """codex-20261109-1-r R2 T3：喝完ソーダ 元気还在变时点按读档位，签名变了被当成打出了牌。开 HIRO_LANDING_ART 时按卡图比整手牌。"""
    import sys
    import types

    from hiro.exec import exam as X
    from hiro.exec import landing as LD
    from hiro.produce.veil_exam import VeilScreen

    shots = iter(["before", "after"])
    monkeypatch.setitem(sys.modules, "kotonebot", types.SimpleNamespace(device=types.SimpleNamespace(screenshot=lambda: next(shots))))
    monkeypatch.setenv("HIRO_LANDING_ART", "1")
    monkeypatch.setattr(LD, "read_ids", lambda img: LD.HandIds(["a", "b"], [255, 465], [1000, 1000], "ok"))
    monkeypatch.setattr(X, "_lite_sig", lambda img: ((8, 42, 30) if img == "before" else (8, 42, 45), ((1, 2), (3, 4))))
    screen = VeilScreen({}, set(), probe=lambda frame, card: "卡名:a")
    logs = []
    screen._log = lambda kind, **kw: logs.append(kind)

    class M:
        turn = 3
        strategy_data = {"veil_piles": {"need_view": False}}

    class C:
        name, slot = "a", 0

    screen._probe(None, C, M)
    assert logs == [] and not M.strategy_data["veil_piles"]["need_view"]
