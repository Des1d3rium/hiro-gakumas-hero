"""通用附魔引擎：P 道具、技能卡附魔、支援卡道具共用一套「触发 + 条件 + 效果 + 次数」。

主数据里这三种来源是同一种结构（docs/play-modules.md 第六节）：
- P 道具 / 支援卡道具：考试开始就装上，次数 = 「レッスン内N回」（ProduceItemEffect.effectCount，0 = 不限）；
  支援卡道具多一个回合属性条件（【ダンスターンのみ】= Trigger.lesson）。
- 技能卡附魔：打出时装上，次数 = 「以降N回まで」。

推演（sim.TurnState）在固定时机发 Event，dispatch 对每个附魔实例判触发 → 判条件 → 执行效果，返回新的实例列表和触发结果。
条件、效果按字段 / 效果类型登记（register_condition / register_effect）：通用的登记在这里，流派资源（アノマリー的姿态、
全力値；以后的センス、ロジック）登记在各自的模块（hiro.brain.play.anomaly）。没登记的条件判「不知道」（None），
没登记的效果记进 Outcome.unknown，不动局面。这里不写任何偶像 / 道具的名字。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
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


@dataclass(frozen=True)
class Event:
    """一次事件。stance：判条件用的姿态；current：执行效果时的姿态（出牌后 = 牌结算完的姿态）。
    card：出牌事件的牌（CardView）；attribute：本回合属性（Vo / Da / Vi，不知道 None）。"""

    phase: str
    turn: int = 0
    stance: str | None = None
    current: str | None = None
    card: object = None
    attribute: str | None = None
    remaining_turns: int | None = None
    fields: dict = field(default_factory=dict)  # 流派资源等其余条件字段的当前值


@dataclass
class Outcome:
    """一次触发对局面的改变。delta：资源增减（actions / draws / genki / fp …）；stance / level：要切到的姿态。"""

    source: str = ""
    stance: str | None = None
    level: int | None = None
    delta: Counter = field(default_factory=Counter)
    unknown: list = field(default_factory=list)


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


@dataclass(frozen=True)
class Inst:
    """附魔实例：剩余次数（None = 不限）、计数器（None = 有一手判断不了，之后不知道）、本回合计数、截止回合。"""

    spec: Spec
    left: int | None = None
    count: int | None = 0
    until: int | None = None

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


def _field_condition(cond, ev):
    """Event.fields 里给了值的字段：按「≥ value」判（主数据的 …Up 条件都是「N以上」）。"""
    got = ev.fields.get(cond.field)
    if got is None or cond.value is None:
        return None
    hit = got >= int(cond.value)
    return (not hit) if cond.negate else hit


def condition(cond, ev: Event) -> bool | None:
    fn = CONDITIONS.get(cond.field)
    return fn(cond, ev) if fn is not None else _field_condition(cond, ev)


def conditions(trigger, ev: Event) -> bool | None:
    """全部条件成立 True，有一条不成立 False，其余（有判断不了的）None。回合属性条件也在这里判。"""
    unknown = False
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


def _card_matches(trigger, ev: Event) -> bool | None:
    if trigger.search is None:
        return True
    if ev.card is None:
        return False
    from hiro.brain.pitems import view_profile

    try:
        return trigger.search.matches(view_profile(ev.card))
    except Exception:  # noqa: BLE001  没有主数据
        return None


def apply(effects, ev: Event, source: str = "") -> Outcome:
    out = Outcome(source=source)
    for e in effects:
        fn = EFFECTS.get(e.type) if e is not None else None
        if fn is None:
            out.unknown.append(getattr(e, "type", e))
        else:
            fn(e, out, ev)
    return out


# ---------------------------------------------------------------- 装上 / 分发

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
            return inst, ok
    return inst, False


def dispatch(insts, ev: Event):
    """对一个事件分发全部附魔实例。返回 (新实例 tuple, [(实例, Outcome | None)])；Outcome None = 判断不了触发没触发。"""
    new, fired = [], []
    for inst in insts:
        inst, hit = _fires(inst, ev)
        if hit:
            if inst.left is not None:
                inst = replace(inst, left=inst.left - 1)
            fired.append((inst, apply(inst.spec.effects, ev, inst.spec.key)))
        elif hit is None:
            fired.append((inst, None))
        new.append(inst)
    return tuple(new), fired


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
                 en.turns if en.turns and en.turns > 0 else None, en.text)
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
