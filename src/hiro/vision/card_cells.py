"""换卡页卡格的图片认卡（渲染模板 + 稳健相关），给 KAA 缩略图库认不出的格子补答案。

KAA match_card_region 在换卡阶段 2 等卡格上会漏认：实机扫描（2026-11-07，22 次换卡阶段 2 整副扫描）每副 20–22 张里
有 4–7 张 card_id 为 None。认不出的卡换卡时只能跳过或キャンセル。

做法：
- 模板：cache/card_icons.npz（hiro.vision.card_icon_render 按游戏 UI 结构渲染的干净卡图标，含稀有度底色、分类遮罩、外框）。
  卡格大小不同（换卡阶段 1 是 124、阶段 2 和删卡页是 118、一览网格是 141），一律缩到 SIZE 边长再比。
- 清晰度：缩小（INTER_AREA）后两边都做同样的轻微模糊，截图的 JPEG / 缩放差异落在模糊尺度以下。
- 亮度：先按灰度中位数把整格拉回同一亮度（压暗的卡是整体乘系数），再在 Lab 上每通道减中位数、除以 MAD。
- 角标：效果图标、「+」、费用、定制计数等盖在卡面上、位置因卡而异。按像素算距离后只取最像的 KEEP 比例求平均，
  被角标盖住的像素自然落在剔除的那部分里。
- 位置：卡格框由类别字母推出，可能差一两个像素。先按原位置比（每格约 6 ms），认不出再在 ±2 像素内搜一次（SHIFT_RETRY）。
- 同一张卡各偶像的配色版本都比，按卡（主数据里 assetId 相同的卡 ID）汇总取最好。

「认出」要同时满足：最佳分 ≤ MAX_SCORE、和第二张卡的差 ≥ MIN_MARGIN、卡格灰度标准差（按亮度归一）在 MIN_STD–MAX_STD（不是空格 / 平色，也不是跨格 / 别的界面）。
阈值的依据见 docs/assets.md「换卡卡格的图片认卡」。

只补 KAA 认不出的格子：两边都认出但不一致时不采用任何一边（调用方当认不出处理）。开关 HIRO_CARD_ICON=1（默认关）。
没有渲染库（cache/card_icons.npz）时整个功能关闭，返回认不出。
"""

from __future__ import annotations

import os
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

SIZE = 32  # 比对边长
SIGMA = 0.7  # 缩小后的模糊（两边相同）
INSET = 2  # 四边去掉的像素（SIZE 尺度）：外框边缘对位置最敏感
CORNER = 0.14  # 圆角半径（占边长）：圆角外是页面背景
KEEP = 0.6  # 只取最像的这部分像素
PREFILTER = 60  # 先按整体相关取前 N 个模板，再逐个算剔除后的距离
SHIFT_RETRY = (-2, 0, 2)  # 原位置认不出时，卡格框在这些偏移（截图像素）里再搜一次
MAD_FLOOR = 2.0  # MAD 下限（Lab 单位），平色区域不被放大成噪声
BRIGHT_LEVEL = 200.0  # 亮度拉回到的灰度中位数
MAX_SCORE = 0.95
MIN_MARGIN = 0.12
MIN_STD, MAX_STD = 25.0, 85.0  # 归一后的灰度标准差：实机卡格 25–75；空格 / 平色 ≤16，跨两格 / 饮料栏 / 底栏多在 90–270
FLAT_MEAN = 200.0


def enabled() -> bool:
    return os.environ.get("HIRO_CARD_ICON") == "1"


@lru_cache(maxsize=1)
def _mask() -> np.ndarray:
    yy, xx = np.mgrid[:SIZE, :SIZE]
    m = (xx >= INSET) & (xx < SIZE - INSET) & (yy >= INSET) & (yy < SIZE - INSET)
    r = SIZE * CORNER
    for cx in (r, SIZE - 1 - r):
        for cy in (r, SIZE - 1 - r):
            near = ((xx < r) if cx < SIZE / 2 else (xx > SIZE - 1 - r)) & ((yy < r) if cy < SIZE / 2 else (yy > SIZE - 1 - r))
            m &= ~(near & ((xx - cx) ** 2 + (yy - cy) ** 2 > r * r))
    return m


def features(img: MatLike) -> np.ndarray:
    """卡格（BGR，任意边长的方图）→ 参与比对的像素 P×3（Lab，各通道减中位数、除以 MAD）。
    游戏压暗卡格是整体乘一个系数：先在 BGR 上按灰度中位数拉回 BRIGHT_LEVEL，再转 Lab（Lab 非线性，直接归一抵消不了压暗）。"""
    g = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    if SIGMA:
        g = cv2.GaussianBlur(g, (0, 0), SIGMA)
    m = _mask()
    f = g.astype(np.float32)
    level = float(np.median(cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)[m]))
    f = np.clip(f * (BRIGHT_LEVEL / max(level, 1.0)), 0, 255)  # 压暗 = 整体乘系数：先按比例拉回
    px = cv2.cvtColor(f.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)[m]
    med = np.median(px, 0)
    mad = np.median(np.abs(px - med), 0) * 1.4826 + MAD_FLOOR
    return (px - med) / mad


class Library:
    """渲染模板库：names（素材名）、keys（每个模板对应的卡 ID 元组）、Z（N×P×3 特征）、F（展平后单位化，预筛用）。"""

    def __init__(self, names: list[str], imgs: np.ndarray, keys: list[tuple[str, ...]]):
        self.names, self.keys = names, keys
        self.Z = np.stack([features(im) for im in imgs]) if len(imgs) else np.zeros((0, int(_mask().sum()), 3), np.float32)
        f = self.Z.reshape(len(self.Z), -1)
        self.F = f / (np.linalg.norm(f, axis=1, keepdims=True) + 1e-6)

    def rank(self, img: MatLike, rect: tuple[int, int, int, int] | None = None,
             shifts: tuple[int, ...] = (0,)) -> list[tuple[tuple[str, ...], float, str]]:  # fmt: skip
        """→ [(卡 ID 元组, 分数, 素材名)]，分数越小越像，按卡汇总后从好到差。rect=None 时 img 就是卡格；
        shifts：卡格框在 x / y 上各试这些偏移，取最好的。"""
        if not len(self.Z):
            return []
        crops = []
        if rect is None:
            crops.append(img)
        else:
            x, y, w, h = rect
            H, W = img.shape[:2]
            for dx in shifts:
                for dy in shifts:
                    if 0 <= x + dx and 0 <= y + dy and x + dx + w <= W and y + dy + h <= H:
                        crops.append(img[y + dy : y + dy + h, x + dx : x + dx + w])
        best: dict[tuple[str, ...], tuple[float, str]] = {}
        qs = [features(c) for c in crops]
        sims = self.F @ np.stack([q.ravel() / (np.linalg.norm(q) + 1e-6) for q in qs]).T  # N×位置
        pre = np.argsort(-sims.max(1))[:PREFILTER]  # 各位置里整体相关最好的前 N 个模板，再逐位置精算
        Zp = self.Z[pre]
        for q in qs:
            dist = np.linalg.norm(Zp - q[None], axis=2)
            k = max(1, int(dist.shape[1] * KEEP))
            score = np.partition(dist, k - 1, axis=1)[:, :k].mean(1)
            for i, s in zip(pre, score):
                key = self.keys[i]
                if key not in best or s < best[key][0]:
                    best[key] = (float(s), self.names[i])
        return sorted(((k, s, n) for k, (s, n) in best.items()), key=lambda t: t[1])


def _base_asset(name: str, assets: set[str]) -> str:
    base = name
    while base not in assets and "-" in base.removeprefix("img_general_skillcard_"):
        base = base.rsplit("-", 1)[0]
    return base


@lru_cache(maxsize=1)
def library() -> Library | None:
    """读 cache/card_icons.npz（没有就 None）；素材名按主数据 assetId 对到卡 ID。"""
    from hiro import plugins
    from hiro.vision.card_icon_render import CACHE

    path = Path(os.environ.get("HIRO_CARD_ICON_LIB") or CACHE)
    if not path.exists():
        return None
    data = np.load(path, allow_pickle=False)
    names = [str(n) for n in data["names"]]
    src = plugins.get("asset_source")
    catalog = (src.card_catalog() if src is not None else None) or {}
    by_asset: dict[str, set[str]] = defaultdict(set)
    for c in catalog.values():
        if c.get("asset"):
            by_asset[c["asset"]].add(c["id"])
    keys = [tuple(sorted(by_asset.get(_base_asset(n, set(by_asset)), ()))) or (n,) for n in names]
    return Library(names, data["imgs"], keys)


def flatness(cell: MatLike) -> float:
    """卡格灰度标准差，先按平均亮度归一到 FLAT_MEAN（压暗的卡标准差跟着变小，不能当成空格）。"""
    if not cell.size:
        return 0.0
    g = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(g.std() * FLAT_MEAN / max(float(g.mean()), 1.0))


def match(img: MatLike, rect: tuple[int, int, int, int] | None = None, lib: Library | None = None) -> dict:
    """卡格 → {card_id, ids, score, margin, std, asset, known}。
    card_id：认出且这张素材只对应一个卡 ID 时给出；ids：最像那张素材对应的全部卡 ID。"""
    lib = lib if lib is not None else library()
    if rect is not None:
        x, y, w, h = rect
        cell = img[y : y + h, x : x + w]
    else:
        cell = img
    std = flatness(cell)
    out = _judge(lib.rank(img, rect) if lib is not None else [], std)
    if not out["known"] and rect is not None and lib is not None and MIN_STD <= std <= MAX_STD:
        retry = _judge(lib.rank(img, rect, SHIFT_RETRY), std)
        if retry["known"]:
            out = {**retry, "shifted": True}
    return out


def _judge(ranked: list[tuple[tuple[str, ...], float, str]], std: float) -> dict:
    if not ranked:
        return {"card_id": None, "ids": [], "score": None, "margin": None, "std": round(std, 1), "asset": None, "known": False}
    ids, score, asset = ranked[0]
    margin = ranked[1][1] - score if len(ranked) > 1 else float("inf")
    known = score <= MAX_SCORE and margin >= MIN_MARGIN and MIN_STD <= std <= MAX_STD and all(i.startswith("p_card-") for i in ids)
    return {"card_id": ids[0] if known and len(ids) == 1 else None, "ids": list(ids), "score": round(score, 3),
            "margin": round(margin, 3), "std": round(std, 1), "asset": asset, "known": known}  # fmt: skip


def resolve(kaa_id: str | None, img: MatLike, rect: tuple[int, int, int, int], lib: Library | None = None) -> tuple[str | None, dict]:
    """KAA 的结果 + 图片认卡 → (采用的卡 ID, 图片认卡详情)。
    KAA 认出：图片也认出且不同 → None（不猜）；其余照用 KAA。KAA 认不出：用图片认出的结果（认不出仍是 None）。"""
    m = match(img, rect, lib)
    if kaa_id is not None:
        if m["known"] and kaa_id not in m["ids"]:
            return None, {**m, "conflict": kaa_id}
        return kaa_id, m
    return m["card_id"], m
