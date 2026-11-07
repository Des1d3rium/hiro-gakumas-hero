"""Opt-in H.I.F finals strategy for [VEIL] 秦谷美玲."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from hiro.contracts import Decision
from hiro.loop.errors import ResumeBlocked

IDOL = "i_card-skin-hmsz-3-016"
ENTERTAINER, DRIVE, TEACH, REBOUND = "エンターテイナー+", "オーバードライブ+", "教えてあげる+", "巻き返し+"
CORE = frozenset((ENTERTAINER, DRIVE, TEACH, REBOUND))
BOOST, SODA, WHEY = "ブーストエキス", "センブリソーダ", "初星ホエイプロテイン"
WARM = {"温存", "温存1段階目", "温存2段階目", "のんびり"}
STANCES = ("温存", "温存1段階目", "温存2段階目", "のんびり", "強気", "全力", "指針解除")
ORDERS = {2: (DRIVE, REBOUND, TEACH, ENTERTAINER), 1: (DRIVE, TEACH, REBOUND, ENTERTAINER)}
LIMITS = {"r1": 700_000, "r2": 1_400_000}


def norm(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def stance(state):
    names = {norm(s.get("name") or "") for s in state.statuses or []}
    return next((name for name in STANCES if name in names), None)


_STANCE_CHANGE = re.compile(r"(強気|温存(?:[12]段階目)?|のんびり|全力)に変更|指針を解除")
_DELAYED = re.compile(r"次のターン|ターン開始|以降")


def destination(card):
    immediate = _DELAYED.split(norm(card.text), maxsplit=1)[0]
    match = _STANCE_CHANGE.search(immediate)
    return (match[1] or "指針解除") if match else None


def next_stance(card):
    match = re.search(r"(?:次のターン(?:開始時)?|ターン開始時)[、,に：:]*(強気|温存(?:[12]段階目)?|のんびり|全力)に変更",
                      norm(card.text))
    return match[1] if match else None


def after_play(current, flower, card):
    """Resolve the card first, then the flower's overriding stance effect."""
    dest = destination(card)
    if dest != "強気" or current == "指針解除":
        return dest or current, flower
    if current is None or flower not in (1, 2, 3):
        return None, None
    return ("のんびり", 3) if flower == 1 else (dest, flower - 1)


def flower_count(img):
    """Read the digit beside VEIL's flower ring, not the generic P-item badge."""
    from hiro.vision.digits import read_number

    if img is None or img.shape[:2] != (1280, 720):
        return None
    a = read_number(img, (596, 263, 617, 291))
    b = read_number(img, (599, 266, 615, 289))
    return a if a in (1, 2, 3) and a == b else None


def small_deck(state):
    if state.piles is None or not all(isinstance(state.piles.get(k), list) for k in ("deck", "grave")):
        return False
    hand = [norm(c.name) for c in state.hand if c.card_id]
    if len(hand) != len(state.hand):
        return False
    names = Counter(hand + [norm(c) for k in ("deck", "grave", "unlocated") for c in state.piles.get(k, [])])
    if REBOUND in state.piles.get("once", []):
        return False
    if any(names[c] != 1 for c in CORE) or names.total() not in (4, 5):
        return False
    extra = names - Counter(CORE)
    once = {norm(c) for c in state.piles.get("once", [])} | {norm(c.name) for c in state.hand if c.tags.once}
    return not extra or next(iter(extra)) in once | {"眠気"}


class VeilAdvisor:
    def on_round_start(self, round_no, context):
        pass

    def on_round_end(self, result):
        pass

    def decide(self, state, memory, previews, legal):
        if state.exam not in LIMITS:
            raise ResumeBlocked("VEIL 策略仅用于 H.I.F 本战 R1/R2")
        actions = {a.legal_key(): a for a in legal}

        def act(key, reason):
            if key not in actions:
                raise ResumeBlocked(f"VEIL 所需动作不可用：{reason}")
            return Decision(actions[key], reason, "rule", decider="veil")

        def play(card, reason):
            return act(f"play:{card.slot}", f"{reason}：{card.name}")

        def skip(reason):
            return act("skip", reason)

        score = state.score if state.score is not None else memory.last_score
        if score is not None and score > LIMITS[state.exam]:
            return skip("超过本轮停手阈值")

        observed = {norm(c.name): c for c in state.hand if c.card_id}
        by_name = {name: c for name, c in observed.items() if f"play:{c.slot}" in actions}
        hand = observed.keys()
        drinks = {d.name: f"drink:{d.slot}" for d in state.drinks if f"drink:{d.slot}" in actions}
        current = stance(state)
        flower = state.pitem_charges
        count = max(1, state.actions_left or 1)
        played = [norm(a[2:]) for a in memory.this_turn if a.startswith("打 ")]
        compact = small_deck(state)
        cycle = memory.strategy_data.get("veil_cycle")
        if (not cycle or cycle["turn"] != state.turn) and not played and compact and current in WARM and CORE <= hand:
            if flower in ORDERS and len(state.hand) in (4, 5):
                cycle = {"turn": state.turn, "flower": flower, "fifth": next(iter(hand - CORE), None)}
                memory.strategy_data["veil_cycle"] = cycle
        if cycle and cycle["turn"] == state.turn:
            order = ORDERS[cycle["flower"]]
            for drink in (WHEY, BOOST, SODA):
                if drink in drinks:
                    return act(drinks[drink], f"循环：使用 {drink}")
            # 第五张是アイドルになります+：它要求手里至少两张 エンターテイナー+／オーバードライブ+／教えてあげる+，
            # 放到最后时三张都已打出、永远打不了。改为循环第一张（温存に変更、+1 出牌次数，不动夜空计数，循环条件不变）。
            idol = next((n for n in (cycle.get("fifth"),) if n and n.rstrip("+") == "アイドルになります"), None)
            if idol and not played and idol in by_name and len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2:
                cycle["idol_first"] = True
                return play(by_name[idol], "五卡循环：アイドルになります+ 先打（打出条件要核心牌在手）")
            if cycle.get("idol_first") and played and played[0] == idol:
                played = played[1:]
            if played[:len(order)] != list(order[:len(played)]):
                raise ResumeBlocked("VEIL 循环出牌记录和预定顺序不一致")
            if played and played[-1] in hand:
                raise ResumeBlocked(f"VEIL 循环上一张牌仍在手中，无法确认已打出：{played[-1]}")
            if len(played) < len(order):
                name = order[len(played)]
                if name not in by_name:
                    raise ResumeBlocked(f"VEIL 循环下一张牌不可用：{name}")
                return play(by_name[name], "五卡循环")
            if len(played) == len(order) and cycle.get("idol_first"):
                return skip("本回合循环完成（アイドルになります+ 已先打）")
            if len(played) == len(order):
                once = set((state.piles or {}).get("once", []))
                extra = next((name for name, c in observed.items() if name not in CORE
                              and (c.tags.once or c.tags.is_sleep or name in once)), None)
                fifth = extra or cycle.get("fifth")
                if fifth and fifth.rstrip("+") == "アイドルになります" and len({ENTERTAINER, DRIVE, TEACH} & hand) < 2:
                    return skip("循环：第五张受アイドルになります+禁打条件限制")
                if fifth:
                    if fifth not in by_name:
                        raise ResumeBlocked(f"VEIL 循环第五张尚未打出，当前不可用：{fifth}")
                    return play(by_name[fifth], "五卡循环收尾")
            return skip("本回合循环完成")

        eligible = [c for name, c in by_name.items() if name.rstrip("+") != DRIVE.rstrip("+") and (
            name.rstrip("+") != "アイドルになります" or len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2
        )]
        if compact:
            if flower is None and not played:
                raise ResumeBlocked("VEIL 已进入过渡，但夜空を従える花+剩余次数读不出")
            statuses = [norm(s.get("name") or "") for s in state.statuses or []]
            warm_next = any(
                ("次のターン" in label or "ターン開始" in label) and ("温存" in label or "のんびり" in label)
                for label in statuses
            )
            # Recompute all three needs on each frame. Teaching can cover both
            # the flower and draw, and can change the stance we need to restore.
            need_flower = flower == 3
            need_draw = state.piles.get("next_draw") not in (4, 5) and TEACH not in played
            need_warm = not warm_next and current not in WARM
            teach = by_name.get(TEACH) if TEACH not in played else None
            if teach and (need_flower or need_draw):
                return play(teach, "过渡：推进夜空计数／预约抽牌" if need_flower else "过渡：预约下回合抽两张")
            if need_flower:
                strong = next((c for c in eligible if destination(c) == "強気"), None)
                if strong:
                    return play(strong, "过渡：推进夜空计数")
            if need_warm and current is None and not played:
                raise ResumeBlocked("VEIL 过渡无法确认当前姿态或下回合切换")
            if need_warm and REBOUND in by_name and REBOUND not in played:
                return play(by_name[REBOUND], "过渡：切回温存")
            return skip("过渡：等待下回合")

        if state.piles is None and CORE <= hand:
            raise ResumeBlocked("VEIL 核心同手，尚未核实完整牌库，暂停以确认剩余牌")
        stance_cards = {
            name.rstrip("+"): c for name, c in observed.items()
            if name.rstrip("+") in ("ガタゴトすやすや", "一心不乱")
        }
        for name, allowed in (("ガタゴトすやすや", current in WARM), ("一心不乱", current == "強気")):
            card = stance_cards.get(name)
            if allowed and card and f"play:{card.slot}" in actions:
                return play(card, "清牌：姿态限定牌优先")
        if not eligible:
            return skip("清牌：没有允许打出的已识别牌")

        from hiro.brain.memory_ability import pending as ability_of

        abilities = memory.strategy_data.get("veil_abilities")

        def ability_kind(card):
            hit = ability_of(abilities, card.name)
            return hit["kind"] if hit else None

        def immediate_draw(card):
            return ((card.tags.draws and "次のターン" not in norm(card.text)) or "手札をすべて入れ替える" in norm(card.text)
                    or ability_kind(card) == "draw")

        def resulting_stance(card):
            return after_play(current, flower, card)[0]

        def gains_now(card):
            # 回忆能力（再动 / 再动并抽牌）在打出后再给一次出牌次数
            return (card.tags.extra_actions + int(resulting_stance(card) == "強気" and current in WARM)
                    + int(ability_kind(card) in ("draw", "play")))

        def unlocks(card):
            dest = resulting_stance(card)
            return (dest in WARM and current not in WARM and "ガタゴトすやすや" in stance_cards
                    or dest == "強気" and current in WARM and "一心不乱" in stance_cards)

        def priority(card):
            dest = resulting_stance(card)
            delays_warm = current == "強気" and dest in WARM
            opens_followup = unlocks(card) and (count > 1 or gains_now(card) > 0 or WHEY in drinks)
            return (gains_now(card), opens_followup, not delays_warm,
                    bool(card.tags.once and norm(card.name) != REBOUND),
                    bool(immediate_draw(card)), ability_kind(card) == "sleep", dest != current, card.badge_score or 0)

        # Preserve a later play for ひと呼吸 whenever another legal card is available.
        preferred = [c for c in eligible if norm(c.name).rstrip("+") != "ひと呼吸"]
        best = max(preferred or eligible, key=priority)
        late = next((c for c in eligible if norm(c.name).rstrip("+") == "ひと呼吸"), None)
        if late and count == 1 and gains_now(late) > gains_now(best) and WHEY not in drinks:
            best = late
        if count > len(eligible) and not unlocks(best):
            draw_card = next((c for c in sorted(eligible, key=priority, reverse=True) if immediate_draw(c)), None)
            if draw_card:
                return play(draw_card, "清牌：额外行动优先用技能卡抽牌")
            if SODA in drinks:
                return act(drinks[SODA], "清牌：有行动但缺手牌")
        if count == 1 and (len(eligible) > 1 or unlocks(best)) and not gains_now(best) and WHEY in drinks:
            return act(drinks[WHEY], "清牌：有手牌但出牌次数不足")
        return play(best, "清牌")
