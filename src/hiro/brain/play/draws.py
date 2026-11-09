"""抽牌概率（play 第 10 块）：按牌堆账算下回合开局抽到哪些牌的概率，精确计算、不模拟。

规则（学マス 考试）：回合结束时手牌全部进捨札；下回合开局从山札抽 N 张，山札不够时先抽完山札，再把捨札洗成新山札接着抽。
洗牌是均匀的（随机数实现不影响概率），所以只要牌堆组成和抽牌数对，概率就是精确的：
- 山札够：山札里无放回抽 N 张（超几何分布）；
- 山札不够：山札的牌全部到手，剩下 N - 山札张数 从「捨札 + 本回合留下的手牌」里无放回抽。

账本里不确定的部分不猜，分情况算出区间（lo, hi）：
- 位置不明（unlocated）：全在山札 / 全在捨札 两种极端；
- 存疑（doubt，账面在山札、可能已不在牌库）：在 / 不在 两种；
- 暗抽（ghost）：山札账里有 ghost 张其实已经在捨札，是哪几张不知道——按「哪几张都有可能」取极端。
都没有时 lo == hi。

只用公开信息：牌堆组成在游戏里能查看，顺序不可见。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations


@dataclass(frozen=True)
class Odds:
    lo: float
    hi: float

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2

    def __str__(self) -> str:
        return f"{self.lo:.0%}" if abs(self.hi - self.lo) < 0.005 else f"{self.lo:.0%}–{self.hi:.0%}"


def _none_of(size: int, hits: int, draws: int) -> Fraction:
    """size 张里 hits 张是目标，无放回抽 draws 张一张都没抽到的概率（分数，容斥相加不留浮点残差）。"""
    draws = min(draws, size)
    if hits <= 0 or draws <= 0:
        return Fraction(1)
    if size - hits < draws:
        return Fraction(0)
    return Fraction(math.comb(size - hits, draws), math.comb(size, draws))


def _all_from(pool: Counter, draws: int, targets: list[str]) -> float:
    """从 pool 里无放回抽 draws 张，targets 里每个名字都至少抽到一张的概率（容斥）。"""
    size = pool.total()
    if not targets:
        return 1.0
    if any(pool[n] <= 0 for n in targets):
        return 0.0
    total = Fraction(0)
    for r in range(len(targets) + 1):
        for subset in combinations(targets, r):
            total += (-1) ** r * _none_of(size, sum(pool[n] for n in subset), draws)
    return float(total)


def exact(deck: Counter, refill: Counter, draws: int, targets) -> float:
    """山札 deck、洗牌时的捨札 refill（含回合末进捨札的手牌）、抽 draws 张：targets 每个名字都至少抽到一张的概率。"""
    deck, refill = +Counter(deck), +Counter(refill)
    want = sorted(set(targets))
    n = deck.total()
    if draws <= n:
        return _all_from(deck, draws, want)
    rest = [name for name in want if deck[name] <= 0]  # 山札里有的全部到手
    return _all_from(refill, draws - n, rest)


def _ghost_variants(deck: Counter, ghost: int):
    """山札账里有 ghost 张已暗抽进捨札：取让目标最难 / 最容易抽到的两种移法由调用方比较。这里列出所有按卡名的移法（张数小，可穷举）。"""
    if ghost <= 0:
        yield deck, Counter()
        return
    names = sorted(deck.elements())
    seen = set()
    for picked in combinations(names, min(ghost, len(names))):
        key = tuple(sorted(picked))
        if key in seen:
            continue
        seen.add(key)
        moved = Counter(picked)
        yield deck - moved, moved


def next_turn(piles: dict, hand_end, targets, draws: int | None = None) -> Odds | None:
    """下回合开局抽牌后，targets 每个名字都在手里的概率区间。

    piles：牌堆账（deck / grave / unlocated / doubt 为卡名列表，可选 ghost 张数、next_draw）；
    hand_end：本回合结束时还留在手里、会进捨札的牌（卡名列表）；draws：下回合开局抽几张（默认 piles["next_draw"]，再默认 3）。
    牌堆账没有时返回 None。"""
    if piles is None or not isinstance(piles.get("deck"), list):
        return None
    draws = int(draws if draws is not None else piles.get("next_draw") or 3)
    deck = Counter(piles.get("deck") or [])
    grave = Counter(piles.get("grave") or []) + Counter(hand_end or [])
    unknown = Counter(piles.get("unlocated") or [])
    doubt = Counter(piles.get("doubt") or []) & deck
    ghost = int(piles.get("ghost") or 0)
    values = []
    for doubt_gone in ((False, True) if doubt else (False,)):
        d0 = deck - doubt if doubt_gone else deck
        for unknown_in_deck in ((True, False) if unknown else (True,)):
            d1 = d0 + unknown if unknown_in_deck else d0
            g1 = grave if unknown_in_deck else grave + unknown
            for d2, moved in _ghost_variants(d1, ghost):
                values.append(exact(d2, g1 + moved, draws, targets))
    return Odds(min(values), max(values))
