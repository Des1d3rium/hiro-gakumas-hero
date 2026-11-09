"""循环（play 第 3 块）：进入循环的判定、循环回合的执行、全力过渡、进循环规划、过渡，以及循环顺序的求解。

「循环」= 牌库压缩到一组复用牌，存在一个出牌顺序，打完后下回合开局的状态（姿态、P 道具计数）和本回合开局一样。
VEIL 的顺序用使用者给的 profile.ORDERS（实机验证过）；solve_orders 在 sim 上把所有满足条件的顺序找出来，
用来校验给定顺序、也给别的偶像起草顺序（选哪一个还要靠 objectives 的得分规则）。
"""

from __future__ import annotations

from collections import Counter

from hiro.brain.play import planner
from hiro.brain.play.guards import drive_useful, harmless
from hiro.brain.play.objectives import ability_of_card, cycle_entry_score
from hiro.brain.play.profile import (
    CORE, DRIVE, ENTERTAINER, FP_RESERVE_AT, FULL_POWER_AT, IDOL_CARD, ORDERS, PLAN_MAX,
    REBOUND, SODA, STAR, TEACH, WHEY, clear_first, cycle_plan, draw_all, drive_fp, hp_floor,
    hp_guard, sim_unified, draw_prob, full_plan,
)
from hiro.brain.play.sim import (
    WARM, TurnState, after_play, cost_mods, destination, fp_delta, gain, genki_gain, hp_loss, _master_text,
    next_stance, norm, stance_requirement, swaps_hand, warm_next_turn,
)
from hiro.loop.errors import ResumeBlocked

PHASE = "cycle"  # Decision.plan[0]["phase"]：循环出牌（exec.cycle_tap 按它连打）


# ---------------------------------------------------------------- 判定

def start(t) -> None:
    """本回合开头满足循环条件（牌库已压缩、在温存、核心 4 张都在手、夜空 1 / 2）就记下本回合的循环。"""
    state, memory = t.state, t.memory
    if (not t.cycle or t.cycle["turn"] != state.turn) and not t.played and t.compact and t.current in WARM and CORE <= t.hand:
        if t.flower in ORDERS and len(state.hand) in (4, 5):
            t.cycle = {"turn": state.turn, "flower": t.flower, "fifth": next(iter(t.hand - CORE), None)}
            memory.strategy_data["veil_cycle"] = t.cycle


def active(t) -> bool:
    return bool(t.cycle and t.cycle["turn"] == t.state.turn)


# ---------------------------------------------------------------- 全力过渡

def full_power(t):
    """使用者 2026-11-07（1-h R2 剩 2）：循环里全力値满 10 进全力，切不了姿态 → 多过渡一回合再回循环。
    夜空 2：教えてあげる+ → 随便一张 OD 以外的牌（推荐眠気）→ 结束；夜空 1：OD → 教えてあげる+ → 眠気 → 结束。
    打完夜空 2→1、1→2，下回合按新的计数回到循环"""
    state, memory = t.state, t.memory
    keep = full_plan()
    full = memory.strategy_data.get("veil_full")
    started = keep and bool(full) and full.get("turn") == state.turn and full.get("flower") in (1, 2)
    if not (clear_first() and t.current == "全力" and (t.compact or started) and state.remaining_turns not in (None, 1)):
        return None
    by_name, played = t.by_name, t.played
    if not full or full.get("turn") != state.turn:
        full = memory.strategy_data["veil_full"] = {"turn": state.turn, "flower": t.flower}
    if full["flower"] not in (1, 2):
        return None
    plan = ([DRIVE] if full["flower"] == 1 else []) + [TEACH, None]
    done = full_plan_done(plan, played) if keep else len(played)
    if done < len(plan):
        want = plan[done]
        if want is None:
            filler = by_name.get("眠気") or next(
                (c for n, c in by_name.items() if n != DRIVE and n not in CORE), None) or next(
                (c for n, c in by_name.items() if n != DRIVE), None)
            if filler is not None:
                return t.play(filler, f"全力过渡（夜空 {full['flower']}）：打一张 OD 以外的牌收尾")
        elif want in by_name:
            return t.play(by_name[want], f"全力过渡（夜空 {full['flower']}）")
        else:
            return t.skip(f"全力过渡（夜空 {full['flower']}）：{want} 不在手里或打不了，结束回合")
    return t.skip(f"全力过渡（夜空 {full['flower']}）：打完，结束回合，下回合回到循环")


def full_plan_done(plan, played) -> int:
    """全力过渡规划里已经打完几步（VEIL_FULL_PLAN）：按出牌记录逐步对名字，规划外的牌（被别的阶段打掉的）不算一步；
    None 步（收尾：OD 以外随便一张）在前面各步都打完之后、任何一张非 OD 的牌都算。"""
    done = 0
    for name in played:
        if done >= len(plan):
            break
        want = plan[done]
        if want == name or want is None and name != DRIVE:
            done += 1
    return done


# ---------------------------------------------------------------- 循环回合

def execute(t, extra_turn_ok):
    """循环回合：先喝饮料，再按 ORDERS[夜空] 打核心 4 张，最后补第五张。"""
    from hiro.brain.play.resources import cycle_drink

    state, memory, cycle = t.state, t.memory, t.cycle
    by_name, observed, hand, played = t.by_name, t.observed, t.hand, t.played
    order = ORDERS[cycle["flower"]]
    if (d := cycle_drink(t, extra_turn_ok)) is not None:
        return d
    tag = {"phase": PHASE, "plan": order}
    # 第五张是アイドルになります+：它要求手里至少两张 エンターテイナー+／オーバードライブ+／教えてあげる+，
    # 放到最后时三张都已打出、永远打不了。改为循环第一张（温存に変更、+1 出牌次数，不动夜空计数，循环条件不变）。
    idol = next((n for n in (cycle.get("fifth"),) if n and n.rstrip("+") == IDOL_CARD), None)
    if idol and not played and idol in by_name and len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2:
        cycle["idol_first"] = True
        return t.play(by_name[idol], "五卡循环：アイドルになります+ 先打（打出条件要核心牌在手）", **tag)
    if cycle.get("idol_first") and played and played[0] == idol:
        played = played[1:]
    if played[:len(order)] != list(order[:len(played)]):
        raise ResumeBlocked("VEIL 循环出牌记录和预定顺序不一致")
    # 上一张打出后又喝过饮料（センブリソーダ 抽 2）：牌堆薄时洗牌会把刚打出的牌抽回手里，手里这张是新抽的，不是没打出
    #（codex-20261109-1-b R1 T6：OD → ソーダ 抽回 OD，原来在这里停机）
    drew_since = bool(memory.this_turn) and not memory.this_turn[-1].startswith("打 ")
    if played and played[-1] in hand and not drew_since:
        raise ResumeBlocked(f"VEIL 循环上一张牌仍在手中，无法确认已打出：{played[-1]}")
    if len(played) < len(order):
        name = order[len(played)]
        if name not in by_name and name in observed:
            # 在手里但发灰（多半是全力値不够：巻き返し+ 消費1）：跳过本回合，不停机（验证局 1-c R2 剩 3 回合停在这里）
            return t.skip(f"循环：下一张 {name} 在手里但打不出，本回合跳过")
        if name not in by_name:
            raise ResumeBlocked(f"VEIL 循环下一张牌不可用：{name}")
        # 循环回合一律按顺序打完。「温存下最后一张复用牌不打、温存留到下回合」只属于清牌阶段（使用者 2026-11-08：
        # 「这个是清理牌库阶段才会有的规则」；1-g R1 剩 4 アイドル 先打的循环回合因此少打一张 エンターテイナー+）
        return t.play(by_name[name], "五卡循环", **tag)
    if len(played) == len(order) and cycle.get("idol_first"):
        return t.skip("本回合循环完成（アイドルになります+ 已先打）")
    if len(played) == len(order):
        once = set((state.piles or {}).get("once", []))
        extra = next((name for name, c in observed.items() if name not in CORE
                      and (c.tags.once or c.tags.is_sleep or name in once)), None)
        fifth = extra or cycle.get("fifth")
        if fifth and fifth.rstrip("+") == IDOL_CARD and len({ENTERTAINER, DRIVE, TEACH} & hand) < 2:
            return t.skip("循环：第五张受アイドルになります+禁打条件限制")
        if fifth:
            fp_now = memory.strategy_data.get("veil_fp")
            if (observed.get(fifth) in t.idol_starved
                    or (fifth not in by_name and fifth in observed and fp_now is not None
                        and fp_now < (fp_delta(observed[fifth])[1] or 0))):
                # 验证局 1-d R2 再挑戦 剩 4：第五张 アンダンテ+（全力値消費2），OD +2 后 巻き返し+ 花 1，剩 1 打不出。
                # 发灰能用账本解释（全力値不够）才结束回合；别的原因发灰（读错、条件牌）照旧停机
                return t.skip(f"循环：第五张 {fifth} 全力値不够（账本 {fp_now}），本回合结束")
            if fifth not in by_name:
                raise ResumeBlocked(f"VEIL 循环第五张尚未打出，当前不可用：{fifth}")
            return t.play(by_name[fifth], "五卡循环收尾", **tag)
    return t.skip("本回合循环完成")


# ---------------------------------------------------------------- 进循环规划

def plan_cycle(state, memory, hand_cards, actions, drinks, current, flower, count, played):
    """VEIL_CYCLE_PLAN：枚举本回合出牌顺序（手牌 + 最多一瓶ホエイ），找「打完后下回合开局就能进五卡循环」的顺序。
    返回 (第一步, 整个顺序的名字)，第一步是 CardView / WHEY / None（什么都不用打）；找不到返回 None（交给原来的规则）。

    推演（sim.TurnState）：出牌次数（再动、温存→強気 的解除）、姿态 / 夜空、全力値（到 10 进全力 → 不要）、体力（体力闸）。
    条件：剩下的牌库是核心 4 张（+ 至多 1 张一次性牌 / 眠気，OD 生成的眠気算进去）；回合末在温存 / のんびり，或下回合开局会自动进温存；
    夜空 1 / 2；下回合抽的张数不少于牌库。打法依据：prompts/游戏接手提示词.md「过渡」「循环」。
    只在牌库快压缩完（手牌外最多 1 张非核心牌）时推演；抽到的新牌不算（拿不准是什么）。"""
    rem = state.remaining_turns
    if rem is None or rem < 2 or current in (None, "全力") or flower not in (1, 2, 3):
        return None
    piles = state.piles
    if piles is None or not all(isinstance(piles.get(k), list) for k in ("deck", "grave")):
        return None
    if len(hand_cards) != len(state.hand):
        return None
    once_pile = {norm(c) for c in piles.get("once") or []}
    if REBOUND in once_pile:
        return None
    hand_names = Counter(norm(c.name) for c in hand_cards)
    allc = hand_names + Counter(norm(c) for k in ("deck", "grave", "unlocated") for c in piles.get(k) or [])
    if any(allc[c] != 1 for c in CORE):
        return None
    once_known = once_pile | {norm(c.name) for c in hand_cards if c.tags.once} | set(memory.strategy_data.get("veil_known_once") or ())
    outside = (allc - Counter(CORE)) - hand_names
    if outside.total() > 1 or any(n not in once_known | {"眠気"} for n in outside):
        return None
    fp0 = memory.strategy_data.get("veil_fp")
    mods = cost_mods(memory.strategy_data.get("veil_log"), _master_text)
    warm0 = warm_next_turn(state, memory)
    draw_extra = (piles.get("next_draw") or 3) - 3 - 2 * (TEACH in played)
    floor = hp_floor()
    unified = sim_unified()

    def stance_ok(c, cur):
        need_stance = stance_requirement(c)
        return not ((need_stance == "温存" and cur not in WARM) or (need_stance == "強気" and cur != "強気"))

    def usable(c, cur, fpv):
        if not stance_ok(c, cur):
            return False
        need = fp_delta(c)[1]
        if need and (fpv is None or fpv < need):
            return False
        if f"play:{c.slot}" in actions:
            return True
        # 现在打不了：只认「现在的姿态 / 全力値不满足牌面条件」这种本回合会变的原因；条件现在已满足还打不了的（体力、读错…）不算
        if c.tags.genki_min is not None:
            return False
        return bool(not stance_ok(c, current) or (need and (fp0 is None or fp0 < need)))

    def run(seq):
        ts = TurnState(stance=current, flower=flower, acts=count, fp=fp0)
        spent = genki = 0
        cleared, sleeps, teach, warm, bad_next, swapped = Counter(), 0, TEACH in played, warm0, False, False
        for item in seq:
            if ts.acts < 1 or swapped:
                return None
            if item == WHEY:
                ts = ts.drink()
                continue
            c = item
            if not usable(c, ts.stance, ts.fp):
                return None
            spent += hp_loss(state, c, ts.stance, mods, spent=True)
            genki += genki_gain(c)
            ts = ts.step(c, ability=ability_of_card(memory, c) if unified else None, boosts=unified)
            if ts.fp is not None and ts.fp >= FULL_POWER_AT:
                return None
            if ts.stance is None:
                return None
            name = norm(c.name)
            nxt = next_stance(c)
            if nxt in WARM or name.startswith(STAR):
                warm = True
            elif nxt is not None:
                bad_next = True
            if c.tags.once or c.tags.is_sleep or name in once_known:
                cleared[name] += 1
            sleeps += int(c.tags.makes_sleep)
            teach = teach or name == TEACH
            swapped = swaps_hand(c)
        return cycle_entry_score(state, ts, allc, cleared, sleeps, teach, warm, bad_next, once_known, draw_extra,
                                 spent, genki, floor, len(seq))

    items = list(hand_cards) + ([WHEY] if WHEY in drinks else [])
    top = planner.best(items, run, max_len=PLAN_MAX, prefixes=True)
    if top is None:
        return None
    seq = top[1]
    names = [x if x == WHEY else norm(x.name) for x in seq]
    return (seq[0] if seq else None), names


def enter(t):
    """VEIL_CYCLE_PLAN：本回合能把局面推到「下回合开局就进循环」时按推演的顺序打。"""
    if not cycle_plan() or active(t):
        return None
    planned = plan_cycle(t.state, t.memory, t.hand_cards, t.actions, t.drinks, t.current, t.flower, t.count, t.played)
    if planned is None:
        return None
    first, names = planned
    why = "→".join(names) if names else "不用再打"
    if first is None:
        return t.skip(f"进循环规划：下回合开局就能进循环（{why}）")
    if first == WHEY:
        return t.act(t.drinks[WHEY], f"进循环规划：{why}")
    return t.play(first, f"进循环规划：{why}")


# ---------------------------------------------------------------- 过渡

def core_draw_odds(t):
    """下回合开局核心 4 张都在手的概率：(不预约, 预约抽牌 +2)。回合末留在手里的复用牌进捨札；一次性牌按会清掉不算，
    核心牌除外（巻き返し+ 分了版本，手里这张的一次性标记不一定对；过渡不清核心牌，1-d R1 T8）。没有牌堆账时 None。"""
    from hiro.brain.play.draws import next_turn

    piles = t.state.piles
    if not piles:
        return None
    hand_end = [norm(c.name) for c in t.state.hand if c.card_id and (not c.tags.once or norm(c.name) in CORE)]
    n = int(piles.get("next_draw") or 3)
    now = next_turn(piles, hand_end, sorted(CORE), draws=n)
    reserved = next_turn(piles, hand_end, sorted(CORE), draws=n + 2)
    return None if now is None or reserved is None else (now, reserved)


def transition(t, eligible):
    """牌库已压缩、还没进循环：凑齐下回合开局进循环的三个条件——夜空 1 / 2（need_flower）、下回合抽满（need_draw）、
    下回合开局在温存（need_warm），顺手清掉不碍事的一次性牌。"""
    state, memory = t.state, t.memory
    by_name, observed, hand, drinks = t.by_name, t.observed, t.hand, t.drinks
    current, flower, count, played = t.current, t.flower, t.count, t.played
    unified = sim_unified()
    if flower is None and not played:
        raise ResumeBlocked("VEIL 已进入过渡，但夜空を従える花+剩余次数读不出")
    warm_next = warm_next_turn(state, memory)
    # Recompute all three needs on each frame. Teaching can cover both
    # the flower and draw, and can change the stance we need to restore.
    need_flower = flower == 3
    need_draw = state.piles.get("next_draw") not in (4, 5) and TEACH not in played
    if draw_all() and TEACH not in played:
        # 牌库张数（核心 4 + 第五张）：下回合抽不满就预约（VEIL_DRAW_ALL）
        total = len(state.hand) + sum(len(state.piles.get(k) or []) for k in ("deck", "grave", "unlocated"))
        need_draw = (state.piles.get("next_draw") or 3) < total
    draw_note = ""
    if draw_prob() and TEACH not in played and (odds := core_draw_odds(t)) is not None:
        now, reserved = odds
        need_draw = reserved.lo > now.lo + 0.01
        draw_note = f"（下回合核心齐手 {now} → 预约后 {reserved}）"
    if clear_first() and SODA in drinks:
        # 使用者 2026-11-07：过渡不喝ソーダ（当场抽的 2 张用不上、下回合只多抽 1）；留到下回合开局喝，抽 2 补齐核心
        need_draw = False
    need_warm = not warm_next and current not in WARM
    teach = by_name.get(TEACH) if TEACH not in played else None
    fp = memory.strategy_data.get("veil_fp")
    drive = by_name.get(DRIVE) if DRIVE not in played else None
    rebound = observed.get(REBOUND)
    idol = next((c for n, c in observed.items() if n.rstrip("+") == IDOL_CARD), None)
    # 过渡里 オーバードライブ 优先级很低（生成眠気，使用者 2026-11-07）：只在 アイドルになります+ 因全力値不够打不出，
    # 或 巻き返し+ 因全力値发灰、下回合又不会自动温存（一番星用完）时打（1-g R1 剩 5→1：切不回温存，五卡循环一直进不去）
    starved = [c for c in (idol,) if c is not None and (not c.available or c in t.idol_starved) and fp is not None
               and fp < (fp_delta(c)[1] or FP_RESERVE_AT)]
    if need_warm and rebound is not None and REBOUND not in by_name and fp is not None \
            and fp < (fp_delta(rebound)[1] or 1):
        starved.append(rebound)
    if drive_fp() and drive and starved and drive_useful(drive, starved, fp, current, hand):
        names = "、".join(norm(c.name) for c in starved)
        return t.play(drive, f"过渡：全力値 {fp} 打不了 {names}，先打 オーバードライブ 攒全力値")
    mods = cost_mods(memory.strategy_data.get("veil_log"), _master_text) if hp_guard() else None
    last_turn = state.remaining_turns is not None and state.remaining_turns <= 1

    def is_harmless(c):
        return harmless(state, c, current, mods, last_turn)

    if clear_first():
        # 1-g R2 剩 6：フルスロットル+（温存に変更、再动、抽 3、生成眠気）不占次数，先打，再把眠気清掉就是四卡；
        # 原来先打 教えてあげる+ 切強気、带着它 Skip，剩 3 才进循环
        free = next((c for c in eligible if c.tags.extra_actions and is_harmless(c)), None)
        if free:
            return t.play(free, "过渡：先打不占次数的一次性牌")

    def shed_card():
        # 只生成眠気、不补次数的不打（清掉一张又塞回一张）
        return next((c for c in eligible if is_harmless(c) and not (c.tags.makes_sleep and not c.tags.extra_actions)), None)

    def after(c, acts, cur, fl):
        """过渡顺序里打出 c 之后的出牌次数（重构前不算再动，只扣 1；VEIL_SIM_UNIFIED 按 sim.gain 算）。"""
        if unified:
            return acts + gain(c, cur, fl, ability=ability_of_card(memory, c)) - 1
        return acts - 1

    def cycle_reachable():
        """按过渡顺序（教えて / 推夜空 → 切回温存）模拟本回合：下回合开局能否温存、夜空 1/2、出牌次数够不够。"""
        acts, cur, fl = count, current, flower
        if need_flower or need_draw:
            c = teach if teach else (next((c for c in eligible if destination(c) == "強気"), None)
                                     if not need_draw else None)
            if c is None or cur is None:
                return False
            acts = after(c, acts, cur, fl)
            cur, fl = after_play(cur, fl, c)
        if fl not in ORDERS:
            return False
        if cur not in WARM and not warm_next:
            if REBOUND not in by_name or REBOUND in played:
                return False
            acts = after(by_name[REBOUND], acts, cur, fl)
        return acts >= 0

    if clear_first() and not cycle_reachable() and (shed := shed_card()):
        # 使用者 2026-11-07：下回合能进循环是第一优先；预判下回合仍是过渡（次数不够 / 夜空回到 3 / 切不回温存）时，
        # 过渡牌白打，先清一次性牌
        return t.play(shed, "过渡：下回合仍进不了循环，先清一次性牌")

    def warm_back(c):
        """VEIL_CYCLE_PLAN：打完 c 后本回合还能回到温存（或下回合开局自动进温存）。推了夜空切不回温存，
        下回合开局在強気，这回合的过渡全白做（3-3 第 3 局 R2 剩 5：教えて 后又打 エンタ，T9–T11 一直过渡）。"""
        cur, _ = after_play(current, flower, c)
        if cur in WARM or warm_next or next_stance(c) in WARM:
            return True
        if memory.strategy_data.get("veil_star") != 0:
            return True  # 一番星次数不明（状态栏常读不出名字）：可能下回合自动のんびり，不拦（3-b 第 1 局 R1 attempt1 剩 4）
        left = count - 1 + (gain(c, current, flower, ability=ability_of_card(memory, c)) if unified
                            else gain(c, current, flower, mode="dest"))
        return REBOUND in by_name and REBOUND not in played and c is not by_name[REBOUND] and left >= 1

    if teach and (need_flower or need_draw):
        if cycle_plan() and need_flower and not warm_back(teach):
            return t.skip("过渡：推夜空后切不回温存，留到下回合")
        return t.play(teach, ("过渡：推进夜空计数／预约抽牌" if need_flower else "过渡：预约下回合抽两张") + draw_note)
    if need_flower:
        strong = next((c for c in eligible if destination(c) == "強気"), None)
        if strong and cycle_plan() and not warm_back(strong):
            return t.skip("过渡：推夜空后切不回温存，留到下回合")
        if strong:
            return t.play(strong, "过渡：推进夜空计数")
    if need_warm and current is None and not played:
        raise ResumeBlocked("VEIL 过渡无法确认当前姿态或下回合切换")
    if need_warm and REBOUND in by_name and REBOUND not in played:
        return t.play(by_name[REBOUND], "过渡：切回温存")
    if clear_first():
        # 使用者 2026-11-07（1-g R1）：过渡尽量从五卡缩到四卡。过渡要打的（教えて / 推花 / 切温存）都打完、本来要等下回合时，
        # 用剩下的出牌次数清掉不碍事的一次性牌（ひと呼吸+、アイドルになります+、眠気 等）。花全力値可以：循环第一张
        # オーバードライブ 先加 2。只生成眠気、不补次数的不打（清掉一张又塞回一张）。
        shed = shed_card()
        if shed:
            return t.play(shed, "过渡：清掉不碍事的一次性牌，五卡缩成四卡")
    return t.skip("过渡：等待下回合")


# ---------------------------------------------------------------- 循环顺序求解

def solve_orders(cards, *, start_stance="温存", flowers=(1, 2), fp=None, counter_of=None):
    """在 sim 上找所有「打完后下回合开局回到同一状态」的顺序：{夜空: [(剩余出牌次数, 顺序名字), …]}，剩余次数多的在前。
    同一状态 = 下回合开局姿态（算上预约姿态、一番星这类回合开始附魔不算）是温存 / のんびり，夜空和本回合开局相同；
    每张都要有出牌次数、全力値够消費。只判可行，不判得分：选哪一个交给得分规则（VEIL 用 profile.ORDERS）。"""
    out = {}
    for f in flowers:
        found = []
        for order in planner.orders(cards):
            ts = TurnState(stance=start_stance, flower=f, acts=1, fp=fp)
            ok = True
            for c in order:
                need = fp_delta(c)[1]
                if ts.acts < 1 or (need and ts.fp is not None and ts.fp < need):
                    ok = False
                    break
                ts = ts.step(c)
            if not ok:
                continue
            nxt = ts.next_stance if ts.next_stance and ts.stance != "全力" else ts.stance
            if nxt in WARM and ts.flower == f:
                found.append((ts.acts, tuple(norm(c.name) for c in order)))
        found.sort(key=lambda x: -x[0])
        out[f] = found
    return out

