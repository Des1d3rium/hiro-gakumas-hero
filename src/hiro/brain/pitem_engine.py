"""通用 P 道具引擎：按持有的 P 道具自动装上全部附魔，在账本推演的各个时点判触发、执行效果、记发动次数。

开关 VEIL_PITEM_ENGINE（默认关，偶像档案 flags.known）。关着时本模块不被调用，账本和原来逐位一致。

编译靠 hiro.brain.pitems（ProduceItem → 附魔 = 触发器 + 效果）；本模块只管「装哪些」「什么时候判」「判得了判不了」「发动几次」。

持有哪些（install / observe）：
  · 画面道具列（hiro.vision.items.read_pitems，asset 形如 img_general_pitem_3-258）→ ProduceItem.assetId 相同的 id。
    同一 asset 常有好几个 id（ゼミパーカー 有 10 个、7 种不同效果）：结构（触发器 + 效果 + 次数）全一样才装；
    不一样就看偶像档案 pitem_assets 有没有写明是哪个 id，没写就不装、记进 unsure。
  · 偶像专属道具（夜空を従える花+）画面道具列里没有：按偶像卡 ProduceItem.originIdolCardId 找强化版（ring_item），
    它的「每 N 张」计数就是 VEIL 账本的夜空计数（veil_position.flower），仍由 hiro.brain.veil.after_play 结算，
    本引擎不重复处理它（ring_counts / ring_outcome 给 veil 用，替掉写死的 FLOWER_ITEM）。

触发时机（PHASES_SUPPORTED）：考试开始、回合开始、回合开始后（StartPlay）、回合结束、每 N 回合（回合开始 / 回合结束）、
  出牌时、出牌后、每 N 张（出牌时 / 出牌后）、回合内每 N 张、姿态变成強気 / 全力 / 温存、离开全力。
  其余（状态变化、体力减少、跳过回合、计时器、除外…）装的时候记进 unsupported，推演里当「不知道」。
触发条件（cond_holds）：没有指针、強気 / 温存（含段階）/ 全力、剩余回合、元気、体力（按最大体力的比例）、全力値。
  判断不了的字段、不知道的局面（姿态不明、出牌后的元気…）返回 None。
效果（run_effects）：沿用 pitems.apply_effects；另认「次のターン、…に変更」（ExamEffectTimer）。只把姿态 / 段階 / 下回合姿态 /
  追加出牌次数落到账本，元気 / 全力値 / 抽牌 / 熱意 / 生成牌等只记日志（cat：score = 只影响分数，ledger = 会动账本但没建模）。
发动次数：附魔次数（ProduceItemEffect.effectCount，「レッスン内5回」）、道具 fireLimit（一场几次）/ fireInterval（隔几回合）、
  附魔有效回合（ProduceItemEffect.effectTurn）。
不知道（None）时一律不改账本：调用方照原来的手写规则走。

Common 牌算不算「某计划的牌」（2026-11-09 实机核对）：pitems.CardSearch.matches 把 ProducePlanType_Common 当成符合任意 planType。
VEIL 81 份 trace 带 ゼミパーカー（搜索 planType = Plan1），按道具角标「5→4」看发动：通用牌单张打出 138 次一次都没减，
Plan1 牌（ひと呼吸+ / 演出計画+）10 次每次都减。所以「某计划的牌」不含 Common，COMMON_PLAN_MATCHES = False。
同一核对还查清 VEIL 带的是「センスのスキルカード使用後、パラメータ上昇量増加10%」那一版（research-event-008..010）：
ひと呼吸+ 打出、角标减 1 后画面姿态仍是強気，不是「温存に変更」那一版（pitem_00-3-258-0）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

from hiro.brain import pitems as P

KEY = "veil_pitems"  # memory.strategy_data 里的引擎状态（JSON，可随记忆快照撤回）
FLAG = "PITEM_ENGINE"

COMMON = "ProducePlanType_Common"
# 实机核对结论（见模块说明）：Common 牌不算「某计划的牌」。设成 None = 未核对，只因 Common 才匹配时返回「不知道」
COMMON_PLAN_MATCHES: bool | None = False

_T = "ProduceExamPhaseType_"
START_EXAM, START_TURN, START_PLAY, END_TURN = (_T + x for x in ("ExamStartExam", "ExamStartTurn", "StartPlay", "ExamEndTurn"))
TURN_INTERVAL, END_TURN_INTERVAL = _T + "ExamTurnInterval", _T + "ExamEndTurnInterval"
CARD_PLAY, CARD_AFTER = _T + "ExamCardPlay", _T + "ExamCardPlayAfter"
COUNT, COUNT_AFTER, TURN_COUNT = (_T + x for x in ("ExamPlayCountInterval", "ExamPlayCountIntervalAfter", "ExamPlayTurnCountInterval"))
TO_STRONG, TO_FULL, TO_WARM, FROM_FULL = (_T + x for x in ("ExamStanceChangeConcentration", "ExamStanceChangeFullPower",
                                                          "ExamStanceChangePreservation", "ExamStanceChangeFromFullPower"))
PHASES_SUPPORTED = frozenset({START_EXAM, START_TURN, START_PLAY, END_TURN, TURN_INTERVAL, END_TURN_INTERVAL,
                              CARD_PLAY, CARD_AFTER, COUNT, COUNT_AFTER, TURN_COUNT, TO_STRONG, TO_FULL, TO_WARM, FROM_FULL})
PLAY_PHASES = frozenset({CARD_PLAY, COUNT})  # 出牌时（牌的效果之前）
AFTER_PHASES = frozenset({CARD_AFTER, COUNT_AFTER, TURN_COUNT})  # 出牌后

WARM = frozenset({"温存", "温存1段階目", "温存2段階目", "のんびり"})
LEVELS = frozenset({1, 2})  # 強気 / 温存 的段階
NO_META = tuple([0, -1])  # 附魔次数不限、整场有效
NO_STANCE = frozenset({"指針なし", "指針解除"})
STANCE_TYPES = frozenset(P.STANCE_OF_EFFECT)
TIMER = "ProduceExamEffectType_ExamEffectTimer"
# 只影响分数 / 不在 VEIL 账本里的效果：触发了只记日志
_E = "ProduceExamEffectType_"
SCORE_ONLY = frozenset(_E + x for x in (
    "ExamLesson", "ExamLessonValueMultiple", "ExamLessonValueMultipleDown", "ExamLessonBuff", "ExamLessonBuffAdditive",
    "ExamLessonBuffMultiple", "ExamParameterBuff", "ExamParameterBuffMultiplePerTurn", "ExamReview", "ExamReviewAdditive",
    "ExamReviewMultiple", "ExamReviewValueMultiple", "ExamCardPlayAggressive", "ExamAggressiveValueMultiple",
    "ExamEnthusiasticMultiple", "ExamEnthusiasticAdditive", "ExamBlock", "ExamBlockFix", "ExamBlockAddDown",
    "ExamBlockAddMultipleAggressive", "ExamBlockRestriction", "ExamStaminaRecoverFix", "ExamStaminaReduceFix",
    "ExamStaminaRecoverRestriction", "ExamStaminaConsumptionAdd", "ExamStaminaConsumptionAddFix", "ExamStaminaConsumptionDown",
    "ExamStaminaConsumptionDownFix", "ExamAntiDebuff", "ExamDebuffRecover", "ExamLessonDependBlock",
    "ExamLessonDependExamReview", "ExamLessonDependParameterBuff", "ExamLessonDependExamCardPlayAggressive",
    "ExamLessonDependStaminaConsumptionSum", "ExamLessonDependPlayCardCountSum", "ExamLessonFullPowerPoint",
    "ExamLessonAddMultipleLessonBuff"))


def enabled() -> bool:
    from hiro.brain.veil import _flag

    return _flag(FLAG)


def _db():
    return P._db()


# ---------------------------------------------------------------- 持有哪些


def _asset_id(asset: str) -> str:
    asset = (asset or "").strip()
    return asset if asset.startswith("img_general_") else f"img_general_{asset}"


@lru_cache(maxsize=None)
def _item_row(item_id: str) -> dict | None:
    return P._row("ProduceItem", item_id)


@lru_cache(maxsize=None)
def enchant_meta(item_id: str) -> tuple[tuple[int, int], ...]:
    """和 load_pitem(item).enchants 一一对应的 (effectCount, effectTurn)：附魔次数（0 = 不限）、有效回合（-1 = 整场）。"""
    r = _item_row(item_id)
    out = []
    for eid in P._json((r or {}).get("produceItemEffectIds")):
        ie = P._row("ProduceItemEffect", eid)
        if ie is None or ie.get("effectType") != "ProduceItemEffectType_ExamStatusEnchant":
            continue
        if P._row("ProduceExamStatusEnchant", ie.get("produceExamStatusEnchantId")) is None:
            continue
        turn = ie.get("effectTurn")
        out.append((int(ie.get("effectCount") or 0), -1 if turn is None else int(turn)))
    return tuple(out)


@lru_cache(maxsize=None)
def signature(item_id: str) -> str:
    """道具在考试里的结构：fireLimit / fireInterval + 每个附魔的（触发器、效果、次数、回合）。同 asset 的几个 id 比这个。"""
    item = P.load_pitem(item_id)
    if item is None:
        return ""
    meta = enchant_meta(item_id)
    return json.dumps([item.fire_limit, item.fire_interval,
                       [[e.trigger.id if e.trigger else None, [x.id for x in e.effects], *(meta[i] if i < len(meta) else NO_META)]
                        for i, e in enumerate(item.enchants)]])


def candidates(asset: str) -> list[str]:
    return [r[0] for r in _db().execute("SELECT id FROM ProduceItem WHERE assetId = ? ORDER BY id", (_asset_id(asset),))]


def resolve(asset: str, hints: dict | None = None) -> tuple[str | None, dict]:
    """画面 asset → ProduceItem id。返回 (id 或 None, 说明)。几个 id 结构不同又没有提示时 None（不装）。"""
    ids = candidates(asset)
    info = {"asset": _asset_id(asset), "candidates": len(ids)}
    if not ids:
        return None, {**info, "why": "主数据里没有这个 asset"}
    hint = (hints or {}).get(_asset_id(asset)) or (hints or {}).get(asset)
    if hint:
        if hint in ids:
            return hint, {**info, "by": "hint"}
        return None, {**info, "why": f"偶像档案写的 {hint} 不是这个 asset 的道具"}
    sigs = {signature(i) for i in ids}
    if len(sigs) == 1:
        return ids[0], {**info, "by": "same"}
    return None, {**info, "signatures": len(sigs), "why": "同一图标有几种效果不同的道具，认不出是哪个"}


@lru_cache(maxsize=None)
def ring_item(idol: str) -> str | None:
    """偶像专属 P 道具（强化版）里带「每 N 张」计数的那个：VEIL 的夜空を従える花+。idol 可以是 skin id 或偶像卡 id。"""
    card = idol
    r = _db().execute("SELECT idolCardId FROM IdolCardSkin WHERE id = ?", (idol,)).fetchone()
    if r is not None and r[0]:
        card = r[0]
    rows = _db().execute("SELECT id FROM ProduceItem WHERE originIdolCardId = ? ORDER BY isUpgraded DESC, id", (card,)).fetchall()
    for (item_id,) in rows:
        item = P.load_pitem(item_id)
        if item is not None and item.play_counters:
            return item_id
    return None


def ring_counts(idol: str, card, stance: str | None) -> bool | None:
    """同 pitems.flower_counts，但道具按偶像找（不写死 FLOWER_ITEM）。判断不了 None。"""
    try:
        item_id = ring_item(idol)
        item = P.load_pitem(item_id) if item_id else None
        if item is None or not item.play_counters:
            return None
        return search_play(item.play_counters[0].trigger, P.view_profile(card), stance)
    except Exception:  # noqa: BLE001  没有主数据
        return None


def ring_outcome(idol: str, current: str | None) -> P.Outcome | None:
    """同 pitems.flower_trigger_outcome，道具按偶像找。"""
    try:
        item_id = ring_item(idol)
        item = P.load_pitem(item_id) if item_id else None
    except Exception:  # noqa: BLE001
        return None
    if item is None or not item.play_counters:
        return None
    return P.apply_effects(item.play_counters[0].effects, current)


def search_play(trig: P.Trigger, card: P.CardProfile | None, stance: str | None) -> bool | None:
    """ring 用：和 Trigger.counts_play 一样，但牌搜索按 search_matches（Common 规则）。"""
    if trig.play_interval is None:
        return False
    for cond in trig.conditions:
        ok = cond.holds(stance)
        if ok is None:
            return None
        if not ok:
            return False
    return search_matches(trig.search, card)


def search_matches(search: P.CardSearch | None, card: P.CardProfile | None) -> bool | None:
    """牌搜索；「只因 Common 才匹配某计划」按 COMMON_PLAN_MATCHES（实机核对：不匹配）。"""
    if search is None:
        return True
    hit = search.matches(card)
    if hit and search.plan and search.plan != COMMON and card is not None and card.plan == COMMON:
        return COMMON_PLAN_MATCHES
    return hit


def _new_state(idol: str | None) -> dict:
    return {"items": [], "ring": None, "unsure": [], "unsupported": [], "assets": [], "fired": {}, "item_fired": {},
            "item_last": {}, "counters": {}, "turn_counters": {}, "boundary": None, "ended": None, "log": [], "idol": idol}


def _install(st: dict, item_id: str, source: str) -> None:
    if any(e["item"] == item_id for e in st["items"]):
        return
    item = P.load_pitem(item_id)
    if item is None:
        st["unsure"].append({"item": item_id, "why": "主数据查不到"})
        return
    st["items"].append({"item": item_id, "name": item.name, "source": source})
    for i, en in enumerate(item.enchants):
        phases = [p for p, _ in en.trigger.phases] if en.trigger else []
        bad = [p.rsplit("_", 1)[-1] for p in phases if p not in PHASES_SUPPORTED]
        if en.trigger is None or bad or not phases:
            st["unsupported"].append({"key": f"{item_id}#{i}", "name": item.name, "why": f"触发时机不支持：{bad or '没有触发器'}"})


def observe(data: dict, pitems_raw: list | None, idol: str, hints: dict | None = None) -> dict:
    """每帧调用：第一次装偶像专属道具（ring）+ 画面道具列；之后画面多读到的道具补装（记 late）。返回引擎状态。"""
    st = data.get(KEY)
    if st is None:
        st = data[KEY] = _new_state(idol)
        try:
            st["ring"] = ring_item(idol)
        except Exception:  # noqa: BLE001
            st["ring"] = None
    seen = set(st["assets"])
    for p in pitems_raw or []:
        asset = _asset_id(p.get("asset") or "")
        if not p.get("asset") or asset in seen:
            continue
        seen.add(asset)
        st["assets"].append(asset)
        try:
            item_id, info = resolve(asset, hints)
        except Exception as e:  # noqa: BLE001
            item_id, info = None, {"asset": asset, "why": f"主数据不可用：{e}"}
        if item_id is not None and st["ring"] and signature(item_id) == signature(st["ring"]):
            continue  # 偶像专属道具也出现在道具列：已经作为 ring 处理
        if item_id is None:
            if st["ring"] and all(i in _idol_items(st["ring"]) for i in candidates(asset)):
                continue  # 偶像专属道具的未强化 / 强化两版：ring 已管
            st["unsure"].append({**info, "name": p.get("name")})
            continue
        before = len(st["items"])
        _install(st, item_id, "screen")
        if len(st["items"]) > before and st.get("boundary") is not None:
            st["items"][-1]["late"] = True  # 考试中途才读到：之前的发动没记
    return st


@lru_cache(maxsize=None)
def _idol_items(ring: str) -> frozenset[str]:
    r = _item_row(ring) or {}
    rows = _db().execute("SELECT id FROM ProduceItem WHERE originIdolCardId = ?", (r.get("originIdolCardId") or "",)).fetchall()
    return frozenset(x[0] for x in rows)


# ---------------------------------------------------------------- 条件


@dataclass
class Ctx:
    """判条件用的局面；不知道的字段留 None。"""
    stance: str | None
    level: int | None = None
    remaining: int | None = None
    genki: int | None = None
    hp: int | None = None
    hp_max: int | None = None
    fp: int | None = None
    turn: int | None = None


_F = "ProduceExamFieldStatusType_"


def cond_holds(cond: P.Condition, ctx: Ctx) -> bool | None:
    """一个触发条件按局面判；字段不支持、局面不知道时 None。"""
    if cond.search is not None:
        return None  # fieldStatusProduceCardSearchIds（「某处有 N 张某类牌」）：账本数不准
    f, v = cond.field, cond.value
    hit = None
    if f == _F + "NoStance":
        if ctx.stance is None:
            return None
        hit = ctx.stance in NO_STANCE
    elif f in (_F + "PreservationUp", _F + "ConcentrationUp", _F + "FullPowerUp"):
        if ctx.stance is None:
            return None
        hit = ctx.stance in P.FIELD_STANCE[f]
        if hit and v and f != _F + "FullPowerUp":
            if ctx.stance == "のんびり":
                return None  # 「温存N段階目の場合」碰上のんびり：没核对过
            level = ctx.level if ctx.level in LEVELS else ({"温存1段階目": 1, "温存2段階目": 2}.get(ctx.stance))
            if level is None:
                return None
            hit = level >= v
    elif f == _F + "RemainingTurn":
        if ctx.remaining is None or v is None:
            return None
        hit = ctx.remaining <= v
    elif f == _F + "BlockUp":
        if ctx.genki is None or v is None:
            return None
        hit = ctx.genki >= v
    elif f == _F + "NoBlock":
        if ctx.genki is None:
            return None
        hit = ctx.genki == 0
    elif f in (_F + "StaminaUpMultiple", _F + "StaminaLessMultiple"):
        if ctx.hp is None or not ctx.hp_max or v is None:
            return None
        hit = ctx.hp * 1000 >= ctx.hp_max * v if f.endswith("UpMultiple") else ctx.hp * 1000 <= ctx.hp_max * v
    elif f == _F + "FullPowerPointUp":
        if ctx.fp is None or v is None:
            return None
        hit = ctx.fp >= v
    else:
        return None
    return (not hit) if cond.negate else hit


@lru_cache(maxsize=None)
def _trigger_extra(trigger_id: str) -> tuple[str, ...]:
    """触发器里 pitems.Trigger 没编译、这里也判断不了的字段（课程类型限定、移动位置、效果类型限定）。"""
    r = P._row("ProduceExamTrigger", trigger_id) or {}
    out = []
    if P._set(r.get("lessonType")):
        out.append("lessonType")
    if P._set(r.get("cardMovePositionType")):
        out.append("cardMovePositionType")
    if P._json(r.get("effectTypes")):
        out.append("effectTypes")
    return tuple(out)


def trigger_holds(trig: P.Trigger, ctx: Ctx, card: P.CardProfile | None = None, *, needs_card: bool = False) -> bool | None:
    """条件 + 牌搜索（出牌类时机）。判断不了 None。"""
    if _trigger_extra(trig.id):
        return None
    for cond in trig.conditions:
        ok = cond_holds(cond, ctx)
        if ok is None:
            return None
        if not ok:
            return False
    if needs_card:
        if (trig.lower or 0) > 1 or trig.upper:
            return None  # 「N 张以上 / 以下」类计数：出牌时机用不上，没核对
        return search_matches(trig.search, card)
    if trig.search is not None:
        return None  # 非出牌时机带牌搜索（数某处的牌）：账本数不准
    return True


# ---------------------------------------------------------------- 效果


@dataclass
class Fire:
    key: str
    name: str
    phase: str
    outcome: P.Outcome
    next_stance: str | None = None
    score: list = field(default_factory=list)  # 只影响分数的效果
    ledger: list = field(default_factory=list)  # 会动账本、没建模的效果


def run_effects(effects, current: str | None) -> tuple[P.Outcome, str | None, list, list]:
    """效果 → (Outcome, 下回合姿态, 只影响分数的, 动账本却没建模的)。
    「次のターン、…に変更」（ExamEffectTimer，1 回合后、连锁效果里有姿态）认成下回合姿态；别的计时器算没建模。"""
    out = P.apply_effects(effects, current)
    next_stance, score, ledger = None, [], []
    for t in out.unknown:
        if t == TIMER:
            continue
        (score if t in SCORE_ONLY else ledger).append(t)
    out.unknown = []
    for e in effects:
        if e is None or e.type != TIMER:
            continue
        chained = [P.exam_effect(c) for c in e.chain]
        if e.value1 != 1 or not chained or any(c is None for c in chained):  # value1 = 几回合后（1 = 次のターン）
            ledger.append(TIMER)
            continue
        stances = [c for c in chained if c.type in STANCE_TYPES]
        if stances:
            last = stances[-1]
            next_stance = P.STANCE_OF_EFFECT[last.type]
            if next_stance == "温存" and last.value1 in LEVELS:
                next_stance = f"温存{last.value1}段階目"  # 和 veil.next_stance_change 的写法一致
        for c in chained:
            if c.type not in STANCE_TYPES:
                (score if c.type in SCORE_ONLY else ledger).append(c.type)
    return out, next_stance, score, ledger


# ---------------------------------------------------------------- 推演


def _enchants(st: dict, turn: int | None):
    """装着的（非 ring）附魔：[(key, item, enchant, (count, turns))]，过了有效回合的不算。"""
    for entry in st["items"]:
        item = P.load_pitem(entry["item"])
        if item is None:
            continue
        meta = enchant_meta(entry["item"])
        for i, en in enumerate(item.enchants):
            if en.trigger is None:
                continue
            count, turns = meta[i] if i < len(meta) else NO_META
            if turns is not None and turns > 0 and turn is not None and turn > turns:
                continue
            yield f"{entry['item']}#{i}", item, en, count


def _can_fire(st: dict, key: str, item: P.PItem, count: int, turn: int | None) -> bool:
    if count and st["fired"].get(key, 0) >= count:
        return False
    if item.fire_limit and st["item_fired"].get(item.id, 0) >= item.fire_limit:
        return False
    last = st["item_last"].get(item.id)
    if item.fire_interval and last is not None and (turn is None or turn - last < item.fire_interval):
        return False
    return True


def _mark_fired(st: dict, key: str, item: P.PItem, turn: int | None) -> None:
    st["fired"][key] = st["fired"].get(key, 0) + 1
    st["item_fired"][item.id] = st["item_fired"].get(item.id, 0) + 1
    st["item_last"][item.id] = turn


def _log(st: dict, turn, phase, key, name, what, **extra) -> None:
    st["log"].append({"turn": turn, "phase": phase.rsplit("_", 1)[-1], "key": key, "name": name, "what": what, **extra})
    del st["log"][:-200]


def _phase_value(en: P.Enchant, phase: str):
    return next((v for p, v in en.trigger.phases if p == phase), None)


def fire_phase(st: dict, phases: set[str], ctx: Ctx, *, profile=None, turn=None) -> tuple[list[Fire], list[str]]:
    """一个时点：返回 (发动了的, 判断不了的附魔 key)。计数类（每 N 张 / 回合内每 N 张 / 每 N 回合）在这里记数。"""
    fired, unknown = [], []
    for key, item, en, count in _enchants(st, turn):
        hits = [p for p, _ in en.trigger.phases if p in phases]
        if not hits:
            continue
        phase = hits[0]
        if phase in (COUNT, COUNT_AFTER, TURN_COUNT):
            n = _phase_value(en, phase)
            ok = trigger_holds(en.trigger, ctx, profile, needs_card=True)
            if phase == TURN_COUNT:  # 回合内计数：换回合从 0 数
                rec = st["turn_counters"].get(key)
                played = rec[1] if rec and rec[0] == turn else 0
                store = lambda v, key=key: st["turn_counters"].__setitem__(key, [turn, v])  # noqa: E731
            else:
                played = st["counters"].get(key, 0)
                store = lambda v, key=key: st["counters"].__setitem__(key, v)  # noqa: E731
            if played is None or ok is None or not n:
                store(None)  # 有一手判断不了算不算：之后的计数都不知道，不再发动
                unknown.append(key)
                _log(st, turn, phase, key, item.name, "不知道算不算这一手，之后计数不明")
                continue
            if not ok:
                continue
            played += 1
            store(played)
            if played % int(n):
                continue
        elif phase in (TURN_INTERVAL, END_TURN_INTERVAL):
            n = _phase_value(en, phase)
            if not n or turn is None:
                unknown.append(key)
                continue
            if turn % int(n):
                continue
            ok = trigger_holds(en.trigger, ctx)
            if ok is None:
                unknown.append(key)
                continue
            if not ok:
                continue
        else:
            ok = trigger_holds(en.trigger, ctx, profile, needs_card=phase in (CARD_PLAY, CARD_AFTER))
            if ok is None:
                unknown.append(key)
                _log(st, turn, phase, key, item.name, "条件判断不了")
                continue
            if not ok:
                continue
        if not _can_fire(st, key, item, count, turn):
            _log(st, turn, phase, key, item.name, "次数用完")
            continue
        _mark_fired(st, key, item, turn)
        out, nxt, score, ledger = run_effects(en.effects, ctx.stance)
        fired.append(Fire(key, item.name, phase, out, nxt, score, ledger))
        _log(st, turn, phase, key, item.name, "发动", stance=out.stance, next_stance=nxt, actions=out.actions,
             score=score or None, ledger=ledger or None, left=(count - st["fired"][key]) if count else None)
    return fired, unknown


def _stance_level(before, before_level, dest, explicit=None):
    """同 veil.level_after（不 import veil，免得循环）。"""
    base = lambda s: "温存" if s and s.startswith("温存") else s  # noqa: E731
    if base(dest) not in ("強気", "温存"):
        return None
    if explicit in LEVELS:
        return explicit
    if dest.endswith("段階目"):
        return int(dest[-4])
    return 2 if base(before) == base(dest) else 1


def apply_stance(current, level, fires: list[Fire], unknown_keys, st) -> tuple[str | None, int | None, bool]:
    """把发动结果里的姿态落到 (current, level)。同一时点几个道具要切的姿态不同 → 姿态不明（None）。
    有判断不了、又可能切姿态的附魔 → 不动（调用方照原来的规则），第三项 changed=False。"""
    if any(_may_change_stance(st, k) for k in unknown_keys):
        return current, level, False
    dests = [f.outcome for f in fires if f.outcome.stance is not None]
    if not dests:
        return current, level, False
    if len({(o.stance, o.level) for o in dests}) > 1:
        return None, None, True
    o = dests[0]
    if current == "のんびり" and o.stance.startswith("温存"):
        return current, level, False  # のんびり 覆盖温存（veil.keeps_nonbiri）
    if current == "全力" and o.stance != "全力":
        return current, level, False
    return o.stance, _stance_level(current, level, o.stance, o.level), True


@lru_cache(maxsize=None)
def _enchant_stance(item_id: str, index: int) -> bool:
    item = P.load_pitem(item_id)
    if item is None or index >= len(item.enchants):
        return True
    for e in item.enchants[index].effects:
        if e.type in STANCE_TYPES:
            return True
        if e.type == TIMER and any((c := P.exam_effect(x)) is None or c.type in STANCE_TYPES for x in e.chain):
            return True
    return False


def _may_change_stance(st, key: str) -> bool:
    item_id, _, idx = key.rpartition("#")
    return _enchant_stance(item_id, int(idx))


def _stance_phases(before: str | None, after: str | None) -> set[str] | None:
    """姿态从 before 变成 after 时触发的时机；判断不了（姿态不明、のんびり 算不算温存没核对）None。"""
    if before == after:
        return set()
    if before is None or after is None:
        return None
    out = set()
    if after == "強気":
        out.add(TO_STRONG)
    if after == "全力":
        out.add(TO_FULL)
    if after in ("温存", "温存1段階目", "温存2段階目"):
        out.add(TO_WARM)
    if after == "のんびり":
        return None
    if before == "全力":
        out.add(FROM_FULL)
    return out


def _has_phase(st, phases) -> bool:
    return any(p in phases for _, _, en, _ in _enchants(st, None) for p, _ in en.trigger.phases)


def stance_changed(st, before, after, level, ctx: Ctx, turn) -> tuple[str | None, int | None]:
    """姿态变化类附魔（变成強気 / 全力 / 温存、离开全力）。只结算一层，不再连锁。"""
    phases = _stance_phases(before, after)
    watched = {TO_STRONG, TO_FULL, TO_WARM, FROM_FULL}
    if phases is None:
        if _has_phase(st, watched):
            _log(st, turn, "StanceChange", None, None, "姿态变化判断不了", before=before, after=after)
        return after, level
    if not phases:
        return after, level
    fires, unknown = fire_phase(st, phases, Ctx(**{**ctx.__dict__, "stance": after, "level": level}), turn=turn)
    new, new_level, changed = apply_stance(after, level, fires, unknown, st)
    return (new, new_level) if changed else (after, level)


def after_play(data: dict, memory, state, card, before: str | None, after: str | None, level: int | None,
               *, card_dest: str | None = None) -> tuple[str | None, int | None]:
    """出牌记账钩子（veil_exam._commit_ledger）：before = 打出前姿态，after / level = 牌（和夜空）结算后的姿态。
    依次结算：出牌时（牌的效果之前，牌自己切姿态的话以牌为准）→ 出牌后 / 每 N 张 → 姿态变化。
    追加出牌次数加到 memory.actions_left。返回最终 (姿态, 段階)；什么都没触发时原样返回。"""
    st = data.get(KEY)
    if st is None:
        return after, level
    turn = memory.turn
    try:
        profile = P.view_profile(card)
    except Exception:  # noqa: BLE001
        profile = None
    max_hp = getattr(state, "max_hp", None)
    pre = Ctx(before, None, state.remaining_turns, state.genki, state.hp, max_hp, data.get("veil_fp"), turn)
    fires, unknown = fire_phase(st, PLAY_PHASES, pre, profile=profile, turn=turn)
    if card_dest is None:
        new, new_level, changed = apply_stance(after, level, fires, unknown, st)
        if changed:
            after, level = new, new_level
    actions = sum(f.outcome.actions for f in fires)
    nxt = [f.next_stance for f in fires if f.next_stance]
    post = Ctx(after, level, state.remaining_turns, None, None, max_hp, None, turn)
    fires2, unknown2 = fire_phase(st, AFTER_PHASES, post, profile=profile, turn=turn)
    final, final_level, changed = apply_stance(after, level, fires2, unknown2, st)
    if changed:
        after, level = final, final_level
    actions += sum(f.outcome.actions for f in fires2)
    nxt += [f.next_stance for f in fires2 if f.next_stance]
    after, level = stance_changed(st, before, after, level, post, turn)
    if actions:
        memory.actions_left += actions
    if nxt:
        st["next_stance"] = nxt[-1] if len(set(nxt)) == 1 else None
        st["next_stance_turn"] = turn + 1
    return after, level


def end_turn(data: dict, memory, state, ended_turn: int | None, current: str | None, level: int | None):
    """回合结束（read_position 新回合分支，上回合的姿态 current）：回合结束 / 每 N 回合结束。"""
    st = data.get(KEY)
    if st is None or ended_turn is None:
        return current, level
    done = st.get("ended")
    if done is not None and done.get("turn") == ended_turn:
        # read_position 早退后重读：同一个回合结束只结算一次
        return (done["stance"], done["level"]) if done.get("input") == [current, level] else (current, level)
    ctx = Ctx(current, level, None, None, None, getattr(state, "max_hp", None), None, ended_turn)
    fires, unknown = fire_phase(st, {END_TURN, END_TURN_INTERVAL}, ctx, turn=ended_turn)
    new, new_level, changed = apply_stance(current, level, fires, unknown, st)
    if changed:
        new, new_level = stance_changed(st, current, new, new_level, ctx, ended_turn)
    else:
        new, new_level = current, level
    st["ended"] = {"turn": ended_turn, "input": [current, level], "stance": new, "level": new_level}
    return new, new_level


def start_turn(data: dict, memory, state, predicted: str | None, level: int | None):
    """回合开始（预约姿态、一番星之后）：第 1 回合先结算考试开始；再回合开始 / 回合开始后 / 每 N 回合。
    返回 (姿态, 段階, changed)。同一回合重复调用（read_position 早退后重读）返回第一次的结果，不重复发动。"""
    st = data.get(KEY)
    turn = memory.turn
    if st is None:
        return predicted, level, False
    b = st.get("boundary")
    if b is not None and b.get("turn") == turn:
        return (b["stance"], b["level"], b["changed"]) if b.get("input") == [predicted, level] else (predicted, level, False)
    ctx = Ctx(predicted, level, state.remaining_turns, None, None, getattr(state, "max_hp", None), None, turn)
    changed = False
    cur, cur_level = predicted, level
    if st.get("next_stance") and st.get("next_stance_turn") == turn and cur is not None:
        if not (cur == "のんびり" and st["next_stance"].startswith("温存")) and cur != "全力":
            cur, cur_level, changed = st["next_stance"], _stance_level(cur, cur_level, st["next_stance"]), True
    st["next_stance"] = None
    phases = {START_TURN, START_PLAY, TURN_INTERVAL}
    if b is None and turn == 1:
        phases.add(START_EXAM)  # 考试中途才装上（续跑）时不补发考试开始
    fires, unknown = fire_phase(st, phases, Ctx(**{**ctx.__dict__, "stance": cur, "level": cur_level}), turn=turn)
    new, new_level, ch = apply_stance(cur, cur_level, fires, unknown, st)
    if ch:
        new, new_level = stance_changed(st, cur, new, new_level, ctx, turn)
        cur, cur_level, changed = new, new_level, True
    st["boundary"] = {"turn": turn, "input": [predicted, level], "stance": cur, "level": cur_level, "changed": changed}
    return cur, cur_level, changed


def summary(data: dict) -> dict | None:
    """trace 用：装了什么、没装上什么、各附魔发动次数。"""
    st = data.get(KEY)
    if st is None:
        return None
    return {"items": [e["name"] for e in st["items"]], "ring": st.get("ring"),
            "unsure": [u.get("name") or u.get("asset") for u in st["unsure"]] or None,
            "unsupported": len(st["unsupported"]) or None, "fired": dict(st["fired"]) or None}


def profile_hints() -> dict:
    """偶像档案 pitem_assets：画面 asset → 实际持有的 ProduceItem id（同图标多版本时写明）。"""
    try:
        from hiro.brain.veil import PROFILE

        return dict(PROFILE.get("pitem_assets") or {})
    except Exception:  # noqa: BLE001
        return {}
