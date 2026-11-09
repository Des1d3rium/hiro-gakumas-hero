"""约束（play 第 6 块）：哪些牌现在不能打 / 不该打，每条都给原因。

体力闸（保底线、净体力、強気下不改姿态的掉血牌留到温存）、全力値闸（アイドルになります+ 全力値不够、预留、封顶）、
使用条件（主数据 playCondition：温存 / 強気 / 次数 / 回合）、过渡里「不碍事」的一次性牌、オーバードライブ 攒全力値有没有用。
"""

from __future__ import annotations

import re

from hiro.brain.play.profile import (
    DRIVE, ENTERTAINER, FP_RESERVE_AT, FULL_POWER_AT, IDOL_CARD, MINOR_LOSS, REBOUND, TEACH, drive_check, fp_cap,
    fp_reserve, hp_floor, hp_guard, hp_net, idol_clear, last_turn_play, minor_hp,
)
from hiro.brain.play.sim import (
    NEUTRAL, WARM, cost_mods, destination, fp_delta, hp_loss, _master_text, next_stance, norm, stance,
    stance_level, usable_net,
)


def harmless(state, c, current, mods, last_turn):
    """过渡里打掉不碍事的一次性牌（使用者 2026-11-07，1-g）：不切強気（夜空计数不动，切温存可以）、下回合不切到非温存、
    不是核心牌；开体力闸时打完不跌破保底线。抽牌可以（牌组只剩核心牌，下回合照样抽得到）。"""
    from hiro.brain.play.profile import CORE

    if not (c.tags.once or c.tags.is_sleep) or norm(c.name) in CORE:
        return False
    if destination(c) not in (None, *WARM) or next_stance(c) not in (None, *WARM):
        return False
    if mods is not None and not last_turn and state.hp is not None:
        loss = hp_loss(state, c, current, mods)
        if loss and state.hp - loss < hp_floor():
            return False
    return True


def drive_useful(drive, starved, fp, current, hand) -> bool:
    """VEIL_DRIVE_CHECK：打 オーバードライブ 攒全力値之后，发灰的牌里至少有一张真能打出：全力値够；
    巻き返し+ 不在全力下（全力切不了姿态，攒了也没用）；アイドルになります+ 在 OD 离手后仍满足三选二（或开了 IDOL_CLEAR）。"""
    if not drive_check():
        return True
    if fp is None:
        return False
    after = fp + fp_delta(drive)[0]
    for c in starved:
        name = norm(c.name).rstrip("+")
        need = fp_delta(c)[1] or (FP_RESERVE_AT if name == IDOL_CARD else 1)
        if after < need:
            continue
        if name == REBOUND.rstrip("+") and current == "全力":
            continue
        if name == IDOL_CARD and not idol_clear() and len({ENTERTAINER, TEACH} & set(hand)) < 2:
            continue
        return True
    return False


def clear_eligible(t):
    """清牌 / 过渡里允许打的牌：（最后一回合？，可打的牌）。オーバードライブ 只在最后一回合清牌（VEIL_LAST_TURN）；
    アイドルになります+ 要手里至少两张 エンタ / OD / 教えて（三选二），VEIL_IDOL_CLEAR 下它是唯一能清的一次性牌时放行。"""
    hand, by_name = t.hand, t.by_name
    final_turn = last_turn_play() and t.state.remaining_turns == 1
    eligible = [c for name, c in by_name.items() if (final_turn or name.rstrip("+") != DRIVE.rstrip("+")) and (
        name.rstrip("+") != IDOL_CARD or len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2
    )]
    from hiro.brain.play.profile import CORE, _flag

    if (idol_clear() and (not _flag("IDOL_FP_GATE") or len(CORE & hand) >= 2)
            and not any(c.tags.once or c.tags.is_sleep for c in eligible)):
        # 三选二挡住的 アイドルになります+ 是手里唯一能清的一次性牌：照打（画面不灰就是游戏允许；VEIL_IDOL_CLEAR）
        idol_card = next((c for n, c in by_name.items() if n.rstrip("+") == IDOL_CARD and c not in eligible), None)
        if idol_card is not None:
            eligible.append(idol_card)
    return final_turn, eligible


class Gates:
    """清牌阶段的体力 / 全力値闸。"""

    def __init__(self, t):
        self.t = t
        self.guard = hp_guard()
        self.mods = cost_mods(t.memory.strategy_data.get("veil_log"), _master_text) if self.guard else None
        self.last_turn = t.state.remaining_turns is not None and t.state.remaining_turns <= 1

    def affordable(self, card):
        """体力闸（VEIL_HP_GUARD）：打完还剩不到保底线的牌不打；最后一回合不限。"""
        state, current = self.t.state, self.t.current
        if not self.guard or self.last_turn or state.hp is None:
            return True
        loss = hp_loss(state, card, current, self.mods)
        if loss == 0 or state.hp - loss >= hp_floor():  # 不掉血的牌永远能打（体力已在线下时也不能全拦）
            return True
        # VEIL_HP_NET：可用体力（体力 + 元気）不减的、核心打分牌不拦，打完体力至少剩 1
        from hiro.brain.play.profile import CORE

        return hp_net() and state.hp - loss >= 1 and (
            norm(card.name) in CORE or usable_net(state, card, current, self.mods) >= 0)

    def minor(self, card):
        state = self.t.state
        loss = hp_loss(state, card, self.t.current, self.mods)
        return minor_hp() and loss <= MINOR_LOSS and state.hp is not None and state.hp - loss >= hp_floor()

    def filter(self, eligible):
        """依次过体力闸、強気下留牌、全力値预留、全力値封顶。返回（剩下的牌，跳过回合的理由或 None）。"""
        from hiro.brain.play.profile import CORE

        t = self.t
        state, memory, current = t.state, t.memory, t.current
        mods, last_turn = self.mods, self.last_turn
        if self.guard:
            cheap = [c for c in eligible if self.affordable(c)]
            if eligible and not cheap:
                return eligible, f"清牌：体力闸，HP {state.hp} 打哪张都会跌破 {hp_floor()}"
            eligible = cheap
            if current == "強気" and not last_turn:
                # 強気 下体力消耗 ×2：不改姿态的掉血牌（ひと呼吸+、モチベ+…）留到温存（×0.5）再打，也不为它们喝ホエイ。
                # 使用者 2026-11-07（1-e R2 T1）：打了 インフルエンサー+、第一印象+ 后又喝ホエイ 打 モチベ+ / ひと呼吸+，
                # 体力 38→1 错过下回合出牌；理想是这两张后跳过回合，下回合进温存
                keep = [c for c in eligible if hp_loss(state, c, current, mods) == 0 or destination(c) is not None
                        or self.minor(c)
                        or (hp_net() and (norm(c.name) in CORE or usable_net(state, c, current, mods) >= 0))]
                if eligible and not keep:
                    return eligible, "清牌：強気下体力 ×2，不改姿态的掉血牌留到温存再打"
                eligible = keep
        if fp_reserve() and not last_turn:
            fp = memory.strategy_data.get("veil_fp")
            deck = memory.strategy_data.get("veil_deck")
            idol_left = deck is None or any(n.rstrip("+") == IDOL_CARD and k > 0 for n, k in deck.items())

            def keeps_reserve(card):
                gained, cost = fp_delta(card, current)
                return not cost or norm(card.name).rstrip("+") == IDOL_CARD or fp - cost + gained >= FP_RESERVE_AT

            if fp is not None and idol_left:
                kept = [c for c in eligible if keeps_reserve(c)]
                if eligible and not kept:
                    return eligible, f"清牌：预留全力値，账本 {fp}，花完不足 {FP_RESERVE_AT}（留给 アイドルになります+）"
                eligible = kept
        if fp_cap() and not last_turn and (fp := memory.strategy_data.get("veil_fp")) is not None:
            def under_cap(card):
                gained, cost = fp_delta(card, current)
                return not gained or fp - cost + gained < FULL_POWER_AT

            capped = [c for c in eligible if under_cap(c)]
            if capped and len(capped) < len(eligible):
                # 只在还有别的牌可打时避开；全是加满的牌就照打，不空转。记一笔供审计（验证局 1-d 看不出它有没有起作用）
                log = memory.strategy_data.setdefault("veil_fp_cap_log", [])
                entry = {"turn": memory.turn, "fp": fp, "avoided": sorted(norm(c.name) for c in eligible if c not in capped)}
                if not log or log[-1] != entry:
                    log.append(entry)
                eligible = capped
        return eligible, None


# ---------------------------------------------------------------- 使用条件（主数据 playCondition；veil_exam 记账也用）

_CONDITIONS = (
    (re.compile(r"温存になった回数が(\d+)回以上の場合、使用可"), "warm"),
    (re.compile(r"指針を変更した回数が(\d+)回以上の場合、使用可"), "changes"),
    (re.compile(r"全力になった回数が(\d+)回以上の場合、使用可"), "full"),
)


_TEXT_STANCE_CONDITIONS = (
    (re.compile(r"強気(\d)段階目の場合、使用可"), "強気"),
    (re.compile(r"強気の場合、使用可"), "強気"),
    (re.compile(r"温存の場合、使用可"), "温存"),
    (re.compile(r"全力の場合、使用可"), "全力"),
)


def play_condition(card) -> dict | None:
    """这张牌的使用条件，格式同 card_fx.trigger：{kind, value, not, text[, level]}。结构化效果为准（使用者 2026-11-09），
    没有结构化效果时按牌面文字认（认得的几种），都没有返回 None。"""
    from hiro.brain import card_fx

    if (fx := card_fx.of(card)) is not None:
        return fx.get("condition")
    text = norm(card.text or "")
    if "使用可" not in text:
        return None
    for pattern, key in _CONDITIONS:
        if m := pattern.search(text):
            return {"kind": key, "value": int(m.group(1)), "not": False, "text": m.group(0)}
    if m := re.search(r"(\d+)ターン目以降の場合、使用可", text):
        return {"kind": "turn", "value": int(m.group(1)), "not": False, "text": m.group(0)}
    for pattern, value in _TEXT_STANCE_CONDITIONS:
        if m := pattern.search(text):
            out = {"kind": "stance", "value": value, "not": False, "text": m.group(0)}
            if m.groups():
                out["level"] = int(m.group(1))
            return out
    if "いずれかの指針の場合、使用可" in text:
        return {"kind": "stance", "value": "なし", "not": True, "text": "いずれかの指針の場合、使用可"}
    return {"kind": "other", "value": None, "not": False, "text": text}


def condition_met(cond, state, memory, counts, current) -> bool | None:
    """使用条件按账本 / 画面成不成立：True / False；判断不了（姿态不明、计数没记、认不出的条件）None。"""
    if not cond:
        return True
    kind, value = cond["kind"], cond.get("value")
    if kind in ("warm", "changes", "full", "strong"):
        if kind not in counts and kind == "strong":
            return None
        ok = counts.get(kind, 0) >= int(value or 0)
    elif kind == "turn":
        ok = memory.turn >= int(value or 0)
    elif kind == "stance":
        if current is None:
            return None
        if value == "なし":
            ok = current == NEUTRAL
        elif value == "温存":
            ok = current in WARM
        else:
            ok = current == value
            if ok and cond.get("level") in (1, 2):
                level = stance_level(state, current)
                ok = level in (None, cond["level"])
    else:
        return None
    return not ok if cond.get("not") else ok


def play_condition_blocked(state, memory, data) -> dict:
    """手牌里按主数据使用条件肯定打不了的牌 {槽位: 原因}。只判账本 / 画面确定的（计数往多里算、姿态不明不判），
    宁可漏判（照旧按画面）也不误拦。使用条件以结构化效果为准（card_fx），没有时按牌面文字。"""
    counts = data.get("veil_stance_counts") or {}
    current = stance(state)
    out = {}
    for card in state.hand:
        if not card.available:
            continue
        cond = play_condition(card)
        if condition_met(cond, state, memory, counts, current) is False:
            seen = counts.get(cond["kind"], 0) if cond["kind"] in ("warm", "changes", "full", "strong") else None
            where = f"账本 {seen} 次" if seen is not None else (f"第 {memory.turn} 回合" if cond["kind"] == "turn" else f"现在 {current}")
            out[card.slot] = f"{cond['text']}（{where}）"
    return out
