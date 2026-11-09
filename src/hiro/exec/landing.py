"""出牌落地核对：按卡图认牌比对整手牌（HIRO_LANDING_ART=1，默认关）。

原来的核对看「画面变没变」（SELECT 标签、卡名条 OCR、剩余回合 + 角标位置签名、手牌带像素差），每种信号都有反例，
1-g 到 1-p 每局补一个例外（上一张结算改了元気、抽牌补到同一格、卡名 OCR 并段、签名读不到……）。这里只问一件事：
**点之前和结算稳定后，手牌里各是哪几张牌**（卡图相关识别，定位 + 认 5 张约 18ms，p90 36ms，codex-20261109-1-p 30 帧实测）。

判定（classify）：
- played 另一种：点之前只剩 1 张，结算后手牌稳定为空；
- moving：读不到手牌、手牌布局不是 n 张的稳定布局（发牌 / 出牌动画中）——再等；
- unclear：有格认不出、目标牌本身认不出，或手牌变了但目标牌没少（可能是上一张的效果、也可能打出后又抽回同一张）——
  交回原来的核对兜底；
- none：手牌张数、各格的牌、顺序都和点之前一样——没打出（目标格被抬起 = 停在选中态，lifted=True）；
- played：目标牌在整手牌里少了一张（按张数比，同名几张也对；抽牌补到同一格也对）。
SELECT、卡名条、签名、像素差只在 unclear 时兜底（见 hiro.exec.exam._play_not_landed）。
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

LIFT_PX = 12  # 目标格首字母框比点之前高出这么多 = 被选中抬起
STABLE_GAP_S = 0.15  # 连读两帧判稳定的间隔
SETTLE_TIMEOUT_S = 3.0
LAST: dict = {}  # 最近一次核对（trace 备注用）


def enabled() -> bool:
    return os.environ.get("HIRO_LANDING_ART", "0") not in ("", "0")


@dataclass
class HandIds:
    ids: list[str | None]  # 每格的卡图身份（素材名去掉偶像配色后缀），认不出 None
    xs: list[int]
    ys: list[int]
    layout: str
    scores: list[float] = field(default_factory=list)

    def same(self, other: "HandIds") -> bool:
        return self.ids == other.ids and len(self.xs) == len(other.xs) and all(abs(a - b) <= 4 for a, b in zip(self.xs, other.xs))


def _base(key: str) -> str:
    return "-".join(key.removeprefix("img_general_skillcard_").split("-")[:2])


def read_ids(img) -> HandIds | None:
    """手牌每格的卡图身份。只做定位 + 卡面相关（不读卡名、徽章、强化），读不到手牌返回 None。"""
    if img is None:
        return None
    from kaa.tasks.produce.new.play_cards import ui as kaa_ui
    from kaa.tasks.produce.new.play_cards.ui import _locate_letters

    from hiro.vision import hif

    try:
        letters = sorted(_locate_letters(img), key=lambda r: r.x1)
    except Exception:  # noqa: BLE001
        return None
    if not letters:
        return HandIds([], [], [], "empty")  # 手牌空（打出最后一张）；动画中也可能一时定位不到，靠 settle 连读两帧确认
    ids, scores, xs, ys = [], [], [], []
    for r in letters:
        x2, y2 = r.center.x, r.top_left.y
        x1, y1 = x2 - kaa_ui.CARD_OFFSET[0], y2 - kaa_ui.CARD_OFFSET[1]
        top = hif._match_shifted(img, int(x1), int(y1), int(x2), int(y2))
        key, score = top[0]
        consensus = sum(1 for k, _ in top if _base(k) == _base(key))
        ids.append(_base(key) if key and (score >= hif.ART_MIN_SCORE or consensus >= 3) else None)
        scores.append(round(score, 3))
        xs.append(int(r.x1 + r.w // 2))
        ys.append(int(r.y1))
    return HandIds(ids, xs, ys, hif.hand_layout(xs), scores)


def classify(h0: HandIds | None, h1: HandIds | None, slot: int) -> tuple[str, str]:
    """(状态, 说明)：状态 ∈ moving / unclear / none / played，见模块说明。h0 = 点之前（决策帧），h1 = 结算后。"""
    if h0 is None or not 0 <= slot < len(h0.ids):
        return "unclear", "点之前的手牌没读到"
    if h1 is None:
        return "moving", "读不到手牌"
    if h1.layout == "empty":
        if len(h0.ids) == 1:
            return "played", "最后一张打出，手牌空了"
        return "moving", "手牌一时定位不到"
    target = h0.ids[slot]
    if target is None:
        return "unclear", f"目标格（第 {slot} 格）卡图认不出"
    if h1.layout != "ok":
        if len(h1.ids) == len(h0.ids) and h1.ids == h0.ids and slot < len(h1.ys) and h0.ys[slot] - h1.ys[slot] >= LIFT_PX:
            return "none", "目标格被抬起（停在选中态），手牌没变"
        return "moving", f"手牌布局 {h1.layout}"
    if None in h1.ids:
        return "unclear", "结算后有格卡图认不出"
    c0, c1 = Counter(i for i in h0.ids if i is not None), Counter(h1.ids)
    if None not in h0.ids and h0.ids == h1.ids:
        lifted = slot < len(h1.ys) and h0.ys[slot] - h1.ys[slot] >= LIFT_PX
        return "none", "手牌和点之前一样" + ("（目标格被抬起）" if lifted else "")
    if c1[target] < c0[target]:
        return "played", f"目标牌少了一张（{c0[target]}→{c1[target]}）"
    return "unclear", "手牌变了但目标牌没少（上一张的效果，或打出后又抽回同一张）"


def settle(shoot: Callable, h0: HandIds | None, slot: int, *, timeout: float = SETTLE_TIMEOUT_S,
           gap: float = STABLE_GAP_S, sleep: Callable = time.sleep, clock: Callable = time.monotonic,
           read: Callable = read_ids) -> tuple[str, str, object]:  # fmt: skip
    """结算稳定后判定：连读两帧手牌（卡图身份 + 位置）一样才算稳定；超时还在动就返回 moving。返回 (状态, 说明, 最后一帧)。"""
    deadline = clock() + timeout
    prev, img = None, None
    while True:
        img = shoot()
        cur = read(img)
        if prev is not None and cur is not None and cur.same(prev):
            state, why = classify(h0, cur, slot)
            if state != "moving":
                LAST.update(state=state, why=why, before=h0.ids if h0 else None, after=cur.ids)
                return state, why, img
        prev = cur
        if clock() >= deadline:
            state, why = classify(h0, cur, slot)
            state = "moving" if state == "moving" else state
            LAST.update(state=state, why=why + "（超时）", before=h0.ids if h0 else None, after=cur.ids if cur else None)
            return state, why + "（超时）", img
        sleep(gap)
