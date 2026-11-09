"""本战结算后的回忆生成 / 再生成挑选（给 batch_hif settle 用）。

挑选规则在回忆档案的 memory_regen（hiro.strategies.profiles，docs/strategies.md）：按顺序，第一条命中的版本；
同名普通版不算命中。代码缺省（hiro/strategies/data/memory-defaults.json）来自交接提示词：
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


def priority() -> tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]:
    """回忆档案 memory_regen → ((规则名, 卡名基名, 效果里必须有的词, 效果里不能有的词), …)。"""
    from hiro.strategies import profiles

    out = []
    for r in profiles.get("memory_regen", []):
        base = norm(r["card"]).rstrip("+")
        out.append((r.get("name") or base + "+", base, tuple(r.get("need") or ()), tuple(r.get("forbid") or ())))
    return tuple(out)

CHOICE_WORDS = ("再生成前", "再生成後", "どちらのメモリー", "どちらを残")
KEEP_WORDS = ("こちらを残す", "残す", "こちらにする", "選択する", "決定")


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).replace("＋", "+")


def evaluate(texts: list[str]) -> dict:
    """一份回忆的文字（卡名 + 效果，顺序任意）→ {hit: 规则名 | None, card: 认到的卡名 | None}。
    卡名要带「+」（强化版）；同名普通版（效果里没有定制后的词）不算命中。"""
    flat = "".join(norm(t) for t in texts)
    rules = priority()
    for rule, base, need, forbid in rules:
        if re.search(re.escape(base) + r"\+", flat) and all(n in flat for n in need) and not any(f in flat for f in forbid):
            return {"hit": rule, "card": base + "+"}
    card = next((base + "+" for _, base, _, _ in rules if base + "+" in flat), None)
    return {"hit": None, "card": card}


def card_name(texts: list[str]) -> str | None:
    """挑选页卡面区（batch_regen.CARD_CROP）的 OCR 文字 → 「獲得可能スキルカード」那张卡的卡名（不管在不在优先表里）。
    版式（codex-6 实机帧量过）：「獲得可能スキルカード」→ 获得时机徽章（「開始時に獲得」「中間試験/1次オーディション後に獲得」，
    都带「獲得」）→ 卡名 → 「アノマリーのみ」之类。取标题后第一个不带「獲得」的文字；认不出标题返回 None。
    只用来记录 / 核对（evaluate 的 card 只报优先表里的强化版卡名，没命中时是 None，看不出读到了什么）。"""
    items = [norm(t) for t in texts]
    start = next((i for i, t in enumerate(items) if "獲得可能" in t), None)
    if start is None:
        return None
    # 跳过纯数字 / 单字符（codex-20261109-1-p：「開始時に獲得」徽章后读出数量角标「2」「5」，记成卡名）
    return next((t for t in items[start + 1:start + 5] if len(t) > 1 and "獲得" not in t and not t.isdigit()), None)


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
