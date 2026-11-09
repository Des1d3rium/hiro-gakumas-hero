"""技能卡效果的结构化读法：主数据 ProduceCard.playEffects → ProduceExamEffect / ProduceExamTrigger。

使用者 2026-11-09：「以结构化为准。这对记账也有帮助」。牌面文字是给人看的，同一件事措辞可以不同；
playEffects 是游戏结算用的效果列表（效果类型 + 数值 + 生效条件），按它算姿态变化、全力値、追加出牌次数、抽牌、
生成眠気、使用条件，不再用正则猜文字。

结果是一个可序列化的 dict（挂在 CardView.fx 上，trace 里能看到）：
    effects       [[触发条件 id, 效果 id], …]  按结算顺序；客制化「追加」的效果并进来（apply_custom_effects）
    stance        打出时立即切到的姿态（第一个姿态效果）：強気 / 温存 / 温存2段階目 / のんびり / 全力；None = 不切
    stance_level  牌面写明的段階（強気2段階目 → 2）；普通「強気に変更」是 None（和文字读法一致，段階由 level_after 推）
    next_stance / next_stance_level   「次のターン、…に変更」（ExamEffectTimer 包着的姿态效果）
    fp_gain / fp_cost / fp_boost       全力値：立即获得 / 打出消耗（costType=全力値）/ 全力値増加量増加（百分比, 回合）
    extra_actions / draws / next_draws / genki / makes_sleep / once
    condition     使用条件（playProduceExamTriggerId）：{"kind": …, "value": …, "not": bool, "text": 原文}
    unknown       没认的效果类型（只记，不影响上面的字段）
查不到主数据（没有 card_id、没有主数据库）时 card_fx 返回 None，调用方退回文字读法。HIRO_CARD_FX=0 整个关掉。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

T = "ProduceExamEffectType_"
F = "ProduceExamFieldStatusType_"
SLEEP_ID = "p_card-00-acc-0_002"
TIMER = T + "ExamEffectTimer"

# 立即切姿态的效果类型 → 姿态名（数值是段階：1 = 普通「…に変更」，2 = 「…2段階目に変更」）
STANCE_EFFECTS = {T + "ExamConcentration": "強気", T + "ExamPreservation": "温存",
                  T + "ExamOverPreservation": "のんびり", T + "ExamFullPower": "全力"}
# 使用条件 / 效果条件里的姿态
STANCE_STATUS = {F + "ConcentrationUp": "強気", F + "PreservationUp": "温存", F + "FullPowerUp": "全力"}
COUNT_STATUS = {F + "PreservationChangeCountUp": "warm", F + "StanceChangeCountUp": "changes",
                F + "FullPowerChangeCountUp": "full", F + "ConcentrationChangeCountUp": "strong"}


def enabled() -> bool:
    return os.environ.get("HIRO_CARD_FX", "1") != "0"


def _db():
    from hiro.state.catalog import catalog

    return catalog().db


@lru_cache(maxsize=None)
def effect_row(effect_id: str) -> tuple | None:
    """(类型, 数值1, 数值2, 次数, 回合, 状态附魔 id)。"""
    try:
        row = _db().execute("SELECT effectType, effectValue1, effectValue2, effectCount, effectTurn, produceExamStatusEnchantId "
                            "FROM ProduceExamEffect WHERE id = ?", (effect_id,)).fetchone()
    except Exception:  # noqa: BLE001  没有主数据
        return None
    return tuple(row) if row else None


@lru_cache(maxsize=None)
def trigger(trigger_id: str) -> dict | None:
    """触发条件 → {kind, value, not, text}。kind：stance（value 是姿态名，level 可选）/ warm / changes / full / strong（计数，value 是次数）/
    turn（第 value 回合以后）/ other（认不出的条件，原文在 text）。"""
    if not trigger_id:
        return None
    try:
        from hiro.brain.cards import description_text

        row = _db().execute("SELECT fieldStatusCheckTypes, fieldStatusTypes, fieldStatusValues, produceDescriptions "
                            "FROM ProduceExamTrigger WHERE id = ?", (trigger_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return {"kind": "other", "value": None, "not": False, "text": trigger_id}
    checks, types, values = (json.loads(x or "[]") for x in row[:3])
    text = description_text(row[3])
    neg = any("Not" in c for c in checks)
    if len(types) != 1:
        return {"kind": "other", "value": None, "not": neg, "text": text}
    kind, value = types[0], (values[0] if values else None)
    if kind in STANCE_STATUS:
        out = {"kind": "stance", "value": STANCE_STATUS[kind], "not": neg, "text": text}
        if value in (1, 2):
            out["level"] = value
        return out
    if kind == F + "NoStance":
        return {"kind": "stance", "value": "なし", "not": neg, "text": text}  # not → いずれかの指針の場合
    if kind in COUNT_STATUS:
        return {"kind": COUNT_STATUS[kind], "value": value, "not": neg, "text": text}
    if kind == F + "TurnProgressUp":
        return {"kind": "turn", "value": (value or 0) + 1, "not": neg, "text": text}  # 2 → 3ターン目以降
    return {"kind": "other", "value": value, "not": neg, "text": text}


def timer_inner(effect_id: str) -> str | None:
    """ExamEffectTimer 的 id 里包着被延后的效果 id：e_effect-exam_effect_timer-0001-01-<内层 id>。"""
    head, sep, inner = effect_id.partition("-e_effect-")
    return "e_effect-" + inner if sep and "effect_timer" in head else None


def _stance_of(etype: str, value) -> tuple[str, int | None] | None:
    name = STANCE_EFFECTS.get(etype)
    if name is None:
        return None
    level = value if value == 2 else None
    if name == "温存" and level == 2:
        return "温存2段階目", 2  # 和文字读法 destination 一致：温存2段階目 是单独的姿态名
    return name, level


@lru_cache(maxsize=None)
def enchant_stance(enchant_id: str | None) -> tuple[str, int | None] | None:
    """回合开始、没有别的条件就切姿态的附魔 → (姿态, 段階)；其余 None。"""
    if not enchant_id:
        return None
    try:
        row = _db().execute("SELECT e.produceExamEffectIds, t.phaseTypes, t.fieldStatusTypes FROM ProduceExamStatusEnchant e "
                            "LEFT JOIN ProduceExamTrigger t ON t.id = e.produceExamTriggerId WHERE e.id = ?", (enchant_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None or "ExamStartTurn" not in str(row[1] or "") or json.loads(row[2] or "[]"):
        return None
    for eid in json.loads(row[0] or "[]"):
        inner = effect_row(eid)
        if inner and (st := _stance_of(inner[0], inner[1])):
            return st[0], (inner[1] if inner[1] == 2 else None)
    return None


def _cond_ok(cond: dict | None, current: str | None) -> bool:
    """效果自带的姿态条件，在本张牌前面已经切过姿态时能不能成立（前面没切姿态的，取决于打出时姿态，照算）。"""
    if not cond or cond["kind"] != "stance" or current is None:
        return True
    want = cond["value"]
    base = "温存" if current.startswith("温存") else current
    hit = base == want
    return not hit if cond.get("not") else hit


def build(effects, *, cost_type=None, cost_value=0, move=None, condition_id=None) -> dict:
    fx = {"effects": [list(e) for e in effects], "stance": None, "stance_level": None, "next_stance": None,
          "next_stance_level": None, "fp_gain": 0, "fp_cost": int(cost_value or 0) if "FullPowerPoint" in str(cost_type or "") else 0,
          "fp_boost": None, "extra_actions": 0, "draws": 0, "next_draws": 0, "genki": 0, "makes_sleep": False,
          "once": "Lost" in str(move or ""), "condition": trigger(condition_id), "unknown": []}
    current = None  # 本张牌里已经切到的姿态（结算顺序）
    for trig_id, eid in effects:
        row = effect_row(eid)
        if row is None:
            fx["unknown"].append(eid)
            continue
        etype, v1, _v2, count, turns, enchant_id = row
        cond = trigger(trig_id)
        if etype == TIMER:
            inner = timer_inner(eid)
            irow = effect_row(inner) if inner else None
            if irow is None:
                fx["unknown"].append(eid)
                continue
            if (st := _stance_of(irow[0], irow[1])) and fx["next_stance"] is None:
                fx["next_stance"], fx["next_stance_level"] = st[0], (irow[1] if irow[1] == 2 else None)
            elif irow[0] == T + "ExamCardDraw":
                fx["next_draws"] += int(irow[1] or 0)
            continue
        if etype == T + "ExamStatusEnchant":
            # 「以降の2ターンの間、ターン開始時、温存に変更」（バレンタイン大作戦！）：回合开始无条件切姿态的附魔，算预约的下回合姿态。
            # 带条件的（一番星「非温存状態の場合、のんびりに変更」）不算，由 VEIL 的一番星账单独记
            if (st := enchant_stance(enchant_id)) and fx["next_stance"] is None:
                fx["next_stance"], fx["next_stance_level"] = st
            continue
        if (st := _stance_of(etype, v1)) is not None:
            if fx["stance"] is None:
                fx["stance"], fx["stance_level"] = st
            current = st[0]
        elif etype == T + "ExamFullPowerPoint":
            if _cond_ok(cond, current):
                fx["fp_gain"] += int(v1 or 0)
        elif etype == T + "ExamFullPowerPointAdditive":
            fx["fp_boost"] = (int(v1 or 0) // 10, int(turns or 0))
        elif etype == T + "ExamPlayableValueAdd":
            fx["extra_actions"] += int(count or 0)
        elif etype == T + "ExamCardDraw":
            fx["draws"] += int(v1 or 0)
        elif etype in (T + "ExamBlock", T + "ExamBlockFix"):  # 元気+N / 固定元気+N
            fx["genki"] += int(v1 or 0)
        elif etype == T + "ExamCardCreateId" and SLEEP_ID in eid:
            fx["makes_sleep"] = True
    return fx


@lru_cache(maxsize=None)
def _card_fx(card_id: str, upgrade: int) -> str | None:
    try:
        row = _db().execute("SELECT playEffects, playProduceExamTriggerId, playMovePositionType, costType, costValue "
                            "FROM ProduceCard WHERE id = ? AND upgradeCount = ?", (card_id, upgrade)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    effects = [(e.get("produceExamTriggerId") or "", e.get("produceExamEffectId"))
               for e in json.loads(row[0] or "[]") if e.get("produceExamEffectId")]
    fx = build(effects, cost_type=row[3], cost_value=row[4], move=row[2], condition_id=row[1] or None)
    fx["condition_id"] = row[1] or None
    fx["cost_type"], fx["cost_value"], fx["move"] = row[3], row[4], row[2]
    return json.dumps(fx, ensure_ascii=False)


def card_fx(card_id: str | None, upgrade: int | None) -> dict | None:
    """主数据这一级（卡 id + 强化次数）的结构化效果；查不到 / 关着返回 None。每次返回新 dict，调用方可以改。"""
    if not enabled() or not card_id:
        return None
    raw = _card_fx(card_id, int(upgrade or 0))
    return json.loads(raw) if raw else None


def add_effects(fx: dict | None, effect_ids, *, delayed=None) -> dict | None:
    """客制化「追加」的效果并进来重算：立即结算的排在原效果前面，「次のターン」这类（ExamEffectTimer）排在后面
    （和文字版 apply_customs 的拼接顺序一致）。effect_ids 里有 None（认不出）时整个返回 None，退回文字读法。"""
    if fx is None:
        return None
    ids = list(effect_ids)
    if any(e is None for e in ids):
        return None
    before = [["", e] for e in ids if (effect_row(e) or ("",))[0] != TIMER]
    after = [["", e] for e in ids if (effect_row(e) or ("",))[0] == TIMER]
    have = {e for _, e in fx["effects"]}
    effects = [x for x in before if x[1] not in have] + fx["effects"] + [x for x in after if x[1] not in have]
    out = build(effects, cost_type=fx.get("cost_type"), cost_value=fx.get("cost_value"), move=fx.get("move"),
                condition_id=fx.get("condition_id"))
    for key in ("condition_id", "cost_type", "cost_value", "move"):
        out[key] = fx.get(key)
    return out


def of(card) -> dict | None:
    """CardView（或任何带 fx 属性的东西）上挂着的结构化效果；没有就 None。"""
    fx = getattr(card, "fx", None)
    return fx if enabled() and isinstance(fx, dict) else None
