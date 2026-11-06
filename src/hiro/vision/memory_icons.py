# 来源：digital-hiro src/hiro/vision/memory_icons.py（95294d2）；开源版：卡面目录走 AssetSource.card_art_dir()（GPL-3.0-or-later）
"""回忆小图标认卡：开局第 3 步「メモリー選択」每格回忆右侧的技能卡小图（约 84 像素），以及回忆选择页详情栏的大图标。

做法（2026-11-06，离线 13 个带标注样本 12 个排第一）：
- 图标四边各裁 7%（去掉外框），缩到 24x24，转 Lab；卡面库（素材包 cards/，每张 256 像素整图）同样处理。
- 逐像素算 Lab 距离，只取最像的 60% 像素求平均：左侧效果角标、右上「+」、右下费用、左上计数这些叠加物最多盖掉四成，不影响分数。
- 同一张卡有各偶像的插画版本（文件名后缀 -shro / -amao …）。回忆可以来自别的偶像，所以全部角色都比，按卡汇总（取最像的版本）。
- 分数越小越像。认对的样本最佳分 4.6–18.7，和第二名至少差 3.8；唯一认错的是 360 宽低清帧、角标压满的アノマリー卡，最佳分 27.4。
- 「认出」要同时满足：最佳分 ≤ UNKNOWN_SCORE、和第二名差 ≥ MIN_MARGIN、图标不是一片平色（灰度标准差 ≥ MIN_STD）。
  纯灰图、截到背景的位置最佳分可能不高，但和第二名只差 0.2–1.9，靠分差挡掉。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

import cv2
import numpy as np
from cv2.typing import MatLike

INSET = 0.07  # 图标四边裁掉的比例（外框）
SIZE = 24
KEEP = 0.6  # 只取最像的这部分像素
UNKNOWN_SCORE = 22.0  # 最佳分比这个差 = 认不出（遮挡 / 低清 / 布局变了）
MIN_MARGIN = 3.0  # 和第二名的分差小于这个 = 认不出
MIN_STD = 15.0  # 图标灰度标准差小于这个 = 不是图标（平色）
CACHE = os.path.join("cache", "memory_icon_index.npz")

# 第 3 步「メモリー選択」四格回忆的技能卡小图标（720x1280；2026-11-04 实机帧 tests/fixtures/support/step3-memory.jpg）
STEP3_ICONS = ((262, 745, 346, 828), (588, 745, 672, 828), (262, 877, 346, 960), (588, 877, 672, 960))
DETAIL_ICON = (44, 224, 190, 366)  # 回忆选择页详情栏的大图标（x1, y1, x2, y2），同 vision.memory.CARD_ICON


def _feat(img: MatLike, inset: float) -> np.ndarray:
    h, w = img.shape[:2]
    dy, dx = int(h * inset), int(w * inset)
    img = img[dy : h - dy, dx : w - dx]
    g = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(g, cv2.COLOR_BGR2LAB).astype(np.float32).reshape(-1, 3)


def card_key(name: str) -> str | None:
    """素材名（img_general_skillcard_act-2_062-shro）或卡 ID（p_card-02-act-2_062）→ 卡键 act-2_062；对不上返回 None。"""
    m = re.search(r"(acc|act|men|sup|ido)-(\d+_\d+)", name)
    return m.group(0) if m else None


@lru_cache(maxsize=1)
def _index() -> tuple[list[str], list[str], np.ndarray]:
    """(素材名, 卡键, 特征矩阵 N×P×3)。按素材目录的文件数 / 修改时间缓存到 cache/。"""
    from hiro.vision import assets

    root = assets.card_art_dir()
    if root is None:
        return [], [], np.zeros((0, SIZE * SIZE, 3), np.float32)  # 没有卡面素材：一律「认不出」，开局核对只警告
    folder = str(root)
    files = sorted(n for n in os.listdir(folder) if n.endswith(".png") and "_mid-" not in n)
    stamp = f"{len(files)}-{int(os.path.getmtime(folder))}-{SIZE}"
    if os.path.exists(CACHE):
        data = np.load(CACHE, allow_pickle=False)
        if str(data["stamp"]) == stamp:
            names = list(data["names"])
            return names, [card_key(n) or n for n in names], data["matrix"]
    names, rows = [], []
    for n in files:
        img = cv2.imread(os.path.join(folder, n))
        if img is None:
            continue
        names.append(n.rsplit(".", 1)[0])
        rows.append(_feat(img, 0.0))
    matrix = np.stack(rows)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    np.savez(CACHE, names=np.array(names), matrix=matrix, stamp=np.array(stamp))
    return names, [card_key(n) or n for n in names], matrix


def rank_cards(icon: MatLike) -> list[tuple[str, float, str]]:
    """图标 → [(卡键, 分数, 最像的素材名)]，按分数从好到差。"""
    names, keys, matrix = _index()
    d = np.linalg.norm(matrix - _feat(icon, INSET)[None], axis=2)
    k = int(d.shape[1] * KEEP)
    score = np.sort(d, axis=1)[:, :k].mean(1)
    best: dict[str, tuple[float, str]] = {}
    for key, name, s in zip(keys, names, score):
        if key not in best or s < best[key][0]:
            best[key] = (float(s), name)
    return sorted(((key, s, name) for key, (s, name) in best.items()), key=lambda x: x[1])


def read_icon(icon: MatLike, expect: list[str] | None = None) -> dict:
    """一个图标 → {card, score, second, margin, known, asset, expect_scores}。
    card：最像的卡键；margin：和第二名的分差；known：认出了（见模块说明的三个条件）；
    expect：想核对的卡键 / 卡 ID 列表时，另给它们各自的分数（expect_scores），供判断「是不是方案里的卡」。"""
    r = rank_cards(icon)
    if not r:
        return {"card": None, "score": None, "asset": None, "second": None, "margin": None, "known": False, "expect_scores": {}}
    top = r[0]
    margin = r[1][1] - top[1] if len(r) > 1 else 0.0
    std = float(cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY).std()) if icon.ndim == 3 else float(icon.std())
    out = {"card": top[0], "score": round(top[1], 2), "asset": top[2], "second": r[1][0] if len(r) > 1 else None,
           "margin": round(margin, 2), "known": top[1] <= UNKNOWN_SCORE and margin >= MIN_MARGIN and std >= MIN_STD}  # fmt: skip
    if expect:
        scores = {k: s for k, s, _ in r}
        out["expect_scores"] = {e: round(scores[card_key(e)], 2) for e in expect if card_key(e) in scores}
    return out


def read_step3_icons(img: MatLike, expect: list[str] | None = None) -> list[dict]:
    """第 3 步截图（720x1280）→ 四格回忆的图标读数，顺序为 左上、右上、左下、右下。"""
    return [dict(read_icon(img[y1:y2, x1:x2], expect), slot=i) for i, (x1, y1, x2, y2) in enumerate(STEP3_ICONS)]
