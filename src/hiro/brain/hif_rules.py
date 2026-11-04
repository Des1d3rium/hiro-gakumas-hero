"""H.I.F（初星アイドルフェスティバル）里由服务端决定、公开攻略写明的规则：价格、次数、效果分组。

来源（都是公开的社区攻略，按其表格整理，数值以实机为准随时可改）：
- seesaawiki「学園アイドルマスター 攻略 wiki」H.I.F 页（相談 / インターバル 价格表）
- wikiwiki「学マス攻略 wiki」HIF/基本情報（インターバル 刷新价格序列、回复价格）
不包含任何从客户端还原的公式（考试要求、倍率、星星换算等）；那些通过插件接口取，拿不到就是未知。
"""

from __future__ import annotations

import math

# ---- 相談（普通商店）：强化 / 删除按本局已执行次数递增，两者分别计数
SERVICE_PRICES = (100, 125, 150, 175, 200, 250)  # 第 6 次以后按最后一档
DRINK_PRICE = {"ProduceDrinkRarity_R": 50, "ProduceDrinkRarity_Sr": 75, "ProduceDrinkRarity_Ssr": 100}  # 相談与轮间相同

# ---- 轮间（インターバル）
INTERVAL_REFRESH_PRICES = (10, 10, 20, 30, 40, 50)  # 之后保持 50
INTERVAL_UPGRADE_BASE = 100  # 轮间强化：首次 100，每次 +25（不继承相談次数）
INTERVAL_UPGRADE_STEP = 25
INTERVAL_RECOVER_PER_2HP = 10  # 每 2 体力 10P，不足 2 也收 10P

# ---- 计划类型 → 两种おすすめ効果（授業 A / B 档的顺序）
PLAN_EFFECTS = {
    "ProducePlanType_Plan1": ("ExamParameterBuff", "ExamLessonBuff"),  # センス：好調、集中
    "ProducePlanType_Plan2": ("ExamReview", "ExamCardPlayAggressive"),  # ロジック：好印象、やる気
    "ProducePlanType_Plan3": ("ExamConcentration", "ExamFullPower"),  # アノマリー：強気、全力
}


def service_price(times_done: int) -> int:
    """相談的强化 / 删除价格（本局已做 times_done 次）。"""
    return SERVICE_PRICES[min(max(times_done, 0), len(SERVICE_PRICES) - 1)]


def interval_refresh_price(times_done: int) -> int:
    return INTERVAL_REFRESH_PRICES[min(max(times_done, 0), len(INTERVAL_REFRESH_PRICES) - 1)]


def interval_upgrade_price(times_done: int) -> int:
    return INTERVAL_UPGRADE_BASE + INTERVAL_UPGRADE_STEP * max(times_done, 0)


def interval_recover_price(hp: int) -> int:
    """回复 hp 点体力要多少 P（每 2 点 10P，向上取整）。"""
    return INTERVAL_RECOVER_PER_2HP * math.ceil(max(hp, 0) / 2)


def effect_group(effect_type: str) -> str:
    """效果类型 → 卡牌上的可见效果组 id，例如 ProduceExamEffectType_ExamCardPlayAggressive
    → effect_group-visible-exam_card_play_aggressive-000（主数据里卡牌的 effectGroupIds 用的就是这个格式）。"""
    name = effect_type.split("_", 1)[-1]
    snake = "".join("_" + ch.lower() if ch.isupper() else ch for ch in name).lstrip("_")
    return f"effect_group-visible-{snake}-000"


def recommended_effect_group(idol_row: dict) -> str:
    """偶像的おすすめ効果（IdolCard.examEffectType）对应的卡牌效果组。"""
    return effect_group(idol_row["examEffectType"])


def is_recommended(card_row: dict, idol_row: dict) -> bool:
    return recommended_effect_group(idol_row) in (card_row.get("effectGroupIds") or [])
