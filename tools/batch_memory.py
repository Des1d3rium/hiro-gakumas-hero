"""本战结算后的回忆生成 / 再生成挑选（给 batch_hif settle 用）。

挑选规则来自交接提示词「回忆候选中，以下客制化版本优先于其他挑选条件；同时出现多张时任选其一。同名普通版不算命中」：
- エンターテイナー+：パラメータ+12
- インフルエンサー+：強気に変更
- 仕切り直し+：消費体力減少+2
- 巻き返し+：すべてのスキルカードのパラメータ値増加+1、レッスン中1回の制限削除（效果里没有「レッスン中1回」）

回忆结果页（「獲得可能スキルカード」）读卡名和效果文字判命中。再生成后若游戏让人在「再生成前 / 再生成後」之间选，
选命中的一边；两边都没命中时不动游戏默认。挑选页的版式还没有实机样本：认不出按钮就停下交代理，并存帧。
"""

from __future__ import annotations

import re
import unicodedata

# (规则名, 卡名基名, 效果里必须有的词, 效果里不能有的词)
PRIORITY = (
    ("エンターテイナー+：パラメータ+12", "エンターテイナー", ("パラメータ+12",), ()),
    ("インフルエンサー+：強気に変更", "インフルエンサー", ("強気に変更",), ()),
    ("仕切り直し+：消費体力減少+2", "仕切り直し", ("消費体力減少+2",), ()),
    ("巻き返し+：パラメータ値増加+1・制限削除", "巻き返し", ("パラメータ値増加+1",), ("レッスン中1回",)),
)
CHOICE_WORDS = ("再生成前", "再生成後", "どちらのメモリー", "どちらを残")
KEEP_WORDS = ("こちらを残す", "残す", "こちらにする", "選択する", "決定")


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).replace("＋", "+")


def evaluate(texts: list[str]) -> dict:
    """一份回忆的文字（卡名 + 效果，顺序任意）→ {hit: 规则名 | None, card: 认到的卡名 | None}。
    卡名要带「+」（强化版）；同名普通版（效果里没有定制后的词）不算命中。"""
    flat = "".join(norm(t) for t in texts)
    for rule, base, need, forbid in PRIORITY:
        if re.search(re.escape(base) + r"\+", flat) and all(n in flat for n in need) and not any(f in flat for f in forbid):
            return {"hit": rule, "card": base + "+"}
    card = next((base + "+" for _, base, _, _ in PRIORITY if base + "+" in flat), None)
    return {"hit": None, "card": card}


def is_choice(texts: list[str]) -> bool:
    flat = "".join(norm(t) for t in texts)
    return sum(w in flat for w in CHOICE_WORDS) >= 1 and ("再生成前" in flat or "どちら" in flat)


def choose(boxes) -> dict:
    """「再生成前 / 再生成後」挑选页 → {sides: {before, after}: evaluate, pick: before|after|None, tap: (x, y) | None, why}。
    按「再生成前 / 再生成後」标签所在的左右（或上下）半边分文字；命中的一边点它那半边的保留按钮。"""
    items = [(norm(t), b) for t, _, b in boxes]
    label = {k: next((b for t, b in items if t.startswith(k)), None) for k in ("再生成前", "再生成後")}
    if not all(label.values()):
        return {"sides": None, "pick": None, "tap": None, "why": "挑选页没认出「再生成前 / 再生成後」两个标签"}
    (bx1, by1, bx2, by2), (ax1, ay1, ax2, ay2) = label["再生成前"], label["再生成後"]
    horizontal = abs((bx1 + bx2) - (ax1 + ax2)) > abs((by1 + by2) - (ay1 + ay2))
    mid = ((bx1 + bx2 + ax1 + ax2) / 4) if horizontal else ((by1 + by2 + ay1 + ay2) / 4)
    before_first = (bx1 + bx2 < ax1 + ax2) if horizontal else (by1 + by2 < ay1 + ay2)

    def side(b) -> str:
        c = (b[0] + b[2]) / 2 if horizontal else (b[1] + b[3]) / 2
        return "before" if (c < mid) == before_first else "after"

    texts = {"before": [], "after": []}
    for t, b in items:
        texts[side(b)].append(t)
    sides = {k: evaluate(v) for k, v in texts.items()}
    hits = [k for k in ("before", "after") if sides[k]["hit"]]
    if len(hits) != 1:
        return {"sides": sides, "pick": None, "tap": None,
                "why": "两边都命中，任选其一按游戏默认" if len(hits) == 2 else "两边都没命中，按游戏默认"}  # fmt: skip
    pick = hits[0]
    buttons = [(t, b) for t, b in items if any(t.startswith(w) or t == w for w in KEEP_WORDS) and side(b) == pick]
    if not buttons:
        return {"sides": sides, "pick": pick, "tap": None, "why": f"{pick} 命中，但它那半边没认出保留按钮"}
    b = buttons[0][1]
    return {"sides": sides, "pick": pick, "tap": ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2), "why": f"{pick} 命中 {sides[pick]['hit']}"}
