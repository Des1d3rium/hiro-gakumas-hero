"""通用附魔引擎：P 道具、技能卡附魔、支援卡道具共用一套「触发 + 条件 + 效果 + 次数」。

主数据里这三种来源是同一种结构（docs/play-modules.md 第六节）：
- P 道具 / 支援卡道具：考试开始就装上，次数 = 「レッスン内N回」（ProduceItemEffect.effectCount，0 = 不限）；
  支援卡道具多一个回合属性条件（【ダンスターンのみ】= Trigger.lesson）。
- 技能卡附魔：打出时装上，次数 = 「以降N回まで」。

推演（sim.TurnState）和账本（hiro.brain.pitem_engine：持有的 P 道具、打出的牌装上的附魔）都用这一套：
在固定时机发 Event，dispatch 对每个附魔实例判触发 → 判条件 → 执行效果，返回新的实例列表和触发结果。
条件、效果按字段 / 效果类型登记（register_condition / register_effect）：通用的登记在这里，流派资源（アノマリー的姿态、
全力値；以后的センス、ロジック）登记在各自的模块（hiro.brain.play.anomaly）。没登记的条件判「不知道」（None），
没登记的效果记进 Outcome.unknown，不动局面。这里不写任何偶像 / 道具的名字。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Callable

P = "ProduceExamPhaseType_"

# 事件时机（推演发的）
START_EXAM = P + "ExamStartExam"  # レッスン開始時
START_EXAM_PLAY = P + "StartExamPlay"  # レッスン開始後（开局抽牌后）
START_TURN = P + "ExamStartTurn"  # ターン開始時（抽牌前）
START_PLAY = P + "StartPlay"  # ターン開始後（抽牌后）
CARD_PLAY = P + "ExamCardPlay"  # スキルカード使用時
CARD_PLAY_AFTER = P + "ExamCardPlayAfter"  # スキルカード使用後（牌的效果结算完）
END_TURN = P + "ExamEndTurn"
TO_STRONG = P + "ExamStanceChangeConcentration"  # 姿态变成 強気 / 全力 / 温存、离开全力（推演发、只结算一层）
TO_FULL = P + "ExamStanceChangeFullPower"
TO_WARM = P + "ExamStanceChangePreservation"
FROM_FULL = P + "ExamStanceChangeFromFullPower"
STANCE_EVENTS = frozenset({TO_STRONG, TO_FULL, TO_WARM, FROM_FULL})

# 计数型触发：在哪个事件上计数、计数器是否每回合清零。值 = 触发条件的 phaseValue（每 N 次 / 每 N 回合 / 第 N 回合）。
COUNTED = {
    P + "ExamPlayCountInterval": (CARD_PLAY, False),
    P + "ExamPlayCountIntervalAfter": (CARD_PLAY_AFTER, False),
    P + "ExamPlayTurnCountInterval": (CARD_PLAY, True),
}
TURN_PHASES = {  # 按回合号触发：(事件, 判定)
    P + "ExamTurnInterval": (START_TURN, lambda turn, n: n and turn % n == 0),
    P + "ExamEndTurnInterval": (END_TURN, lambda turn, n: n and turn % n == 0),
    P + "ExamTurnTimer": (START_TURN, lambda turn, n: turn == n),
}
CARD_EVENTS = frozenset({CARD_PLAY, CARD_PLAY_AFTER})
# 这套引擎认得的触发时机；其余（状态变化、体力减少、跳过回合、除外…）装的时候记成 unsupported，永远不触发
PHASES_SUPPORTED = frozenset({START_EXAM, START_EXAM_PLAY, START_TURN, START_PLAY, CARD_PLAY, CARD_PLAY_AFTER, END_TURN,
                              *COUNTED, *TURN_PHASES, *STANCE_EVENTS})

LEVELS = frozenset({1, 2})  # 強気 / 温存 的段階
COMMON = "ProducePlanType_Common"
# 「某计划的牌」算不算 Common 牌（2026-11-09 实机核对）：pitems.CardSearch.matches 把 Common 当成符合任意 planType；
# VEIL 81 份 trace 带 ゼミパーカー（搜索 planType = Plan1），按道具角标看发动：通用牌单张打出 138 次一次都没减，
# Plan1 牌 10 次每次都减。所以不算。设成 None = 未核对，只因 Common 才匹配时判「不知道」。
COMMON_PLAN_MATCHES: bool | None = False


@dataclass(frozen=True)
class Event:
    """一次事件。stance：判条件用的姿态；current：执行效果时的姿态（出牌后 = 牌结算完的姿态）。
    card：出牌事件的牌（CardView）；attribute：本回合属性（Vo / Da / Vi，不知道 None）；level：stance 的段階（強気 / 温存）。
    fields：资源当前值，键用资源名（genki / hp / hp_max / fp），不知道就不给。"""

    phase: str
    turn: int = 0
    stance: str | None = None
    current: str | None = None
    card: object = None
    attribute: str | None = None
    remaining_turns: int | None = None
    fields: dict = field(default_factory=dict)  # 资源当前值（genki / hp / hp_max / fp）
    level: int | None = None


@dataclass
class Outcome:
    """一次触发对局面的改变。delta：资源增减（actions / draws / genki / fp …）；stance / level：要切到的姿态；
    next_stance：「次のターン、…に変更」预约的下回合姿态；score：只影响分数的效果（不动局面）；unknown：没建模的效果类型。"""

    source: str = ""
    stance: str | None = None
    level: int | None = None
    delta: Counter = field(default_factory=Counter)
    unknown: list = field(default_factory=list)
    next_stance: str | None = None
    score: list = field(default_factory=list)


@dataclass(frozen=True)
class Spec:
    """附魔的静态定义（主数据编译出来的）。key：来源 id（P 道具 id / 牌名 / 附魔 id）。"""

    key: str
    source: str  # "pitem" / "card" / "support"
    trigger: object  # pitems.Trigger
    effects: tuple
    limit: int | None = None
    turns: int | None = None  # 持续回合；None = 整场
    text: str = ""
    fire_limit: int = 0  # 道具 fireLimit（一场几次，0 = 不限）；主数据里带它的道具都没有考试附魔，按字面实现
    fire_interval: int = 0  # 道具 fireInterval（隔几回合才能再发动）

    @property
    def may_change_stance(self) -> bool:
        """效果里有没有切姿态（含「次のターン、…に変更」）：判断不了触发没触发时，用它决定账本能不能照原来的规则走。"""
        return _changes_stance(self.effects)


@dataclass(frozen=True)
class Inst:
    """附魔实例：剩余次数（None = 不限）、计数器（None = 有一手判断不了，之后不知道）、本回合计数、截止回合。"""

    spec: Spec
    left: int | None = None
    count: int | None = 0
    until: int | None = None
    fired: int = 0  # 发动过几次（fire_limit 用）
    last: int | None = None  # 上次发动的回合（fire_interval 用）

    @property
    def interval(self) -> int | None:
        for phase, value in self.spec.trigger.phases:
            if phase in COUNTED and value:
                return int(value)
        return None

    @property
    def remaining(self) -> int | None:
        """计数型：还差几次触发（画面道具旁的「N回」）。不是计数型或不知道 None。"""
        n = self.interval
        if n is None or self.count is None:
            return None
        return n - self.count % n

    def with_remaining(self, remaining: int) -> "Inst":
        """按画面「N回」重设计数器。"""
        n = self.interval
        return replace(self, count=(n - remaining) % n) if n else self


# ---------------------------------------------------------------- 登记

ConditionFn = Callable[[object, Event], "bool | None"]
EffectFn = Callable[[object, Outcome, Event], None]
CONDITIONS: dict[str, ConditionFn] = {}
EFFECTS: dict[str, EffectFn] = {}


def register_condition(field_type: str, fn: ConditionFn) -> None:
    CONDITIONS[field_type] = fn


def register_effect(effect_type: str, fn: EffectFn) -> None:
    EFFECTS[effect_type] = fn


def _delta(key):
    def fn(e, out, ev):
        out.delta[key] += e.value1 if key != "actions" else (e.count or e.value1 or 1)
    return fn


register_effect("ProduceExamEffectType_ExamPlayableValueAdd", _delta("actions"))  # スキルカード使用数追加
register_effect("ProduceExamEffectType_ExamCardDraw", _delta("draws"))
register_effect("ProduceExamEffectType_ExamBlock", _delta("genki"))  # 元気


def _remaining_turn(cond, ev):
    if ev.remaining_turns is None or cond.value is None:
        return None
    hit = ev.remaining_turns <= int(cond.value)  # 残りNターン以内
    return (not hit) if cond.negate else hit


register_condition("ProduceExamFieldStatusType_RemainingTurn", _remaining_turn)


def _res(name, test):
    def fn(cond, ev):
        got = ev.fields.get(name)
        if got is None or (cond.value is None and test is not _zero):
            return None
        hit = test(got, cond.value, ev)
        if hit is None:
            return None
        return (not hit) if cond.negate else hit
    return fn


def _zero(got, value, ev):
    return got == 0


def _hp_ratio(up):
    def test(got, value, ev):
        hp_max = ev.fields.get("hp_max")
        if not hp_max:
            return None
        return got * 1000 >= hp_max * value if up else got * 1000 <= hp_max * value  # 千分比
    return test


register_condition("ProduceExamFieldStatusType_BlockUp", _res("genki", lambda got, v, ev: got >= v))  # 元気N以上
register_condition("ProduceExamFieldStatusType_NoBlock", _res("genki", _zero))  # 元気0
register_condition("ProduceExamFieldStatusType_StaminaUpMultiple", _res("hp", _hp_ratio(True)))  # 体力N%以上
register_condition("ProduceExamFieldStatusType_StaminaLessMultiple", _res("hp", _hp_ratio(False)))


def _field_condition(cond, ev):
    """Event.fields 里给了值的字段：按「≥ value」判（主数据的 …Up 条件都是「N以上」）。"""
    got = ev.fields.get(cond.field)
    if got is None or cond.value is None:
        return None
    hit = got >= int(cond.value)
    return (not hit) if cond.negate else hit


def condition(cond, ev: Event) -> bool | None:
    if getattr(cond, "search", None) is not None:
        return None  # 「某处有 N 张某类牌」：牌堆位置账本数不准
    fn = CONDITIONS.get(cond.field)
    return fn(cond, ev) if fn is not None else _field_condition(cond, ev)


def conditions(trigger, ev: Event) -> bool | None:
    """全部条件成立 True，有一条不成立 False，其余（有判断不了的）None。回合属性条件也在这里判。"""
    unknown = bool(_trigger_extra(trigger.id))
    if trigger.lesson is not None:
        if ev.attribute is None:
            unknown = True
        elif ev.attribute != trigger.lesson:
            return False
    for cond in trigger.conditions:
        ok = condition(cond, ev)
        if ok is False:
            return False
        unknown |= ok is None
    return None if unknown else True


@lru_cache(maxsize=None)
def _trigger_extra(trigger_id: str) -> tuple[str, ...]:
    """触发器里判断不了的字段（移动位置、效果类型限定）。课程类型（lessonType）在 Trigger.lesson 里判。"""
    from hiro.brain import pitems

    try:
        r = pitems._row("ProduceExamTrigger", trigger_id) or {}
    except Exception:  # noqa: BLE001  没有主数据
        return ()
    out = []
    if pitems._set(r.get("cardMovePositionType")):
        out.append("cardMovePositionType")
    if pitems._json(r.get("effectTypes")):
        out.append("effectTypes")
    return tuple(out)


def search_matches(search, profile) -> bool | None:
    """牌搜索；「只因 Common 才匹配某计划」按 COMMON_PLAN_MATCHES。判断不了 None。"""
    if search is None:
        return True
    hit = search.matches(profile)
    if hit and search.plan and search.plan != COMMON and profile is not None and profile.plan == COMMON:
        return COMMON_PLAN_MATCHES
    return hit


def _card_matches(trigger, ev: Event) -> bool | None:
    if trigger.search is None:
        return True
    if ev.card is None:
        return False
    if (trigger.lower or 0) > 1 or trigger.upper:
        return None  # 「N 张以上 / 以下」类计数：出牌时机用不上，没核对
    from hiro.brain.pitems import view_profile

    try:
        return search_matches(trigger.search, view_profile(ev.card))
    except Exception:  # noqa: BLE001  没有主数据
        return None


_E = "ProduceExamEffectType_"
TIMER = _E + "ExamEffectTimer"
# 只影响分数的效果：触发了记进 Outcome.score，不算「没建模」
SCORE_ONLY = frozenset(_E + x for x in (
    "ExamLesson", "ExamLessonValueMultiple", "ExamLessonValueMultipleDown", "ExamLessonBuff", "ExamLessonBuffAdditive",
    "ExamLessonBuffMultiple", "ExamParameterBuff", "ExamParameterBuffMultiplePerTurn", "ExamReview", "ExamReviewAdditive",
    "ExamReviewMultiple", "ExamReviewValueMultiple", "ExamCardPlayAggressive", "ExamAggressiveValueMultiple",
    "ExamEnthusiasticMultiple", "ExamEnthusiasticAdditive", "ExamBlockFix", "ExamBlockAddDown",
    "ExamBlockAddMultipleAggressive", "ExamBlockRestriction", "ExamStaminaRecoverFix", "ExamStaminaReduceFix",
    "ExamStaminaRecoverRestriction", "ExamStaminaConsumptionAdd", "ExamStaminaConsumptionAddFix", "ExamStaminaConsumptionDown",
    "ExamStaminaConsumptionDownFix", "ExamAntiDebuff", "ExamDebuffRecover", "ExamLessonDependBlock",
    "ExamLessonDependExamReview", "ExamLessonDependParameterBuff", "ExamLessonDependExamCardPlayAggressive",
    "ExamLessonDependStaminaConsumptionSum", "ExamLessonDependPlayCardCountSum", "ExamLessonFullPowerPoint",
    "ExamLessonAddMultipleLessonBuff"))
# 流派模块登记的「切姿态」效果类型（anomaly 登记），判断不了触发时用来决定账本能不能照旧
STANCE_EFFECTS: set[str] = set()


def _changes_stance(effects) -> bool:
    from hiro.brain.pitems import exam_effect

    for e in effects:
        if e is None or e.type in STANCE_EFFECTS:
            return True
        if e.type == TIMER and any((c := exam_effect(x)) is None or c.type in STANCE_EFFECTS for x in e.chain):
            return True
    return False


def _unknown(out: Outcome, t) -> None:
    (out.score if t in SCORE_ONLY else out.unknown).append(t)


def _timer(e, out, ev):
    """「次のターン、…に変更」（ExamEffectTimer，value1 = 几回合后，连锁里有姿态）→ 下回合姿态；别的计时器算没建模。"""
    from hiro.brain.pitems import exam_effect

    chained = [exam_effect(c) for c in e.chain]
    if e.value1 != 1 or not chained or any(c is None for c in chained):
        out.unknown.append(TIMER)
        return
    sub = apply(chained, replace(ev, current=None))  # 下回合才切：不受「全力 下切不了姿态」限制
    if sub.stance:
        out.next_stance = f"温存{sub.level}段階目" if sub.stance == "温存" and sub.level in LEVELS else sub.stance
    for t in sub.unknown:
        _unknown(out, t)
    out.score += sub.score
    out.delta.update(sub.delta)


def apply(effects, ev: Event, source: str = "") -> Outcome:
    out = Outcome(source=source)
    for e in effects:
        fn = EFFECTS.get(e.type) if e is not None else None
        if fn is None:
            _unknown(out, getattr(e, "type", e))
        else:
            fn(e, out, ev)
    return out


register_effect(TIMER, _timer)


# ---------------------------------------------------------------- 装上 / 分发

def unsupported_phases(spec: Spec) -> list[str]:
    """附魔的触发时机里这套引擎不认的（装的时候记下来；这些时机永远不触发）。"""
    if spec.trigger is None:
        return ["没有触发器"]
    return [p.rsplit("_", 1)[-1] for p, _ in spec.trigger.phases if p not in PHASES_SUPPORTED] or (
        [] if spec.trigger.phases else ["没有触发时机"])


def install(spec: Spec, turn: int = 0) -> Inst:
    until = turn + spec.turns - 1 if spec.turns and spec.turns > 0 else None
    return Inst(spec, left=spec.limit, count=0, until=until)


def _fires(inst: Inst, ev: Event) -> tuple[Inst, bool | None]:
    """这个实例对事件的反应：(新实例, 触发 True / 不触发 False / 判断不了 None)。"""
    tr = inst.spec.trigger
    if tr is None or inst.left == 0 or (inst.until is not None and ev.turn > inst.until):
        return inst, False
    for phase, value in tr.phases:
        if phase in COUNTED:
            on, per_turn = COUNTED[phase]
            if ev.phase != on:
                continue
            if inst.count is None:
                return inst, None
            ok = conditions(tr, ev)
            match = _card_matches(tr, ev) if ok else ok
            if match is None:
                return replace(inst, count=None), None
            if not match:
                return inst, False
            count = inst.count + 1
            return replace(inst, count=count), count % int(value) == 0
        if phase in TURN_PHASES:
            on, test = TURN_PHASES[phase]
            if ev.phase != on or not test(ev.turn, int(value or 0)):
                continue
            return inst, conditions(tr, ev)
        if phase == ev.phase:
            ok = conditions(tr, ev)
            if ok and ev.card is not None:
                ok = _card_matches(tr, ev)
            elif ok and tr.search is not None and ev.phase not in CARD_EVENTS:
                ok = None  # 非出牌时机带牌搜索（数某处的牌）：账本数不准
            return inst, ok
    return inst, False


def dispatch(insts, ev: Event):
    """对一个事件分发全部附魔实例。返回 (新实例 tuple, [(实例, Outcome | None)])；Outcome None = 判断不了触发没触发。"""
    new, fired = [], []
    for inst in insts:
        inst, hit = _fires(inst, ev)
        if hit and not _can_fire(inst, ev.turn):
            hit = False  # 道具 fireLimit / fireInterval 用完
        if hit:
            inst = replace(inst, left=inst.left - 1 if inst.left is not None else None, fired=inst.fired + 1, last=ev.turn)
            fired.append((inst, apply(inst.spec.effects, ev, inst.spec.key)))
        elif hit is None:
            fired.append((inst, None))
        new.append(inst)
    return tuple(new), fired


def _can_fire(inst: Inst, turn: int) -> bool:
    spec = inst.spec
    if spec.fire_limit and inst.fired >= spec.fire_limit:
        return False
    return not (spec.fire_interval and inst.last is not None and turn - inst.last < spec.fire_interval)


def new_turn(insts):
    """回合交界：本回合计数型（ExamPlayTurnCountInterval）清零。"""
    out = []
    for inst in insts:
        if any(COUNTED.get(p, (None, False))[1] for p, _ in inst.spec.trigger.phases) and inst.count is not None:
            inst = replace(inst, count=0)
        out.append(inst)
    return tuple(out)


# ---------------------------------------------------------------- 来源

def pitem_specs(item_id: str, source: str = "pitem") -> list[Spec]:
    """P 道具 / 支援卡道具 → 附魔定义（考试开始装上）。"""
    from hiro.brain.pitems import load_pitem

    item = load_pitem(item_id)
    if item is None:
        return []
    return [Spec(item_id if len(item.enchants) == 1 else en.id, source, en.trigger, en.effects, en.limit,
                 en.turns if en.turns and en.turns > 0 else None, en.text, item.fire_limit, item.fire_interval)
            for en in item.enchants if en.trigger is not None]


def card_specs(card) -> list[Spec]:
    """打出这张牌装上的附魔（「以降…」）。key = 牌名（同名牌重复打出各装一份）。"""
    from hiro.brain.pitems import card_enchants

    try:
        found = card_enchants(card)
    except Exception:  # noqa: BLE001  没有主数据
        return []
    name = getattr(card, "name", "") or ""
    return [Spec(name if len(found) == 1 else f"{name}:{en.id}", "card", en.trigger, en.effects, count, None, en.text)
            for en, count in found if en.trigger is not None]
