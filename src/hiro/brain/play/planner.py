"""序列搜索（play 第 4 块）：枚举本回合的出牌顺序，交给目标函数打分。

原来三处各写一份全排列（进循环规划 plan_cycle、清牌顺序推演、过渡可达性）。现在搜索只有这一份；
「怎么推演」统一走 sim.TurnState，「好不好」由 objectives 里的目标函数给，这里只管枚举和取最优。
"""

from __future__ import annotations

from itertools import permutations


def orders(items, *, max_len=None, prefixes=False):
    """出牌顺序。prefixes=True：长度 0..max_len 的全部排列（从短到长，同长按 permutations 的顺序）；
    否则只要全长排列。"""
    items = list(items)
    if not prefixes:
        yield from permutations(items)
        return
    top = len(items) if max_len is None else min(len(items), max_len)
    for k in range(top + 1):
        yield from permutations(items, k)


def best(items, score, **kw):
    """score(order) → 可比较的值或 None（不可行）。返回 (score, order)，同分取先枚举到的；都不可行返回 None。"""
    top = None
    for order in orders(items, **kw):
        value = score(order)
        if value is not None and (top is None or value > top[0]):
            top = (value, order)
    return top


def scored(items, score, **kw):
    """全部可行顺序的 [(score, order)]（清牌顺序推演要在并列最优里再挑）。"""
    out = []
    for order in orders(items, **kw):
        value = score(order)
        if value is not None:
            out.append((value, order))
    return out
