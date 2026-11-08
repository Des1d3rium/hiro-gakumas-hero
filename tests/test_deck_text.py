"""牌组一览列表显示读法（hiro.produce.deck_text）：分组标题、卡名匹配、多屏拼接、切换按钮。"""

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
