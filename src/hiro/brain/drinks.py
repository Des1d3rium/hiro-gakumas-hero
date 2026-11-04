# 来源：digital-hiro src/hiro/brain/drinks.py @ c3fc27f（GPL-3.0-or-later）；去掉模拟比较，只按社区规则
"""饮料持有决策：拿 / 换哪瓶 / 不拿 / 买（用户 2026-11-02 第十六局复盘，经总控 / 协调转达，用户已 review；同日更正）。

总原则（用户更正）：社区规则是「优先参考的优质社区概念」，给默认优先级、默认选择和兜底；完整对局模拟确实算出更好
（同一批种子配对，差距 > 2 个标准误）时可以调整，跨优先档也可以，并在决策记录里写明偏离了哪条社区规则、模拟差多少。
1. 每瓶饮料按主数据效果归类（knowledge.drink_class），类别给出默认档位和同档内的社区默认顺序（knowledge.DRINK_CLASSES）。
2. 默认档位按「持有组合」算：
   - 第一瓶复制 → 0 档（高价值：复制哪张、什么时候喝由考试里的预演决定，せのび / 紙吹雪 / 約束 / デイドリ 都可能）；
   - 前 BURST_MUST 瓶爆发 → 0 档（兑现回合；R2 两瓶）；
   - 抽牌类（抽牌 / 换手）超过 DRAW_CAP 的 → 4 档（默认限量：喝早会转乱牌堆，用户批注）；
   - 空位算 3.5 档：比回复 / 元気 差，比好印象 / 超限抽牌 好。
3. 候选持有组合按「档位从好到坏排序后的列表」比较，取规则默认。
4. 开源版只用规则默认（主仓库另有模拟比较：候选比规则默认好 > 2 个标准误才偏离，这里不包含）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hiro.state.knowledge import BURST_MUST, DRAW_CAP, DRAW_CLASSES, DRINK_CLASSES, drink_class

logger = logging.getLogger(__name__)

EMPTY_TIER = 3.5
OVERRIDE_SE = 2.0  # 模拟要好过规则默认几个标准误才改（用户更正 2026-11-02：建议 > 2 个标准误，跨档也可以）
MAX_SIM_OPTIONS = 8
NOOP = ("keep", "skip", "none")  # 「不动」的候选键  # 一次最多模拟几个候选（按规则排名取前几个）
SIM = False  # 开源版没有模拟器：只按社区规则（私有实现可以接回模拟比较）


def stage_of(mode: str | None, *, interval: bool = False, after_r1: bool = False) -> str:
    """下一场考试是哪一段：选拔 / R1 / R2。"""
    from hiro.state.produce import FINALS

    if mode != FINALS:
        return "selection"
    return "r2" if interval or after_r1 else "r1"


def tag(drinks: list, stage: str) -> list[tuple]:
    """每瓶饮料的 (档位, 同档顺序, 类别)，与输入顺序对齐。必留 / 超限按社区默认顺序先到先得。"""
    cls = [drink_class(d) for d in drinks]
    order = sorted(range(len(drinks)), key=lambda i: (DRINK_CLASSES[cls[i]]["tier"], DRINK_CLASSES[cls[i]]["order"], i))
    out: list = [None] * len(drinks)
    copies = bursts = draws = 0
    for i in order:
        k = cls[i]
        info = DRINK_CLASSES[k]
        tier = info["tier"]
        if k == "copy":
            tier = 0 if copies == 0 else tier
            copies += 1
        elif k == "burst":
            tier = 0 if bursts < BURST_MUST[stage] else tier
            bursts += 1
        if k in DRAW_CLASSES:
            draws += 1
            if draws > DRAW_CAP[stage]:
                tier = 4
        out[i] = (tier, info["order"], k)
    return out


def holding_key(drinks: list, stage: str, capacity: int) -> tuple:
    """持有组合的比较键（小的好）：档位从好到坏排序的 (档位, 顺序) 列表，空位补 3.5 档。"""
    keys = sorted((t, o) for t, o, _ in tag(list(drinks), stage))  # None（没认出）按「未知」类算，不当空位
    keys += [(EMPTY_TIER, 0)] * max(0, capacity - len(keys))
    return tuple(keys)


def tiers_of(key: tuple) -> tuple:
    return tuple(t for t, _ in key)


def describe(drinks: list, stage: str) -> str:
    from hiro.state.context import drink_name

    parts = []
    for d, (t, _, k) in zip(drinks, tag(drinks, stage)):
        parts.append(f"{drink_name(d)}〔{DRINK_CLASSES[k]['label']}·{'优先' if t == 0 else ('超限' if t == 4 and k in DRAW_CLASSES else f'{t:g}档')}〕")
    return "、".join(parts) or "空"


@dataclass
class Option:
    """一个候选：做完之后的持有饮料。action 是对应的契约动作（由调用方给）。"""

    key: str
    label: str
    drinks: list
    action: object = None
    cost: int = 0
    extra: dict = field(default_factory=dict)


def choose(options: list[Option], stage: str, capacity: int, *, sim_context: dict | None = None, seeds=range(100), where: str = "") -> dict:
    """规则默认 + 模拟。返回 {pick: Option, rule: Option, rows: [...], why: str, sim: bool, deviation: dict | None}。
    sim_context：保留参数以兼容调用方；开源版不做模拟比较，只按规则。"""
    keyed = [(holding_key(o.drinks, stage, capacity), i, o) for i, o in enumerate(options)]
    keyed.sort(key=lambda x: (x[0], x[2].key not in NOOP, x[1]))  # 组合打平时不动（不收 / 不拿 / 不买）
    best_key, _, rule = keyed[0]
    tiers_txt = lambda k: "/".join(f"{t:g}" for t in tiers_of(k))  # noqa: E731
    rows = [{"key": o.key, "label": o.label, "tiers": tiers_txt(k), "rule_rank": n + 1, "cost": o.cost} for n, (k, _, o) in enumerate(keyed)]
    out = {"pick": rule, "rule": rule, "rows": rows, "sim": False, "deviation": None, "why": f"社区默认：{rule.label}（档位 {tiers_txt(best_key)}）"}
    return out


DEVIATIONS = "reports/deviations.jsonl"


def log_deviation(rec: dict) -> None:
    """偏离社区默认的记录：定期汇总给协调，由用户 review（用户更正 2026-11-02）。写不了（只读环境 / 测试）不影响决策。"""
    import json
    import time
    from pathlib import Path

    try:
        path = Path(DEVIATIONS)
        if not path.parent.is_dir():
            return
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "kind": "drink", **rec}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def accident(drinks: list, stage: str, capacity: int) -> list[str]:
    """饮料「事故」（社区：事故时才在相談 / 本战相談补）。选拔：没有复制、爆发不够、有用的（0–1 档）不到 2 瓶；
    本战：只看有用的不到 2 瓶（もなみ：最后的相談基本不花，よほど事故ったら才买）。返回缺什么。"""
    tags = tag([d for d in drinks if d], stage)
    out = []
    if stage == "selection":  # 本战按もなみ「よほど事故ったら」：只看有用的饮料够不够（她只缺 ブースト 时忍住没买）
        if not any(k == "copy" for _, _, k in tags):
            out.append("复制")
        if sum(k == "burst" for _, _, k in tags) < BURST_MUST[stage]:
            out.append("爆发")
    if sum(t <= 1 for t, _, _ in tags) < 2:
        out.append("有用的饮料不到 2 瓶")
    return out


def best_incoming(cands: list[dict], held: list | None, stage: str, capacity: int) -> dict | None:
    """栏满时三选一先挑哪瓶去和持有的比（hif 的「所持上限」流程）：每个候选取「丢掉持有里某一瓶换它」能得到的最好持有组合，
    取组合最好的那个候选（之后 PDRINK_MAX 决定丢不丢、丢哪瓶）。held 未知时按单瓶默认档位。"""
    known = [c for c in cands or [] if c.get("drink_id")]
    if not known:
        return None
    hold = [h.get("drink_id") for h in held or []]

    def best_key(c: dict) -> tuple:
        if not hold:
            t, o, _ = tag([c["drink_id"]], stage)[0]
            return ((t, o),)
        return min(holding_key(hold[:j] + [c["drink_id"]] + hold[j + 1:], stage, capacity) for j in range(len(hold)))

    return min(known, key=best_key)
