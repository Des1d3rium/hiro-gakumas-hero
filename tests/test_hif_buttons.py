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
