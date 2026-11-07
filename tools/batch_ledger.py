"""本局账本：整副牌组 + 饮料栏 1–4，随育成路上的变卡、拿卡、买 / 丢 / 喝饮料逐条记账，本战直接读账本而不靠一直识别。

- 回忆栏（reports/memories/<回忆>.json）：本战开局时这份回忆带来的整副牌组（开局牌组一览核对过），整个批次、以后的批次都复用。
- 账本（批次文件 active.ledger）：每局开局从回忆栏载入，之后每次变动记一条 log（带证据），exam 按账本给出牌组和饮料。
饮料栏与游戏一致：丢掉 / 喝掉一瓶后，剩下的往左挤，空格在最右边。
"""

from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / "reports" / "memories"
SLOTS = 4


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def card_name(name: str, upgrade: int | None) -> str:
    """识别给的卡名（不带 +）+ 强化次数 → 账本卡名。"""
    base = norm(name).rstrip("+")
    return base + "+" * (upgrade or 0)


# ------------------------------------------------------------------ 回忆栏
def memory_path(key: str) -> Path:
    if not key or any(c in key for c in "/\\") or key.startswith("."):
        raise ValueError("回忆名不能为空，也不能带路径分隔符")
    return MEMORY_DIR / f"{key}.json"


def save_memory(key: str, deck: dict, idol: str, *, overwrite: bool = False) -> Path:
    path = memory_path(key)
    if path.exists() and not overwrite:
        old = json.loads(path.read_text(encoding="utf-8"))
        if sorted(map(norm, old["cards"])) != sorted(map(norm, deck["cards"])):
            raise ValueError(f"回忆栏「{key}」已存有不同的牌组；确认后加 --overwrite")
    record = {"memory": key, "idol": idol, "cards": [norm(c) for c in deck["cards"]], "once": [norm(c) for c in deck.get("once", [])],
              "rebound_repeatable": deck.get("rebound_repeatable"), "evidence": deck["evidence"],
              "saved": time.strftime("%Y-%m-%d %H:%M:%S")}  # fmt: skip
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_memory(key: str) -> dict:
    path = memory_path(key)
    if not path.exists():
        raise ValueError(f"回忆栏没有「{key}」，先在本战开局时用 memory-save 记下整副牌组")
    return json.loads(path.read_text(encoding="utf-8"))


def static_abilities(abilities: list[dict] | None) -> list[dict]:
    """回忆能力只留静态信息（触发卡、效果类型、次数），去掉考试里的剩余次数 / 识别分数。"""
    return [{k: a.get(k) for k in ("card_id", "card", "kind", "count")} for a in abilities or [] if a.get("card_id")]


def save_abilities(key: str, abilities: list[dict], *, overwrite: bool = False) -> tuple[Path, bool]:
    """把回忆能力写进回忆栏。已有不同的记录时不覆盖（返回 False），除非 overwrite。"""
    path = memory_path(key)
    record = load_memory(key)
    new = static_abilities(abilities)
    old = record.get("abilities")
    if old and sorted(a["card_id"] for a in old) != sorted(a["card_id"] for a in new) and not overwrite:
        return path, False
    record["abilities"] = new
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path, True


# ------------------------------------------------------------------ 账本
def new_ledger(memory: dict, drinks: list[str | None] | None = None) -> dict:
    slots = list(drinks or [])[:SLOTS]
    slots += [None] * (SLOTS - len(slots))
    if None in slots and any(slots[slots.index(None):]):
        raise ValueError("饮料栏要从左往右连续填，空格只能在右边")
    return {"memory": memory["memory"], "cards": list(memory["cards"]), "once": list(memory.get("once", [])),
            "rebound_repeatable": memory.get("rebound_repeatable"), "drinks": slots, "pending_add": None,
            "abilities": static_abilities(memory.get("abilities")),
            "log": [{"t": _now(), "op": "load", "memory": memory["memory"], "evidence": memory["evidence"]}]}  # fmt: skip


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _log(ledger: dict, op: str, **kw) -> None:
    ledger["log"].append({"t": _now(), "op": op, **{k: v for k, v in kw.items() if v is not None}})


def add_card(ledger: dict, name: str, once: bool | None, *, why: str = "") -> None:
    name = norm(name)
    if once is None:
        raise ValueError(f"拿到「{name}」：须记录是否レッスン中1回")
    if name in ledger["cards"] and (name in ledger["once"]) != once:
        raise ValueError(f"牌组里已有「{name}」，一次性状态不一致，核对后再记")
    ledger["cards"].append(name)
    if once and name not in ledger["once"]:
        ledger["once"].append(name)
    _log(ledger, "add", card=name, once=once, why=why or None)


def remove_card(ledger: dict, name: str, *, why: str = "") -> None:
    name = norm(name)
    if name not in ledger["cards"]:
        raise ValueError(f"账本牌组里没有「{name}」")
    ledger["cards"].remove(name)
    if name not in ledger["cards"] and name in ledger["once"]:
        ledger["once"].remove(name)
    _log(ledger, "remove", card=name, why=why or None)


def change_card(ledger: dict, remove: str, add: str, add_once: bool | None, *, why: str = "") -> None:
    remove_card(ledger, remove, why=f"换卡换出 {why}".strip())
    add_card(ledger, add, add_once, why=f"换卡换入 {why}".strip())


def upgrade_card(ledger: dict, name: str, *, why: str = "") -> None:
    """强化：「X」→「X+」（一次性状态跟着这张卡走）。"""
    name = norm(name)
    once = name in ledger["once"]
    remove_card(ledger, name, why=f"强化前 {why}".strip())
    add_card(ledger, name + "+", once, why=f"强化后 {why}".strip())


def drink_add(ledger: dict, name: str, *, why: str = "") -> int:
    """收下一瓶：放进最左边的空格，返回格号；满了报错（先丢一瓶）。"""
    slots = ledger["drinks"]
    if None not in slots:
        raise ValueError(f"账本饮料栏已满（{' / '.join(slots)}），先 drink-drop 再收「{norm(name)}」")
    slot = slots.index(None)
    slots[slot] = norm(name)
    _log(ledger, "drink+", slot=slot, drink=slots[slot], why=why or None)
    return slot


def drink_remove(ledger: dict, slot: int, *, why: str = "", op: str = "drink-") -> str:
    """丢 / 喝掉第 slot 格（0 起）：剩下的往左挤。返回去掉的饮料名。"""
    slots = ledger["drinks"]
    if not 0 <= slot < SLOTS or slots[slot] is None:
        raise ValueError(f"账本饮料栏第 {slot + 1} 格是空的")
    name = slots.pop(slot)
    slots.append(None)
    _log(ledger, op, slot=slot, drink=name, why=why or None)
    return name


def drink_set(ledger: dict, slots: list[str | None], *, why: str) -> None:
    """按实机核对整栏覆盖（识别和账本对不上、确认实机后用）。"""
    new = [norm(s) if s else None for s in slots][:SLOTS]
    new += [None] * (SLOTS - len(new))
    ledger["drinks"] = new
    _log(ledger, "drink=", drinks=new, why=why)


def after_exam(ledger: dict, exam_drinks: list[dict | None] | None, *, why: str) -> None:
    """考试里按格号记的饮料（state drink / consumed / empty）→ 考完的饮料栏：喝掉的去掉，剩下的往左挤。"""
    if exam_drinks is None:
        return
    left = [d["name"] for d in exam_drinks if d and d.get("state") == "drink" and d.get("name")]
    drink_set(ledger, left, why=why)


def compare_drinks(ledger: dict, observed: list[dict]) -> list[str]:
    """实机饮料栏（read_drink_slots）与账本逐格比对，返回不一致的说明；读不清（unknown）的格不比。"""
    out = []
    for slot in observed:
        i, state = slot["slot"], slot["state"]
        want = ledger["drinks"][i] if 0 <= i < SLOTS else None
        if state == "unknown":
            continue
        got = norm(slot["name"]) if state == "drink" and slot.get("name") else None
        if state == "drink" and got is None:
            continue
        if got != want:
            out.append(f"第 {i + 1} 格：账本 {want or '空'}，画面 {got or '空'}")
    return out


def to_deck(ledger: dict) -> dict:
    """给 veil_exam 的牌组清单（同 deck-base 格式）。"""
    return {"cards": list(ledger["cards"]), "once": sorted(set(ledger["once"])), "rebound_repeatable": ledger["rebound_repeatable"],
            "evidence": f"账本（回忆「{ledger['memory']}」+ {len(ledger['log']) - 1} 条变动）"}  # fmt: skip


def summary(ledger: dict) -> dict:
    from collections import Counter

    counts = Counter(ledger["cards"])
    cards = [f"{n}×{k}" if k > 1 else n for n, k in sorted(counts.items())]
    return {"memory": ledger["memory"], "size": len(ledger["cards"]), "cards": cards, "once": sorted(set(ledger["once"])),
            "rebound_repeatable": ledger["rebound_repeatable"],
            "drinks": [f"{i + 1}:{d or '空'}" for i, d in enumerate(ledger["drinks"])],
            "abilities": [f"{a['card']}→{ {'draw': '再动+抽1', 'play': '再动', 'sleep': '删眠気'}.get(a['kind'], a['kind'])}×{a['count']}"
                          for a in ledger.get("abilities") or []],
            "pending_add": ledger.get("pending_add"), "changes": len(ledger["log"]) - 1}  # fmt: skip
