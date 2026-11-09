"""考试牌堆记账（VEIL_PILE_ENGINE，使用者 2026-11-09「按照 1，2，3 来」）：三层，各管一件事。

1. 效果层 moves_of_*：打出一张牌 / 喝一瓶饮料 / 回忆能力发动，按主数据会移动几张牌（抽 N、整手换、生成眠気）。
   牌走 card_fx（playEffects），饮料走 ProduceDrink → ProduceExamEffect，回忆能力按 memory_ability 的类别。
2. 牌堆层 observe：只按「应该抽几张」和画面手牌移牌。手牌之外的牌分 山札 / 捨札 / 位置不明；
   应该抽、却直到回合结束都没在手里出现过的牌（回合末被丢、手牌满作废）记成「暗抽」：
   山札里有 ghost 张其实已经进了捨札，是哪几张不知道——下一次洗牌（捨札洗回山札）时自然对上。
3. 对账层（observe 里的矛盾处理）：抽到的牌山札里没有、按账面山札又不该洗牌时，不再把整副记成位置不明，
   只倒推「山札其实已经空了」：账面山札剩的几张记成 doubt（可能在捨札、也可能已不在牌库），照常洗牌；
   doubt 的牌洗进山札后，山札再次抽空都没出现过 → 它不在牌库里，从牌组账本拿掉（gone）。
   出现矛盾时 need_view=True：调用方打开右下角牌库一览按「山札 / 捨札 / 除外」读一次，apply_view 以画面为准重置。

原来的 track_piles（hiro.produce.veil_exam）一出矛盾就整副位置不明（codex-20261109-1 R2：トレーニングの成果+ 被暗抽，
T3 起 8 张位置不明，T7 起它一直挂着，循环判不成小牌组）。
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

HAND_MAX = 5


def enabled() -> bool:
    """VEIL_PILE_ENGINE（默认关，=1 开）：牌堆记账走本模块。"""
    return os.environ.get("VEIL_PILE_ENGINE", "0") == "1"


# ---------------------------------------------------------------- 1. 效果层


@dataclass(frozen=True)
class Moves:
    draw: int = 0  # 抽几张（整手换的那几张不算在这里）
    swap: bool = False  # 手札をすべて入れ替える：原手牌整手进捨札，再抽同样张数
    sleep: int = 0  # 生成几张眠気进山札


def moves_of_card(card) -> Moves:
    """CardView → Moves。有结构化效果（card_fx）按它；没有按牌面文字的「N枚引く」。"""
    from hiro.brain.veil import swaps_hand

    fx = getattr(card, "fx", None) or {}
    draw = int(fx.get("draws") or 0)
    if not fx:
        import re

        m = re.search(r"スキルカードを(\d+)枚引く", getattr(card, "text", "") or "")
        draw = int(m.group(1)) if m else int("スキルカードを引く" in (getattr(card, "text", "") or ""))
    return Moves(draw=draw, swap=bool(swaps_hand(card)), sleep=int(bool(fx.get("makes_sleep"))))


@lru_cache(maxsize=None)
def moves_of_drink(name: str) -> Moves:
    """饮料名 → Moves：ProduceDrink.produceDrinkEffectIds → ProduceDrinkEffect → ProduceExamEffect 里的 ExamCardDraw。"""
    import json

    try:
        from hiro.state.catalog import catalog

        db = catalog().db
        row = db.execute("SELECT produceDrinkEffectIds FROM ProduceDrink WHERE name = ? LIMIT 1", (name,)).fetchone()
        draw = 0
        for eid in json.loads(row[0]) if row and row[0] else []:
            e = db.execute("SELECT produceExamEffectId FROM ProduceDrinkEffect WHERE id = ?", (eid,)).fetchone()
            x = db.execute("SELECT effectType, effectValue1 FROM ProduceExamEffect WHERE id = ?", (e[0],)).fetchone() if e else None
            if x and x[0] == "ProduceExamEffectType_ExamCardDraw":
                draw += int(x[1] or 0)
        return Moves(draw=draw)
    except Exception:  # noqa: BLE001  没有主数据
        return Moves()


def moves_of_ability(kind: str | None) -> Moves:
    """回忆能力：draw = 再动 + 抽 1；其余不抽。"""
    return Moves(draw=1) if kind == "draw" else Moves()


def expect(piles: dict | None, moves: Moves) -> None:
    """这一手之后同一回合应该再多抽几张（observe 用来认暗抽）。整手换的张数由 observe 按原手牌算。"""
    if piles is not None and moves.draw:
        piles["expect"] = piles.get("expect", 0) + moves.draw


# ---------------------------------------------------------------- 2 + 3. 牌堆层 + 对账层


def _counter(d) -> Counter:
    return Counter({k: v for k, v in (d or {}).items() if v > 0})


def observe(data: dict, turn: int, slots: list, outside: Counter) -> dict:
    """和 track_piles 同一个接口：用这一帧手牌（slots）推进牌堆账，返回 {deck, grave, unlocated, doubt} 列表。
    outside = 牌组账本里此刻不在手里的牌（山札 + 捨札 + 位置不明）。改 data["veil_piles"]；
    认定 doubt 的牌不在牌库时同时改 data["veil_deck"]（记进 veil_gone）。

    山札账 deck 里有 ghost 张其实已经被暗抽进了捨札（是谁不知道）：山札实际张数 = deck 总数 - ghost。
    洗牌时新山札 = 原山札账 + 捨札 - 抽到的（暗抽的牌原本就记在山札账里，不重复算），ghost 清零。"""
    previous = data.get("veil_piles")
    log = data.setdefault("veil_pile_log", [])
    if previous is None:
        deck, unknown = Counter(), Counter()
        (deck if turn == 1 and not data.get("veil_legacy_piles") else unknown).update(outside)
        data["veil_piles"] = {"turn": turn, "hand": slots, "deck": dict(deck), "grave": {}, "unlocated": dict(unknown),
                              "ghost": 0, "doubt": {}, "expect": 0, "need_view": False}
        return _view(data["veil_piles"])
    deck, grave, unknown = (_counter(previous[k]) for k in ("deck", "grave", "unlocated"))
    doubt = _counter(previous.get("doubt"))
    ghost = int(previous.get("ghost") or 0)
    born = _counter(previous.get("born"))
    old_hand = Counter(c for c in previous["hand"] if c)
    hand = Counter(c for c in slots if c)
    expected = int(previous.get("expect") or 0)
    need_view = bool(previous.get("need_view"))
    new_turn = turn != previous["turn"]
    if new_turn:
        grave.update(old_hand)
        drawn = hand
        if expected > 0:
            # 暗抽：上回合应该抽、到回合结束都没在手里出现过（回合末直接被丢 / 手牌满作废）
            ghost += expected
            log.append({"turn": previous["turn"], "event": "hidden_draw", "count": expected})
        expected = 0
        deck += born
        born = Counter()
    elif previous.get("swap"):
        grave.update(old_hand)
        drawn = hand
    else:
        grave.update(old_hand - hand)
        drawn = hand - old_hand
    if not new_turn:
        expected = max(0, expected - drawn.total())
    claimed = drawn & unknown  # 位置不明的牌被抽到：先认领
    unknown -= claimed
    take = drawn - claimed
    real = deck.total() - ghost
    if take - deck or take.total() > real:
        # 洗牌：先抽完山札，再把捨札洗回山札。没被抽到的山札账 rest 只能是暗抽进捨札的（ghost 张）或者不在牌库的
        rest = deck - take
        gone = rest & doubt
        if gone:
            # 规则 ②：存疑的牌洗回山札后，山札又抽空了一轮都没出现 → 不在牌库
            _drop(data, gone)
            log.append({"turn": turn, "event": "gone", "cards": dict(gone)})
            doubt -= gone
            rest -= gone
        if rest.total() > ghost:
            # 对账层：账面山札还有牌没抽到、画面却已经洗牌（抽到了捨札的牌）——倒推山札其实空了。
            # 没抽到的这几张可能是之前暗抽进捨札的，也可能已经不在牌库：先当在捨札、随洗牌回到山札，记成存疑，等牌库一览确认
            doubt += rest
            need_view = True
            log.append({"turn": turn, "event": "deck_empty_inferred", "rest": dict(rest), "ghost": ghost})
        deck = deck - gone + grave - take
        grave = Counter()
        ghost = 0
    else:
        deck -= take
    deck += born  # 同一回合刚生成的眠気：这一手的抽牌之后才并进山札
    doubt -= drawn
    total = deck + grave + unknown
    expect_out = _counter(dict(outside))
    for name, n in (data.get("veil_gone") or {}).items():
        expect_out[name] -= n
    expect_out = +expect_out
    if total != expect_out:
        # 和牌组账本对不上：多的拿掉（先捨札、再山札），少的记位置不明；不再整副作废
        log.append({"turn": turn, "event": "mismatch", "book": dict(total), "outside": dict(expect_out)})
        for name, n in (total - expect_out).items():
            for pile in (grave, deck, unknown):
                k = min(n, pile.get(name, 0))
                pile[name] -= k
                n -= k
        unknown += expect_out - total
        need_view = True
    data["veil_piles"] = {"turn": turn, "hand": slots, "deck": dict(+deck), "grave": dict(+grave), "unlocated": dict(+unknown),
                          "ghost": ghost, "doubt": dict(+doubt), "expect": expected, "need_view": need_view}
    return _view(data["veil_piles"])


def _drop(data: dict, gone: Counter) -> None:
    """认定不在牌库的牌：从牌组账本（veil_deck）拿掉，记进 veil_gone。"""
    deck = data.get("veil_deck")
    record = data.setdefault("veil_gone", {})
    for name, n in gone.items():
        record[name] = record.get(name, 0) + n
        if deck is not None and deck.get(name, 0) >= n:
            deck[name] -= n


def _view(piles: dict) -> dict:
    return {"deck": list(_counter(piles["deck"]).elements()), "grave": list(_counter(piles["grave"]).elements()),
            "unlocated": list(_counter(piles["unlocated"]).elements()), "doubt": list(_counter(piles.get("doubt")).elements())}


def _to_book(name: str, book) -> str:
    """一览里的卡名 → 账本卡名。考试中一览带支援卡临时强化（「アンダンテ+」「++」），账本卡名最多一个「+」、考试页不改强化
    （codex-20261109-1-r R2 T3：一览把账本的 アンダンテ 记成 アンダンテ+，T4 抽到「アンダンテ」对不上，又白开一次一览，trust 2 坏步）。
    账本里有这个名字就用它；否则同一基名里取「+」不多于画面的最高一档。"""
    if not book or name in book:
        return name
    base, plus = name.rstrip("+"), len(name) - len(name.rstrip("+"))
    same = sorted((k for k in book if k.rstrip("+") == base and len(k) - len(base) <= plus), key=len)
    return same[-1] if same else name


def apply_view(data: dict, groups: dict) -> dict | None:
    """右下角牌库一览读到的分组（山札 / 捨札 / 除外 / 手札）→ 牌堆账以画面为准。山札、捨札都读到才用；返回差异。"""
    piles = data.get("veil_piles")
    if piles is None or "山札" not in groups and "捨札" not in groups:
        return None
    book = data.get("veil_deck")
    deck = Counter(_to_book(n, book) for n in groups.get("山札") or [])
    grave = Counter(_to_book(n, book) for n in groups.get("捨札") or [])
    before = {"deck": dict(_counter(piles["deck"])), "grave": dict(_counter(piles["grave"])),
              "unlocated": dict(_counter(piles["unlocated"])), "doubt": dict(_counter(piles.get("doubt")))}
    if book is not None:
        hand = Counter(c for c in piles["hand"] if c)
        gone = (Counter({k: v for k, v in book.items() if v > 0}) - hand) - deck - grave
        if gone:
            _drop(data, gone)
    piles.update(deck=dict(deck), grave=dict(grave), unlocated={}, doubt={}, ghost=0, need_view=False)
    diff = {"before": before, "deck": dict(deck), "grave": dict(grave)}
    data.setdefault("veil_pile_log", []).append({"turn": piles["turn"], "event": "view", **diff})
    return diff
