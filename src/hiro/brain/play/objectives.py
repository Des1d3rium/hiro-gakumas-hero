"""打分（play 第 8 块）：各阶段「哪个顺序 / 哪张牌更好」的目标函数。

- cycle_entry_score：进循环规划，打完后下回合开局能不能进循环、进得多好（四卡优先、给第五张留全力値、少打、体力多）。
- ClearScore：清牌阶段，单张牌的优先级（priority）和整段出牌顺序的得分（order_score：清掉的一次性牌、剩余次数……）。
推演都调 sim（gain / after_play），这里只比较结果。私有插件可以换成价值表或模拟器的期望分。
"""

from __future__ import annotations

from collections import Counter

from hiro.brain.play.profile import (
    CORE, FP_RESERVE_AT, HAND_MAX, IDOL_CARD, ORDERS, REBOUND, STANCE_LIMITED, STAR, clear_first, draw_last, hp_guard, hp_net,
    WHEY, keep_warm,
)
from hiro.brain.play.sim import WARM, after_play, gain, hp_loss, in_stance, next_stance, norm, swaps_hand


def ability_of_card(memory, card):
    """这张牌打出后会触发的回忆能力种类（draw / play / sleep），没有 None。"""
    from hiro.brain.memory_ability import pending as ability_of

    hit = ability_of(memory.strategy_data.get("veil_abilities"), card.name)
    return hit["kind"] if hit else None


def cycle_entry_score(state, ts, allc, cleared, sleeps, teach, warm, bad_next, once_known, draw_extra,
                      spent, genki, floor, length):
    """进循环规划一个顺序打完后的得分；下回合开局进不了循环返回 None。
    条件：夜空 1 / 2、没有把下回合预约成非温存、回合末在温存或下回合开局自动进温存；剩下的牌库是核心 4 张
    （+ 至多 1 张一次性 / 眠気）；下回合抽得到全部；开体力闸时体力不跌破保底线。
    得分：（剩四卡，第五张 アイドルになります+ 的全力値够，少打，打完体力多）。"""
    cur, fl = ts.stance, ts.flower
    if fl not in ORDERS or bad_next or not (cur in WARM or warm):
        return None
    left = allc - cleared + Counter({"眠気": sleeps})
    total = left.total()
    extra = left - Counter(CORE)
    if any(left[c] != 1 for c in CORE) or total not in (4, 5) or any(n not in once_known | {"眠気"} for n in extra):
        return None
    if 3 + draw_extra + 2 * teach < total:
        return None
    hp_after = None
    if state.hp is not None:
        hp_after = state.hp - max(0, spent - (state.genki or 0) - genki)
        if hp_guard() and hp_after < floor and not (hp_net() and hp_after >= 1):
            return None
    idol_left = any(n.rstrip("+") == IDOL_CARD for n in extra)
    return (total == 4, not idol_left or (ts.fp or 0) >= FP_RESERVE_AT, -length, hp_after or 0)


class ClearScore:
    """清牌阶段的打分。t 是 deckview.Turn；stance_cards 是手里的姿态限定牌（ガタゴトすやすや / 一心不乱）。"""

    def __init__(self, t, stance_cards, guard, mods):
        self.t = t
        self.stance_cards = stance_cards
        self.guard = guard
        self.mods = mods
        data = t.memory.strategy_data
        self.keep = keep_warm()
        self.once_known = set((t.state.piles or {}).get("once") or ()) | set(data.get("veil_known_once") or ())
        self.initial_once = set(data.get("veil_initial_once") or ())
        extra = data.get("veil_extra") or {}
        this_turn = extra.get("turn") == t.memory.turn
        self.stacks = t.count - 1  # 当前追加层数（基础 1 次之外）
        self.carry_now = not (this_turn and extra.get("start")) or bool(this_turn and extra.get("zeroed"))

    # -- 单张牌的性质
    def ability_kind(self, card):
        return ability_of_card(self.t.memory, card)

    def immediate_draw(self, card):
        return ((card.tags.draws and "次のターン" not in norm(card.text)) or swaps_hand(card)
                or self.ability_kind(card) == "draw")

    def resulting_stance(self, card):
        return after_play(self.t.current, self.t.flower, card)[0]

    def gains_now(self, card):
        # 回忆能力（再动 / 再动并抽牌）在打出后再给一次出牌次数
        # 温存解除：开关打开时看牌自己的切换（先结算牌，夜空 1→のんびり 在后），不看夜空覆盖后的最终姿态
        return gain(card, self.t.current, self.t.flower, ability=self.ability_kind(card))

    def gains_at(self, card, cur, fl):
        return None if cur is None else gain(card, cur, fl, ability=self.ability_kind(card))

    def unlocks(self, card):
        dest = self.resulting_stance(card)
        current = self.t.current
        return any(in_stance(dest, spec["stance"]) and in_stance(current, spec["unlock_from"]) and name in self.stance_cards
                   for name, spec in STANCE_LIMITED.items())

    def clears(self, card):
        """打出后这张牌离开牌库（一次性牌 / 眠気）：清牌阶段真正推进的出牌。"""
        name = norm(card.name)
        if name == REBOUND and self.t.slot_keys.get(str(card.slot), name) == name:
            return name in self.initial_once  # 分了版本的巻き返し+ 按这一张的一次性（apply_variants 已按账本改好）
        return bool(card.tags.once or card.tags.is_sleep or name in self.once_known)

    def reusable_ok(self, card):
        """不清牌的复用牌（エンターテイナー、教えてあげる…）：只在不减少「温存 + 能留到下回合的再动」时打。
        代价：温存下切強気用掉温存存着的一次再动；有可保留的追加层时，打牌消耗一层。
        收益：打出后得到的追加次数（温存→強気、牌面、回忆能力），只有能留到下回合（本回合开始时没有，或这一张把层数用到 0）才算。"""
        warm_spent = self.t.current in WARM and self.resulting_stance(card) == "強気"
        cost = int(warm_spent) + int(self.stacks > 0 and self.carry_now)
        got = self.gains_now(card) if self.carry_now or self.stacks <= 1 else 0
        return got >= cost

    def priority(self, card):
        t = self.t
        current, count = t.current, t.count
        dest = self.resulting_stance(card)
        delays_warm = current == "強気" and dest in WARM
        opens_followup = self.unlocks(card) and (count > 1 or self.gains_now(card) > 0 or WHEY in t.drinks)
        # 体力闸开着时：強気下先打切回温存的牌（「強気に変更」的牌留到温存 / のんびり 下打，消耗减半或为 0），再比掉血少
        # 切回温存只在本回合还有下一张要打时才有用（后面的牌消耗减半）；只剩 1 次出牌时不靠它排前（1-f R2 剩 8：
        # 教えてあげる+ 因夜空最后 1 次会变のんびり 被排在 ひと呼吸+ 前，一次性牌又被压后）
        hp_first = ((dest in WARM and count > 1, -hp_loss(t.state, card, current, self.mods))
                    if self.guard and current == "強気" else ())
        return hp_first + ((self.clears(card),) if self.keep or clear_first() else ()) + (
            self.gains_now(card), opens_followup, not delays_warm,
            bool(card.tags.once and norm(card.name) != REBOUND),
            # 都是再动时立即抽牌减分（使用者 2026-11-07：先清不抽牌的再动牌，避免满手抽牌作废）；开关关时照旧加分
            # 同样不抽牌时不改姿态的先（温存下先清 みずたまりスキップ，切強気会让后面的牌消耗翻倍）
            ((not self.immediate_draw(card), dest == current) if draw_last() and self.gains_now(card) > 0
             else (bool(self.immediate_draw(card)), False)),
            self.ability_kind(card) == "sleep", dest != current, card.badge_score or 0)

    # -- 整段顺序
    def order_score(self, order, start, warm_already, last_turn):
        """（清掉的一次性牌数，剩余次数，少打复用牌，抽牌晚打）：清牌阶段只算一次性牌（复用牌用掉温存、
        1-f R1 一次性牌被拖后），抽进来的牌不因手牌满作废作最后比较。
        剩余次数按回合末算：牌都打完、没抽到新牌时剩下的次数作废（不算）；回合末留在温存 / のんびり 算 1 次
        （下回合第一次切強気的温存解除；下回合开局本来就会进温存时——预约「次のターン温存に変更」、一番星还有次数——
        停在強気也算，几种来源同时有也只算 1 次）。使用者 2026-11-07（1-h R1 T1）：喝ホエイ 后手里 プライド+（温存）、
        心・技・体（強気），应先打心・技・体 再打プライド+ 留温存到下回合，而不是温存→強気 拿一次用不掉的解除。"""
        t = self.t
        acts, cur, fl, n, cleared, reused = t.count, start, t.flower, 0, 0, 0
        warm_later = warm_already
        zeroed = self.carry_now
        size, waste, draws = len(t.state.hand), 0, []
        for c in order:
            if acts < 1 or cur is None:
                break
            acts += gain(c, cur, fl, ability=self.ability_kind(c)) - 1
            if acts <= 1:
                zeroed = True  # 再动层数用到 0：之后再拿到的能留到下回合
            cur, fl = after_play(cur, fl, c)
            if norm(c.name).startswith(STAR) or next_stance(c) in WARM:
                warm_later = True
            n += 1
            if self.clears(c):
                cleared += 1
            else:
                reused += 1
            d = max(c.tags.draws or 0, int(self.ability_kind(c) == "draw")) if self.immediate_draw(c) else 0
            size = size - 1 + d
            waste += max(0, size - HAND_MAX)
            size = min(size, HAND_MAX)
            draws.append(-d)
            if swaps_hand(c) and n < len(order):
                break  # 1-h R2 剩 10：仕切り直し+ 换掉整手牌，排在它后面的牌（頂点へ+）打不到了
        if n == len(order) and not any(draws):
            # 牌打完了、也没抽到牌：基础的 1 次用不掉；再动层数按 buff 保留规则留到下回合（track_extra：回合开始
            # 没有层数、或本回合用到过 0 → 全留；否则衰退 1）。1-h R2 剩 10 Skip 时剩 2 次，剩 9 实际多打了 1 张
            stacks = max(0, acts - 1)
            acts = stacks if zeroed else max(0, stacks - 1)
        # 下回合开局本来就会进温存（预约 / 一番星）时回合末停在強気也行；几种来源只算一次（使用者 2026-11-07 补充）
        acts += int((cur in WARM or warm_later) and not last_turn)
        # 抽牌：先比手牌满作废的张数，再按出牌顺序比抽牌数（抽得少的先打）
        return (cleared, acts, -reused, -waste, tuple(draws))
