"""Opt-in H.I.F finals strategy for [VEIL] 秦谷美玲."""

from __future__ import annotations

import os
import sys
import re
import unicodedata
from collections import Counter

from hiro.contracts import Decision
from hiro.loop.errors import ResumeBlocked

IDOL = "i_card-skin-hmsz-3-016"
ENTERTAINER, DRIVE, TEACH, REBOUND = "エンターテイナー+", "オーバードライブ+", "教えてあげる+", "巻き返し+"
CORE = frozenset((ENTERTAINER, DRIVE, TEACH, REBOUND))
BOOST, SODA, WHEY = "ブーストエキス", "センブリソーダ", "初星ホエイプロテイン"
EXTRA_TURN = "初星湯"  # ターン追加+1体力消費2消費体力増加1ターン
WARM = {"温存", "温存1段階目", "温存2段階目", "のんびり"}
STANCES = ("温存", "温存1段階目", "温存2段階目", "のんびり", "強気", "全力", "指針解除")
ORDERS = {2: (DRIVE, REBOUND, TEACH, ENTERTAINER), 1: (DRIVE, TEACH, REBOUND, ENTERTAINER)}
LIMITS = {"r1": 700_000, "r2": 1_400_000}
HAND_MAX = 5  # 手牌上限：抽牌超出的作废


# 默认开的策略开关（使用者 2026-11-07，22 个 trace 回放后定）：设 VEIL_<name>=0 关掉。其余默认关，=1 打开
DEFAULT_ON = frozenset({"HP_GUARD", "RELEASE_GAIN", "DRAW_LAST", "DRIVE_FP", "FP_CAP"})
STRATEGY_FLAGS = ("KEEP_WARM", "HP_GUARD", "RELEASE_GAIN", "FP_RESERVE", "DRAW_LAST", "CLEAR_FIRST", "DRIVE_FP", "FP_CAP")


def _flag(name: str) -> bool:
    """VEIL 出牌策略开关：环境变量 VEIL_<name>=1 打开、=0 关掉，没设时按 DEFAULT_ON。旧名 HIRO_VEIL_<name> 仍认，
    两个都设时以新名为准（使用者 2026-11-07：策略开关去掉 HIRO_ 前缀）。"""
    value = os.environ.get(f"VEIL_{name}")
    value = value if value is not None else os.environ.get(f"HIRO_VEIL_{name}")
    if value in ("0", "1"):
        return value == "1"
    return name in DEFAULT_ON


def keep_warm():
    """VEIL_KEEP_WARM=1：清牌阶段保留温存 / 能留到下回合的再动（默认关，回放验证后再开）。"""
    return _flag("KEEP_WARM")


def hp_guard():
    """VEIL_HP_GUARD=1：清牌阶段按姿态和消耗加成算出牌实际掉的体力，跌破保底线的不打；強気下不改姿态的掉血牌留到温存；
    強気下优先打切回温存的牌
    （使用者 2026-11-07：脚本在強気、一心不乱加成后、元気不足时照打高消耗牌，trace 里一张牌 25→4）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("HP_GUARD")


def release_gain():
    """VEIL_RELEASE_GAIN=1：温存 / のんびり 下打「強気に変更」的牌，按「先结算牌、再结算夜空」算温存解除的 +1 出牌次数——
    夜空只剩 1 次、最终变成のんびり时也算（原来算 0，于是手里有 エンターテイナー+、巻き返し+ 也去喝ホエイ）。
    实机 codex-20261107-1 第 1 局 R2 剩 8 回合：喝了ホエイ再打这两张，打完同回合还剩 1 次（trace 第 23 步）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("RELEASE_GAIN")


def fp_reserve():
    """VEIL_FP_RESERVE=1：清牌阶段花全力値的牌（アンダンテ+、清牌用的 巻き返し+…）只在花完还剩 ≥ FP_RESERVE_AT 时打，
    给五卡循环留住 アイドルになります+（全力値消費3）先打的全力値。账本不知道（None）、アイドルになります+ 已不在牌组、
    最后一回合时不限制。实机 codex-20261107-1 第 1 局 R2：清牌把 成果+ 的 +4 花在 アンダンテ+ / 巻き返し+ ×2 上，
    剩 5 回合进循环时全力値 0，アイドルになります+ 灰着打不出。默认关，回放验证后再开。"""
    return _flag("FP_RESERVE")


def draw_last():
    """VEIL_DRAW_LAST=1：清牌时好几张都能加出牌次数，先打不抽牌、也不改姿态的那张（みずたまりスキップ 等），
    带抽牌的（インフルエンサー+、フルスロットル+、仕切り直し+）留后：手牌少了，抽进来的牌不会因手牌满而作废
    （使用者 2026-11-07，验证局 R1 剩 7 回合：满手先打了 インフルエンサー+ / フルスロットル+，みずたまりスキップ 留到最后）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("DRAW_LAST")


def clear_first():
    """VEIL_CLEAR_FIRST=1：清牌阶段一次性牌（打出就离开牌库）排在复用牌前面，哪怕复用牌在温存下切強気能补一次出牌次数；
    ひと呼吸 只让给别的一次性牌，不再让给复用牌。使用者 2026-11-07（验证局 1-f R1 剩 5–2 回合）：温存下总是先打
    教えてあげる+ / エンターテイナー+（切強気 +1 次），到強気又被体力闸压后，頂点へ+ 从剩 5 拖到剩 2 才打。默认关。"""
    return _flag("CLEAR_FIRST")


def drive_fp():
    """VEIL_DRIVE_FP=1：清牌阶段手里 アイドルになります+ / 巻き返し+ 因全力値不够发灰、又没有别的可打的加全力値牌时，
    打 オーバードライブ+（全力値+2、自带再动）攒全力値。原来清牌一律不打 オーバードライブ，它又是唯一的全力値来源，
    全力値永远 0、两张一直灰着（使用者 2026-11-07，验证局 1-f R2 剩 7–1 回合：死循环）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("DRIVE_FP")


def fp_cap():
    """VEIL_FP_CAP=1：清牌阶段别把全力値加到 FULL_POWER_AT（满了下回合自动进全力、全力値清零）。有别的牌可打时，
    不打会让全力値到顶的加全力値牌。验证局 codex-20261107-1-c R2：プライド +3、成果+ +4、涙の思い出+ +4 在 T3 加到 11，
    T4 自动全力打的全是清牌牌，之后 T6–T10 全力値 0，巻き返し+ / アイドルになります+ 打不出。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("FP_CAP")


FULL_POWER_AT = 10  # 全力値到 10 自动进全力（实机）
IDOL_CARD = "アイドルになります"  # 去「+」的基名
FP_RESERVE_AT = 3  # アイドルになります+：全力値消費3（主数据）

# 全力値：牌面「全力値+N」「全力値消費N」「全力値増加量増加+X%（Nターン）」。只看打出时立即结算的部分：
# 「以降」「次のターン」「ターン開始時」「成長：」之后是延迟 / 常驻 / 成长效果（如 待ち望んだ瞬間 的「直接効果で全力値が増加後…全力値+1」），不在打出时记。
_FP_GAIN = re.compile(r"全力値\+(\d+)")
_FP_COST = re.compile(r"全力値消費(\d+)")
_FP_BOOST = re.compile(r"全力値増加量増加\+(\d+)[%％][（(](\d+)ターン[）)]")  # norm 后全角括号变半角
_FP_LATER = re.compile(r"以降|次のターン|ターン開始時|成長：")


def fp_effect(card) -> dict:
    """牌面文字 → {gain, cost, boost: (百分比, 回合数) | None}。主数据原文：オーバードライブ+ 全力値+2、巻き返し+ 全力値消費1、
    アイドルになります+ 全力値消費3、リスキーチャンス+ 全力値+3 / 全力値増加量増加+50%（4ターン）。"""
    text = _FP_LATER.split(norm(getattr(card, "text", card) or ""), maxsplit=1)[0]
    boost = _FP_BOOST.search(text)
    return {"gain": sum(map(int, _FP_GAIN.findall(text))), "cost": sum(map(int, _FP_COST.findall(text))),
            "boost": (int(boost[1]), int(boost[2])) if boost else None}


def fp_delta(card) -> tuple[int, int]:
    """牌面文字里的（全力値+N, 全力値消費N）。"""
    eff = fp_effect(card)
    return eff["gain"], eff["cost"]


def hp_floor():
    """体力闸的保底线：打完这张至少还剩这么多体力（HIRO_VEIL_HP_FLOOR，默认 10）。"""
    try:
        return int(os.environ.get("HIRO_VEIL_HP_FLOOR", "10"))
    except ValueError:
        return 10


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


def hp_loss(state, card, current, mods=None):
    """打出这张牌实际掉的体力（估计，宁高勿低）。

    主数据（2026-11-07 核对）：コスト值先加一心不乱（強気効果的牌 +7）、盛り上げ上手（アクティブ +1）；
    姿态倍率 強気 ×2（2 段階另外每张直接扣 1）、温存 1 段階 ×0.5、2 段階 ×0.25、のんびり ×0；先扣元気。
    未在主数据文本核实、按保守处理：消費体力増加 / 減少按 ±50%；体力消費（直接扣）也乘姿态倍率、不被元気挡。"""
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
        per_card = STRONG2_DIRECT if stance_level(state, current) != 1 else 0
    elif current == "のんびり":
        mult = 0.0
    elif current in WARM:
        mult = 0.25 if stance_level(state, current) == 2 else 0.5
    if "消費体力増加" in names:
        mult *= 1.5
    if "消費体力減少" in names:
        mult *= 0.5
    paid = max(0, math.ceil(cost * mult) - (state.genki or 0))
    direct = math.ceil((card.hp_cost or 0) * max(mult, 1.0))  # 体力消費：只往高估，温存 / のんびり 不减
    return paid + direct + per_card


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
    immediate = _DELAYED.split(norm(card.text), maxsplit=1)[0]
    match = _STANCE_CHANGE.search(immediate)
    return (match[1] or "指針解除") if match else None


def stance_change_level(card):
    immediate = _DELAYED.split(norm(card.text), maxsplit=1)[0]
    match = _STANCE_CHANGE.search(immediate)
    if match:
        for level in (1, 2):
            if f"{level}段階目" in match[0]:
                return level
    return None


def next_stance_change(card):
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


NEUTRAL = "指針なし"  # 考试第 1 回合还没切过姿态（画面没有姿态图标）：切強気照样算夜空，但没有温存解除
STAR = "星々を見下ろす一番星"  # 以降3回まで、ターン開始時、非温存状態の場合、のんびりに変更


def start_turn(current, star):
    """回合开始：一番星还有次数且不在温存 / のんびり 时切のんびり，返回（姿态，剩余次数）。姿态不明时不动。"""
    if current is not None and current not in WARM and (star or 0) > 0:
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


def keeps_nonbiri(current, dest):
    """のんびり 覆盖温存：温存 → のんびり 可以，のんびり 收到「温存に変更」（任一段階）仍是のんびり（使用者 2026-11-07，
    1-g R2 再挑戦3 剩 9：のんびり 下打 アイドルになります+，画面仍のんびり、账本记成温存）。"""
    return current == "のんびり" and dest is not None and dest.startswith("温存")


def after_play(current, flower, card):
    """Resolve the card first, then the flower's overriding stance effect."""
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
    if current is None or flower not in (1, 2, 3):
        return None, None
    return ("のんびり", 3) if flower == 1 else (dest, flower - 1)


def flower_count(img):
    """Read the digit beside VEIL's flower ring, not the generic P-item badge."""
    from hiro.vision.digits import read_number

    if img is None or img.shape[:2] != (1280, 720):
        return None
    a = read_number(img, (596, 263, 617, 291))
    b = read_number(img, (599, 266, 615, 289))
    return a if a in (1, 2, 3) and a == b else None


FP_BOXES = ((126, 248, 162, 278), (130, 251, 158, 276))  # 左上「★ N」药丸（右边粉色圈是到 10 的进度）


def full_power_count(img):
    """画面左上姿态图标右边「★ N」里的全力値（0–10，满 10 进全力、下回合开始归零）。两个框读数一致才算，读不清 None。
    验证局 codex-20261107-1-c：账本没算「满 10 进全力归零」和条件获得（涙の思い出+「強気の場合」），记成 9、画面 0，
    按账本解灰去打 アイドルになります+ 连续没打出去停机 4 次。"""
    from hiro.vision.digits import read_number

    if img is None or img.shape[:2] != (1280, 720):
        return None
    a, b = (read_number(img, box) for box in FP_BOXES)
    return a if a is not None and a == b and 0 <= a <= 20 else None


def small_deck(state):
    if state.piles is None or not all(isinstance(state.piles.get(k), list) for k in ("deck", "grave")):
        return False
    hand = [norm(c.name) for c in state.hand if c.card_id]
    if len(hand) != len(state.hand):
        return False
    names = Counter(hand + [norm(c) for k in ("deck", "grave", "unlocated") for c in state.piles.get(k, [])])
    if REBOUND in state.piles.get("once", []):
        return False
    if any(names[c] != 1 for c in CORE) or names.total() not in (4, 5):
        return False
    extra = names - Counter(CORE)
    once = {norm(c) for c in state.piles.get("once", [])} | {norm(c.name) for c in state.hand if c.tags.once}
    return not extra or next(iter(extra)) in once | {"眠気"}


class VeilAdvisor:
    def on_round_start(self, round_no, context):
        pass

    def on_round_end(self, result):
        pass

    def decide(self, state, memory, previews, legal):
        if state.exam not in LIMITS:
            raise ResumeBlocked("VEIL 策略仅用于 H.I.F 本战 R1/R2")
        actions = {a.legal_key(): a for a in legal}

        def act(key, reason):
            if key not in actions:
                raise ResumeBlocked(f"VEIL 所需动作不可用：{reason}")
            return Decision(actions[key], reason, "rule", decider="veil")

        def play(card, reason):
            return act(f"play:{card.slot}", f"{reason}：{card.name}")

        def skip(reason):
            return act("skip", reason)

        score = state.score if state.score is not None else memory.last_score
        if score is not None and score > LIMITS[state.exam]:
            return skip("超过本轮停手阈值")

        observed = {norm(c.name): c for c in state.hand if c.card_id}
        by_name = {name: c for name, c in observed.items() if f"play:{c.slot}" in actions}
        hand = observed.keys()
        drinks = {d.name: f"drink:{d.slot}" for d in state.drinks if f"drink:{d.slot}" in actions}
        current = stance(state)
        flower = state.pitem_charges
        count = max(1, state.actions_left or 1)
        played = [norm(a[2:]) for a in memory.this_turn if a.startswith("打 ")]
        compact = small_deck(state)
        cycle = memory.strategy_data.get("veil_cycle")
        if (not cycle or cycle["turn"] != state.turn) and not played and compact and current in WARM and CORE <= hand:
            if flower in ORDERS and len(state.hand) in (4, 5):
                cycle = {"turn": state.turn, "flower": flower, "fifth": next(iter(hand - CORE), None)}
                memory.strategy_data["veil_cycle"] = cycle
        if (clear_first() and not played and not (cycle and cycle["turn"] == state.turn) and compact and current in WARM
                and flower in ORDERS and SODA in drinks and 1 <= len(CORE - hand) <= 2 and len(state.hand) + 2 <= HAND_MAX):
            # 开局只差 1–2 张核心：先喝ソーダ 抽 2 补齐再进循环（过渡里留着没喝的那瓶）
            return act(drinks[SODA], f"循环前：喝 {SODA} 抽 2 补齐核心（缺 {'、'.join(sorted(CORE - hand))}）")
        # 初星湯（ターン追加+1）：原来不在候选里，1-g R2 三次再挑戦都没喝（使用者 2026-11-07）。进循环时和别的饮料一起喝；
        # 到最后一回合还在就照喝。体力消費2：开体力闸时喝完不跌破保底线
        extra_turn_ok = clear_first() and EXTRA_TURN in drinks and (
            not hp_guard() or state.hp is None or state.hp - 2 >= hp_floor())
        if extra_turn_ok and state.remaining_turns == 1:
            return act(drinks[EXTRA_TURN], f"最后一回合：使用 {EXTRA_TURN}（回合+1）")
        if clear_first() and current == "全力" and compact and state.remaining_turns not in (None, 1):
            # 使用者 2026-11-07（1-h R2 剩 2）：循环里全力値满 10 进全力，切不了姿态 → 多过渡一回合再回循环。
            # 夜空 2：教えてあげる+ → 随便一张 OD 以外的牌（推荐眠気）→ 结束；夜空 1：OD → 教えてあげる+ → 眠気 → 结束。
            # 打完夜空 2→1、1→2，下回合按新的计数回到循环
            full = memory.strategy_data.get("veil_full")
            if not full or full.get("turn") != state.turn:
                full = memory.strategy_data["veil_full"] = {"turn": state.turn, "flower": flower}
            if full["flower"] in (1, 2):
                plan = ([DRIVE] if full["flower"] == 1 else []) + [TEACH, None]
                if len(played) < len(plan):
                    want = plan[len(played)]
                    if want is None:
                        filler = by_name.get("眠気") or next(
                            (c for n, c in by_name.items() if n != DRIVE and n not in CORE), None) or next(
                            (c for n, c in by_name.items() if n != DRIVE), None)
                        if filler is not None:
                            return play(filler, f"全力过渡（夜空 {full['flower']}）：打一张 OD 以外的牌收尾")
                    elif want in by_name:
                        return play(by_name[want], f"全力过渡（夜空 {full['flower']}）")
                    else:
                        return skip(f"全力过渡（夜空 {full['flower']}）：{want} 不在手里或打不了，结束回合")
                return skip(f"全力过渡（夜空 {full['flower']}）：打完，结束回合，下回合回到循环")
        if cycle and cycle["turn"] == state.turn:
            order = ORDERS[cycle["flower"]]
            for drink in (WHEY, BOOST, SODA) + ((EXTRA_TURN,) if extra_turn_ok else ()):
                if drink in drinks:
                    return act(drinks[drink], f"循环：使用 {drink}")
            # 第五张是アイドルになります+：它要求手里至少两张 エンターテイナー+／オーバードライブ+／教えてあげる+，
            # 放到最后时三张都已打出、永远打不了。改为循环第一张（温存に変更、+1 出牌次数，不动夜空计数，循环条件不变）。
            idol = next((n for n in (cycle.get("fifth"),) if n and n.rstrip("+") == "アイドルになります"), None)
            if idol and not played and idol in by_name and len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2:
                cycle["idol_first"] = True
                return play(by_name[idol], "五卡循环：アイドルになります+ 先打（打出条件要核心牌在手）")
            if cycle.get("idol_first") and played and played[0] == idol:
                played = played[1:]
            if played[:len(order)] != list(order[:len(played)]):
                raise ResumeBlocked("VEIL 循环出牌记录和预定顺序不一致")
            if played and played[-1] in hand:
                raise ResumeBlocked(f"VEIL 循环上一张牌仍在手中，无法确认已打出：{played[-1]}")
            if len(played) < len(order):
                name = order[len(played)]
                if name not in by_name and name in observed:
                    # 在手里但发灰（多半是全力値不够：巻き返し+ 消費1）：跳过本回合，不停机（验证局 1-c R2 剩 3 回合停在这里）
                    return skip(f"循环：下一张 {name} 在手里但打不出，本回合跳过")
                if name not in by_name:
                    raise ResumeBlocked(f"VEIL 循环下一张牌不可用：{name}")
                # 1-g R2 再挑戦 剩 7：本回合已先打 アイドルになります+，巻き返し+ 进温存后又打最后一张复用牌 エンターテイナー+
                # （补的次数没牌接、没有牌被消耗），白丢温存存下的次数 → Skip，温存留到下回合。只限这种回合、夜空在 1 / 2：
                # 普通循环回合按顺序打完（使用者 2026-11-07：进了循环就只按循环打；再挑戦2 剩 6 / 4 のんびり+夜空 3 跳过后下回合进不了循环）
                if clear_first() and cycle.get("idol_first") and len(played) == len(order) - 1 and current in WARM \
                        and flower in ORDERS and name not in CORE - {ENTERTAINER, TEACH} \
                        and (state.remaining_turns is None or state.remaining_turns > 1):
                    return skip(f"循环：アイドルになります+ 已先打，温存下最后一张 {name} 不打，温存留到下回合")
                return play(by_name[name], "五卡循环")
            if len(played) == len(order) and cycle.get("idol_first"):
                return skip("本回合循环完成（アイドルになります+ 已先打）")
            if len(played) == len(order):
                once = set((state.piles or {}).get("once", []))
                extra = next((name for name, c in observed.items() if name not in CORE
                              and (c.tags.once or c.tags.is_sleep or name in once)), None)
                fifth = extra or cycle.get("fifth")
                if fifth and fifth.rstrip("+") == "アイドルになります" and len({ENTERTAINER, DRIVE, TEACH} & hand) < 2:
                    return skip("循环：第五张受アイドルになります+禁打条件限制")
                if fifth:
                    if fifth not in by_name:
                        raise ResumeBlocked(f"VEIL 循环第五张尚未打出，当前不可用：{fifth}")
                    return play(by_name[fifth], "五卡循环收尾")
            return skip("本回合循环完成")

        eligible = [c for name, c in by_name.items() if name.rstrip("+") != DRIVE.rstrip("+") and (
            name.rstrip("+") != "アイドルになります" or len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2
        )]
        if compact:
            if flower is None and not played:
                raise ResumeBlocked("VEIL 已进入过渡，但夜空を従える花+剩余次数读不出")
            warm_next = warm_next_turn(state, memory)
            # Recompute all three needs on each frame. Teaching can cover both
            # the flower and draw, and can change the stance we need to restore.
            need_flower = flower == 3
            need_draw = state.piles.get("next_draw") not in (4, 5) and TEACH not in played
            if clear_first() and SODA in drinks:
                # 使用者 2026-11-07：过渡不喝ソーダ（当场抽的 2 张用不上、下回合只多抽 1）；留到下回合开局喝，抽 2 补齐核心
                need_draw = False
            need_warm = not warm_next and current not in WARM
            teach = by_name.get(TEACH) if TEACH not in played else None
            fp = memory.strategy_data.get("veil_fp")
            drive = by_name.get(DRIVE) if DRIVE not in played else None
            rebound = observed.get(REBOUND)
            idol = next((c for n, c in observed.items() if n.rstrip("+") == IDOL_CARD), None)
            # 过渡里 オーバードライブ 优先级很低（生成眠気，使用者 2026-11-07）：只在 アイドルになります+ 因全力値不够打不出，
            # 或 巻き返し+ 因全力値发灰、下回合又不会自动温存（一番星用完）时打（1-g R1 剩 5→1：切不回温存，五卡循环一直进不去）
            starved = [c for c in (idol,) if c is not None and not c.available and fp is not None
                       and fp < (fp_delta(c)[1] or FP_RESERVE_AT)]
            if need_warm and rebound is not None and REBOUND not in by_name and fp is not None \
                    and fp < (fp_delta(rebound)[1] or 1):
                starved.append(rebound)
            if drive_fp() and drive and starved:
                names = "、".join(norm(c.name) for c in starved)
                return play(drive, f"过渡：全力値 {fp} 打不了 {names}，先打 オーバードライブ 攒全力値")
            mods = cost_mods(memory.strategy_data.get("veil_log"), _master_text) if hp_guard() else None
            last_turn = state.remaining_turns is not None and state.remaining_turns <= 1

            def harmless(c):
                """过渡里打掉不碍事的一次性牌（使用者 2026-11-07，1-g）：不切強気（夜空计数不动，切温存可以）、下回合不切到非温存、
                不是核心牌；开体力闸时打完不跌破保底线。抽牌可以（牌组只剩核心牌，下回合照样抽得到）。"""
                if not (c.tags.once or c.tags.is_sleep) or norm(c.name) in CORE:
                    return False
                if destination(c) not in (None, *WARM) or next_stance(c) not in (None, *WARM):
                    return False
                if mods is not None and not last_turn and state.hp is not None:
                    loss = hp_loss(state, c, current, mods)
                    if loss and state.hp - loss < hp_floor():
                        return False
                return True

            if clear_first():
                # 1-g R2 剩 6：フルスロットル+（温存に変更、再动、抽 3、生成眠気）不占次数，先打，再把眠気清掉就是四卡；
                # 原来先打 教えてあげる+ 切強気、带着它 Skip，剩 3 才进循环
                free = next((c for c in eligible if c.tags.extra_actions and harmless(c)), None)
                if free:
                    return play(free, "过渡：先打不占次数的一次性牌")
            def shed_card():
                # 只生成眠気、不补次数的不打（清掉一张又塞回一张）
                return next((c for c in eligible if harmless(c) and not (c.tags.makes_sleep and not c.tags.extra_actions)), None)

            def cycle_reachable():
                """按过渡顺序（教えて / 推夜空 → 切回温存）模拟本回合：下回合开局能否温存、夜空 1/2、出牌次数够不够。"""
                acts, cur, fl = count, current, flower
                if need_flower or need_draw:
                    c = teach if teach else (next((c for c in eligible if destination(c) == "強気"), None)
                                             if not need_draw else None)
                    if c is None or cur is None:
                        return False
                    acts -= 1
                    cur, fl = after_play(cur, fl, c)
                if fl not in ORDERS:
                    return False
                if cur not in WARM and not warm_next:
                    if REBOUND not in by_name or REBOUND in played:
                        return False
                    acts -= 1
                return acts >= 0

            if clear_first() and not cycle_reachable() and (shed := shed_card()):
                # 使用者 2026-11-07：下回合能进循环是第一优先；预判下回合仍是过渡（次数不够 / 夜空回到 3 / 切不回温存）时，
                # 过渡牌白打，先清一次性牌
                return play(shed, "过渡：下回合仍进不了循环，先清一次性牌")
            if teach and (need_flower or need_draw):
                return play(teach, "过渡：推进夜空计数／预约抽牌" if need_flower else "过渡：预约下回合抽两张")
            if need_flower:
                strong = next((c for c in eligible if destination(c) == "強気"), None)
                if strong:
                    return play(strong, "过渡：推进夜空计数")
            if need_warm and current is None and not played:
                raise ResumeBlocked("VEIL 过渡无法确认当前姿态或下回合切换")
            if need_warm and REBOUND in by_name and REBOUND not in played:
                return play(by_name[REBOUND], "过渡：切回温存")
            if clear_first():
                # 使用者 2026-11-07（1-g R1）：过渡尽量从五卡缩到四卡。过渡要打的（教えて / 推花 / 切温存）都打完、本来要等下回合时，
                # 用剩下的出牌次数清掉不碍事的一次性牌（ひと呼吸+、アイドルになります+、眠気 等）。花全力値可以：循环第一张
                # オーバードライブ 先加 2。只生成眠気、不补次数的不打（清掉一张又塞回一张）。
                shed = shed_card()
                if shed:
                    return play(shed, "过渡：清掉不碍事的一次性牌，五卡缩成四卡")
            return skip("过渡：等待下回合")

        if state.piles is None and CORE <= hand:
            raise ResumeBlocked("VEIL 核心同手，尚未核实完整牌库，暂停以确认剩余牌")
        stance_cards = {
            name.rstrip("+"): c for name, c in observed.items()
            if name.rstrip("+") in ("ガタゴトすやすや", "一心不乱")
        }
        guard = hp_guard()
        mods = cost_mods(memory.strategy_data.get("veil_log"), _master_text) if guard else None
        last_turn = state.remaining_turns is not None and state.remaining_turns <= 1

        def affordable(card):
            """体力闸（VEIL_HP_GUARD）：打完还剩不到保底线的牌不打；最后一回合不限。"""
            if not guard or last_turn or state.hp is None:
                return True
            loss = hp_loss(state, card, current, mods)
            return loss == 0 or state.hp - loss >= hp_floor()  # 不掉血的牌永远能打（体力已在线下时也不能全拦）

        for name, allowed in (("ガタゴトすやすや", current in WARM), ("一心不乱", current == "強気")):
            card = stance_cards.get(name)
            if allowed and card and f"play:{card.slot}" in actions and affordable(card):
                return play(card, "清牌：姿态限定牌优先")
        if drive_fp() and (fp := memory.strategy_data.get("veil_fp")) is not None:
            drive = next((c for n, c in by_name.items() if n.rstrip("+") == DRIVE.rstrip("+")), None)
            starved = [n for n, c in observed.items() if not c.available
                       and n.rstrip("+") in (IDOL_CARD, REBOUND.rstrip("+"))
                       and fp < (fp_delta(c)[1] or (FP_RESERVE_AT if n.rstrip("+") == IDOL_CARD else 1))]
            if drive and starved and not any(fp_delta(c)[0] for c in eligible) and affordable(drive):
                return play(drive, f"清牌：全力値 {fp} 打不了 {'、'.join(starved)}，オーバードライブ 是唯一来源，先攒全力値")
        playable = list(eligible)  # 体力闸过滤前：被闸暂时压住的一次性牌（切回温存后再打）也算后面有牌可接
        if guard:
            cheap = [c for c in eligible if affordable(c)]
            if eligible and not cheap:
                return skip(f"清牌：体力闸，HP {state.hp} 打哪张都会跌破 {hp_floor()}")
            eligible = cheap
            if current == "強気" and not last_turn:
                # 強気 下体力消耗 ×2：不改姿态的掉血牌（ひと呼吸+、モチベ+…）留到温存（×0.5）再打，也不为它们喝ホエイ。
                # 使用者 2026-11-07（1-e R2 T1）：打了 インフルエンサー+、第一印象+ 后又喝ホエイ 打 モチベ+ / ひと呼吸+，
                # 体力 38→1 错过下回合出牌；理想是这两张后跳过回合，下回合进温存
                keep = [c for c in eligible if hp_loss(state, c, current, mods) == 0 or destination(c) is not None]
                if eligible and not keep:
                    return skip("清牌：強気下体力 ×2，不改姿态的掉血牌留到温存再打")
                eligible = keep
        if fp_reserve() and not last_turn:
            fp = memory.strategy_data.get("veil_fp")
            deck = memory.strategy_data.get("veil_deck")
            idol_left = deck is None or any(n.rstrip("+") == IDOL_CARD and k > 0 for n, k in deck.items())

            def keeps_reserve(card):
                gain, cost = fp_delta(card)
                return not cost or norm(card.name).rstrip("+") == IDOL_CARD or fp - cost + gain >= FP_RESERVE_AT

            if fp is not None and idol_left:
                kept = [c for c in eligible if keeps_reserve(c)]
                if eligible and not kept:
                    return skip(f"清牌：预留全力値，账本 {fp}，花完不足 {FP_RESERVE_AT}（留给 アイドルになります+）")
                eligible = kept
        if fp_cap() and not last_turn and (fp := memory.strategy_data.get("veil_fp")) is not None:
            def under_cap(card):
                gain, cost = fp_delta(card)
                return not gain or fp - cost + gain < FULL_POWER_AT

            capped = [c for c in eligible if under_cap(c)]
            if capped and len(capped) < len(eligible):
                # 只在还有别的牌可打时避开；全是加满的牌就照打，不空转。记一笔供审计（验证局 1-d 看不出它有没有起作用）
                log = memory.strategy_data.setdefault("veil_fp_cap_log", [])
                entry = {"turn": memory.turn, "fp": fp, "avoided": sorted(norm(c.name) for c in eligible if c not in capped)}
                if not log or log[-1] != entry:
                    log.append(entry)
                eligible = capped
        if not eligible:
            return skip("清牌：没有允许打出的已识别牌")

        from hiro.brain.memory_ability import pending as ability_of

        abilities = memory.strategy_data.get("veil_abilities")

        def ability_kind(card):
            hit = ability_of(abilities, card.name)
            return hit["kind"] if hit else None

        def immediate_draw(card):
            return ((card.tags.draws and "次のターン" not in norm(card.text)) or "手札をすべて入れ替える" in norm(card.text)
                    or ability_kind(card) == "draw")

        def resulting_stance(card):
            return after_play(current, flower, card)[0]

        release_first = release_gain()

        def gains_now(card):
            # 回忆能力（再动 / 再动并抽牌）在打出后再给一次出牌次数
            # 温存解除：开关打开时看牌自己的切换（先结算牌，夜空 1→のんびり 在后），不看夜空覆盖后的最终姿态
            to_strong = destination(card) == "強気" if release_first else resulting_stance(card) == "強気"
            return (card.tags.extra_actions + int(to_strong and current in WARM)
                    + int(ability_kind(card) in ("draw", "play")))

        def unlocks(card):
            dest = resulting_stance(card)
            return (dest in WARM and current not in WARM and "ガタゴトすやすや" in stance_cards
                    or dest == "強気" and current in WARM and "一心不乱" in stance_cards)

        keep = keep_warm()
        once_known = set((state.piles or {}).get("once") or ()) | set(memory.strategy_data.get("veil_known_once") or ())
        initial_once = set(memory.strategy_data.get("veil_initial_once") or ())

        def clears(card):
            """打出后这张牌离开牌库（一次性牌 / 眠気）：清牌阶段真正推进的出牌。"""
            name = norm(card.name)
            if name == REBOUND:
                return name in initial_once
            return bool(card.tags.once or card.tags.is_sleep or name in once_known)

        extra = memory.strategy_data.get("veil_extra") or {}
        this_turn = extra.get("turn") == memory.turn
        stacks = count - 1  # 当前追加层数（基础 1 次之外）
        carry_now = not (this_turn and extra.get("start")) or bool(this_turn and extra.get("zeroed"))

        def reusable_ok(card):
            """不清牌的复用牌（エンターテイナー、教えてあげる…）：只在不减少「温存 + 能留到下回合的再动」时打。
            代价：温存下切強気用掉温存存着的一次再动；有可保留的追加层时，打牌消耗一层。
            收益：打出后得到的追加次数（温存→強気、牌面、回忆能力），只有能留到下回合（本回合开始时没有，或这一张把层数用到 0）才算。"""
            warm_spent = current in WARM and resulting_stance(card) == "強気"
            cost = int(warm_spent) + int(stacks > 0 and carry_now)
            gain = gains_now(card) if carry_now or stacks <= 1 else 0
            return gain >= cost

        def priority(card):
            dest = resulting_stance(card)
            delays_warm = current == "強気" and dest in WARM
            opens_followup = unlocks(card) and (count > 1 or gains_now(card) > 0 or WHEY in drinks)
            # 体力闸开着时：強気下先打切回温存的牌（「強気に変更」的牌留到温存 / のんびり 下打，消耗减半或为 0），再比掉血少
            # 切回温存只在本回合还有下一张要打时才有用（后面的牌消耗减半）；只剩 1 次出牌时不靠它排前（1-f R2 剩 8：
            # 教えてあげる+ 因夜空最后 1 次会变のんびり 被排在 ひと呼吸+ 前，一次性牌又被压后）
            hp_first = (dest in WARM and count > 1, -hp_loss(state, card, current, mods)) if guard and current == "強気" else ()
            return hp_first + ((clears(card),) if keep or clear_first() else ()) + (
                gains_now(card), opens_followup, not delays_warm,
                bool(card.tags.once and norm(card.name) != REBOUND),
                # 都是再动时立即抽牌减分（使用者 2026-11-07：先清不抽牌的再动牌，避免满手抽牌作废）；开关关时照旧加分
                # 同样不抽牌时不改姿态的先（温存下先清 みずたまりスキップ，切強気会让后面的牌消耗翻倍）
                ((not immediate_draw(card), dest == current) if draw_last() and gains_now(card) > 0
                 else (bool(immediate_draw(card)), False)),
                ability_kind(card) == "sleep", dest != current, card.badge_score or 0)

        if keep:
            eligible = [c for c in eligible if clears(c) or reusable_ok(c)]
            if not eligible:
                return skip("清牌：只剩复用牌，打出会用掉温存或可留到下回合的再动")

        # Preserve a later play for ひと呼吸 whenever another legal card is available.
        preferred = [c for c in eligible if norm(c.name).rstrip("+") != "ひと呼吸"]
        if clear_first() and not any(clears(c) for c in preferred):
            preferred = []  # ひと呼吸 只让给别的一次性牌：剩下的全是复用牌时它照常参与排序（一次性优先）
        best = max(preferred or eligible, key=priority)
        good_first = None
        start = current if current is not None else (NEUTRAL if memory.turn == 1 and not played else None)
        if clear_first() and start is not None and 2 <= len(eligible) <= 5:
            # 使用者 2026-11-07（1-g R2 再挑戦 剩 10）：のんびり 开局 第一印象（強気、解除+1）→ 達成感（温存、+1）→
            # 全身全霊（温存→強気 解除+1、+2）比实际 達成感 → 第一印象 → 全身全霊 多 1 次出牌。贪心一张张比、DRAW_LAST 只比两张
            # 的先后都看不出 → 枚举可打牌的出牌顺序，按姿态 / 夜空推演，取「打出张数、剩余次数」最多的顺序；best 开头的顺序
            # 一样好时不换。抽牌带来的新牌、体力不算（体力闸已在 eligible 里筛过）
            from itertools import permutations

            def gain_at(card, cur, fl):
                to_strong = destination(card) == "強気" if release_first else after_play(cur, fl, card)[0] == "強気"
                return card.tags.extra_actions + int(to_strong and cur in WARM) + int(ability_kind(card) in ("draw", "play"))

            warm_already = warm_next_turn(state, memory)

            def run(order):
                """（清掉的一次性牌数，剩余次数，少打复用牌，抽牌晚打）：清牌阶段只算一次性牌（复用牌用掉温存、
                1-f R1 一次性牌被拖后），抽进来的牌不因手牌满作废作最后比较。
                剩余次数按回合末算：牌都打完、没抽到新牌时剩下的次数作废（不算）；回合末留在温存 / のんびり 算 1 次
                （下回合第一次切強気的温存解除；下回合开局本来就会进温存时——预约「次のターン温存に変更」、一番星还有次数——
                停在強気也算，几种来源同时有也只算 1 次）。使用者 2026-11-07（1-h R1 T1）：喝ホエイ 后手里 プライド+（温存）、
                心・技・体（強気），应先打心・技・体 再打プライド+ 留温存到下回合，而不是温存→強気 拿一次用不掉的解除。"""
                acts, cur, fl, n, cleared, reused = count, start, flower, 0, 0, 0
                warm_later = warm_already
                zeroed = carry_now
                size, waste, draws = len(state.hand), 0, []
                for c in order:
                    if acts < 1 or cur is None:
                        break
                    acts += gain_at(c, cur, fl) - 1
                    if acts <= 1:
                        zeroed = True  # 再动层数用到 0：之后再拿到的能留到下回合
                    cur, fl = after_play(cur, fl, c)
                    if norm(c.name).startswith(STAR) or next_stance(c) in WARM:
                        warm_later = True
                    n += 1
                    if clears(c):
                        cleared += 1
                    else:
                        reused += 1
                    d = max(c.tags.draws or 0, int(ability_kind(c) == "draw")) if immediate_draw(c) else 0
                    size = size - 1 + d
                    waste += max(0, size - HAND_MAX)
                    size = min(size, HAND_MAX)
                    draws.append(-d)
                    if "手札をすべて入れ替える" in norm(c.text) and n < len(order):
                        break  # 1-h R2 剩 10：仕切り直し+ 换掉整手牌，排在它后面的牌（頂点へ+）打不到了
                if n == len(order) and not any(draws):
                    # 牌打完了、也没抽到牌：基础的 1 次用不掉；再动层数按 buff 保留规则留到下回合（track_extra：回合开始
                    # 没有层数、或本回合用到过 0 → 全留；否则衰退 1）。1-h R2 剩 10 Skip 时剩 2 次，剩 9 实际多打了 1 张
                    stacks = max(0, acts - 1)
                    acts = stacks if zeroed else max(0, stacks - 1)
                # 下回合开局本来就会进温存（预约 / 一番星）时回合末停在強気也行；几种来源只算一次（使用者 2026-11-07 补充）
                acts += int((cur in WARM or warm_later) and not last_turn)
                # 抽牌：先比手牌满作废的张数，再按出牌顺序比抽牌数（抽得少的先打）
                return (cleared, acts, -reused, -waste, tuple(draws))

            scored = [(run(o), o) for o in permutations(eligible)]
            # 前四项（清掉的一次性牌、剩余次数、少打复用牌、抽牌作废）严格更好才换；打平时不换，只把后面 DRAW_LAST 的换牌限制在
            # 最优顺序的开头里。抽得少的先打只在最优开头之间挑
            top4 = max(v[:4] for v, _ in scored)
            good = [(v, o) for v, o in scored if v[:4] == top4]
            good_first = {id(o[0]) for _, o in good}
            from_best = max((v[:4] for v, o in scored if o[0] is best), default=None)
            if from_best is not None and top4 > from_best:
                pick = max(good, key=lambda vo: (vo[0], priority(vo[1][0])))[1][0]
                why = ("多清 %d 张一次性牌" % (top4[0] - from_best[0]) if top4[0] > from_best[0] else
                       "多留 %d 次出牌" % (top4[1] - from_best[1]) if top4[1] > from_best[1] else
                       "少打复用牌" if top4[2] > from_best[2] else "抽牌少作废 %d 张" % (top4[3] - from_best[3]))
                return play(pick, f"清牌：按出牌顺序推演先打它（{why}）")
        late = next((c for c in eligible if norm(c.name).rstrip("+") == "ひと呼吸"), None)
        if late and count == 1 and gains_now(late) > gains_now(best) and WHEY not in drinks:
            best = late
        if clear_first() and not last_turn and eligible and not any(clears(c) for c in playable):
            # 1-g R2 再挑戦 剩 10：全力値 0、巻き返し+ 灰着，手里只剩复用牌 エンターテイナー+ 也打了（切強気用掉温存，
            # 加的次数没有一次性牌可接）。清牌阶段可打的只剩复用牌时不打，温存留到下回合
            grey = [n for n, c in observed.items() if not c.available and clears(c)]
            return skip("清牌：可打的只剩复用牌" + (f"（{'、'.join(grey)} 发灰）" if grey else "") + "，不打、温存留到下回合")
        if count > len(eligible) and not unlocks(best):
            # 换掉整手牌的（仕切り直し+）只在手里没有别的可打牌时才先打，否则别的牌被换回牌库
            draw_card = next((c for c in sorted(eligible, key=priority, reverse=True) if immediate_draw(c)
                              and (not clear_first() or "手札をすべて入れ替える" not in norm(c.text) or len(eligible) == 1)), None)
            if draw_card:
                return play(draw_card, "清牌：额外行动优先用技能卡抽牌")
            # 手牌满时喝ソーダ 抽的 2 张作废（1-g R2 再挑戦 T1：5 张满手连喝三瓶）→ DRAW_LAST 下手牌放得下才喝
            if SODA in drinks and (not draw_last() or len(state.hand) + 2 <= HAND_MAX):
                return act(drinks[SODA], "清牌：有行动但缺手牌")
        if draw_last() and immediate_draw(best) and gains_now(best) > 0:
            # 换成「不抽牌、自己也加出牌次数」的牌先打：出牌次数不减少，手牌先少一张，best 抽的牌不会因手牌满作废。
            # 两种先后的总加次数（含姿态切换带来的温存解除）要不少于原顺序；姿态读不准（None）时不换。
            # 验证局 R2 剩 11 回合：のんびり、满手，オーバードライブ（再动、切強気）应先于 フルスロットル+（再动、抽 3）
            def gains_at(card, cur, fl):
                if cur is None:
                    return None
                to_strong = destination(card) == "強気" if release_first else after_play(cur, fl, card)[0] == "強気"
                return card.tags.extra_actions + int(to_strong and cur in WARM) + int(ability_kind(card) in ("draw", "play"))

            def total(first, second):
                g1 = gains_at(first, current, flower)
                cur2, fl2 = after_play(current, flower, first)
                g2 = gains_at(second, cur2, fl2)
                return None if g1 is None or g2 is None else g1 + g2

            # 一次性优先（CLEAR_FIRST）时，复用牌只在 best 抽的牌会溢出手牌（>5）时才拿来垫（1-f R2 T1：手牌 4 张，
            # インフルエンサー 只抽 1 张不会溢出，却先打了 教えてあげる+ 用掉温存）
            overflow = len(state.hand) - 1 + max(best.tags.draws or 0, int(ability_kind(best) == "draw")) > HAND_MAX
            plain = []
            for c in eligible:
                if c is best or immediate_draw(c) or not gains_now(c):
                    continue
                if good_first is not None and id(c) not in good_first:
                    continue  # 出牌顺序推演里它开头不是最优
                if clear_first() and not clears(c) and not overflow:
                    continue
                c_first, b_first = total(c, best), total(best, c)
                if c_first is not None and b_first is not None and c_first >= b_first:
                    plain.append(c)
            if plain:
                # 优先不改姿态的（みずたまりスキップ）：切強気会让后面的牌消耗翻倍
                pick = max(plain, key=lambda c: (resulting_stance(c) == current, priority(c)))
                return play(pick, "清牌：先打不抽牌的再动牌，抽牌的留后")
            if overflow and clear_first():
                # 1-g R2 再挑戦 T1：手牌 5 张，先打了 全身全霊（再动、抽 3，溢出 2 张），インフルエンサー（再动、抽 1）不溢出却留后。
                # 抽得少、打完手牌不溢出的一次性再动牌先打（两种先后总加次数不少于原顺序）
                fit = []
                for c in eligible:
                    if c is best or not immediate_draw(c) or not gains_now(c) or not clears(c):
                        continue
                    if good_first is not None and id(c) not in good_first:
                        continue
                    if len(state.hand) - 1 + max(c.tags.draws or 0, 1) > HAND_MAX:
                        continue
                    c_first, b_first = total(c, best), total(best, c)
                    if c_first is not None and b_first is not None and c_first >= b_first:
                        fit.append(c)
                if fit:
                    return play(max(fit, key=priority), "清牌：先打抽得少、手牌不溢出的再动牌")
        if release_first and count == 1 and len(eligible) > 1 and not gains_now(best):
            gainer = max((c for c in eligible if gains_now(c)), key=priority, default=None)
            # 一次性优先（CLEAR_FIRST / KEEP_WARM）时，best 是一次性牌、又没有ホエイ 要省：不拿复用牌（切強気用掉温存）插队
            if gainer is not None and (keep or clear_first()) and clears(best) and not clears(gainer) and WHEY not in drinks:
                gainer = None
            if gainer is not None:
                return play(gainer, "清牌：先打能加出牌次数的牌，不用ホエイ")
        if count == 1 and (len(eligible) > 1 or unlocks(best)) and not gains_now(best) and WHEY in drinks:
            return act(drinks[WHEY], "清牌：有手牌但出牌次数不足")
        return play(best, "清牌")
