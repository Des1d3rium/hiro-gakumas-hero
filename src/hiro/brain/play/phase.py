"""阶段判定与调度（play 第 5 块）：一帧局面属于哪个阶段、按什么顺序问各阶段。

    停手（本轮分数超过阈值）
    → 记下循环（cycle.start）
    → 循环前喝ソーダ / 最后一回合喝初星湯（resources）
    → 全力过渡（cycle.full_power）
    → 循环回合（cycle.execute）
    → 进循环规划（cycle.enter）
    → 过渡：牌库已压缩、还没进循环（cycle.transition）
    → 清牌：默认阶段（clear，本文件）

顺序和重构前 VeilAdvisor.decide 一一对应，前面的阶段给出决策就不再往下问。
"""

from __future__ import annotations

from hiro.brain.play import cycle, planner, resources
from hiro.brain.play.deckview import build_turn
from hiro.brain.play.guards import Gates, clear_eligible
from hiro.brain.play.objectives import ClearScore
from hiro.brain.play.profile import (
    CORE, DRIVE, FP_RESERVE_AT, IDOL_CARD, LIMITS, PLAY_LATER, REBOUND, STANCE_LIMITED, TEACH, WHEY, clear_first, drive_fp, release_gain, sim_unified, stance_later, stance_unlock,
    strong_teach,
)
from hiro.brain.play.sim import (
    NEUTRAL, WARM, after_play, fp_delta, gain, in_stance, norm, swaps_hand, warm_next_turn,
)
from hiro.contracts import Decision
from hiro.loop.errors import ResumeBlocked


def decide(state, memory, previews, legal):
    if state.exam not in LIMITS:
        raise ResumeBlocked("VEIL 策略仅用于 H.I.F 本战 R1/R2")
    actions = {a.legal_key(): a for a in legal}
    score = state.score if state.score is not None else memory.last_score
    if score is not None and score > LIMITS[state.exam]:
        if "skip" not in actions:
            raise ResumeBlocked("VEIL 所需动作不可用：超过本轮停手阈值")
        return Decision(actions["skip"], "超过本轮停手阈值", "rule", decider="veil")

    t = build_turn(state, memory, legal)
    cycle.start(t)
    if (d := resources.pre_cycle_soda(t)) is not None:
        return d
    extra_ok = resources.extra_turn_ok(t)
    if (d := resources.last_turn_extra(t, extra_ok)) is not None:
        return d
    if (d := cycle.full_power(t)) is not None:
        return d
    if cycle.active(t):
        return cycle.execute(t, extra_ok)
    if (d := cycle.enter(t)) is not None:
        return d
    final_turn, eligible = clear_eligible(t)
    if t.compact and not final_turn:
        return cycle.transition(t, eligible)
    return clear(t, eligible)


def clear(t, eligible):
    """清牌：把一次性牌按能多打、少掉血、多留次数的顺序打掉，牌库压缩到循环牌组。"""
    state, memory = t.state, t.memory
    by_name, observed, hand, drinks = t.by_name, t.observed, t.hand, t.drinks
    current, flower, count, played = t.current, t.flower, t.count, t.played
    fp = t.fp
    if state.piles is None and CORE <= hand:
        raise ResumeBlocked("VEIL 核心同手，尚未核实完整牌库，暂停以确认剩余牌")
    stance_cards = {
        name.rstrip("+"): c for name, c in observed.items()
        if name.rstrip("+") in STANCE_LIMITED
    }
    gates = Gates(t)
    affordable, last_turn = gates.affordable, gates.last_turn
    score = ClearScore(t, stance_cards, gates.guard, gates.mods)

    for name, spec in STANCE_LIMITED.items():
        allowed = in_stance(current, spec["stance"])
        card = stance_cards.get(name)
        if allowed and card and f"play:{card.slot}" in t.actions and affordable(card):
            if stance_later() and count == 1 and not card.tags.extra_actions:
                # 它用掉最后一次出牌就结束回合：先打一张不改掉这个姿态的一次性再动牌，或先喝ホエイ（VEIL_STANCE_LATER）
                keeps = (lambda c, want=spec["stance"]: in_stance(after_play(current, flower, c)[0], want))
                first = next((c for c in eligible if c is not card and c.tags.extra_actions and c.tags.once
                              and not c.tags.makes_sleep and keeps(c) and affordable(c) and not swaps_hand(c)), None)
                if first is not None:
                    return t.play(first, f"清牌：先打不改姿态的再动牌，{name} 留后")
                if WHEY in drinks and any(c is not card and (c.tags.once or c.tags.is_sleep) for c in eligible):
                    return t.act(drinks[WHEY], f"清牌：{name} 会用掉最后一次出牌，先喝ホエイ")
            return t.play(card, "清牌：姿态限定牌优先")
    if t.idol_starved:
        generator = next((c for c in eligible if fp_delta(c)[0] > 0 and affordable(c)), None)
        if generator is not None:
            return t.play(generator, f"清牌：全力値 {fp} 打不了 {IDOL_CARD}，先攒全力値")
    if drive_fp() and fp is not None:
        from hiro.brain.play.guards import drive_useful

        drive = next((c for n, c in by_name.items() if n.rstrip("+") == DRIVE.rstrip("+")), None)
        starved = [n for n, c in observed.items() if (not c.available or c in t.idol_starved)
                   and n.rstrip("+") in (IDOL_CARD, REBOUND.rstrip("+"))
                   and fp < (fp_delta(c)[1] or (FP_RESERVE_AT if n.rstrip("+") == IDOL_CARD else 1))]
        if drive and starved and not any(fp_delta(c)[0] for c in eligible) and affordable(drive) \
                and drive_useful(drive, [observed[n] for n in starved], fp, current, hand):
            return t.play(drive, f"清牌：全力値 {fp} 打不了 {'、'.join(starved)}，オーバードライブ 是唯一来源，先攒全力値")
    playable = list(eligible)  # 体力闸过滤前：被闸暂时压住的一次性牌（切回温存后再打）也算后面有牌可接
    eligible, why = gates.filter(eligible)
    if why:
        return t.skip(why)
    if not eligible:
        if (d := resources.stuck(t, playable)) is not None:
            return d
        return t.skip("清牌：没有允许打出的已识别牌")

    clears, gains_now, priority, unlocks = score.clears, score.gains_now, score.priority, score.unlocks
    immediate_draw = score.immediate_draw
    if score.keep:
        eligible = [c for c in eligible if clears(c) or score.reusable_ok(c)]
        if not eligible:
            return t.skip("清牌：只剩复用牌，打出会用掉温存或可留到下回合的再动")

    # Preserve a later play for ひと呼吸 whenever another legal card is available.
    preferred = [c for c in eligible if norm(c.name).rstrip("+") not in PLAY_LATER]
    if clear_first() and not any(clears(c) for c in preferred):
        preferred = []  # ひと呼吸 只让给别的一次性牌：剩下的全是复用牌时它照常参与排序（一次性优先）
    best = max(preferred or eligible, key=priority)
    good_first = None
    start = current if current is not None else (NEUTRAL if memory.turn == 1 and not played else None)
    if clear_first() and start is not None and 2 <= len(eligible) <= 5:
        # 使用者 2026-11-07（1-g R2 再挑戦 剩 10）：のんびり 开局 第一印象（強気、解除+1）→ 達成感（温存、+1）→
        # 全身全霊（温存→強気 解除+1、+2）比实际 達成感 → 第一印象 → 全身全霊 多 1 次出牌。贪心一张张比、DRAW_LAST 只比两张
        # 的先后都看不出 → 枚举可打牌的出牌顺序，按姿态 / 夜空推演，取「打出张数、剩余次数」最多的顺序；best 开头的顺序
        # 一样好时不换。抽牌带来的新牌、体力不算（体力闸已在 eligible 里筛过）
        warm_already = warm_next_turn(state, memory)
        scored = planner.scored(eligible, lambda o: score.order_score(o, start, warm_already, last_turn))
        # 前四项（清掉的一次性牌、剩余次数、少打复用牌、抽牌作废）严格更好才换；打平时不换，只把后面 DRAW_LAST 的换牌限制在
        # 最优顺序的开头里。抽得少的先打只在最优开头之间挑
        top4 = max(v[:4] for v, _ in scored)
        good = [(v, o) for v, o in scored if v[:4] == top4]
        good_first = {id(o[0]) for _, o in good}
        from_best = max((v[:4] for v, o in scored if o[0] is best), default=None)
        if from_best is not None and top4 > from_best:
            pick = max(good, key=lambda vo: (vo[0], priority(vo[1][0])))[1][0]
            why = ("多清 %d 张一次性牌" % (top4[0] - from_best[0]) if top4[0] > from_best[0] else
                   "多留 %d 次出牌" % (top4[1] - from_best[1]) if top4[1] > from_best[1] else
                   "少打复用牌" if top4[2] > from_best[2] else "抽牌少作废 %d 张" % (top4[3] - from_best[3]))
            return t.play(pick, f"清牌：按出牌顺序推演先打它（{why}）")
    late = next((c for c in eligible if norm(c.name).rstrip("+") in PLAY_LATER), None)
    if late and count == 1 and gains_now(late) > gains_now(best) and WHEY not in drinks:
        best = late
    if clear_first() and not last_turn and eligible and not any(clears(c) for c in playable):
        # 1-g R2 再挑戦 剩 10：全力値 0、巻き返し+ 灰着，手里只剩复用牌 エンターテイナー+ 也打了（切強気用掉温存，
        # 加的次数没有一次性牌可接）。清牌阶段可打的只剩复用牌时不打，温存留到下回合
        if strong_teach() and current not in WARM and flower in (2, 3) and TEACH in by_name and TEACH not in played:
            # 不在温存：没有温存可留。教えて 推夜空、预约下回合多抽 2（VEIL_STRONG_TEACH；1-c R2 attempt1 剩 8、7 连跳）
            return t.play(by_name[TEACH], "清牌：只剩复用牌、不在温存，打 教えてあげる+ 推夜空并预约抽牌")
        if stance_unlock():
            unified = sim_unified()
            for name, need in (("一心不乱", ("強気",)), ("ガタゴトすやすや", tuple(WARM))):
                locked = stance_cards.get(name)
                if locked is None or current in need or not locked.tags.once:
                    continue
                for c in sorted(eligible, key=priority, reverse=True):
                    to, _ = after_play(current, flower, c)
                    left = count - 1 + (gain(c, current, flower, ability=score.ability_kind(c)) if unified
                                        else gain(c, current, flower, mode="after"))
                    if to in need and left >= 1 and affordable(c) and not swaps_hand(c):
                        return t.play(c, f"清牌：先切到{to}，下一手清掉发灰的 {norm(locked.name)}")
        grey = [n for n, c in observed.items() if not c.available and clears(c)]
        return t.skip("清牌：可打的只剩复用牌" + (f"（{'、'.join(grey)} 发灰）" if grey else "") + "，不打、温存留到下回合")
    if count > len(eligible) and not unlocks(best):
        # 换掉整手牌的（仕切り直し+）只在手里没有别的可打牌时才先打，否则别的牌被换回牌库
        draw_card = next((c for c in sorted(eligible, key=priority, reverse=True) if immediate_draw(c)
                          and (not clear_first() or not swaps_hand(c) or len(eligible) == 1)), None)
        if draw_card:
            return t.play(draw_card, "清牌：额外行动优先用技能卡抽牌")
        if (d := resources.short_of_cards(t)) is not None:
            return d
    if (d := draw_last_swap(score, eligible, best, good_first, state, current, flower, clears, t)) is not None:
        return d
    release_first = release_gain()
    if release_first and count == 1 and len(eligible) > 1 and not gains_now(best):
        gainer = max((c for c in eligible if gains_now(c)), key=priority, default=None)
        # 一次性优先（CLEAR_FIRST / KEEP_WARM）时，best 是一次性牌、又没有ホエイ 要省：不拿复用牌（切強気用掉温存）插队
        if gainer is not None and (score.keep or clear_first()) and clears(best) and not clears(gainer) and WHEY not in drinks:
            gainer = None
        if gainer is not None:
            return t.play(gainer, "清牌：先打能加出牌次数的牌，不用ホエイ")
    if count == 1 and (len(eligible) > 1 or unlocks(best)) and not gains_now(best):
        if (d := resources.short_of_actions(t)) is not None:
            return d
    return t.play(best, "清牌")


def draw_last_swap(score, eligible, best, good_first, state, current, flower, clears, t):
    """VEIL_DRAW_LAST：best 会立即抽牌、又加出牌次数时，换成「不抽牌、自己也加出牌次数」的牌先打。"""
    from hiro.brain.play.profile import HAND_MAX, draw_last

    gains_now, immediate_draw, priority = score.gains_now, score.immediate_draw, score.priority
    if not (draw_last() and immediate_draw(best) and gains_now(best) > 0):
        return None
    # 换成「不抽牌、自己也加出牌次数」的牌先打：出牌次数不减少，手牌先少一张，best 抽的牌不会因手牌满作废。
    # 两种先后的总加次数（含姿态切换带来的温存解除）要不少于原顺序；姿态读不准（None）时不换。
    # 验证局 R2 剩 11 回合：のんびり、满手，オーバードライブ（再动、切強気）应先于 フルスロットル+（再动、抽 3）

    def total(first, second):
        g1 = score.gains_at(first, current, flower)
        cur2, fl2 = after_play(current, flower, first)
        g2 = score.gains_at(second, cur2, fl2)
        return None if g1 is None or g2 is None else g1 + g2

    # 一次性优先（CLEAR_FIRST）时，复用牌只在 best 抽的牌会溢出手牌（>5）时才拿来垫（1-f R2 T1：手牌 4 张，
    # インフルエンサー 只抽 1 张不会溢出，却先打了 教えてあげる+ 用掉温存）
    overflow = len(state.hand) - 1 + max(best.tags.draws or 0, int(score.ability_kind(best) == "draw")) > HAND_MAX
    plain = []
    for c in eligible:
        if c is best or immediate_draw(c) or not gains_now(c):
            continue
        if good_first is not None and id(c) not in good_first:
            continue  # 出牌顺序推演里它开头不是最优
        if clear_first() and not clears(c) and not overflow:
            continue
        c_first, b_first = total(c, best), total(best, c)
        if c_first is not None and b_first is not None and c_first >= b_first:
            plain.append(c)
    if plain:
        # 优先不改姿态的（みずたまりスキップ）：切強気会让后面的牌消耗翻倍
        pick = max(plain, key=lambda c: (score.resulting_stance(c) == current, priority(c)))
        return t.play(pick, "清牌：先打不抽牌的再动牌，抽牌的留后")
    if overflow and clear_first():
        # 1-g R2 再挑戦 T1：手牌 5 张，先打了 全身全霊（再动、抽 3，溢出 2 张），インフルエンサー（再动、抽 1）不溢出却留后。
        # 抽得少、打完手牌不溢出的一次性再动牌先打（两种先后总加次数不少于原顺序）
        fit = []
        for c in eligible:
            if c is best or not immediate_draw(c) or not gains_now(c) or not clears(c):
                continue
            if good_first is not None and id(c) not in good_first:
                continue
            if len(state.hand) - 1 + max(c.tags.draws or 0, 1) > HAND_MAX:
                continue
            c_first, b_first = total(c, best), total(best, c)
            if c_first is not None and b_first is not None and c_first >= b_first:
                fit.append(c)
        if fit:
            return t.play(max(fit, key=priority), "清牌：先打抽得少、手牌不溢出的再动牌")
    return None
