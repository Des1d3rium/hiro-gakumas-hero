"""hiro.brain.play.draws：下回合抽牌概率（精确公式和穷举洗牌对照）。"""

from collections import Counter
from fractions import Fraction
from itertools import permutations

import pytest

from hiro.brain.play import draws

pytestmark = pytest.mark.synthetic


def brute(deck, refill, n, targets):
    """穷举山札、捨札的所有排列：先抽完山札，不够再从洗过的捨札抽。"""
    hit = total = 0
    d_orders = set(permutations(deck))
    r_orders = set(permutations(refill)) if n > len(deck) else {()}
    for d in d_orders:
        for r in r_orders:
            hand = (list(d) + list(r))[:n]
            total += 1
            hit += all(t in hand for t in targets)
    return Fraction(hit, total)


@pytest.mark.parametrize("deck,refill,n,targets", [
    ("AABC", "", 2, "A"),
    ("ABCDE", "", 3, "AB"),
    ("AB", "CDE", 3, "AC"),          # 山札不够：A、B 必到，C 从捨札抽 1
    ("A", "BCDD", 3, "BD"),
    ("", "ABCD", 2, "AB"),           # 山札空：全从捨札
    ("AB", "CD", 3, "E"),            # 牌库里没有
])
def test_exact_matches_brute_force(deck, refill, n, targets):
    got = draws.exact(Counter(deck), Counter(refill), n, list(targets))
    assert got == pytest.approx(float(brute(deck, refill, n, list(targets))))


def test_unlocated_gives_range():
    """位置不明的核心牌：在山札（必到）/ 在捨札（山札够抽时抽不到）→ 区间 0–100%。"""
    piles = {"deck": ["A", "B", "C"], "grave": [], "unlocated": ["D"]}
    odds = draws.next_turn(piles, [], ["D"], draws=3)
    assert odds.lo == 0.0 and odds.hi > 0.7


def test_ghost_range_and_known_case():
    piles = {"deck": ["A", "B", "C", "D"], "grave": ["E"], "ghost": 1}
    exact = draws.next_turn({"deck": ["A", "B", "C", "D"], "grave": ["E"]}, [], ["A"], draws=3)
    ranged = draws.next_turn(piles, [], ["A"], draws=3)
    assert exact.lo == exact.hi == pytest.approx(0.75)
    assert ranged.lo < 1.0 and ranged.hi == pytest.approx(1.0)  # A 被暗抽走时 0 张…其余 3 张必到 A


def test_hand_end_goes_to_refill():
    """本回合留在手里的牌回合末进捨札：山札不够时能洗回来再抽到。"""
    piles = {"deck": ["A"], "grave": []}
    assert draws.next_turn(piles, ["B"], ["B"], draws=2).lo == pytest.approx(1.0)
    assert draws.next_turn(piles, [], ["B"], draws=2).hi == 0.0


def test_core_draw_odds_counts_rebound_as_reusable():
    """1-d R1 T8：山札只剩 OD，手里 教えて / 巻き返し+（标成一次性的版本）/ エンタ。不预约下回合抽 3：OD + 洗回的 3 张里抽 2，
    核心凑不齐（0%）；预约 +2 抽 5 → 全到（100%）。巻き返し+ 的一次性标记不当真（过渡不清核心牌）。"""
    from types import SimpleNamespace as NS

    from hiro.brain.play import cycle

    def c(name, once=False):
        return NS(name=name, card_id="x", tags=NS(once=once))

    state = NS(piles={"deck": ["オーバードライブ+"], "grave": [], "unlocated": [], "doubt": [], "next_draw": 3},
               hand=[c("教えてあげる+"), c("巻き返し+", once=True), c("エンターテイナー+")])
    now, reserved = cycle.core_draw_odds(NS(state=state))
    assert (now.lo, reserved.lo) == (0.0, 1.0)
