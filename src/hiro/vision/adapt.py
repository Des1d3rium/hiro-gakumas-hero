# 来源：digital-hiro src/hiro/vision/adapt.py（develop c3fc27f）
"""把旧窗口比例的截图换算成 720x1280 逻辑画面，以便复用历史记录验证识别。

历史记录（2026-10 中旬）是 540x890pt 窗口的 2x 截图：1080x1780，含 56px 标题栏，内容区约 10:16。
游戏 UI 大多贴顶或贴底排布，所以按宽度缩放后，上半贴顶、下半贴底，中间缺的高度用中线附近的像素拉伸补齐。
这是近似换算：贴顶/贴底元素位置可用，中部元素（立绘、中间对话框）会有偏移。
"""

import cv2
import numpy as np
from cv2.typing import MatLike

TITLE_BAR_PX = 56  # 2x 截图中的标题栏高度


def to_logical(img: MatLike, *, has_title_bar: bool | None = None) -> MatLike:
    h, w = img.shape[:2]
    if has_title_bar is None:
        has_title_bar = h / w > 1.6 and img[: TITLE_BAR_PX // 2].mean() < 90
    if has_title_bar:
        img = img[TITLE_BAR_PX:]
        h = img.shape[0]
    scaled_h = round(h * 720 / w)
    img = cv2.resize(img, (720, scaled_h), interpolation=cv2.INTER_AREA)
    if scaled_h == 1280:
        return img
    if scaled_h > 1280:
        return cv2.resize(img, (720, 1280), interpolation=cv2.INTER_AREA)
    half = scaled_h // 2
    gap = 1280 - scaled_h
    middle = cv2.resize(img[half - 1 : half + 1], (720, gap), interpolation=cv2.INTER_LINEAR)
    return np.vstack([img[:half], middle, img[half:]])
