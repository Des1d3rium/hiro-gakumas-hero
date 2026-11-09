"""牌库视图（play 第 2 块）：账本 + 画面 → 这一帧的局面上下文 Turn，以及「牌库是否已压缩成循环牌组」。

Turn 是决策各阶段共用的输入（手牌、按名字的索引、可打的牌、饮料、姿态、夜空、出牌次数、本回合已打、牌库是否压缩），
也负责把选好的动作包成 Decision（只能从合法动作里选，不在就停机）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from hiro.brain.play.profile import CORE, FP_RESERVE_AT, IDOL_CARD, REBOUND, _flag
from hiro.brain.play.sim import fp_delta, norm, stance
from hiro.contracts import Decision
from hiro.loop.errors import ResumeBlocked


def small_deck(state):
    if state.piles is None or not all(isinstance(state.piles.get(k), list) for k in ("deck", "grave")):
        return False
    hand = [norm(c.name) for c in state.hand if c.card_id]
    if len(hand) != len(state.hand):
        return False
    names = Counter(hand + [norm(c) for k in ("deck", "grave", "unlocated") for c in state.piles.get(k, [])])
    # 存疑的牌（VEIL_PILE_ENGINE：账面在山札、画面一直没出现，可能已不在牌库）不算：codex-20261109-1 R2 トレーニングの成果+
    # 被暗抽后一直挂着，4 核心 + 眠気 + 它 = 6 张，循环判不成小牌组
    names -= Counter(norm(c) for c in state.piles.get("doubt", []))
    if REBOUND in state.piles.get("once", []):
        return False
    if any(names[c] != 1 for c in CORE) or names.total() not in (4, 5):
        return False
    extra = names - Counter(CORE)
    once = {norm(c) for c in state.piles.get("once", [])} | {norm(c.name) for c in state.hand if c.tags.once}
    return not extra or next(iter(extra)) in once | {"眠気"}


@dataclass
class Turn:
    """一帧的决策输入。by_name 只含合法可打的牌（再去掉全力値闸挡住的 アイドルになります+）；observed 是全部认出的手牌。"""

    state: object
    memory: object
    actions: dict
    hand_cards: list
    slot_keys: dict
    observed: dict
    by_name: dict
    fp: int | None
    idol_starved: list
    drinks: dict
    current: str | None
    flower: int | None
    count: int
    played: list
    compact: bool
    cycle: dict | None = None
    notes: dict = field(default_factory=dict)

    @property
    def hand(self):
        return self.observed.keys()

    def act(self, key, reason, *, phase=None, plan=None):
        """合法动作 → Decision。phase 写进 Decision.plan 第一项（{"phase": …, "order": […]}），执行层按它判断，
        不再从理由文字里找关键字（cycle_tap 原来认「五卡循环」几个字）。"""
        if key not in self.actions:
            raise ResumeBlocked(f"VEIL 所需动作不可用：{reason}")
        steps = [{"phase": phase, **({"order": list(plan)} if plan else {})}] if phase else []
        return Decision(self.actions[key], reason, "rule", decider="veil", plan=steps)

    def play(self, card, reason, **kw):
        return self.act(f"play:{card.slot}", f"{reason}：{card.name}", **kw)

    def skip(self, reason, **kw):
        return self.act("skip", reason, **kw)


def build_turn(state, memory, legal) -> Turn:
    """读局面（不改记忆）：手牌、全力値闸、饮料、姿态、夜空、出牌次数、本回合已打、牌库是否压缩。"""
    actions = {a.legal_key(): a for a in legal}
    hand_cards = [c for c in state.hand if c.card_id]
    slot_keys = memory.strategy_data.get("veil_slot_keys") or {}
    if any(slot_keys.get(str(c.slot), norm(c.name)) != norm(c.name) for c in hand_cards):
        # 分版本记账（HIRO_VEIL_SIGNATURE=1）：同名两张（一次性的巻き返し+ 和常驻的核心）都在手里时，
        # 按名字取常驻的那张（循环要打的是核心）。只在手里有分了版本的牌时重排，其余情况顺序不变
        hand_cards.sort(key=lambda c: not c.tags.once)
    observed = {norm(c.name): c for c in hand_cards}
    by_name = {name: c for name, c in observed.items() if f"play:{c.slot}" in actions}
    fp = memory.strategy_data.get("veil_fp")
    idol_starved = [c for name, c in observed.items()
                    if _flag("IDOL_FP_GATE") and name.rstrip("+") == IDOL_CARD and fp is not None
                    and fp < (fp_delta(c)[1] or FP_RESERVE_AT)]
    for card in idol_starved:
        by_name.pop(norm(card.name), None)
    drinks = {d.name: f"drink:{d.slot}" for d in state.drinks if f"drink:{d.slot}" in actions}
    return Turn(
        state=state, memory=memory, actions=actions, hand_cards=hand_cards, slot_keys=slot_keys, observed=observed,
        by_name=by_name, fp=fp, idol_starved=idol_starved, drinks=drinks, current=stance(state),
        flower=state.pitem_charges, count=max(1, state.actions_left or 1),
        played=[norm(a[2:]) for a in memory.this_turn if a.startswith("打 ")], compact=small_deck(state),
        cycle=memory.strategy_data.get("veil_cycle"),
    )


def once_known(state, memory) -> set:
    """已知会离开牌库的牌名：账本 once + 本场核对出来的一次性牌。"""
    return set((state.piles or {}).get("once") or ()) | set(memory.strategy_data.get("veil_known_once") or ())


def deck_total(state) -> int:
    """牌库张数：手牌 + 山札 + 捨札 + 位置不明。"""
    return len(state.hand) + sum(len(state.piles.get(k) or []) for k in ("deck", "grave", "unlocated"))
