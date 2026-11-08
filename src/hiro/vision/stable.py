"""按几个局部小块判断画面是否稳定（比整帧比较 / OCR 快得多，也能看出淡入淡出）。

只采界面元素所在的位置（按钮栏、标题条、文字区），避开一直在动的背景（回忆生成页、首页的背景动画会让整帧永远不稳定）。
每块取 size×size，缩成 8×8 灰度，和上一帧比平均差；所有块都小于 threshold、连续 frames 帧，就算稳定。
"""

from __future__ import annotations

import time

import cv2
import numpy as np

GENERIC = ((360, 80), (360, 640), (360, 1160))  # 不知道是哪一页时：顶部标题、中间、底部按钮栏
BOTTOM_BAR = ((110, 1162), (360, 1160), (590, 1165))  # 回忆页 / 结算页底部按钮栏


def signature(img, points=GENERIC, size: int = 40):
    """每个点周围 size×size 的小块缩成 8×8 灰度。超出画面的部分自动裁掉。"""
    h, w = img.shape[:2]
    half = size // 2
    out = []
    for x, y in points:
        patch = img[max(0, y - half) : min(h, y + half), max(0, x - half) : min(w, x + half)]
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
        out.append(cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float32))
    return out


def difference(a, b) -> float:
    """两组小块里变化最大的那块的平均灰度差。"""
    return max(float(np.abs(x - y).mean()) for x, y in zip(a, b))


def wait_stable(shoot, points=GENERIC, *, frames: int = 2, threshold: float = 1.5, timeout: float = 3.0,
                poll: float = 0.08, tap=None, size: int = 40):
    """一直截图直到这几块连续 frames 帧都几乎不变。tap：每次没稳定时调用（比如点 CORNER 快进）。
    返回 (最后一帧, 是否稳定)。"""
    deadline = time.monotonic() + timeout
    prev = None
    still = 0
    img = None
    while True:
        img = shoot()
        sig = signature(img, points, size) if img is not None else None
        if sig is not None and prev is not None and difference(sig, prev) < threshold:
            still += 1
            if still >= frames:
                return img, True
        else:
            still = 0
        prev = sig
        if time.monotonic() >= deadline:
            return img, False
        if tap is not None and still == 0:
            tap()
        time.sleep(poll)
