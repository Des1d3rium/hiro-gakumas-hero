"""按钮表（hiro.produce.hif_buttons）与各模块已有实测坐标一致；局部稳定判断（hiro.vision.stable）。"""

import numpy as np

from hiro.produce import hif_buttons as BTN
from hiro.vision import stable


def test_buttons_match_existing_modules():
    from hiro.loop.advance import ADVANCE_TAP
    from hiro.produce import shop
    from hiro.vision.event import ROW_Y

    assert BTN.CORNER == ADVANCE_TAP
    assert BTN.OPTION_ROWS == ROW_Y
    assert BTN.SHOP_END == shop.END_TAP
    assert (BTN.SHOP_UPGRADE, BTN.SHOP_DELETE) == (shop.SERVICE_TAP["upgrade"], shop.SERVICE_TAP["delete"])
    assert BTN.REROLL == shop.REROLL_TAP


def test_all_points_inside_portrait_frame():
    for name in dir(BTN):
        if name.startswith("_"):
            continue
        v = getattr(BTN, name)
        pts = list(v.values()) if isinstance(v, dict) else [v] if isinstance(v, tuple) and len(v) == 2 and all(isinstance(c, int) for c in v) else []
        for x, y in pts:
            assert 0 <= x < 720 and 0 <= y < 1280, name


def test_wait_stable_ignores_moving_background():
    """背景（采样块以外）一直在动、按钮栏不动：算稳定；按钮栏还在淡入：不稳定。"""
    frames = []
    for i in range(6):
        img = np.zeros((1280, 720, 3), np.uint8)
        img[:900] = (i * 40) % 255  # 背景动画
        img[1100:1220] = 200  # 按钮栏
        frames.append(img)
    it = iter(frames)
    img, ok = stable.wait_stable(lambda: next(it), stable.BOTTOM_BAR, timeout=1, poll=0)
    assert ok

    fading = iter([np.full((1280, 720, 3), v, np.uint8) for v in (0, 40, 80, 120, 160, 200, 240)])
    taps = []
    img, ok = stable.wait_stable(lambda: next(fading), stable.BOTTOM_BAR, timeout=0.05, poll=0.01, tap=lambda: taps.append(1))
    assert not ok and taps


# ---- 实机画面核对（tests/fixtures/walkthrough：使用者 2026-11-07 的 H.I.F 流程截图笔记，tools/fixtures/build_local.py 生成）
# 截图是手动框选后缩放到 720×1280，边缘可能差十几像素：按「按钮文字框的并集外扩 MARGIN 像素」判断点在不在按钮上。
from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

WALK = Path(__file__).with_name("fixtures") / "walkthrough"
MARGIN = 28


@lru_cache(maxsize=None)
def _ocr(name: str):
    import cv2

    from hiro.produce.ops import ocr_boxes

    img = cv2.imread(str(WALK / f"{name}.png"))
    return [("".join(t.split()), tuple(int(v) for v in b)) for t, _, b in ocr_boxes(img)]


def _union(name: str, words) -> tuple[int, int, int, int]:
    boxes = [b for t, b in _ocr(name) if any(w in t for w in words)]
    assert boxes, f"{name} 上没读到 {words}"
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


@pytest.mark.fixtures
@pytest.mark.parametrize(("name", "point", "words"), [
    ("02-idol-select", "IDOL_NEXT", ["次へ"]),
    ("03-start-confirm", "START", ["プロデュース開始", "32→12"]),  # 按钮下半有「32→12」AP 行；START 贴着文字上缘
    ("03-start-confirm", "MEMORY_CHANGE", ["変更"]),
    ("08-drink-select", "BOTTOM_LOW", ["受け取る"]),
    ("10-card-acquire", "BOTTOM_LOW", ["受け取る"]),
    ("11-consult-action", "BOTTOM_LOW", ["相談"]),  # 相談行动页只有这一个选项（笔记）
    ("09-outing-supply", "REST", ["休む"]),
    ("12-consult-shop", "SHOP_END", ["終了"]),
    ("12-consult-shop", "SHOP_UPGRADE", ["強化", "125"]),
    ("12-consult-shop", "SHOP_DELETE", ["削除", "175"]),
    ("14-interval-shop", "SHOP_END", ["終了"]),  # interval-end 也点 SHOP_END
    ("14-interval-shop", "INTERVAL_UPGRADE", ["強化", "100"]),
    ("14-interval-shop", "INTERVAL_CUSTOMIZE", ["スキルカード", "カスタマイズ"]),
    ("14-interval-shop", "INTERVAL_RECOVER", ["回復"]),
    ("13-r1-result", "RESULT_RETRY", ["再挑戦"]),
    ("13-r1-result", "RESULT_NEXT", ["次へ"]),
    ("16-r2-lost", "RESULT_RETRY", ["再挑戦"]),
    ("16-r2-lost", "RESULT_NEXT", ["次へ"]),
    ("17-retry-confirm", "RETRY_DIALOG_END", ["プロデュース", "終了"]),
    ("17-retry-confirm", "RETRY_DIALOG_RETRY", ["再挑戦", "あと3回"]),
    ("19-memory-generate", "MEMORY_GENERATE", ["生成"]),
    ("20-memory-result", "MEMORY_REGEN", ["再生成"]),
    ("20-memory-result", "BOTTOM_HIGH", ["次へ"]),
])  # fmt: skip
def test_buttons_land_on_real_frames(name, point, words):
    """笔记：「饮料、技能卡、强化 / 删除、刷新、终了的位置全都是固定的」——固定坐标要落在实机按钮文字上。"""
    x, y = getattr(BTN, point)
    x1, y1, x2, y2 = _union(name, words)
    assert x1 - MARGIN <= x <= x2 + MARGIN and y1 - MARGIN <= y <= y2 + MARGIN, (point, (x, y), (x1, y1, x2, y2))


@pytest.mark.fixtures
def test_reroll_points_per_page():
    for name, page in (("12-consult-shop", "consult"), ("14-interval-shop", "interval")):
        x1, y1, x2, y2 = _union(name, ["リフレッシュ"])
        x, y = BTN.REROLL[page]
        assert x1 - MARGIN <= x <= x2 + MARGIN and y1 - MARGIN <= y <= y2 + MARGIN, page


@pytest.mark.fixtures
@pytest.mark.parametrize(("name", "words", "xs"), [
    ("04-lesson-three", ["授業"], BTN.THREE_X),
    ("07-public-lesson", ["公開レッスン"], BTN.THREE_X),
    ("09-outing-supply", ["おでかけ", "差し入れ"], (278, 444)),  # 两张卡居中排，不在 THREE_X 上
])  # fmt: skip
def test_action_cards_fixed_columns(name, words, xs):
    """笔记：三个授業 / 公開レッスン「位置一样顺序也一样」；支给和外出「顺序和位置始终固定」（两张，左おでかけ 右差し入れ）。"""
    cols = sorted((b[0] + b[2]) // 2 for t, b in _ocr(name) if any(t.endswith(w) for w in words) and b[1] > 1000)
    assert len(cols) == len(xs) and all(abs(c - x) <= MARGIN for c, x in zip(cols, xs)), cols


@pytest.mark.fixtures
def test_option_rows_on_real_lesson_branch():
    """授業选项三行（笔记：非凡角色上 = 強気、中 = 全力、下 = 未特定）：OPTION_ROWS 对得上三行文字。"""
    rows = sorted((b[1] + b[3]) // 2 for t, b in _ocr("05-lesson-options")
                  if any(w in t for w in ("レッスンは適度に", "入念にストレッチ", "メンタルケア")))
    assert len(rows) == 3 and all(abs(r - y) <= 15 for r, y in zip(rows, BTN.OPTION_ROWS)), rows


@pytest.mark.fixtures
@pytest.mark.parametrize("name", ["04-lesson-three", "07-public-lesson", "09-outing-supply", "11-consult-action", "13-r1-result",
                                  "16-r2-lost", "18-final-evaluation", "21-produce-complete"])  # fmt: skip
def test_corner_and_bar_gap_hit_no_text(name):
    """(12,12) 和饮料栏右侧空缺 BAR_GAP 在非选择页上不压任何文字（笔记：选择中以外这个位置几乎不会出现按钮）。"""
    for x, y in (BTN.CORNER, BTN.BAR_GAP):
        hits = [t for t, b in _ocr(name) if b[0] - 20 <= x <= b[2] + 20 and b[1] - 20 <= y <= b[3] + 20]
        assert not hits, ((x, y), hits)
