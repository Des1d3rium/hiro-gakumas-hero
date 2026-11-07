"""tools/batch_change：换卡页判阶段、屏幕对齐整副行列表、简表。纯函数，不需要设备或截图。"""

import sys
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
