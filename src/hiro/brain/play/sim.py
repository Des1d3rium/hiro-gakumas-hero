"""回合推演器（play 第 1 块）：唯一回答「打出这张牌之后局面怎么变」的地方。

姿态（含段階、のんびり 覆盖、全力下切不了）、P 道具计数（夜空：主数据触发条件 + 附魔效果）、回合开始附魔（一番星）、
出牌次数（再动、温存→強気 的温存解除、回忆能力）、全力値（消費 / 获得 / 増加量増加 / 满 10 进全力）、体力消耗（姿态倍率、
消耗增减、元気先扣）。决策（cycle / phase / objectives）、记账（veil_exam.VeilBrain）、回放审计（tools/audit/sim_replay.py）
都调这里，不再各写一份。卡牌效果以主数据结构化效果（card_fx）为准，没有时按牌面文字。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, replace
from functools import lru_cache

from hiro.brain import card_fx
from hiro.brain.play.profile import (
    FULL_POWER_AT, STAR, VEIL, _flag, enchant_runtime, fp_play_stance, hp_zero, release_gain, stance_screen,
)

WARM = {"温存", "温存1段階目", "温存2段階目", "のんびり"}  # 游戏机制，不进档案
STANCES = ("温存", "温存1段階目", "温存2段階目", "のんびり", "強気", "全力", "指針解除")


def in_stance(current, spec: str) -> bool:
    """档案里的姿态写法：warm = 温存类；!warm = 非温存类；其余按姿态名比较（!名 = 不是它）。"""
    if spec.startswith("!"):
        return not in_stance(current, spec[1:])
    return current in WARM if spec == "warm" else current == spec


# 全力値：牌面「全力値+N」「全力値消費N」「全力値増加量増加+X%（Nターン）」。只看打出时立即结算的部分：
# 「以降」「次のターン」「ターン開始時」「成長：」之后是延迟 / 常驻 / 成长效果（如 待ち望んだ瞬間 的「直接効果で全力値が増加後…全力値+1」），不在打出时记。
_FP_GAIN = re.compile(r"全力値\+(\d+)")
_FP_COST = re.compile(r"全力値消費(\d+)")
_FP_BOOST = re.compile(r"全力値増加量増加\+(\d+)[%％][（(](\d+)ターン[）)]")  # norm 后全角括号变半角
_FP_LATER = re.compile(r"以降|次のターン|ターン開始時|成長：")
# 姿态条件的全力値：「強気の場合、全力値+4」。牌面前面先「温存に変更」的，结算到这里时姿态已是温存，条件不成立
# （涙の思い出+「温存に変更 元気+4 強気の場合、全力値+4」：codex-20261108-3-b 第 1 局 R1 被无条件记 +4，
# 账本 7、画面 3，T8 按账本解灰打 アイドルになります+ 打不出，撤销后又解灰，循环卡死）
_FP_COND = re.compile(r"(非全力状態|強気|温存|のんびり|全力)の場合、全力値\+\d+")
_STANCE_SET = re.compile(r"(強気|温存|のんびり|全力)(?:\d段階目)?に変更")


def _fp_drop_false_conditions(text: str) -> str:
    """去掉牌面里「先变姿态、后接与新姿态不符的姿态条件」的全力値段；前面没有变姿态的条件取决于打出时的姿态，照旧算。"""
    out, last = [], 0
    for m in _FP_COND.finditer(text):
        changes = _STANCE_SET.findall(text, 0, m.start())
        cond = m[1]
        if changes and not (cond == changes[-1] or (cond == "非全力状態" and changes[-1] != "全力")):
            out.append(text[last:m.start()])
            last = m.end()
    return "".join(out) + text[last:]


def fp_effect(card, stance=None) -> dict:
    """牌面文字 → {gain, cost, boost: (百分比, 回合数) | None}。主数据原文：オーバードライブ+ 全力値+2、巻き返し+ 全力値消費1、
    アイドルになります+ 全力値消費3、リスキーチャンス+ 全力値+3 / 全力値増加量増加+50%（4ターン）。
    stance：打出时的姿态。VEIL_FP_PLAY_STANCE 开着、结构化效果里有带姿态条件的全力値时，条件按它判。"""
    if fx := card_fx.of(card):  # 结构化效果为准（使用者 2026-11-09）
        gain = fx["fp_gain"]
        if stance is not None and fx.get("fp_cond") and fp_play_stance():
            gain -= sum(v for _, v, counted in fx["fp_cond"] if counted)
            gain += sum(v for cond, v, _ in fx["fp_cond"] if card_fx._cond_ok(cond, stance))
        return {"gain": gain, "cost": fx["fp_cost"], "boost": tuple(fx["fp_boost"]) if fx["fp_boost"] else None}
    text = _fp_drop_false_conditions(_FP_LATER.split(norm(getattr(card, "text", card) or ""), maxsplit=1)[0])
    boost = _FP_BOOST.search(text)
    return {"gain": sum(map(int, _FP_GAIN.findall(text))), "cost": sum(map(int, _FP_COST.findall(text))),
            "boost": (int(boost[1]), int(boost[2])) if boost else None}


def fp_delta(card, stance=None) -> tuple[int, int]:
    """牌面文字里的（全力値+N, 全力値消費N）。stance 见 fp_effect。"""
    eff = fp_effect(card, stance)
    return eff["gain"], eff["cost"]


STRONG2_DIRECT = 1  # 強気 2 段階：每张牌另外直接扣 1（使用者 2026-11-07 确认就是 1）
ISSHIN_COST_ADD = 7  # 一心不乱+：強気効果のスキルカードの…コスト値増加+7（主数据，重複不可，本场一直有效）
MORIAGE_COST_ADD = 1  # 盛り上げ上手：アクティブスキルカードの…コスト値増加+1（主数据）
# 打出后本场一直有效的消耗增减：「（メンタル|アクティブ|強気効果の|すべての）スキルカードの…コスト値増加+N / 減少-N」。
# 使用者 2026-11-07：一心不乱、盛り上げ上手 增加，巻き返し 减少；1-e R2 T1 インフルエンサー+（メンタル +1）没算，
# モチベ+ 估 4 实际 7（(2+1)×2+1）、ひと呼吸+ (7+1)×2+1 = 17
_COST_MOD = re.compile(r"(メンタル|アクティブ|強気効果の|すべての)スキルカードの[^。]{0,24}?コスト値(?:増加|減少)([+-]?\d+)")


def cost_mods(plays, text_of) -> list[tuple[str, int]] | None:
    """本场打过的牌（[(回合, 打/喝, 名字)]）→ [(范围, 增减)]；范围 mental / active / strong / all。没有出牌记录 None（退回按状态名估）。"""
    if not plays:
        return None
    out = []
    for _, kind, name in plays:
        if kind != "打":
            continue
        for scope, n in _COST_MOD.findall(norm(text_of(name) or "")):
            out.append(({"メンタル": "mental", "アクティブ": "active", "強気効果の": "strong", "すべての": "all"}[scope], int(n)))
    return out


def _master_text(name):
    try:
        from hiro.brain.cards import description_text, select

        row = select("SELECT produceDescriptions FROM ProduceCard WHERE name = ?;", name)
    except Exception:  # noqa: BLE001
        return None
    return description_text(row[0]) if row else None


def _status_value(status) -> int:
    v = (status or {}).get("value")
    return int(v) if isinstance(v, (int, float)) and v > 0 else 0


SCREEN_STANCE = "_screen_stance"  # veil_exam 放进 statuses：画面姿态和账本不一致时的画面读数（VEIL_STANCE_SCREEN）


def hp_loss(state, card, current, mods=None, *, spent=False):
    """打出这张牌实际掉的体力（估计，宁高勿低）。VEIL_STANCE_SCREEN：current 就是账本姿态、画面读到别的姿态时，取掉血多的。"""
    loss = _hp_loss_at(state, card, current, mods, spent=spent)
    if stance_screen() and current is not None and current == stance(state):
        seen = next((s for s in state.statuses or [] if s.get("name") == SCREEN_STANCE), None)
        if seen and seen.get("value") and seen["value"] != current:
            level = seen.get("level") if seen.get("level") in (1, 2) else None
            loss = max(loss, _hp_loss_at(state, card, seen["value"], mods, spent=spent, level=level, use_level=True))
    return loss


def _hp_loss_at(state, card, current, mods=None, *, spent=False, level=None, use_level=False):
    """打出这张牌实际掉的体力（估计，宁高勿低）。

    主数据（2026-11-07 核对）：コスト值先加一心不乱（強気効果的牌 +7）、盛り上げ上手（アクティブ +1）；
    姿态倍率 強気 ×2（2 段階另外每张直接扣 1）、温存 1 段階 ×0.5、2 段階 ×0.25、のんびり ×0；先扣元気。
    未在主数据文本核实、按保守处理：消費体力増加 ×2、減少 ×0.5，消費体力追加 / 削減 按状态数值；体力消費（直接扣）也乘姿态倍率、不被元気挡。
    spent=True：算花掉的元気 + 体力（不先扣元気），给 usable_net 用。"""
    import math

    names = {norm(s.get("name") or ""): s for s in state.statuses or []}
    cost = card.cost or 0
    if mods is not None:
        # 按本场出牌记录里的牌面效果算（一心不乱、盛り上げ上手、インフルエンサー、巻き返し…）
        for scope, n in mods:
            if (scope == "all" or scope == card.category or (scope == "strong" and destination(card) == "強気")):
                cost += n
        cost = max(0, cost)
    else:
        if "一心不乱" in names and destination(card) == "強気":
            cost += ISSHIN_COST_ADD
        if "盛り上げ上手" in names and card.category == "active":
            cost += MORIAGE_COST_ADD
    mult, per_card = 1.0, 0
    if current == "強気":
        mult = 2.0
        # 強気 2 段階：每张另外直接扣 STRONG2_DIRECT。实测 1-e R2 T1：第一印象++（cost 4，強気 1）33→25 = 4×2；
        # モチベ+（cost 2，強気 2）25→18 = 2×2+3；ひと呼吸+（cost 7，強気 2）18→1 = 7×2+3（原来按 +1 估，低估 2）
        # 段階不明时按二阶段估计，避免低估扣血。
        per_card = STRONG2_DIRECT if (level if use_level else stance_level(state, current)) != 1 else 0
    elif current == "のんびり":
        mult = 0.0
    elif current in WARM:
        mult = 0.25 if (level if use_level and current == "温存" else stance_level(state, current)) == 2 else 0.5
    if "消費体力増加" in names:
        mult *= 2.0  # 原按 1.5；游戏里应是翻倍（未在主数据核实），按高估
    if "消費体力減少" in names:
        mult *= 0.5
    # 固定值：消費体力追加 N 在倍率前加（高估），消費体力削減 N 在倍率后减（少减，高估）。
    # 体力消費軽減 / 消費体力変化 效果未核实、状态栏也读不到数值：不减（宁高勿低）
    add_fix = _status_value(names.get("消費体力追加"))
    cut_fix = _status_value(names.get("消費体力削減"))
    scaled = max(0, math.ceil((cost + add_fix) * mult) - cut_fix)
    direct = math.ceil((card.hp_cost or 0) * max(mult, 1.0))  # 体力消費：只往高估，温存 / のんびり 不减
    if hp_zero() and _status_value(names.get("消費体力変化")) > 0:
        # 全身全霊+「次に使用したスキルカードの消費体力を0にする（2回）」：状态栏「消費体力変化 N」还有次数，这一张不掉体力
        # （元気也不花）。強気 2 段階的直接扣 1 没核实，照旧算（宁高勿低）
        scaled = direct = 0
    paid = max(0, scaled - (state.genki or 0))
    if spent:
        return scaled + direct + per_card
    return paid + direct + per_card


_GENKI_GAIN = re.compile(r"元気\+(\d+)")


def genki_gain(card) -> int:
    """牌面「元気+N」（打出时立刻加的元気；「元気値増加+N」这类加成不算）。"""
    if fx := card_fx.of(card):
        return fx["genki"]
    return sum(int(n) for n in _GENKI_GAIN.findall(norm(card.text or "")))


def usable_net(state, card, current, mods=None) -> int:
    """打出这张后可用体力（体力 + 元気）的变化：加的元気 − 花掉的（元気 + 体力）。≥ 0 表示可用体力不减。"""
    return genki_gain(card) - hp_loss(state, card, current, mods, spent=True)

def norm(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def stance(state):
    names = {norm(s.get("name") or "") for s in state.statuses or []}
    return next((name for name in STANCES if name in names), None)


def stance_level(state, current):
    if current is None:
        return None
    if current in ("温存1段階目", "温存2段階目"):
        return 2 if current == "温存2段階目" else 1
    return next((s["value"] for s in state.statuses or []
                 if norm(s.get("name") or "") == current and s.get("value") in (1, 2)), None)


# 「強気2段階目に変更」（心・技・体）也是切強気：原来认不出，当成不改姿态（验证局 R2 剩 11 回合：没算它的温存解除 +1）
_STANCE_CHANGE = re.compile(r"(強気|温存(?:[12]段階目)?|のんびり|全力)(?:[12]段階目)?に変更|指針を解除")
_DELAYED = re.compile(r"次のターン|ターン開始|以降")


def destination(card):
    if fx := card_fx.of(card):
        return fx["stance"]
    immediate = _DELAYED.split(norm(card.text), maxsplit=1)[0]
    match = _STANCE_CHANGE.search(immediate)
    return (match[1] or "指針解除") if match else None


def stance_change_level(card):
    if fx := card_fx.of(card):
        return fx["stance_level"]
    immediate = _DELAYED.split(norm(card.text), maxsplit=1)[0]
    match = _STANCE_CHANGE.search(immediate)
    if match:
        for level in (1, 2):
            if f"{level}段階目" in match[0]:
                return level
    return None


def level_after(before, before_level, dest, explicit=None):
    """切到 dest 之后的段階（強気 / 温存 才有）：牌面写明「N段階目」用 N；已经在同一姿态再切一次 → 2 段階（封顶）；
    从别的姿态切进来 → 1 段階。のんびり / 全力 / 指針解除 没有段階（None）。
    使用者 2026-11-08：「把强气阶段几，温存阶段几也都计入」——原来普通的「強気に変更」记成段階不明，体力闸按 2 段階多估 1 点。"""
    base = lambda s: "温存" if s and s.startswith("温存") else s  # noqa: E731
    if base(dest) not in ("強気", "温存"):
        return None
    if explicit in (1, 2):
        return explicit
    if m := re.search(r"([12])段階目", dest):
        return int(m.group(1))
    return 2 if base(before) == base(dest) else 1


def next_stance_change(card):
    if fx := card_fx.of(card):
        return fx["next_stance"], fx["next_stance_level"]
    match = re.search(r"(?:次のターン(?:開始時)?|ターン開始時)[、,に：:]*(強気|温存|のんびり|全力)(?:([12])段階目)?に変更",
                      norm(card.text))
    if match is None:
        return None, None
    name, stage = match.groups()
    if name == "温存" and stage:
        name = f"温存{stage}段階目"
    return name, int(stage) if stage else None


def next_stance(card):
    return next_stance_change(card)[0]

NEUTRAL = "指針なし"  # 考试第 1 回合还没切过姿态（画面没有姿态图标）：切強気不推夜空计数，也没有温存解除


HAND_SWAP = "ProduceExamEffectType_ExamHandGraveCountCardDraw"  # 手札をすべて入れ替える


def swaps_hand(card) -> bool:
    """这张牌会不会换掉整手牌。结构化效果为准（效果类型 ExamHandGraveCountCardDraw），没有时按牌面文字。"""
    if (fx := card_fx.of(card)) is not None:
        from hiro.brain.pitems import exam_effect

        try:
            return any((e := exam_effect(eid)) is not None and e.type == HAND_SWAP for _, eid in fx.get("effects") or [])
        except Exception:  # noqa: BLE001
            pass
    return "手札をすべて入れ替える" in norm(card.text or "")


def start_turn(current, star):
    """回合开始：一番星还有次数且不在温存 / のんびり 时切のんびり，返回（姿态，剩余次数）。姿态不明时不动。
    VEIL_ENCHANT_ENGINE（默认开）：触发条件和效果按主数据的附魔（hiro.brain.pitems）判 / 执行，主数据不可用时按下面的手写规则。"""
    if current is None or not (star or 0) > 0:
        return current, star
    if enchant_runtime() and (got := _start_turn_runtime(current, star)) is not None:
        return got
    if _flag("ENCHANT_ENGINE"):
        from hiro.brain.pitems import card_start_turn_enchant, start_turn_fire

        try:
            found = card_start_turn_enchant(STAR)
        except Exception:  # noqa: BLE001  没有主数据
            found = None
        if found is not None:
            out = start_turn_fire(found[0], current)
            return ((out.stance or current), star - 1) if out is not None else (current, star)
    if current not in WARM:
        return "のんびり", star - 1
    return current, star


def warm_next_turn(state, memory=None) -> bool:
    """下回合开局会不会自己进温存 / のんびり：预约「次のターン…温存 / のんびりに変更」，或一番星还有次数。
    几种来源同时存在也只进一次温存（使用者 2026-11-07），所以只回答有没有。"""
    for s in state.statuses or []:
        label = norm(s.get("name") or "")
        if ("次のターン" in label or "ターン開始" in label) and ("温存" in label or "のんびり" in label):
            return True
        if label.startswith(STAR) and (s.get("value") or 0) > 0:
            return True
    return bool(memory is not None and (memory.strategy_data.get("veil_star") or 0) > 0)


def stance_requirement(card):
    """使用条件里要求的姿态（強気 / 温存 / 全力），没有姿态条件 None。结构化效果为准，没有时按牌面文字。"""
    if (fx := card_fx.of(card)) is not None:
        cond = fx.get("condition") or {}
        return cond.get("value") if cond.get("kind") == "stance" and not cond.get("not") else None
    t = norm(card.text or "")
    return next((s for s in ("強気", "温存", "全力") if f"{s}の場合、使用可" in t or (s == "強気" and "強気2段階目の場合、使用可" in t)), None)


def keeps_nonbiri(current, dest):
    """のんびり 覆盖温存：温存 → のんびり 可以，のんびり 收到「温存に変更」（任一段階）仍是のんびり（使用者 2026-11-07，
    1-g R2 再挑戦3 剩 9：のんびり 下打 アイドルになります+，画面仍のんびり、账本记成温存）。"""
    return current == "のんびり" and dest is not None and dest.startswith("温存")


def flower_counts(current, card):
    """打出这张牌推不推夜空计数（主数据 P 道具触发条件）；判断不了 None。"""
    from hiro.brain.pitems import flower_counts as counts

    if current is None:
        return None  # 姿态不明（不是「指針なし」）：交给手写规则
    return counts(card, current)


def card_stance(current, card):
    """只结算牌自己的姿态切换（のんびり 不被温存覆盖、全力 下切不了、姿态不明时切強気仍不明）。"""
    dest = destination(card)
    if keeps_nonbiri(current, dest) or current == "全力":
        return current
    if dest == "強気" and current is None:
        return None  # 姿态不明
    return dest or current


# ---------------------------------------------------------------- 通用附魔引擎（VEIL_ENCHANT_RUNTIME）

def _engine():
    from hiro.brain.play import anomaly  # noqa: F401  登记アノマリー的条件和效果
    from hiro.brain.play import enchants

    return enchants


@lru_cache(maxsize=None)
def _counter_spec():
    """profile 的计数器 P 道具（VEIL：夜空を従える花+）的附魔定义；没有主数据 None。"""
    try:
        specs = [s for s in _engine().pitem_specs(VEIL.counter_item) if s.trigger.play_interval] if VEIL.counter_item else []
    except Exception:  # noqa: BLE001
        return None
    return specs[0] if specs else None


@lru_cache(maxsize=None)
def _start_turn_spec():
    """profile 的回合开始附魔牌（VEIL：一番星）打出时装上的附魔定义。"""
    from hiro.brain.pitems import card_start_turn_enchant

    try:
        found = card_start_turn_enchant(STAR) if STAR else None
    except Exception:  # noqa: BLE001
        return None
    if found is None:
        return None
    en, count = found
    return _engine().Spec(STAR, "card", en.trigger, en.effects, count, None, en.text)


def _after_play_runtime(current, flower, card):
    """after_play 走引擎：牌先结算姿态，夜空按主数据触发条件在「出牌时 / 出牌后」计数，到了执行它的效果。
    引擎判断不了（没有主数据、条件判不了）而计数原本已知时返回 None，调用方退回原来的规则。"""
    spec = _counter_spec()
    if spec is None:
        return None
    eng = _engine()
    new = card_stance(current, card)
    inst = eng.install(spec)
    inst = inst.with_remaining(flower) if flower in (1, 2, 3) else replace(inst, count=None)
    insts, _ = eng.dispatch((inst,), eng.Event(eng.CARD_PLAY, stance=current, current=current, card=card))
    insts, fired = eng.dispatch(insts, eng.Event(eng.CARD_PLAY_AFTER, stance=current, current=new, card=card))
    if insts[0].remaining is None and flower in (1, 2, 3):
        return None
    for _, out in fired:
        if out is not None and out.stance:
            new = out.stance
    return new, insts[0].remaining


def _start_turn_runtime(current, star):
    spec = _start_turn_spec()
    if spec is None:
        return None
    eng = _engine()
    insts, fired = eng.dispatch((eng.Inst(spec, left=star),), eng.Event(eng.START_TURN, stance=current, current=current))
    for _, out in fired:
        if out is not None and out.stance:
            current = out.stance
    return current, insts[0].left


def _after_play_counted(current, flower, card, counted):
    """已知推不推计数时的结算：先结算牌的姿态，再结算夜空（计数到了切のんびり、回到 3；全力 下切不了姿态，计数照样走）。"""
    new = card_stance(current, card)
    if not counted:
        return new, flower
    if flower not in (1, 2, 3):
        return new, None
    if flower == 1:
        # 计数到了：效果按主数据执行（夜空を従える花：のんびりに変更、熱意、元気；全力 下切不了姿态）
        from hiro.brain.pitems import flower_trigger_outcome

        out = flower_trigger_outcome(current) if _flag("ENCHANT_ENGINE") else None
        if out is not None:
            return (out.stance or new), 3
        return (current if current == "全力" else "のんびり"), 3
    return new, flower - 1


def after_play(current, flower, card):
    """Resolve the card first, then the flower's overriding stance effect.
    VEIL_PITEM_FLOWER（默认开，=0 关）：推不推夜空计数按主数据的 P 道具触发条件判（hiro.brain.pitems：
    「いずれかの指針の場合、強気効果のスキルカード使用後3回ごとに」= 带「強気」效果组的牌、有指针时才算），
    判断不了（没有卡 id / 主数据）时退回下面的手写规则。2026-11-09 回放 54 份 trace，52 份每帧和画面「N回」一致。"""
    if enchant_runtime() and current is not None and (got := _after_play_runtime(current, flower, card)) is not None:
        return got
    counted = flower_counts(current, card) if _flag("PITEM_FLOWER") else None
    if counted is not None:
        return _after_play_counted(current, flower, card, counted)
    dest = destination(card)
    if keeps_nonbiri(current, dest):
        return current, flower
    if current == "全力":
        # 全力 下切不了姿态，但「強気」效果照样推夜空计数，到 1 时切不了のんびり、计数回到 3（使用者 2026-11-07）
        if dest == "強気" and flower in (1, 2, 3):
            return current, 3 if flower == 1 else flower - 1
        return current, flower
    if dest != "強気" or current == "指針解除":
        return dest or current, flower
    if current == NEUTRAL:
        # 夜空要在任意姿态下用強気才计数，没有指针不算指针（使用者 2026-11-08）；指針解除 同理（上面已直接返回）。
        # 考试第 1 回合从「指針なし」切強気不推夜空计数（2026-11-08 核对：1-g R2 全身全霊+ 后画面「3回」不变；
        # 第 1 回合从指針なし 切強気的 5 份 trace（1-g-1-exam2、3-1-exam1-attempt1、3-b-1-exam1-attempt2、
        # 3-c-1-exam2-attempt1、3-c-3-exam2）账本都从这里起比画面少 1，之后のんびり 触发全错位）
        return dest, flower
    if current is None or flower not in (1, 2, 3):
        return None, None
    return ("のんびり", 3) if flower == 1 else (dest, flower - 1)


# ---------------------------------------------------------------- 出牌次数（全部调用处共用）

def release(card, cur, fl, mode=None) -> bool:
    """打这张牌拿不拿得到温存解除（温存 / のんびり 下切強気 +1 次出牌）。
    mode=None 按 VEIL_RELEASE_GAIN：开着看牌自己的切换（先结算牌，夜空 1→のんびり 在后），关着看夜空覆盖后的最终姿态；
    "dest" 永远看牌自己的切换，"after" 永远看最终姿态（这两个只给重构前各自写死口径的调用处用，VEIL_SIM_UNIFIED=1 时不用）。"""
    if cur not in WARM:
        return False
    if mode is None:
        mode = "dest" if release_gain() else "after"
    if mode == "dest":
        return destination(card) == "強気"
    return after_play(cur, fl, card)[0] == "強気"


def gain(card, cur, fl, *, ability=None, mode=None) -> int:
    """打出这张牌新加的出牌次数（不含它自己用掉的 1 次）：牌面再动 + 温存解除 + 回忆能力（再动 / 再动并抽牌）。
    ability：回忆能力的种类（memory_ability.pending 的 kind），None 表示不算。"""
    return card.tags.extra_actions + int(release(card, cur, fl, mode)) + int(ability in ("draw", "play"))


def resolve(before, before_level, flower, card):
    """出牌后的（姿态，段階，夜空）：VeilBrain 记账与 TurnState.step 共用。"""
    current, flower = after_play(before, flower, card)
    level = (level_after(before, before_level, current, stance_change_level(card))
             if destination(card) == current else None)
    if current == before and destination(card) != current:
        level = before_level
    return current, level, flower


# ---------------------------------------------------------------- 全力値（账本与推演共用）

def fp_apply(fp, boosts, turn, rec):
    """一次出牌 / 喝饮料：先扣消費，再加获得（获得吃本回合生效的全力値増加量増加），最后挂上这张给的増加量増加。
    増加量増加的取整方式未在主数据里核实，按向上取整（宁高勿低会让账本偏乐观，所以 log 里记 pct 供实机核对）。"""
    pct = sum(b["pct"] for b in boosts if b["until"] >= turn)
    got = math.ceil(rec["gain"] * (100 + pct) / 100) if rec["gain"] and pct else rec["gain"]
    fp = max(0, fp - rec["cost"]) + got
    if rec.get("boost"):
        boosts = [*boosts, {"pct": rec["boost"][0], "until": turn + rec["boost"][1] - 1}]  # （Nターン）含本回合
    return fp, boosts, got, pct


def fp_next_turn(fp, boosts, per_turn, turn):
    """进入 turn：上回合结束 ≥10 → 扣 10（进全力）；再加「以降、ターン開始時、全力値+N」；去掉过期的増加量増加。"""
    ops = []
    if fp >= FULL_POWER_AT:
        fp -= FULL_POWER_AT
        ops.append("全力")
    if per_turn:
        fp += per_turn
        ops.append(f"开局+{per_turn}")
    return fp, [b for b in boosts if b["until"] >= turn], ops


# ---------------------------------------------------------------- 回合内局面

@dataclass(frozen=True)
class TurnState:
    """推演用的回合内局面。None = 不知道。"""

    stance: str | None
    flower: int | None
    acts: int
    fp: int | None = None
    level: int | None = None
    next_stance: str | None = None
    next_level: int | None = None
    star: int | None = None  # 回合开始附魔（一番星）剩余次数
    turn: int = 0
    boosts: tuple = ()
    enchants: tuple = ()  # 其余附魔实例（enchants.Inst）：打出的牌装上的、开考装上的 P 道具；VEIL_ENCHANT_RUNTIME 时才动

    def step(self, card, *, ability=None, mode=None, boosts=True) -> "TurnState":
        """打出 card：先扣这一次出牌、加它带来的次数，再结算姿态 / 夜空 / 段階 / 全力値 / 下回合预约姿态。
        boosts=False：全力値只按「消費 / 获得」加减，不挂这张牌给的増加量増加（进循环规划重构前的口径）。"""
        acts = self.acts + gain(card, self.stance, self.flower, ability=ability, mode=mode) - 1
        stance, level, flower = resolve(self.stance, self.level, self.flower, card)
        fp, held = self.fp, self.boosts
        if fp is not None:
            eff = fp_effect(card, self.stance)
            fp, blist, _, _ = fp_apply(fp, list(held), self.turn, {**eff, "boost": eff["boost"] if boosts else None})
            held = tuple(blist)
        nxt, nxt_level = next_stance_change(card)
        if nxt is None:
            nxt, nxt_level = self.next_stance, self.next_level
        out = replace(self, acts=acts, stance=stance, level=level, flower=flower, fp=fp, boosts=held,
                      next_stance=nxt, next_level=nxt_level)
        return out._play_enchants(card, self.stance) if enchant_runtime() else out

    def _apply(self, fired) -> "TurnState":
        """附魔触发结果落到局面：姿态、出牌次数、全力値。"""
        st = self
        for _, o in fired:
            if o is None:
                continue
            if o.stance and o.stance != st.stance:
                st = replace(st, stance=o.stance, level=o.level)
            if o.delta.get("actions"):
                st = replace(st, acts=st.acts + o.delta["actions"])
            if o.delta.get("fp") and st.fp is not None:
                st = replace(st, fp=st.fp + o.delta["fp"])
        return st

    def _fire(self, phase, before=None, card=None) -> "TurnState":
        if not self.enchants:
            return self
        eng = _engine()
        stance = self.stance if before is None else before
        insts, fired = eng.dispatch(self.enchants, eng.Event(phase, turn=self.turn, stance=stance, current=self.stance,
                                                             card=card))
        return replace(self, enchants=insts)._apply(fired)

    def _play_enchants(self, card, before) -> "TurnState":
        """出牌时 / 出牌后触发已装上的附魔，再装上这张牌自己的附魔（一番星记进 star，其余进 enchants）。"""
        eng = _engine()
        st = self._fire(eng.CARD_PLAY, before, card)._fire(eng.CARD_PLAY_AFTER, before, card)
        added = []
        for spec in eng.card_specs(card):
            star_spec = _start_turn_spec()
            if star_spec is not None and spec.key == star_spec.key:
                st = replace(st, star=(st.star or 0) + (spec.limit or 0))
            else:
                added.append(eng.install(spec, st.turn))
        return replace(st, enchants=st.enchants + tuple(added)) if added else st

    def drink(self, extra: int = 1) -> "TurnState":
        """喝一瓶加出牌次数的饮料（ホエイ）。"""
        return replace(self, acts=self.acts + extra)

    def new_turn(self) -> "TurnState":
        """回合交界：预约姿态生效（のんびり 不被温存覆盖）、一番星附魔、全力値满 10 扣 10、出牌次数回到 1。
        VEIL_ENCHANT_RUNTIME：其余附魔在回合结束、下回合开始（抽牌前 / 后）触发。"""
        if enchant_runtime() and self.enchants:
            eng = _engine()
            ended = self._fire(eng.END_TURN)
            return replace(ended, enchants=eng.new_turn(ended.enchants))._advance()
        return self._advance()

    def _advance(self) -> "TurnState":
        scheduled = self.next_stance if not keeps_nonbiri(self.stance, self.next_stance) else None
        stance = scheduled or self.stance
        level = level_after(self.stance, self.level, scheduled, self.next_level) if scheduled else self.level
        stance2, star = start_turn(stance, self.star)
        if stance2 != stance:
            level = None
        fp = self.fp
        if fp is not None:
            fp, _, _ = fp_next_turn(fp, [], 0, self.turn + 1)
        nt = replace(self, stance=stance2, level=level, star=star, fp=fp, acts=1, next_stance=None, next_level=None,
                     turn=self.turn + 1)
        if enchant_runtime() and nt.enchants:
            eng = _engine()
            nt = nt._fire(eng.START_TURN)._fire(eng.START_PLAY)
        return nt
