"""アノマリー资源模块：给通用附魔引擎（enchants）登记姿态条件、姿态切换、全力値。

条件：没有指针（指針なし / 指針解除）、温存（のんびり 也算）/ 強気 / 全力 状态。姿态不明（None）判断不了。
效果：切強気 / 温存 / のんびり / 全力（全力 下切不了别的姿态，游戏规则；使用者 2026-11-07）、全力値+N。
センス / ロジック 以后各写一个同样的模块，登记自己的资源（好調、集中 / 好印象、やる気）。
"""

from __future__ import annotations

from hiro.brain.pitems import FIELD_STANCE, NO_STANCE_NAMES, STANCE_OF_EFFECT
from hiro.brain.play.enchants import register_condition, register_effect

FULL_POWER_EFFECT = "ProduceExamEffectType_ExamFullPower"


def _no_stance(cond, ev):
    if ev.stance is None:
        return None  # 姿态不明（不是「指針なし」）
    hit = ev.stance in NO_STANCE_NAMES
    return (not hit) if cond.negate else hit


def _stance_field(cond, ev):
    if ev.stance is None:
        return None
    hit = ev.stance in FIELD_STANCE[cond.field]
    return (not hit) if cond.negate else hit


def _stance_effect(e, out, ev):
    if ev.current == "全力" and e.type != FULL_POWER_EFFECT:
        return  # 全力 下切不了姿态
    out.stance = STANCE_OF_EFFECT[e.type]
    out.level = e.value1 if e.value1 in (1, 2) and out.stance in ("強気", "温存") else None


def _full_power_point(e, out, ev):
    out.delta["fp"] += e.value1


register_condition("ProduceExamFieldStatusType_NoStance", _no_stance)
for _f in FIELD_STANCE:
    register_condition(_f, _stance_field)
for _t in STANCE_OF_EFFECT:
    register_effect(_t, _stance_effect)
register_effect("ProduceExamEffectType_ExamFullPowerPoint", _full_power_point)
