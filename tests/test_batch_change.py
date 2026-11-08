"""tools/batch_change：换卡页判阶段、屏幕对齐整副行列表、简表。纯函数，不需要设备或截图。"""

import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_change as BC  # noqa: E402


def card(row, col, cid, name=None, **kw):
    return {"row": row, "col": col, "card_id": cid, "name": name or cid, **kw}


# 整副 4 行：r0 a ? b | r1 c d e | r2 f g ? | r3 h
DECK = [card(0, 0, "a"), card(0, 1, None), card(0, 2, "b"),
        card(1, 0, "c"), card(1, 1, "d"), card(1, 2, "e"),
        card(2, 0, "f"), card(2, 1, "g"), card(2, 2, None),
        card(3, 0, "h")]  # fmt: skip
ROWS = BC.deck_rows(DECK)


def test_stage_of():
    assert BC.stage_of(["チェンジで獲得するスキルカードを選んでください"]) == 1
    assert BC.stage_of(["チェンジするスキルカードを選択してください"]) == 2
    assert BC.stage_of(["スキルカードが選択されていません"]) == 2
    assert BC.stage_of(["授業", "レッスンは適度に"]) is None


def test_offset_top_screen():
    visible = [card(0, 0, "a"), card(0, 1, None), card(0, 2, "b"), card(1, 0, "c"), card(1, 1, "d"), card(1, 2, "e")]
    assert BC.screen_offset(visible, ROWS) == 0


def test_offset_scrolled_screen_with_unknown_cells():
    visible = [card(0, 0, "c"), card(0, 1, None), card(0, 2, "e"), card(1, 0, "f"), card(1, 1, "g"), card(1, 2, None),
               card(2, 0, "h")]  # fmt: skip
    assert BC.screen_offset(visible, ROWS) == 1


def test_offset_refuses_mismatch_and_unrecognized_rows():
    assert BC.screen_offset([card(0, 0, "x"), card(0, 1, "d")], ROWS) is None  # 牌组变了
    assert BC.screen_offset([card(0, 0, None), card(0, 1, None)], ROWS) is None  # 整行没认出：不猜
    assert BC.screen_offset([], ROWS) is None


def test_offset_refuses_ambiguous():
    rows = BC.deck_rows([card(0, 0, "a"), card(1, 0, "a"), card(2, 0, "b")])
    assert BC.screen_offset([card(0, 0, "a")], rows) is None


def test_summary_marks_once_and_unknown():
    scan = {"cards": [card(0, 0, "x", "タフネス", upgrade=1, once=True), card(0, 1, None, None)]}
    assert BC.summary(scan) == ["r0: c0 タフネス+（1回） | c1 ?"]


# ------------------------------------------------------------------ 实机流程（假设备）
class Dev:
    def __init__(self):
        self.clicks, self.swipes = [], []

    def screenshot(self):
        return "img"

    def click(self, x, y):
        self.clicks.append((x, y))

    def swipe(self, *a, **k):
        self.swipes.append(a)


def fake_tools(visible, selected, scroll=None):
    """阶段 2 画面：visible 是当前屏卡格（屏内行号）；selected 是点选后详情栏读到的卡（{} = 没认出）。"""
    for c in visible:
        c.setdefault("center", (100 + 100 * c["col"], 700 + 140 * c["row"]))
    scrolls = iter(scroll or [])

    def dialog_scroll(img, area):
        return next(scrolls, {"scrollable": True, "thumb": [0, 100], "at_top": True})

    return {
        "ocr_boxes": lambda img: [("チェンジするスキルカードを選択してください", 1, (0, 580, 720, 610))],
        "read_card_grid": lambda img, area, geometry=None, icons=False: visible,
        "read_card_dialog": lambda img, area, icons=False: {"cards": visible, "selected": visible.index(selected[0]) if selected else None,
                                                            "selected_card": selected[1] if selected else {}},
        "dialog_scroll": dialog_scroll, "_dialog_swipe": lambda area, down, swipe: swipe(0, 0, 0, 0),
        "CHANGE2_CARD_AREA": (0, 600, 720, 1100), "CARD_OP_SWIPE_DURATION": 0.1,
        "find_text": lambda *a, **k: None,
    }


@pytest.mark.parametrize("target, why", [
    (card(0, 1, None), "目标卡认不出"),                                  # 扫描表里是「?」
    (card(0, 0, "a", customized=True), "目标卡客制化过"),
])
def test_commit_refuses_unknown_or_customized_target(tmp_path, monkeypatch, target, why):
    """1-i：扫描表 r1c0 是「?」（实为客制化过的巻き返し+），原来 same_card 对认不出的目标直接放行，加 --commit 会点「チェンジ」。"""
    import json

    scan = tmp_path / "scan.json"
    deck = [target if (c["row"], c["col"]) == (target["row"], target["col"]) else c for c in DECK]
    scan.write_text(json.dumps({"cards": deck}))
    visible = [dict(c) for c in deck if c["row"] <= 1]
    cell = next(c for c in visible if (c["row"], c["col"]) == (target["row"], target["col"]))
    monkeypatch.setattr(BC, "_tools", lambda: fake_tools(visible, (cell, {"card_id": target["card_id"], "name": "x"})))
    monkeypatch.setattr(BC, "SETTLE", 0)
    dev = Dev()
    out = BC.pick(dev, "b", 1, col=target["col"], row=target["row"], commit=True, scan_file=scan)
    assert not out["ok"] and why in out["why"]
    assert dev.clicks == [cell["center"]]  # 只点选了格子，没点「チェンジ」


def test_commit_refuses_when_detail_name_unread(tmp_path, monkeypatch):
    import json

    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"cards": DECK}))
    visible = [dict(c) for c in DECK if c["row"] <= 1]
    cell = visible[0]
    monkeypatch.setattr(BC, "_tools", lambda: fake_tools(visible, (cell, {})))
    monkeypatch.setattr(BC, "SETTLE", 0)
    dev = Dev()
    out = BC.pick(dev, "b", 1, col=0, row=0, commit=True, scan_file=scan)
    assert not out["ok"] and "详情栏没认出" in out["why"] and dev.clicks == [cell["center"]]


def test_to_top_swipes_up_until_thumb_at_top(monkeypatch):
    monkeypatch.setattr(BC.time, "sleep", lambda s: None)
    t = fake_tools([], None, scroll=[{"scrollable": True, "thumb": [200, 300], "at_top": False},
                                     {"scrollable": True, "thumb": [100, 200], "at_top": False},
                                     {"scrollable": True, "thumb": [0, 100], "at_top": True}])  # fmt: skip
    dev = Dev()
    assert BC.to_top(dev, t, dev.swipe) and len(dev.swipes) == 2


def test_to_top_gives_up_when_list_does_not_move(monkeypatch):
    monkeypatch.setattr(BC.time, "sleep", lambda s: None)
    stuck = {"scrollable": True, "thumb": [200, 300], "at_top": False}
    t = fake_tools([], None, scroll=[stuck] * 6)
    dev = Dev()
    assert not BC.to_top(dev, t, dev.swipe)


def test_selection_matches_by_position_not_screen_row(tmp_path, monkeypatch):
    """点选后那帧顶上多认出一整行，选中格的屏内行号 +1，但位置和卡都对：算选中正确（1-i 实机误报「选中框对不上」）。"""
    import json

    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"cards": DECK}))
    visible = [dict(c) for c in DECK if c["row"] <= 1]
    cell = next(c for c in visible if (c["row"], c["col"]) == (1, 0))
    fake = fake_tools(visible, None)
    shifted = dict(cell, row=cell["row"] + 1)  # 下一帧的屏内行号
    fake["read_card_dialog"] = lambda img, area, icons=False: {"cards": [shifted], "selected": 0,
                                                               "selected_card": {"card_id": "c", "name": "c"}}
    monkeypatch.setattr(BC, "_tools", lambda: fake)
    monkeypatch.setattr(BC, "SETTLE", 0)
    out = BC.pick(Dev(), "b", 1, col=0, row=1, commit=False, scan_file=scan)
    assert out["ok"], out


def test_changed_text_split_across_boxes():
    assert BC.changed_text(["嬉しい誤算+を嬉しい誤算+にチェンジ", "しました"])
    assert BC.changed_text(["AをBにチェンジしました"])
    assert not BC.changed_text(["チェンジするスキルカードを選択してください"])
