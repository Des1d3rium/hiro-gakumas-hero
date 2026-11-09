# 来源：digital-hiro src/hiro/sim/fidelity.py（develop 864e037）
"""高保真考试引擎（gakumas-player 的 gakumas_exam，按 3.5.1 反汇编移植、实机整体对账）的开关与入口。

默认关（HIRO_EXAM_ENGINE 未设或为 legacy）：一切照旧，用 gakumas_sim.campus.restore 的旧引擎。
HIRO_EXAM_ENGINE=fidelity：实机局面改由新引擎直接读原始存档还原（不经有损的状态翻译），预演 / 推演 / 续打在新引擎上跑。
接口与旧 Battle 相同（gakumas_exam.battle.FidelityBattle），hiro.sim 的 clone / determinize / state_signature 各有一处分支。

隐藏信息与旧路径同口径：山札顺序按 seed 重洗、对局随机数重设、未来回合属性按配额重新抽样（存档里有真实值，不用）。
引擎目录：GAKUMAS_EXAM 指定；默认与 gakumas_sim 同一个目录（hiro.sim.ROOT）。
开源版不带 gakumas_exam（也不带读取实机存档的部分），这个开关在开源版里没有效果。
"""

import os
import random
import sys
from pathlib import Path



def enabled() -> bool:
    return os.environ.get("HIRO_EXAM_ENGINE", "legacy") == "fidelity"


def is_fidelity(env) -> bool:
    return getattr(env, "fidelity", False) is True


def candidates() -> list:
    """GAKUMAS_EXAM → 与 gakumas_sim 同一个引擎目录（hiro.sim.ROOT）。"""
    import hiro.sim

    return [os.environ.get("GAKUMAS_EXAM"), str(hiro.sim.ROOT)]


def engine_root() -> Path | None:
    for c in candidates():
        if c and (Path(c).expanduser() / "gakumas_exam" / "battle.py").exists():
            return Path(c).expanduser()
    return None


def _import():
    root = engine_root()
    if root is None:
        raise RuntimeError("找不到 gakumas_exam（GAKUMAS_PLAYER 指向的 gakumas-player 需要包含 gakumas_exam/，或用 GAKUMAS_EXAM 指定）")
    if str(root) not in sys.path:
        sys.path.append(str(root))  # 追加在后：gakumas_sim 仍从 GAKUMAS_PLAYER 加载，这里只补上 gakumas_exam
    from gakumas_exam.battle import FidelityBattle
    from gakumas_exam.master import shared

    return FidelityBattle, shared


def turn_attributes(exam, obs: dict, seed: int) -> list:
    """本回合起的剩余回合属性（含追加回合）：与 gakumas_sim.campus.restore 同口径（末三回合固定，前段未公开部分按配额随机）。"""
    from gakumas_sim.campus import ATTR_BY_TYPE, turn_attributes_for

    s = obs["scalars"]
    meta = exam.meta
    rng = random.Random(seed)
    remaining = s["remainTurn"]
    base_left = max(0, s["limitTurn"] - s["currentTurn"] + 1)
    current = ATTR_BY_TYPE.get(obs.get("current_parameter_type"))
    attrs = turn_attributes_for(meta, s["currentTurn"], s["limitTurn"], rng, current)
    last = attrs[-1] if attrs else (current or meta["allocation"]["fixed_tail"][-1])
    return attrs[:base_left] + [last] * (remaining - base_left) if remaining > base_left else attrs[:remaining]


def battle(raw: dict, exam, obs: dict, seed: int, *, settle: bool = False, commit: str | None = None):
    """原始存档 → FidelityBattle。settle=True：存档停在「卡牌结算中」，把这张牌算完（费用已付）。
    exam 是同一局的 CampusExam（只用它的 meta：回合配额、抽牌数）。"""
    FidelityBattle, shared = _import()
    from gakumas_sim.catalog import Catalog

    if commit is None:
        c = Catalog()
        commit = c.info["commit"]
        c.db.close()
    master = shared(commit)
    attrs = turn_attributes(exam, obs, seed)
    make = FidelityBattle.settle_from_save if settle else FidelityBattle.from_save
    env = make(raw, master, seed, turn_attributes=attrs, draw=exam.meta["draw"])
    env.turn_attributes = attrs
    return env


def episode(obs: dict, exam, seed: int, *, keep_first: bool = True, commit: str | None = None):
    """开考快照（第 1 回合）→ 从开考前重新开一局（离线基准 SimExam.episode）：回合属性与旧引擎同口径（campus.turn_attributes），
    洗牌、发牌、支援强化、开考附魔全部由新引擎按 seed 重来。"""
    FidelityBattle, shared = _import()
    from gakumas_sim.campus import turn_attributes as campus_turn_attributes
    from gakumas_sim.catalog import Catalog

    if commit is None:
        c = Catalog()
        commit = c.info["commit"]
        c.db.close()
    attrs = campus_turn_attributes(exam.meta, random.Random(seed), keep_first=keep_first)
    return FidelityBattle.episode_from_observation(obs, shared(commit), seed, turn_attributes=attrs, draw=exam.meta["draw"])
