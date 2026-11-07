"""H.I.F 回忆能力（メモリーアビリティ）：一份回忆从四张回忆卡各继承一项，考试里「打出指定牌后」触发，每场考试（R1、R2 各算一场）
最多 N 次（主数据「以降N回まで」），重复发动不可。

主数据 ProduceExamStatusEnchant 里 id 为 enchant-p_ef-hif_memory-<触发卡>-enc01（效果）/ -enc02（「以降N回まで」包装）。
每张触发卡只有一项（115 张卡、230 行），所以认出图标上的卡就知道效果。效果三类：
- draw：スキルカード使用数追加+1・スキルカードを引く（再动一次并抽一张）
- play：スキルカード使用数追加+1（只再动一次）
- sleep：ランダムな山札か捨札の眠気を除外に移動（删一张眠気）
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

PREFIX = "enchant-p_ef-hif_memory-"


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def base_name(name: str) -> str:
    return norm(name).rstrip("+")


def classify(text: str) -> str | None:
    t = norm(text)
    if "眠気" in t and "除外" in t:
        return "sleep"
    if "使用数追加" in t and "引く" in t:
        return "draw"
    if "使用数追加" in t:
        return "play"
    return None


def _text(descriptions) -> str:
    items = descriptions if isinstance(descriptions, list) else []
    return re.sub(r"<[^>]+>", "", "".join(d.get("text", "") for d in items if isinstance(d, dict)))


@lru_cache(maxsize=1)
def table() -> dict[str, dict]:
    """触发卡 id → {card_id, card, kind, count, text}。没有主数据时返回空表。"""
    try:
        from hiro.state.context import catalog

        rows = catalog().db.execute(
            "SELECT id, produceDescriptions FROM ProduceExamStatusEnchant WHERE id LIKE ?", (PREFIX + "%",)
        ).fetchall()
    except Exception:
        return {}
    import json

    out: dict[str, dict] = {}
    for rid, desc in rows:
        m = re.fullmatch(re.escape(PREFIX) + r"(.+)-enc(\d+)", rid)
        if not m:
            continue
        card_id, part = m.group(1), int(m.group(2))
        try:
            text = _text(json.loads(desc) if isinstance(desc, str) else desc)
        except ValueError:
            continue
        entry = out.setdefault(card_id, {"card_id": card_id, "card": None, "kind": None, "count": 1, "text": None})
        if part == 1:
            entry["text"], entry["kind"] = text, classify(text)
            entry["card"] = text.split("使用後")[0] or None
        n = re.search(r"以降(\d+)回まで", text)
        if n:
            entry["count"] = int(n.group(1))
    return out


def ability(card_id: str | None) -> dict | None:
    return table().get(card_id) if card_id else None


def fresh(abilities: list[dict] | None) -> list[dict]:
    """一场考试开始时的能力状态：每项 left = count。"""
    return [{**a, "left": a.get("count", 1)} for a in abilities or [] if a.get("kind")]


def trigger(abilities: list[dict], played: str) -> dict | None:
    """打出 played 后要触发的能力（同一张牌只触发第一项还有次数的，「重複発動不可」），并把它的 left 减一。"""
    want = base_name(played)
    hit = next((a for a in abilities if a.get("left", 0) > 0 and base_name(a.get("card") or "") == want), None)
    if hit is not None:
        hit["left"] -= 1
    return hit


def pending(abilities: list[dict] | None, name: str) -> dict | None:
    """name 打出后会触发的能力（不消耗次数，给决策估值用）。"""
    want = base_name(name)
    return next((a for a in abilities or [] if a.get("left", 0) > 0 and base_name(a.get("card") or "") == want), None)
