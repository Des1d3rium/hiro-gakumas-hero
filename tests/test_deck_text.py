"""牌组一览列表显示读法（hiro.produce.deck_text）：分组标题、卡名匹配、多屏拼接、切换按钮。"""

import pytest

from hiro.produce import deck_text as dt

NAMES = frozenset({"頂点へ", "頂点へ+", "エンターテイナー+", "トレーニングの成果+", "教えてあげる+"})


def box(text, y, x=100):
    return (text, 0.9, (x, y, x + 200, y + 30))


def test_match_name_exact_plus_and_fuzzy():
    assert dt.match_name("頂点へ+", NAMES) == "頂点へ+"
    assert dt.match_name("頂点へ ＋", NAMES) == "頂点へ+"
    assert dt.match_name("トレ一ニングの成果+", NAMES) == "トレーニングの成果+"
    assert dt.match_name("閉じる", NAMES) is None


def test_parse_screen_groups_and_unknown():
    boxes = [box("手札 (2)", 420), box("頂点へ+", 470), box("エンターテイナー+", 520), box("山札(1)", 580),
             box("教えてあげる+", 630), box("Pアイテム", 1050), box("ほげほげ", 700)]
    page = dt.parse_screen(boxes, NAMES)
    assert page["rows"] == [("手札", "頂点へ+"), ("手札", "エンターテイナー+"), ("山札", "教えてあげる+")]
    assert page["headers"] == {"手札": 2, "山札": 1}
    assert page["unknown"] == ["ほげほげ"]


def test_merge_rows_keeps_duplicates():
    a = [("山札", "頂点へ+"), ("山札", "教えてあげる+"), ("山札", "教えてあげる+")]
    b = [("山札", "教えてあげる+"), ("山札", "教えてあげる+"), ("山札", "エンターテイナー+")]
    assert dt.merge_rows([a, b]) == [*a, ("山札", "エンターテイナー+")]


def test_read_list_toggles_and_checks_total():
    import numpy as np

    frames = [np.zeros((1280, 720, 3), np.uint8), np.full((1280, 720, 3), 50, np.uint8)]
    shots = iter([frames[0], frames[1], frames[1]])
    clicks = []
    pages = {id(frames[1]): [box("スキルカード(2)", 1040), box("手札 (2)", 420), box("頂点へ+", 470), box("エンターテイナー+", 520)]}
    out = dt.read_list(shoot=lambda: next(shots), click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: None,
                       ocr_boxes=lambda im: pages.get(id(im), []), view_diff=lambda a, b: float(abs(int(a.mean()) - int(b.mean()))),
                       sleep=lambda s: None, names=NAMES)
    assert clicks == [dt.TOGGLE, dt.TOGGLE]
    assert out["cards"] == ["頂点へ+", "エンターテイナー+"] and out["total"] == 2 and out["complete"]


def test_header_with_fold_icon_and_groups_carry_across_screens():
    """codex-20261109-1-e 考试中牌库一览：标题带折叠图标「+手札（3）」「+山札(19)」；第 2 屏起标题滚出去，行的组是 None。
    原来标题一个都认不出、重叠按 (组, 卡名) 比对不上 → 少读 / 多读一张，牌堆层拿不到「山札 / 捨札」分组。"""
    import re

    assert dt._HEADER.match(dt.norm("+手札（3）")).groups() == ("手札", "3")
    assert re.match(dt._HEADER, dt.norm("+山札(19)"))[2] == "19"
    s1 = [("手札", "教えてあげる+"), ("山札", "プライド+"), ("山札", "アイドル魂+")]
    s2 = [(None, "プライド+"), (None, "アイドル魂+"), (None, "全身全霊+")]
    assert dt.merge_rows([s1, s2]) == [*s1, ("山札", "全身全霊+")]


@pytest.mark.parametrize("screens, want", [
    ([["a", "b", "c"], ["b", "d", "e"]], "abcde"),  # 这一屏漏读 c
    ([["a", "b", "d"], ["b", "c", "d", "e"]], "abcde"),  # 上一屏漏读 c
    ([["h", "h"], ["h", "h", "x"]], "hhx"),  # 同名两张
    ([["a", "b"], ["x", "y"]], "abxy"),  # 没有重叠
])
def test_merge_rows_tolerates_one_missed_row(screens, want):
    """codex-20261109-1-h：一屏 OCR 漏读一行，重叠对不上整屏又接一遍（读到 23 / 22）。对齐容许一处漏读。"""
    rows = [[(None, n) for n in s] for s in screens]
    assert "".join(n for _, n in dt.merge_rows(rows)) == want
