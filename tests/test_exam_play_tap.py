"""考试出牌点击（hiro.exec.exam._play_card / _play_not_landed）：游戏会丢点击（第一局本战 R1 约 1/5）。
用会「丢点」的假设备验证：补点、确认没收到时再点、卡停在选中态时取消选中且不记账。"""

from types import SimpleNamespace as NS

import pytest

pytest.importorskip("kaa")

import hiro.exec.exam as ex  # noqa: E402
from kotonebot.primitives import Rect  # noqa: E402

pytestmark = pytest.mark.synthetic
X, Y = 360, 1010


class Game:
    """lost：按顺序决定每次点牌是否被丢（True = 丢）。被收到的第一下 → 选中（SELECT），第二下 → 打出。"""

    def __init__(self, lost=()):
        self.lost, self.state, self.clicks = list(lost), "idle", []

    def click(self, x, y):
        self.clicks.append((x, y))
        if (x, y) == ex.DESELECT_TAP:
            self.state = "idle" if self.state == "selected" else self.state
            return
        if self.lost and self.lost.pop(0):
            return
        self.state = {"idle": "selected", "selected": "played"}.get(self.state, self.state)

    def screenshot(self):
        return self.state


@pytest.fixture
def game(monkeypatch):
    clock = [0.0]

    def setup(lost=()):
        g = Game(lost)
        monkeypatch.setattr(ex, "device", g)
        monkeypatch.setattr(ex, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
        monkeypatch.setattr(ex.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(ex, "_roi_texts", lambda img, y1, y2: [ex._Text("SELECT", Rect(X - 30, 1120, 60, 20))] if img == "selected" else [])
        monkeypatch.setattr(ex, "_lite_sig", lambda img: ((5, 20, 10), ((1, 1),)) if img in ("idle", "start") else ((5, 20, 10), ((1, 9),)))
        monkeypatch.setattr(ex, "_screen_calm", lambda max_s=3.0: True)
        return g

    return setup


def test_normal_play_two_taps(game):
    g = game()
    taps = ex._play_card(X, Y, "start")
    assert g.state == "played" and len(taps) == 2 and ex.PLAY_RESULT == {"confirmed": True, "stuck": False, "name_visible": False}


def test_first_taps_lost_then_retap_after_calm(game):
    g = game(lost=[True, True])  # 前两下都丢（以前只补点一次就放弃）
    ex._play_card(X, Y, "start")
    assert g.state == "played" and ex.PLAY_RESULT["confirmed"]


def test_confirm_tap_lost_is_retapped(game):
    g = game(lost=[False, True])  # 选中了，确认那下丢了
    taps = ex._play_card(X, Y, "start")
    assert g.state == "played" and len(taps) == 3 and ex.PLAY_RESULT["confirmed"]


def test_stuck_selected_is_deselected_and_not_committed(game):
    g = game(lost=[False, True, True, True])  # 确认和两次补点全丢：卡停在选中态
    ex._play_card(X, Y, "start")
    assert ex.PLAY_RESULT["stuck"] and g.state == "selected"
    why = ex._play_not_landed(NS(img="start"), X)
    assert why and "选中" in why and g.state == "idle" and g.clicks[-1] == ex.DESELECT_TAP


def test_never_selected_and_hand_same_is_not_landed(game):
    game(lost=[True] * 10)
    ex._play_card(X, Y, "start")
    assert not ex.PLAY_RESULT["confirmed"]
    assert "没被接收" in ex._play_not_landed(NS(img="start"), X)


def test_played_card_passes_verification(game):
    game()
    ex._play_card(X, Y, "start")
    assert ex._play_not_landed(NS(img="start"), X) is None


# 第一局本战 R1（traces/batch-codex-20261107-6-1-exam1 第 19 步，frames/00020.jpg）：手牌只剩两张时卡下方写卡名，
# 选中后也还是卡名、没有「SELECT」。旧逻辑只点了一下，又因画面变了（卡被抬起）当成已打出 → veil_play_unverified。
NAME = "星々を見下ろす一番星"


@pytest.fixture
def name_game(game, monkeypatch):
    def setup(lost=()):
        g = game(lost)
        label = [ex._Text(NAME[:7], Rect(X - 80, 1100, 160, 20))]
        monkeypatch.setattr(ex, "_roi_texts", lambda img, y1, y2: label if img in ("idle", "selected", "start") else [])
        return g

    return setup


def test_selected_without_select_label_gets_confirm_tap(name_game):
    g = name_game()
    taps = ex._play_card(X, Y, "start", NAME)
    assert g.state == "played" and len(taps) == 2
    assert ex.PLAY_RESULT["confirmed"]
    assert ex._play_not_landed(NS(img="start"), X, NAME) is None


def test_name_still_in_place_is_not_landed(name_game):
    g = name_game(lost=[False, True, True, True])  # 选中了，之后的确认都被丢
    ex._play_card(X, Y, "start", NAME)
    assert g.state == "selected" and not ex.PLAY_RESULT["confirmed"]
    assert "卡名仍在原位" in ex._play_not_landed(NS(img="start"), X, NAME)
    assert g.state == "idle"  # 取消了选中，不会被之后的 SELECT 兜底替它打出


def test_name_match_tolerates_ocr():
    assert ex._name_match("星々を見下ろす一番", NAME)
    assert ex._name_match("教えてあげる＋", "教えてあげる+")
    assert not ex._name_match("教えてあげる+", NAME)


def test_signature_sees_unknown_drink_slot_emptied():
    """验证局 1-d R2 剩 10 回合：3 瓶ホエイ、第 3 瓶认不出；喝掉一瓶后认出的仍是两瓶，签名要能看出「占用格」变少。"""
    whey = NS(name="初星ホエイプロテイン")

    def frame(states, n):
        f = ex.Frame.__new__(ex.Frame)
        f.remaining, f.hp, f.genki, f.cards = 10, 29, 29, []
        f.drinks, f.drink_states = [(whey, None)] * n, states
        return f

    before = frame(["drink", "drink", "unknown", "empty"], 2)
    after = frame(["drink", "drink", "empty", "empty"], 2)
    assert before.signature != after.signature
    assert frame(["drink", "drink", "drink", "empty"], 2).signature == before.signature  # unknown↔drink 闪烁不算变化


def test_fixed_drink_slot_unknown_uses_record():
    """按记录优先：固定栏位模式下，目标格读成 unknown（有饮料、认不出）时按账本点这格；读成 empty 时不点。"""
    rect = ex.drink_slot_rect(2)
    f = ex.Frame.__new__(ex.Frame)
    f.fixed_drink_slots, f.drink_states = True, ["drink", "drink", "unknown", "empty"]
    f.drinks = [(NS(id="whey"), ex.drink_slot_rect(0)), (NS(id="whey"), ex.drink_slot_rect(1))]
    state = NS(drinks=[NS(slot=2, drink_id="whey")])
    i = ex._screen_slot(f, state, NS(kind="drink", slot=2))
    assert i is not None and f.drinks[i][1] == rect
    f.drink_states[2] = "empty"
    f.drinks = f.drinks[:2]
    assert ex._screen_slot(f, state, NS(kind="drink", slot=2)) is None


def test_art_changed_flags_swapped_card_same_layout():
    """张数 / 角标位置不变、某张卡面换了：_art_changed 给出槽号（HIRO_STALE_ART）。"""
    import numpy as np

    from hiro.exec.exam import _art_changed

    letters = ((10, 135), (30, 135), (50, 135))
    a = np.full((1280, 720, 3), 120, np.uint8)
    b = a.copy()
    assert _art_changed(a, b, letters) is None
    b[960:1040, 210:290] = 240  # 第 2 张（x=240）卡面换了
    assert _art_changed(a, b, letters) == 1
    b2 = a.copy()
    b2[960:1040, 50:130] = 130  # 轻微发光不算
    assert _art_changed(a, b2, letters) is None


def test_tap_swallowed_while_previous_card_resolves_is_not_landed(game, monkeypatch):
    """codex-20261109-1-e / 1-g 循环 T7：决策帧上 オーバードライブ+ 还在结算，点 教えてあげる+ 被吞掉（一下都没选中）。
    上一张的效果让体力变了，旧判据「签名变了 = 打出」记成已打出。手牌张数没变、卡名还在原位 → 没打出。"""
    game(lost=[True] * 10)
    label = [ex._Text("教えてあげる+", Rect(X - 80, 1100, 160, 20))]
    # 决策帧上卡名被上一张浮起的牌挡着（name_visible 为假，旧的「卡名仍在原位」分支不触发），结算完卡名露出来
    monkeypatch.setattr(ex, "_roi_texts", lambda img, y1, y2: [] if img == "start" else label)
    monkeypatch.setattr(ex, "_lite_sig", lambda img: ((5, 20, 10), ((1, 1), (2, 2))) if img == "start" else ((5, 18, 12), ((1, 1), (2, 2))))
    ex._play_card(X, Y, "start", "教えてあげる+")
    assert not ex.PLAY_RESULT["confirmed"]
    assert "没打出" in ex._play_not_landed(NS(img="start"), X, "教えてあげる+")


def test_wait_flight_waits_until_previous_card_lands(monkeypatch):
    """codex-20261109-1-i R2：オーバードライブ+ 还浮在手牌上方结算时点 教えてあげる+ 被吞。点之前原地等它落下。"""
    import numpy as np

    frames = iter([np.ones((1280, 720, 3), np.uint8) * v for v in (1, 2, 3)])
    monkeypatch.setattr(ex, "device", NS(screenshot=lambda: next(frames)))
    monkeypatch.setattr(ex, "sleep", lambda s: None)
    monkeypatch.setattr(ex, "card_in_flight", lambda img: int(img[0, 0, 0]) < 3)
    first = np.zeros((1280, 720, 3), np.uint8)
    assert int(ex.wait_flight(first)[0, 0, 0]) == 3


def test_confirmed_but_hand_unchanged_is_not_landed(game, monkeypatch):
    """codex-20261109-1-j R2 T6：选中、确认两下都收到了，オーバードライブ+ 还在手里。结算稳定后角标位置、卡面都没变 → 没打出。"""
    import numpy as np

    g = game()
    ex._play_card(X, Y, "start")
    assert ex.PLAY_RESULT["confirmed"]
    same = np.zeros((1280, 720, 3), np.uint8)
    monkeypatch.setattr(ex, "device", NS(screenshot=lambda: same, click=g.click))
    monkeypatch.setattr(ex, "_lite_sig", lambda img: ((5, 20, 10), ((1, 1), (2, 2))))
    assert "没打出" in ex._play_not_landed(NS(img=same), X)
    drawn = same.copy()
    drawn[900:1080, 240:360] = 200  # codex-20261109-1-l：打出后抽牌补到同一格，角标位置不变、那一格卡面换了 = 打出了
    assert ex._play_not_landed(NS(img=drawn), X) is None
