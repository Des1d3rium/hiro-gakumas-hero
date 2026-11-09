"""考试中支援卡的临时强化：手牌卡图右上角叠的支援卡头像个数（使用者 2026-11-09）。

支援卡在考试里随机强化手牌，可以超过「只能强化一次」（卡名显示「++」「+++」，多出的「+」是蓝色）；
每次强化在卡图右上角叠一个红框小头像（右下带红「+」），头像个数 = 临时强化次数。
这个强化不进账本（培育里的卡最多一个「+」），但打出时效果按总档位（账本档位 + 临时强化）算。

读法：头像的左边框是红→橙渐变的竖线，左侧紧贴一道白边；在卡名首字母框中心 cx 右侧 +34..+45 的竖条里找
「红边框且左边 2–3px 是白」的连续段（长 25–60px，每个头像约 44px），段起点按 15px 聚类，一个起点一个头像
（叠放的头像上下错开约 34px）。发灰（不可打出）的牌颜色变淡，严格阈值找不到时再用淡色阈值找一遍。
实测 1500 多张 1–4 张布局的手牌：与卡名「+」个数全部相容（多出的只有 OCR 读坏的卡名）。

5 张手牌时，除了最右一张，头像被下一张卡盖住（下一张卡左缘约在 cx+41）：返回 None（未知），不猜。
"""

from __future__ import annotations

import cv2
import numpy as np

COL_X = (34, 46)  # 头像左边框相对 cx 的 x 范围
SEARCH_Y = (-200, -30)  # 相对首字母框顶 y 的搜索范围
RUN_LEN = (25, 60)  # 一个头像左边框的连续段长度
RUN_GAP = 3
CLUSTER = 15  # 同一头像的段起点相差不超过这么多
MIN_SPACING = 140  # 相邻两张卡中心距小于这个（5 张布局 ≈123）：头像被盖住
STRICT = dict(sat=70, val=120, white_sat=45, white_val=215)
FAINT = dict(sat=30, val=225, white_sat=20, white_val=240)  # 发灰的牌


def _runs(col) -> list[tuple[int, int]]:
    out, run, gap, start = [], 0, 0, 0
    for i, b in enumerate(list(col) + [False] * (RUN_GAP + 2)):
        if b:
            if run == 0:
                start = i
            run += 1 + gap
            gap = 0
        elif run:
            gap += 1
            if gap > RUN_GAP:
                if run >= RUN_LEN[0]:
                    out.append((start, run))
                run = gap = 0
    return out


def _masks(hsv, p):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = ((h <= 20) | (h >= 160)) & (s >= p["sat"]) & (v >= p["val"])
    white = (s <= p["white_sat"]) & (v >= p["white_val"])
    return red, white


def _heads(red, white, cx: int, y: int) -> int:
    y1, y2 = max(0, y + SEARCH_Y[0]), max(0, y + SEARCH_Y[1])
    starts = set()
    for x in range(cx + COL_X[0], cx + COL_X[1]):
        if x - 3 < 0 or x >= red.shape[1]:
            continue
        col = red[y1:y2, x] & (white[y1:y2, x - 2] | white[y1:y2, x - 3])
        starts.update(st for st, ln in _runs(col) if ln <= RUN_LEN[1])
    clusters: list[int] = []
    for st in sorted(starts):
        if not clusters or st - clusters[-1] > CLUSTER:
            clusters.append(st)
    return len(clusters)


def count_boosts(img, centers: list[tuple[int, int]]) -> list[int | None]:
    """centers：每张手牌卡名首字母框的 (中心 x, 顶 y)，从左到右。返回每张的临时强化次数；被盖住的返回 None。"""
    if img is None or not centers:
        return []
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int16)
    strict, faint = _masks(hsv, STRICT), _masks(hsv, FAINT)
    out: list[int | None] = []
    for i, (cx, y) in enumerate(centers):
        nxt = centers[i + 1][0] if i + 1 < len(centers) else None
        if nxt is not None and nxt - cx < MIN_SPACING:
            out.append(None)
            continue
        out.append(_heads(*strict, cx, y) or _heads(*faint, cx, y))
    return out
