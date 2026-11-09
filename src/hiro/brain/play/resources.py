"""饮料（play 第 7 块）：什么时候喝哪一瓶。

ホエイ（出牌次数 +1）、ソーダ（抽 2）、ブースト（加成）、初星湯（回合 +1）。按阶段分：循环开局一起喝、进循环前喝ソーダ 补齐核心、
最后一回合喝初星湯、清牌里没牌可打 / 有牌没次数时补。手牌满时不喝抽牌的（抽到的作废）。
每个函数返回 Decision 或 None（不喝）。
"""

from __future__ import annotations

from hiro.brain.play.profile import (
    BOOST, CORE, EXTRA_TURN, HAND_MAX, ORDERS, SODA, WHEY, clear_first, cycle_soda, cycle_whey, draw_last, hp_floor,
    hp_guard, stuck_soda,
)
from hiro.brain.play.sim import WARM


def pre_cycle_soda(t):
    """开局只差 1–2 张核心：先喝ソーダ 抽 2 补齐再进循环（过渡里留着没喝的那瓶）。"""
    state, hand, drinks = t.state, t.hand, t.drinks
    if (clear_first() and not t.played and not (t.cycle and t.cycle["turn"] == state.turn) and t.compact
            and t.current in WARM and t.flower in ORDERS and SODA in drinks and 1 <= len(CORE - hand) <= 2
            and len(state.hand) + 2 <= HAND_MAX):
        return t.act(drinks[SODA], f"循环前：喝 {SODA} 抽 2 补齐核心（缺 {'、'.join(sorted(CORE - hand))}）")
    return None


def extra_turn_ok(t) -> bool:
    """初星湯（ターン追加+1）：原来不在候选里，1-g R2 三次再挑戦都没喝（使用者 2026-11-07）。进循环时和别的饮料一起喝；
    到最后一回合还在就照喝。体力消費2：开体力闸时喝完不跌破保底线"""
    state = t.state
    return clear_first() and EXTRA_TURN in t.drinks and (
        not hp_guard() or state.hp is None or state.hp - 2 >= hp_floor())


def last_turn_extra(t, ok):
    if ok and t.state.remaining_turns == 1:
        return t.act(t.drinks[EXTRA_TURN], f"最后一回合：使用 {EXTRA_TURN}（回合+1）")
    return None


def cycle_drink(t, extra_ok):
    """循环回合开头把饮料喝掉：ホエイ、ブースト、ソーダ（满手不喝，VEIL_CYCLE_SODA）、初星湯；
    第五张不在手时不喝ホエイ（多的次数没牌打，留给下回合过渡，VEIL_CYCLE_WHEY）。"""
    state, drinks = t.state, t.drinks
    for drink in (WHEY, BOOST, SODA) + ((EXTRA_TURN,) if extra_ok else ()):
        if drink == SODA and cycle_soda() and len(state.hand) + 2 > HAND_MAX \
                and (state.remaining_turns is None or state.remaining_turns > 1):
            continue  # 满手喝ソーダ 抽的 2 张作废（VEIL_CYCLE_SODA）
        if drink == WHEY and cycle_whey() and len(state.hand) < HAND_MAX \
                and (state.remaining_turns is None or state.remaining_turns > 1):
            continue  # 第五张不在手：多的次数没牌打，留给下回合过渡（VEIL_CYCLE_WHEY）
        if drink in drinks:
            return t.act(drinks[drink], f"循环：使用 {drink}")
    return None


def stuck(t, playable):
    """VEIL_STUCK_SODA：还有出牌次数、手里没有能打的牌（不是被体力闸压住）→ 喝ソーダ 抽 2 继续清。"""
    if stuck_soda() and not playable and SODA in t.drinks and len(t.state.hand) + 2 <= HAND_MAX:
        return t.act(t.drinks[SODA], "清牌：手里没有能打的牌，喝ソーダ 抽 2 继续清")
    return None


def short_of_cards(t):
    """有出牌次数但缺手牌：喝ソーダ。手牌满时抽的 2 张作废（1-g R2 再挑戦 T1：5 张满手连喝三瓶）→ DRAW_LAST 下手牌放得下才喝。"""
    if SODA in t.drinks and (not draw_last() or len(t.state.hand) + 2 <= HAND_MAX):
        return t.act(t.drinks[SODA], "清牌：有行动但缺手牌")
    return None


def short_of_actions(t, reason="清牌：有手牌但出牌次数不足"):
    """有手牌但出牌次数不足：喝ホエイ。"""
    if WHEY in t.drinks:
        return t.act(t.drinks[WHEY], reason)
    return None
