"""本局账本：整副牌组 + 饮料栏 1–4，随育成路上的变卡、拿卡、买 / 丢 / 喝饮料逐条记账，本战直接读账本而不靠一直识别。

- 回忆栏（reports/memories/<回忆>.json）：本战开局时这份回忆带来的整副牌组（开局牌组一览核对过），整个批次、以后的批次都复用。
- 账本（批次文件 active.ledger）：每局开局从回忆栏载入，之后每次变动记一条 log（带证据），exam 按账本给出牌组和饮料。
饮料栏与游戏一致：丢掉 / 喝掉一瓶后，剩下的往左挤，空格在最右边。
"""

from __future__ import annotations

import json
import time
import unicodedata
from collections import Counter, defaultdict
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
# 牌组按张记（使用者 2026-11-07：同名两张「巻き返し+」一张一次性、一张可重复，按卡名记账记不下）：
# ledger["deck"] 是逐张的卡对象 {id, name, once, customs, origin}，是牌组的唯一来源；
# ledger["cards"] / ["once"] / ["rebound_repeatable"] 是从 deck 推出来的旧视图，给只认卡名的读者（batch_prep、旧 exam 路径）。
# customs：这一张做过的客制化菜单名（按时间顺序）；None = 不知道（回忆栏带来的卡没有记客制化）。
# origin：{kind: memory|change|lesson|event|sync|legacy, from?, from_id?}，换卡时记从哪张换来。
REBOUND = "巻き返し+"
LIMIT_REMOVED = "制限削除"  # 客制化「制限削除」去掉レッスン中1回


class AmbiguousCard(ValueError):
    """同名的几张卡属性不同（一次性 / 客制化），按卡名分不出要动哪一张：要 --id 或 --where 指定。"""

    def __init__(self, name: str, candidates: list[dict]):
        self.candidates = candidates
        listed = "；".join(describe(c) for c in candidates)
        super().__init__(f"账本里「{name}」有几张属性不同（{listed}），用 --id 或 --where 指定是哪一张")


def new_ledger(memory: dict, drinks: list[str | None] | None = None) -> dict:
    slots = list(drinks or [])[:SLOTS]
    slots += [None] * (SLOTS - len(slots))
    if None in slots and any(slots[slots.index(None):]):
        raise ValueError("饮料栏要从左往右连续填，空格只能在右边")
    ledger = {"memory": memory["memory"], "cards": list(memory["cards"]), "once": list(memory.get("once", [])),
              "rebound_repeatable": memory.get("rebound_repeatable"), "drinks": slots, "pending_add": None,
              "abilities": static_abilities(memory.get("abilities")),
              "log": [{"t": _now(), "op": "load", "memory": memory["memory"], "evidence": memory["evidence"]}]}  # fmt: skip
    ensure_deck(ledger, origin="memory")
    return ledger


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _log(ledger: dict, op: str, **kw) -> None:
    ledger["log"].append({"t": _now(), "op": op, **{k: v for k, v in kw.items() if v is not None}})


def upgrades(name: str) -> int:
    return len(name) - len(name.rstrip("+"))


def signature(card: dict) -> tuple:
    """同名卡是不是「同一张」：一次性和客制化都一样就可以互换（使用者 2026-11-07：客制化一样就是同一张卡）。
    客制化不知道（None）和没做过（[]）不区分：都没有可补的牌面文字。"""
    return card["name"], card.get("once"), tuple(card.get("customs") or ())


def describe(card: dict) -> str:
    """给代理看的一张卡：「c03 巻き返し+（常驻，客制化 制限削除，来自 换卡←立ち回りの基本+）」。"""
    once = {True: "一次性", False: "常驻", None: "一次性未知"}[card.get("once")]
    customs = card.get("customs")
    custom = "客制化未知" if customs is None else ("客制化 " + "/".join(customs) if customs else "未客制化")
    origin = card.get("origin") or {}
    src = {"memory": "回忆", "change": "换卡", "lesson": "课程", "event": "事件", "sync": "对账", "legacy": "旧账本"}.get(
        origin.get("kind"), origin.get("kind") or "?")
    if origin.get("from"):
        src += f"←{origin['from']}"
    return f"{card['id']} {card['name']}（{once}，{custom}，来自 {src}）"


def _new_id(ledger: dict) -> str:
    n = ledger.get("next_id") or 1 + max((int(c["id"][1:]) for c in ledger.get("deck") or [] if str(c.get("id", ""))[1:].isdigit()),
                                         default=0)
    ledger["next_id"] = n + 1
    return f"c{n:02d}"


def _make(ledger: dict, name: str, once: bool | None, customs: list[str] | None, origin: dict | None) -> dict:
    return {"id": _new_id(ledger), "name": norm(name), "once": once, "customs": None if customs is None else list(customs),
            "origin": origin or {"kind": "legacy"}}


def _legacy_once(ledger: dict, name: str) -> bool:
    if name == REBOUND and isinstance(ledger.get("rebound_repeatable"), bool):
        return not ledger["rebound_repeatable"]
    return name in {norm(c) for c in ledger.get("once") or []}


def ensure_deck(ledger: dict, *, origin: str = "legacy") -> list[dict]:
    """取逐张牌组。旧账本（只有卡名）或旧视图被直接改过（和 deck 对不上）时，按卡名补齐 / 去掉：
    名字还在的卡保留原来的对象（一次性、客制化、来处不丢），新出现的名字按旧的 once / rebound_repeatable 建对象。
    第一次建时把 log 里的 customize 记录分给唯一同名的那张；同名多张分不出，客制化记「不知道」。"""
    cards = [norm(c) for c in ledger.get("cards") or []]
    deck = ledger.get("deck")
    if deck is not None and Counter(c["name"] for c in deck) == Counter(cards):
        return deck
    fresh = deck is None
    pool: dict[str, list[dict]] = defaultdict(list)
    for c in deck or []:
        pool[c["name"]].append(c)
    rebuilt = []
    for name in cards:
        rebuilt.append(pool[name].pop(0) if pool[name] else
                       _make(ledger, name, _legacy_once(ledger, name), None, {"kind": origin}))
    ledger["deck"] = rebuilt
    if fresh:
        for e in ledger.get("log") or []:
            if e.get("op") != "customize" or not e.get("card") or not e.get("item"):
                continue
            same = [c for c in rebuilt if c["name"] == norm(e["card"])]
            if len(same) == 1:
                same[0]["customs"] = (same[0]["customs"] or []) + [norm(e["item"])]
            else:
                for c in same:
                    c["customs"] = None
    _refresh(ledger)
    return rebuilt


def _refresh(ledger: dict) -> None:
    """deck → 旧视图。once：同名各张都是一次性才列（同名一次性 / 常驻混着时不列，旧路径分不出，见 summary 的 conflicts）。
    rebound_repeatable：有一张常驻的「巻き返し+」（循环核心）就算 True。"""
    deck = ledger["deck"]
    ledger["cards"] = [c["name"] for c in deck]
    by_name: dict[str, list[dict]] = defaultdict(list)
    for c in deck:
        by_name[c["name"]].append(c)
    ledger["once"] = sorted(n for n, cs in by_name.items() if all(c.get("once") is True for c in cs))
    rebound = by_name.get(REBOUND)
    if rebound:
        ledger["rebound_repeatable"] = any(c.get("once") is False for c in rebound)


def parse_where(text: str | None) -> dict | None:
    """「once=0,customized=1,origin=change」→ {"once": False, "customized": True, "origin": "change"}。"""
    if not text:
        return None
    out = {}
    for part in text.split(","):
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in ("once", "customized", "origin"):
            raise ValueError(f"--where 只认 once / customized / origin，写成 key=value：{part}")
        out[key] = value if key == "origin" else value.lower() in ("1", "true", "yes")
    return out


def _matches(card: dict, where: dict | None) -> bool:
    """where 的条件。不知道的属性（once / customs 为 None）当作都可能，不排除。"""
    for key, want in (where or {}).items():
        if key == "once" and card.get("once") is not None and card["once"] != want:
            return False
        if key == "customized" and card.get("customs") is not None and bool(card["customs"]) != want:
            return False
        if key == "origin" and (card.get("origin") or {}).get("kind") != want:
            return False
    return True


def find_instance(ledger: dict, name: str | None = None, *, id: str | None = None, where: dict | None = None,
                  loose: bool = False) -> dict | None:
    """找一张卡：按 id，或按卡名（+ where 条件）。同名候选属性都一样就随便哪张（同一张卡）；不一样抛 AmbiguousCard。
    loose：完整名字找不到时按去掉「+」的名字找，只有一种强化等级命中才算（有 X 和 X+ 两种就不猜）。找不到返回 None。"""
    deck = ensure_deck(ledger)
    if id is not None:
        hit = next((c for c in deck if c["id"] == id), None)
        if hit is not None and name is not None and hit["name"].rstrip("+") != norm(name).rstrip("+"):
            raise ValueError(f"{id} 是「{hit['name']}」，不是「{norm(name)}」")
        return hit
    read = norm(name or "")
    hits = [c for c in deck if c["name"] == read]
    if not hits and loose:
        hits = [c for c in deck if c["name"].rstrip("+") == read.rstrip("+")]
        if len({c["name"] for c in hits}) != 1:
            return None
    hits = [c for c in hits if _matches(c, where)]
    if not hits:
        return None
    if len({signature(c) for c in hits}) > 1:
        raise AmbiguousCard(hits[0]["name"], hits)
    return hits[-1]


def find_card(ledger: dict, name: str, *, loose: bool = False) -> str | None:
    """账本里这张卡的名字（只要名字，不管同名几张属性是否一样）。"""
    name = norm(name)
    names = {c["name"] for c in ensure_deck(ledger)}
    if name in names:
        return name
    if not loose:
        return None
    hits = {c for c in names if c.rstrip("+") == name.rstrip("+")}
    return hits.pop() if len(hits) == 1 else None


def _need(ledger: dict, name: str | None, *, id=None, where=None, loose=False) -> dict:
    card = find_instance(ledger, name, id=id, where=where, loose=loose)
    if card is None:
        cond = f"（条件 {where}）" if where else ""
        raise ValueError(f"账本牌组里没有「{id or norm(name or '')}」{cond}")
    return card


def add_card(ledger: dict, name: str, once: bool | None, *, customs: list[str] | None = (), origin: dict | None = None,
             why: str = "") -> dict:
    """拿到一张卡。同名已有一次性状态不同的卡也照记（逐张记，不再冲突）。返回新卡对象。"""
    name = norm(name)
    if once is None:
        raise ValueError(f"拿到「{name}」：须记录是否レッスン中1回")
    deck = ensure_deck(ledger)
    card = _make(ledger, name, once, list(customs) if customs is not None else None, origin or {"kind": "lesson"})
    deck.append(card)
    _refresh(ledger)
    _log(ledger, "add", card=name, id=card["id"], once=once, why=why or None)
    return card


def remove_card(ledger: dict, name: str | None, *, id: str | None = None, where: dict | None = None, why: str = "",
                loose: bool = False) -> dict:
    read = norm(name) if name else None
    card = _need(ledger, read, id=id, where=where, loose=loose)
    ledger["deck"].remove(card)
    _refresh(ledger)
    _log(ledger, "remove", card=card["name"], id=card["id"], read=read if read and read != card["name"] else None, why=why or None)
    return card


def _snapshot(ledger: dict) -> dict:
    return json.loads(json.dumps({k: ledger.get(k) for k in ("deck", "cards", "once", "rebound_repeatable", "log", "next_id")}))


def change_card(ledger: dict, remove: str | None, add: str, add_once: bool | None, *, id: str | None = None,
                where: dict | None = None, why: str = "", loose: bool = False, inherit: bool = True) -> dict:
    """换出 remove、换入 add。中途出错时账本原样不动（不会只删不加）。
    inherit：换入的卡继承换出那张的强化（游戏里换卡就是这样，见 record_change）。返回换入的卡。"""
    saved = _snapshot(ledger)
    try:
        old = remove_card(ledger, remove, id=id, where=where, why=f"换卡换出 {why}".strip(), loose=loose)
        new = norm(add)
        if inherit:
            new = new.rstrip("+") + "+" * max(upgrades(new), upgrades(old["name"]))
        return add_card(ledger, new, add_once, origin={"kind": "change", "from": old["name"], "from_id": old["id"]},
                        why=f"换卡换入 {why}".strip())
    except ValueError:
        ledger.update(saved)
        raise


def record_change(ledger: dict, out_card: dict | None, pending: dict | None, *, why: str = "授業换卡") -> str | None:
    """授業换卡阶段 2 在游戏里提交之后记账；记上了返回 None，记不上返回要补记的说明（不抛错：游戏里已经换了，
    抛错会把这次换卡整个丢掉，watch 照样往下点）。

    换入的卡继承换出那张的强化：实机两局都是换出「X+」后换入的卡也带「+」（codex-20261107-6 第 1 局 忍耐力 → 忍耐力+，
    codex-20261107-1 第 1 局 オープニングアクト → オープニングアクト+），阶段 1 候选读到的强化不算数。
    换出那张按去掉「+」的名字找（挑卡时就是这么比的）；同名几张属性不同时，用换卡页读到的客制化标记（customized）
    和详情栏的「レッスン中1回」（once）挑出是哪一张，还分不出就交代理用 --id 补记。"""
    if not (out_card or {}).get("name") or not (pending or {}).get("name"):
        return "换出或换入的卡没认出，用 ledger-edit --change 旧 新 --once/--not-once 补记"
    old = card_name(out_card["name"], out_card.get("upgrade"))
    where = {k: out_card[k] for k in ("customized", "once") if isinstance(out_card.get(k), bool)} or None
    # 换出那张的强化以账本为准（阶段 2 识别把「立ち回りの基本+」读成没强化时，换入的也会少一个「+」）
    booked = find_card(ledger, old, loose=True)
    upgrade = max(out_card.get("upgrade") or 0, upgrades(booked) if booked else 0, pending.get("upgrade") or 0)
    new = card_name(pending["name"], upgrade)
    once_flag = "--once" if pending.get("once") else "--not-once"
    try:
        change_card(ledger, old, new, pending.get("once"), where=where, why=why, loose=True)
    except ValueError as e:
        return (f"游戏里已换成（换出 {old}，换入 {new}），账本没记上：{e}。核对牌组一览后用 "
                f"ledger-edit --change <账本里换出的卡名> {new} {once_flag} [--id <换出那张的 id>] 补记")
    ledger["pending_add"] = None
    return None


def upgrade_card(ledger: dict, name: str | None, *, id: str | None = None, where: dict | None = None, why: str = "") -> dict:
    """强化：「X」→「X+」（同一张卡：id、一次性、客制化都跟着走）。"""
    card = _need(ledger, name, id=id, where=where)
    old = card["name"]
    card["name"] = old + "+"
    _refresh(ledger)
    _log(ledger, "upgrade", card=card["name"], id=card["id"], was=old, why=why or None)
    return card


def customize_card(ledger: dict, name: str | None, item: str, *, id: str | None = None, where: dict | None = None,
                   why: str = "") -> dict:
    """客制化记到那一张卡上；「制限削除」同时去掉一次性。log 照旧记一条 customize（to_deck 的 custom 用）。
    原来客制化不知道（None）的卡：记上这一项，并标 customs_partial（之前做过什么仍不知道）。"""
    card = _need(ledger, name, id=id, where=where, loose=True)
    item = norm(item)
    if card.get("customs") is None:
        card["customs_partial"] = True
    card["customs"] = (card.get("customs") or []) + [item]
    if LIMIT_REMOVED in item:
        card["once"] = False
    _refresh(ledger)
    _log(ledger, "customize", card=card["name"], id=card["id"], item=item, why=why or None)
    return card


def set_once(ledger: dict, name: str | None, once: bool, *, id: str | None = None, where: dict | None = None,
             why: str = "") -> list[dict]:
    """核对后改一次性状态。没给 id / where 时改同名的每一张（旧 --rebound-repeatable 的意思）。"""
    if id is None and where is None:
        cards = [c for c in ensure_deck(ledger) if c["name"] == norm(name or "")]
        if not cards:
            raise ValueError(f"账本牌组里没有「{norm(name or '')}」")
    else:
        cards = [_need(ledger, name, id=id, where=where)]
    for c in cards:
        c["once"] = once
    _refresh(ledger)
    _log(ledger, "once", card=cards[0]["name"], ids=[c["id"] for c in cards], once=once, why=why or None)
    return cards


def diff_ledger(before: dict, after: dict) -> dict:
    """两份账本（或 _snapshot）的差异：卡按 id 比（新增 / 去掉 / 改了名字、一次性、客制化），饮料整栏比。空 dict = 没变。"""
    old = {c["id"]: c for c in before.get("deck") or []}
    new = {c["id"]: c for c in after.get("deck") or []}
    out: dict = {}
    added = [describe(new[i]) for i in new if i not in old]
    removed = [describe(old[i]) for i in old if i not in new]
    changed = [f"{describe(old[i])} → {describe(new[i])}" for i in new if i in old and signature(old[i]) != signature(new[i])]
    cards = {k: v for k, v in (("added", added), ("removed", removed), ("changed", changed)) if v}
    if cards:
        out["cards"] = cards
    if before.get("drinks") != after.get("drinks") and "drinks" in before:
        out["drinks"] = {"before": before.get("drinks"), "after": after.get("drinks")}
    return out


def drink_add(ledger: dict, name: str, *, why: str = "") -> int:
    """收下一瓶：放进最左边的空格，返回格号；满了报错（先丢一瓶）。"""
    slots = ledger["drinks"]
    if None not in slots:
        raise ValueError(f"账本饮料栏已满（{' / '.join(slots)}），先 drink-drop 再收「{norm(name)}」")
    slot = slots.index(None)
    slots[slot] = norm(name)
    _log(ledger, "drink+", slot=slot, drink=slots[slot], why=why or None)
    return slot


def _secs(hms: str) -> int | None:
    try:
        h, m, sec = (int(x) for x in hms.split(":"))
    except (AttributeError, ValueError):
        return None
    return h * 3600 + m * 60 + sec


def acquire_drink(ledger: dict, name: str, *, why: str, window: int = 45, now: str | None = None) -> str:
    """获得演出看到的饮料（清单 10）→ 记账结果：added / dup（window 秒内已有同名「drink+」，是选择页 / 商店那次的演出）/
    full（账本栏满：账本和实机对不上，记一条 drink? 交代理核对）。"""
    name = norm(name)
    t = _secs(now or _now())
    for e in reversed(ledger["log"]):
        dt = None if t is None or _secs(e.get("t", "")) is None else (t - _secs(e["t"])) % 86400
        if dt is None or dt > window:
            break
        if e.get("op") in ("drink+", "drink=") and (e.get("drink") == name or name in (e.get("drinks") or [])):
            return "dup"
    if None not in ledger["drinks"]:
        _log(ledger, "drink?", drink=name, why=f"{why}；账本栏已满，核对实机（ledger --read-drinks）")
        return "full"
    drink_add(ledger, name, why=why)
    return "added"


def acquire_card(ledger: dict, name: str, once: bool | None, *, why: str, window: int = 45, now: str | None = None) -> str:
    """获得演出看到的技能卡（使用者 2026-11-07：随机事件给的卡 / 强化漏记）→ 记账结果：
    added：账本里没有同名（去「+」）的卡，记一张；dup：window 秒内已有这张的 add（差し入れ / 换卡那次的演出）；
    ambiguous：账本已有同名卡（可能是强化演出，也可能是又给了一张）→ 只记一条 card?，由考前 deck-sync 按牌组一览定。"""
    name = norm(name)
    base = name.rstrip("+")
    t = _secs(now or _now())
    for e in reversed(ledger["log"]):
        dt = None if t is None or _secs(e.get("t", "")) is None else (t - _secs(e["t"])) % 86400
        if dt is None or dt > window:
            break
        if e.get("op") in ("add", "card?") and str(e.get("card", "")).rstrip("+") == base:
            return "dup"
    if any(c["name"].rstrip("+") == base for c in ensure_deck(ledger)):
        _log(ledger, "card?", card=name, why=f"{why}；账本已有同名卡，强化还是新增待考前 deck-sync 核对")
        return "ambiguous"
    if once is None:
        _log(ledger, "card?", card=name, why=f"{why}；查不到是否一次性，待 deck-sync 核对")
        return "unknown_once"
    add_card(ledger, name, once, origin={"kind": "event"}, why=why)
    return "added"


def sync_deck(ledger: dict, screen: list[tuple[str, bool | None]], *, why: str) -> list[str]:
    """考前牌组对账（使用者 2026-11-07 第二层）：screen = 牌组一览读到的 [(带「+」的卡名, 一次性)]，整副读全才调用。
    账本缺的补上；同名只差「+」的按画面改强化（同一张卡，id / 客制化不变）；账本多出来的只记差异不删（可能是识别漏读）。
    返回改动 / 差异说明。"""
    deck = ensure_deck(ledger)
    have = Counter(c["name"] for c in deck)
    seen = Counter(norm(n) for n, _ in screen)
    once_of = {norm(n): o for n, o in screen}
    missing = seen - have  # 画面有、账本没有
    extra = have - seen  # 账本有、画面没有
    notes = []
    for name in sorted(missing.elements()):
        base = name.rstrip("+")
        old = next((c for c in sorted(extra.elements()) if c.rstrip("+") == base), None)
        if old is not None:  # 强化次数不同：按画面改
            extra[old] -= 1
            card = next(c for c in deck if c["name"] == old)
            card["name"] = name
            _refresh(ledger)
            _log(ledger, "upgrade", card=name, id=card["id"], was=old, why=f"考前对账：强化 {old}→{name}（{why}）")
            notes.append(f"强化 {old}→{name}")
        elif once_of.get(name) is None:
            notes.append(f"画面有 {name}、账本没有，但查不到是否一次性，未补")
        else:
            add_card(ledger, name, once_of[name], origin={"kind": "sync"}, why=f"考前对账：账本漏记（{why}）")
            notes.append(f"补记 {name}")
    for name in sorted((+extra).elements()):
        notes.append(f"账本多出 {name}（画面没有，未删）")
    _log(ledger, "deck_sync", diff=notes or None, why=why)
    return notes


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


def mark_full(ledger: dict, *, why: str) -> int:
    """游戏说饮料栏已满（领取页出现「受け取らない」/「Pドリンク所持上限」），账本却有空格：空格记成占位「未识别饮料」
    （验证局 1-e：公開レッスン 后 P 道具白送的 2 瓶没有获得演出可认，账本 2 瓶、实机 4 瓶）。之后商店 / 考试按画面认出名字再补。
    返回补了几格。"""
    empty = [i for i, d in enumerate(ledger["drinks"]) if not d]
    if not empty:
        return 0
    new = [d or UNKNOWN_DRINK for d in ledger["drinks"]]
    drink_set(ledger, new, why=f"栏满但账本有 {len(empty)} 个空格，记占位（{why}）")
    return len(empty)


def after_exam(ledger: dict, exam_drinks: list[dict | None] | None, *, why: str) -> None:
    """考试里按格号记的饮料（state drink / consumed / empty）→ 考完的饮料栏：喝掉的去掉，剩下的往左挤。"""
    if exam_drinks is None:
        return
    left = [d["name"] for d in exam_drinks if d and d.get("state") == "drink" and d.get("name")]
    drink_set(ledger, left, why=why)


UNKNOWN_DRINK = "未识别饮料"


def compare_drinks(ledger: dict, observed: list[dict]) -> list[str]:
    """实机饮料栏（read_drink_slots）与账本逐格比对，返回不一致的说明；读不清（unknown）的格不比。"""
    out = []
    for slot in observed:
        i, state = slot["slot"], slot["state"]
        want = ledger["drinks"][i] if 0 <= i < SLOTS else None
        if state == "unknown":
            if want is None:
                # 有饮料、认不出，账本却是空格：也是漏记（验证局 1-d：P 道具 / 事件给的两瓶ホエイ 没记，shop-auto 以为有空格去买失败）
                out.append(f"第 {i + 1} 格：账本 空，画面 有饮料（认不出）")
            continue
        got = norm(slot["name"]) if state == "drink" and slot.get("name") else None
        if state == "drink" and got is None:
            continue
        if got != want:
            out.append(f"第 {i + 1} 格：账本 {want or '空'}，画面 {got or '空'}")
    return out


def sync_drinks(ledger: dict, observed: list[dict] | None, *, why: str) -> list[str]:
    """饮料懒同步（使用者 2026-11-07：「直到饮料拿不下了，才进行更新和追踪」）：在看得到饮料栏的页面（商店 / 相談）
    要按栏位做决定前，按画面覆盖账本。读清的格（drink 带名字 / empty）以画面为准，读不清的格保留账本。
    返回改动说明（空 = 一致，不写日志）。获得演出漏记、多记都在这里被纠正。"""
    if not observed:
        return []
    diff = compare_drinks(ledger, observed)
    if not diff:
        return []
    from collections import Counter

    new = list(ledger["drinks"])
    unread = []
    for slot in observed:
        i, state = slot["slot"], slot["state"]
        if not 0 <= i < SLOTS:
            continue
        if state == "empty":
            new[i] = None
        elif state == "drink" and slot.get("name"):
            new[i] = norm(slot["name"])
        else:
            unread.append(i)
    # 读不清的格：账本里还没在画面读清格出现过的饮料按账本顺序补（栏位会挤动，不能按同一格号沿用）
    seen = Counter(n for j, n in enumerate(new) if n and j not in unread)
    left = []
    for n in ledger["drinks"]:
        if n and seen[n] > 0:
            seen[n] -= 1
        elif n:
            left.append(n)
    for i in unread:
        # 账本没有可补的：记占位名，保证这格算「有饮料」（不当空格去买；make_room 也不会丢认不出的）
        new[i] = left.pop(0) if left else UNKNOWN_DRINK
    drink_set(ledger, new, why=f"懒同步（{why}）：{'；'.join(diff)}")
    return diff


def to_deck(ledger: dict) -> dict:
    """给 veil_exam 的牌组清单（同 deck-base 格式）。instances 是逐张的牌组（HIRO_VEIL_SIGNATURE=1 时考试按它分版本记账）。"""
    deck = ensure_deck(ledger)
    custom = [{"card": e["card"], "item": e["item"]} for e in ledger.get("log", []) if e.get("op") == "customize"]
    return {"cards": list(ledger["cards"]), "once": sorted(set(ledger["once"])), "rebound_repeatable": ledger["rebound_repeatable"],
            "custom": custom, "instances": [{k: c.get(k) for k in ("id", "name", "once", "customs")} for c in deck],
            "evidence": f"账本（回忆「{ledger['memory']}」+ {len(ledger['log']) - 1} 条变动）"}  # fmt: skip


def conflicts(ledger: dict) -> dict[str, list[str]]:
    """同名但属性不同的卡（一次性 / 客制化不同）：{卡名: [每张的说明]}。动这些卡要 --id / --where。"""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for c in ensure_deck(ledger):
        by_name[c["name"]].append(c)
    return {n: [describe(c) for c in cs] for n, cs in sorted(by_name.items()) if len({signature(c) for c in cs}) > 1}


def summary(ledger: dict) -> dict:
    counts = Counter(c["name"] for c in ensure_deck(ledger))
    cards = [f"{n}×{k}" if k > 1 else n for n, k in sorted(counts.items())]
    out = {"memory": ledger["memory"], "size": len(ledger["cards"]), "cards": cards, "once": sorted(set(ledger["once"])),
           "rebound_repeatable": ledger["rebound_repeatable"],
           "drinks": [f"{i + 1}:{d or '空'}" for i, d in enumerate(ledger["drinks"])],
           "abilities": [f"{a['card']}→{ {'draw': '再动+抽1', 'play': '再动', 'sleep': '删眠気'}.get(a['kind'], a['kind'])}×{a['count']}"
                         for a in ledger.get("abilities") or []],
           "pending_add": ledger.get("pending_add"), "changes": len(ledger["log"]) - 1}  # fmt: skip
    if dup := conflicts(ledger):
        out["conflicts"] = dup
    return out
