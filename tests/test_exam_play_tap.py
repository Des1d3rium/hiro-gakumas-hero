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
