"""Screen adapter and single-round runner for the VEIL batch route."""

from __future__ import annotations

import json
import os
import uuid
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from hiro.brain.battle import BattleBrain
from hiro.defaults.exam_state import ScreenExamStateSource
from hiro.loop.errors import ResumeBlocked
from hiro.brain.veil import (
    CORE, DRIVE, IDOL, REBOUND, SODA, STANCES, STRATEGY_FLAGS, TEACH, WARM, VeilAdvisor, _flag,
    NEUTRAL, STAR, after_play, destination, flower_count, keeps_nonbiri, full_power_count, keep_warm, next_stance_change, norm,
    stance, stance_level, stance_change_level, start_turn, level_after, swaps_hand,
)


SLEEP_GENERATORS = {DRIVE, "フルスロットル", "フルスロットル+"}


def track_extra(data, memory, keep):
    """「スキルカード使用数追加」（再动层数）跨回合记账，按同类 buff 的衰退规则：
    回合开始时没有、回合内获得 → 留到下回合；回合开始时已有、回合内又获得 → 衰退（减 1）；
    回合开始时已有、回合内用到 0 后再获得 → 留到下回合。
    data["veil_extra"] = {turn, start（开局有没有再动）, zeroed（本回合用到过 0）, carried, left（剩余出牌次数，含基础 1 次）}。
    keep（VEIL_KEEP_WARM）时新回合把留下的层数加回 memory.actions_left；关着时只记账、不改行为。"""
    ex = data.get("veil_extra")
    if ex is not None and ex.get("turn") == memory.turn:
        return ex
    carried = 0
    if ex is not None:
        left = max(0, (ex.get("left") or 0) - 1)  # 上回合结束时剩的追加层数
        carried = left if not ex.get("start") or ex.get("zeroed") else max(0, left - 1)
    if keep:
        memory.actions_left = 1 + carried
    ex = {"turn": memory.turn, "start": carried > 0, "zeroed": False, "carried": carried, "left": memory.actions_left}
    data["veil_extra"] = ex
    return ex


def note_extra(data, memory, decision, before):
    """动作执行后更新本回合记账。再动层数和基础的 1 次出牌不重叠：层数 = 剩余出牌次数 - 1，
    打牌先扣再动层；扣到只剩 1 次出牌（层数 0）就算用完（zeroed），之后获得的再动能留到下回合。"""
    ex = data.get("veil_extra")
    if ex is None or ex.get("turn") != memory.turn:
        return
    stacks_before = None if before is None else before - 1
    if decision.kind == "play" and stacks_before is not None and stacks_before - 1 <= 0:
        ex["zeroed"] = True
    ex["left"] = memory.actions_left


def signature_mode():
    """HIRO_VEIL_SIGNATURE=1：牌库按「版本」记账（默认关）。同名卡一次性 / 客制化不同时（账本 instances 里看得出），
    各算一个记账键（巻き返し+〔一次性〕/ 巻き返し+〔常驻·制限削除〕），手里同名几张分不出时点按一次读效果文字分辨
    （使用者 2026-11-07：同名的差别只来自客制化，客制化一样就是同一张卡）。同名各张都一样时键就是卡名，和原来完全相同。"""
    return os.environ.get("HIRO_VEIL_SIGNATURE") == "1"


KEY_MARK = "〔"


def name_of(key: str) -> str:
    """记账键 → 卡名（去掉版本后缀）。"""
    return key.split(KEY_MARK, 1)[0]


def instance_keys(instances) -> dict[str, dict]:
    """逐张牌组（batch_ledger.to_deck 的 instances）→ {记账键: {name, once, customs, count}}。"""
    by_name = defaultdict(list)
    for inst in instances:
        by_name[norm(inst["name"])].append(inst)
    out = {}
    for name, same in by_name.items():
        variants = {(bool(i.get("once")), tuple(norm(c) for c in i.get("customs") or ())) for i in same}
        for inst in same:
            once, customs = bool(inst.get("once")), tuple(norm(c) for c in inst.get("customs") or ())
            key = name if len(variants) == 1 else f"{name}{KEY_MARK}{'·'.join(['一次性' if once else '常驻', *customs])}〕"
            entry = out.setdefault(key, {"name": name, "once": once, "customs": list(customs), "count": 0})
            entry["count"] += 1
    return out


def read_variants(source) -> dict[str, dict] | None:
    """牌组清单里的逐张牌组 → 记账键（开关关着 / 没有 instances / 没有同名不同版本时返回 None，走原来的按卡名记账）。"""
    if not signature_mode():
        return None
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    instances = data.get("instances")
    if not isinstance(instances, list) or not instances:
        return None
    keys = instance_keys(instances)
    return keys if any(k != v["name"] for k, v in keys.items()) else None


def validate_deck(data):
    if not isinstance(data, dict) or not isinstance(data.get("evidence"), str) or not data["evidence"].strip():
        raise ValueError("牌组清单须记录证据路径")
    if not isinstance(data.get("cards"), list) or not all(isinstance(c, str) and c.strip() for c in data["cards"]):
        raise ValueError("牌组清单须列出完整卡名及强化，每张单列")
    names = Counter(norm(c) for c in data["cards"])
    core_ok = all(names[c] == 1 for c in CORE)
    if not core_ok and signature_mode() and isinstance(data.get("instances"), list):
        # 分版本记账：多出来的同名巻き返し+ 是一次性的清牌牌，循环核心是那张常驻的（只能有一张）
        rebounds = [i for i in data["instances"] if norm(i.get("name") or "") == REBOUND]
        core_ok = all(names[c] == 1 for c in CORE - {REBOUND}) and sum(1 for i in rebounds if i.get("once") is False) == 1
    if not core_ok or not isinstance(data.get("rebound_repeatable"), bool):
        raise ValueError("须记录四张核心及巻き返し+的一次性限制状态")
    once = data.get("once", [])
    if not isinstance(once, list) or not all(isinstance(c, str) and norm(c) in names for c in once):
        raise ValueError("once 只可列出清单中已核对的一次性卡")
    known_once = {norm(c) for c in once}
    if data["rebound_repeatable"]:
        known_once.discard(REBOUND)
    else:
        known_once.add(REBOUND)
    return dict(names), known_once


# 客制化项 → 打出时立即结算的牌面文字（加在原文前，destination / fp_effect 按「次のターン」之前的部分解析）。
# 使用者 2026-11-07：1-d R2 インフルエンサー+ 客制化「強気追加」后打出会切強気、扣夜空，账本没算（R1 没客制化所以没事）。
CUSTOM_TEXT = {"強気追加": "強気に変更"}


UNKNOWN_DRINK = "未识别饮料"  # batch_ledger 懒同步的占位名：账本知道这格有饮料、不知道是什么


def bar_record(bar) -> list[dict]:
    """饮料栏账本 → 考试结果里的 4 格记录（batch_ledger.after_exam 读 state=drink 的名字）。"""
    out = [{"state": "drink", "name": n} for n in bar[:4]]
    return out + [{"state": "empty"} for _ in range(4 - len(out))]


def sync_bar(data, ledger_drinks, physical, turn):
    """饮料按账本（使用者 2026-11-07：任何时候以账本为准）。本场饮料栏 veil_bar 开考时取批量账本，喝一瓶 pop 一格；
    画面只对账：不一致记 veil_drink_log，不改账本。账本不知道的格（空格在栏尾 / 占位名）画面认得出才补上（同全力値 None 时）。
    返回给决策的饮料（按账本格号）。"""
    from hiro.brain.cards import drink_view

    bar = data.get("veil_bar")
    if bar is None and data.get("veil_drinks"):
        # 旧记忆（按格记 drink / consumed）续跑：没喝的按原顺序接上，喝过的不再补回
        bar = data["veil_bar"] = [norm(d["name"]) for d in data["veil_drinks"] if d and d.get("state") == "drink" and d.get("name")]
    if bar is None:
        bar = data["veil_bar"] = [norm(n) for n in ledger_drinks or [] if n]
    screen = {s["slot"]: s for s in physical if 0 <= s.get("slot", -1) < 4}
    notes = []
    for i in range(4):
        s = screen.get(i) or {}
        seen = norm(s["name"]) if s.get("state") == "drink" and s.get("name") else None
        want = bar[i] if i < len(bar) else None
        if want in (None, UNKNOWN_DRINK) and seen and i <= len(bar):
            if want is None:
                bar.append(seen)
            else:
                bar[i] = seen
            notes.append(f"第 {i + 1} 格按画面补账：{seen}")
        elif want is None and s.get("state") in ("drink", "unknown"):
            notes.append(f"第 {i + 1} 格：账本 空，画面 有饮料")
        elif want is not None and s.get("state") == "empty":
            notes.append(f"第 {i + 1} 格：账本 {want}，画面 空")
        elif want is not None and seen and seen != want:
            notes.append(f"第 {i + 1} 格：账本 {want}，画面 {seen}")
    if notes:
        log = data.setdefault("veil_drink_log", [])
        entry = {"turn": turn, "diff": notes}
        if not log or log[-1] != entry:
            log.append(entry)
    data["veil_drinks"] = bar_record(bar)
    return [drink_view(i, d) for i, n in enumerate(bar[:4]) if (d := _drink_by_name(n))]


def read_customs(source) -> list[dict]:
    """牌组清单里的客制化记录（batch_ledger.to_deck 的 custom）：[{card, item}]。"""
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    return [c for c in data.get("custom") or [] if isinstance(c, dict) and c.get("card") and c.get("item")]


def custom_master():
    """HIRO_VEIL_CUSTOM_MASTER=1：客制化效果按主数据查（ProduceCardCustomize 的菜单名 → 「追加」类成长效果引用的考试效果文字），
    覆盖所有「○○追加」项，例如 インフルエンサー+「温存追加」=「次のターン、温存に変更」（原来只认「強気追加」，
    预约温存没进账本）。查不到时退回 CUSTOM_TEXT。默认关，回放验证后再开。"""
    return os.environ.get("HIRO_VEIL_CUSTOM_MASTER") == "1"


@lru_cache(maxsize=None)
def master_customs(card_name: str) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]:
    """主数据里这张卡（按卡名，带「+」）的客制化项，每项按级排：((菜单名, 这一级追加的效果文字), …)。
    可重复客制化是覆盖：做到第 N 级时牌面是第 N 级的文字（使用者 2026-11-07：巻き返し+ 成長 第 1 级 +1、第 2 级 +2），不累加。
    只有「EffectAdd」（加一段效果）的级才有文字；数值增减（パラメータ値増加、コスト減少…）不改文字，那一级给空。
    没有主数据 / 查不到返回 ()。"""
    import sqlite3

    from hiro.brain.cards import description_text
    from hiro.state.catalog import CatalogUnavailable, catalog

    try:
        db = catalog().db
        row = db.execute("SELECT produceCardCustomizeIds FROM ProduceCard WHERE name = ? LIMIT 1", (card_name,)).fetchone()
        items = []
        for cid in json.loads((row[0] if row else None) or "[]"):
            levels = []
            for desc, grow_ids in db.execute("SELECT description, produceCardGrowEffectIds FROM ProduceCardCustomize "
                                             "WHERE id = ? ORDER BY customizeCount", (cid,)).fetchall():
                texts = []
                for gid in json.loads(grow_ids or "[]"):
                    grow = db.execute("SELECT effectType, playProduceExamEffectId, targetPlayProduceExamEffectIds "
                                      "FROM ProduceCardGrowEffect WHERE id = ?", (gid,)).fetchone()
                    if grow is None or grow[0] != "ProduceCardGrowEffectType_EffectAdd":
                        texts = []
                        break
                    for eid in [grow[1], *json.loads(grow[2] or "[]")]:
                        effect = db.execute("SELECT produceDescriptions FROM ProduceExamEffect WHERE id = ?", (eid,)).fetchone() if eid else None
                        if effect is not None and (text := norm(description_text(effect[0]))):
                            texts.append(text)
                levels.append((norm(desc or ""), tuple(texts)))
            items.append(tuple(levels))
        return tuple(items)
    except (CatalogUnavailable, sqlite3.Error, ValueError, TypeError):
        return ()


def master_custom_texts(card_name: str) -> dict[str, tuple[str, ...]]:
    """只有一级、有菜单名和文字的客制化项：{菜单名: 文字}（核对 / 测试用）。"""
    return {lv[0][0]: lv[0][1] for lv in master_customs(card_name) if len(lv) == 1 and lv[0][0] and lv[0][1]}


def keyword_texts(item):
    text = next((v for k, v in CUSTOM_TEXT.items() if k in item), "強気に変更" if "強気" in item else None)
    return [text] if text else []


def resolve_customs(customs, lookup=None, copies=None, uncertain=None) -> dict[str, list[str]]:
    """客制化记录（按时间顺序）→ {去「+」的卡名: 要补进牌面的文字}。
    开关开着时按主数据：OCR 菜单名取它包含的最长菜单名，认到哪一项哪一级；第 1 级菜单名（「成長追加」）开始一条链，
    之后的「成長+」把这条链升一级，最终取那一级的文字（覆盖，不累加）。认不准时不猜，这一项不补：
    同一项不同级的菜单名认成了不同项、链没开头就升级、同名多张做到的级数不同、级数超过主数据。
    主数据里认不到的记录（或开关关着）按 CUSTOM_TEXT 关键词。
    copies（{去「+」的卡名: 牌组里几张}）给出时，同名多张只补每张都做过的项：某项做过的张数（链条数）少于牌组张数，
    就不知道手里这张是哪一张，这一项不补，名字记进 uncertain（列表，调用方写 trace）。"""
    groups = defaultdict(list)
    for c in customs:
        groups[norm(c["card"])].append(norm(c["item"]))  # 记的是菜单 OCR 名，可能带别的字
    out = defaultdict(list)
    for name, records in groups.items():
        base = name.rstrip("+")
        items = (lookup or master_customs)(name) if custom_master() else ()
        chains, broken = defaultdict(list), set()
        for record in records:
            hits = [(len(desc), k, i) for k, levels in enumerate(items) for i, (desc, _) in enumerate(levels) if desc and desc in record]
            if not hits:
                out[base].extend(keyword_texts(record))
                continue
            longest = max(h[0] for h in hits)
            hits = [h for h in hits if h[0] == longest]
            if len({k for _, k, _ in hits}) != 1:
                continue  # 不同项同名：不知道是哪一项
            k = hits[0][1]
            level_hits = {i for _, _, i in hits}
            if level_hits == {0}:
                chains[k].append(1)  # 第 1 级的菜单名：新的一条链（同名另一张，或这一张第一次做）
            elif 0 in level_hits:
                broken.add(k)  # 第 1 级和后面的级同名：分不清是新做还是升级
            elif len(chains[k]) == 1:
                chains[k][0] += 1
            else:
                broken.add(k)  # 升级菜单名但还没开头 / 同名多张不知道升的是哪张
        for k, chain in chains.items():
            if k in broken or len(set(chain)) != 1 or chain[0] > len(items[k]):
                continue
            if copies and len(chain) < copies.get(base, 1):
                if uncertain is not None:
                    uncertain.append(f"{name}:{items[k][0][0]}")
                continue
            out[base].extend(items[k][chain[0] - 1][1])
    return out


def support_boost():
    """HIRO_VEIL_SUPPORT_BOOST=1：手牌效果按「账本档位 + 支援卡临时强化（卡图头像个数）」算（默认关）。
    使用者 2026-11-09：++ / +++ 不进账本，但打出时效果按 ++ / +++ 算。"""
    return os.environ.get("HIRO_VEIL_SUPPORT_BOOST", "0") == "1"


def apply_boosts(hand, raw_hand, deck, version=None):
    """卡名的「+」被下一张卡盖住（plus 为 None，画面按基础版给效果）但数得出头像（boost）的牌：
    效果换成「账本档位 + boost」那一档。账本档位按牌组账本里这张牌的名字定（同名两档都有时分不出，不改）。
    名字改成账本里的名字（记账键不变），返回 (新手牌, [改动])。卡名「+」看得见时画面已按总档位给效果，不动。"""
    from dataclasses import replace

    from hiro.brain.cards import card_view

    if version is None:
        from hiro.vision.cards import card_version as version
    by_slot = {e.get("slot"): e for e in raw_hand or []}
    out, changed = [], []
    for c in hand:
        e = by_slot.get(c.slot) or {}
        boost = e.get("boost")
        if not c.card_id or e.get("plus") is not None or not boost:
            out.append(c)
            continue
        base = norm(c.name).rstrip("+")
        levels = [n.count("+") for n in (base, base + "+") if n in deck]
        if len(levels) != 1:
            out.append(c)
            continue
        level = levels[0] + boost
        card = version(c.card_id, level)
        if card is None:
            out.append(c)
            continue
        view = card_view(c.slot, card, available=c.available)
        out.append(replace(view, name=base + "+" * levels[0], badge_score=c.badge_score))
        changed.append({"slot": c.slot, "card": base + "+" * levels[0], "boost": boost, "level": level})
    return out, changed


# 档位影响决策 / 记账的结构化字段（只差得分的不算）
BOOST_KEYS = ("stance", "stance_level", "next_stance", "next_stance_level", "fp_gain", "fp_cost", "fp_boost",
              "extra_actions", "draws", "next_draws", "genki", "makes_sleep", "once", "condition")


def boost_matters(card_id, level) -> bool:
    """这张牌多一档临时强化时，影响决策 / 记账的效果会不会变（只多得分的不点开看）。"""
    from hiro.brain.card_fx import card_fx

    a, b = card_fx(card_id, level), card_fx(card_id, level + 1)
    if b is None:  # 没有更高一档（眠気、星々を見下ろす一番星 等）
        return False
    if a is None:
        return True
    return any(a.get(k) != b.get(k) for k in BOOST_KEYS)


def level_from_text(card_id, text, low: int, high: int = 3) -> int | None:
    """点按选中后读到的 → 档位：有完整卡名（PROBE_NAME 行）时数「+」；否则和主数据 low..high 各档的说明文字比相似度，取最像的（分不出返回 None）。"""
    import difflib

    from hiro.brain.cards import description_text
    from hiro.vision.cards import card_version

    if text and text.startswith(PROBE_NAME):
        label, _, text = text[len(PROBE_NAME):].partition("\n")
        plus = norm(label).replace("＋", "+")
        level = len(plus) - len(plus.rstrip("+"))
        if low <= level <= high:
            return level  # 选中后完整卡名上的「+」个数就是档位
    t = norm(text or "")
    if not t:
        return None
    scores = []
    for level in range(low, high + 1):
        card = card_version(card_id, level)
        if card is None:
            continue
        ref = norm(description_text(card.produce_descriptions) or "")
        scores.append((difflib.SequenceMatcher(None, ref, t).ratio(), level))
    scores.sort(reverse=True)
    if not scores or (len(scores) > 1 and scores[0][0] - scores[1][0] < 0.02):
        return None
    return scores[0][1]


def apply_customs(hand, customs, lookup=None, copies=None, uncertain=None):
    """手牌里客制化过的牌：按账本把客制化效果补进牌面文字（同名按去「+」比；已含该文字的不重复加）。
    立即结算的文字加在原文前；「次のターン / ターン開始 / 以降」这类延后的加在原文后
    （destination / fp_effect 只看第一个「次のターン」之前的部分，加在前面会把原文的立即效果挤到延后段里）。"""
    from hiro.brain.veil import _DELAYED

    extra = resolve_customs(customs, lookup, copies, uncertain)
    if not any(extra.values()):
        return hand
    from hiro.brain import card_fx

    out = []
    for card in hand:
        texts = list(dict.fromkeys(extra.get(norm(card.name).rstrip("+"), [])))
        add = [t for t in texts if t not in norm(card.text or "")]
        if add:
            before = "".join(t for t in add if not _DELAYED.search(t))
            after = "".join(t for t in add if _DELAYED.search(t))
            card = replace(card, text=before + (card.text or "") + after)
        if texts and card.fx is not None:
            # 结构化效果也并进客制化追加的效果（主数据效果 id）；有一段对不上 id 就整张退回文字读法（fx=None）
            index = custom_effect_ids(norm(card.name))
            card = replace(card, fx=card_fx.add_effects(card.fx, [index.get(t) for t in texts]))
        out.append(card)
    return out


GENERIC_EFFECT_IDS = {"強気に変更": "e_effect-exam_concentration-0001", "温存に変更": "e_effect-exam_preservation-0001"}


@lru_cache(maxsize=None)
def custom_effect_ids(card_name: str) -> dict[str, str]:
    """客制化「追加」效果的文字 → 主数据效果 id（这张卡的全部客制化项，按 master_customs 同样的读法），
    外加关键词兜底（CUSTOM_TEXT 的「強気に変更」）。"""
    import sqlite3

    from hiro.brain.cards import description_text
    from hiro.state.catalog import CatalogUnavailable, catalog

    out = dict(GENERIC_EFFECT_IDS)
    try:
        db = catalog().db
        for name in dict.fromkeys((card_name, card_name.rstrip("+") + "+", card_name.rstrip("+"))):
            row = db.execute("SELECT produceCardCustomizeIds FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
            for cid in json.loads((row[0] if row else None) or "[]"):
                for (grow_ids,) in db.execute("SELECT produceCardGrowEffectIds FROM ProduceCardCustomize WHERE id = ?", (cid,)).fetchall():
                    for gid in json.loads(grow_ids or "[]"):
                        grow = db.execute("SELECT effectType, playProduceExamEffectId, targetPlayProduceExamEffectIds "
                                          "FROM ProduceCardGrowEffect WHERE id = ?", (gid,)).fetchone()
                        if grow is None or grow[0] != "ProduceCardGrowEffectType_EffectAdd":
                            continue
                        for eid in [grow[1], *json.loads(grow[2] or "[]")]:
                            effect = db.execute("SELECT produceDescriptions FROM ProduceExamEffect WHERE id = ?", (eid,)).fetchone() if eid else None
                            if effect is not None and (text := norm(description_text(effect[0]))):
                                out.setdefault(text, eid)
    except (CatalogUnavailable, sqlite3.Error, ValueError, TypeError):
        pass
    return out


def remap_hand(hand, names_by_base, customs, cards=None, copies=None, uncertain=None):
    """把手牌名对到审核过的牌组账本（只处理去「+」后基名在牌组里唯一的牌；同基名有多个版本时不猜，原样留着）。
    - 画面比账本少「+」（OCR 漏了强化角标，1-f R2 トレーニングの成果）：按卡 id 和账本那一级查主数据整张重建——
      名字、强化等级、文字、标签（追加行动等）、费用都换成那一级，保留物理槽位、可用性和预计得分徽章
      （原来只换名字和文字，tags.extra_actions 还是旧版本的）；
    - 画面比账本多「+」（考试中临时强化）：只把名字对回账本那一张（牌库记账按名字），文字 / 标签 / 费用 / 强化等级
      保持画面那一级——游戏按强化后的效果结算，不降级；
    - 主数据查不到时退回只换名字（同原来的行为），免得账本对不上手牌。
    重建后再补客制化（1-g R2：インフルエンサー 换成主数据牌面后「強気追加」没了），已含的不重复加。"""
    from hiro.brain.cards import card_view, kaa_cards

    cards = kaa_cards() if cards is None else cards
    out = []
    for card in hand:
        name = norm(card.name)
        base = name.rstrip("+")
        variants = names_by_base.get(base, ())
        if len(variants) != 1 or name == variants[0] or not card.card_id:
            out.append(card)
            continue
        target = variants[0]
        upgrade = len(target) - len(base)
        if upgrade > max(card.upgrade or 0, len(name) - len(base)):
            master = cards.get((card.card_id, upgrade))
            if master is not None and norm(master.name or "") == target and master.upgrade_count == upgrade:
                rebuilt = card_view(card.slot, master, available=card.available)
                out.append(replace(rebuilt, badge_score=card.badge_score))
                continue
        out.append(replace(card, name=target))
    return apply_customs(out, customs, copies=copies, uncertain=uncertain) if customs else out


def read_deck(source):
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    return validate_deck(data)


def apply_deck_change(base, change):
    cards = [norm(c) for c in base["cards"]]
    once = {norm(c) for c in base.get("once", [])}
    if not isinstance(change, dict) or not isinstance(change.get("evidence"), str) or not change["evidence"].strip():
        raise ValueError("本局变卡记录须提供证据")
    removed, added = change.get("remove"), change.get("add")
    if removed is not None or added is not None:
        if not all(isinstance(c, str) and c.strip() for c in (removed, added)):
            raise ValueError("变卡须同时记录换出和换入")
        removed, added = norm(removed), norm(added)
        if removed not in cards or not isinstance(change.get("add_once"), bool):
            raise ValueError("换出牌须在基准牌组中，并须记录换入牌是否一次性")
        cards.remove(removed)
        if removed not in cards:
            once.discard(removed)
        if added in cards and (added in once) != change["add_once"]:
            raise ValueError("同名卡的一次性状态有歧义，请提交该轮完整清单")
        cards.append(added)
        if change["add_once"]:
            once.add(added)
    deck = {**base, "cards": cards, "once": sorted(once),
            "evidence": f"{base['evidence']} | {change['evidence']}"}
    if "rebound_repeatable" in change:
        deck["rebound_repeatable"] = change["rebound_repeatable"]
    validate_deck(deck)
    return deck


def strict_shuffle():
    """HIRO_VEIL_STRICT_SHUFFLE=1：牌堆记账按洗牌规则收紧（默认关）。原来只看「抽到牌堆里没有的牌、抽牌数大于牌堆剩余」就当洗牌，
    牌堆没抽完也认；打开后牌堆没抽完就不认，整副记成位置不明（循环判断照样能用，自检 trace_trust 判这步不可信）。"""
    return os.environ.get("HIRO_VEIL_STRICT_SHUFFLE") == "1"


def _base(name):
    return norm(name).rstrip("+")


def name_probe():
    """HIRO_VEIL_NAME_PROBE（默认开，=0 关）：同名不同强化的牌读数和账本对不上、账本也定不下是哪个版本时，点按一次
    （只选中、不打出），读浮起来的全名再定；读不出按「+」个数最接近的版本（使用者 2026-11-08：只作完全无法确定时的兜底）。"""
    return os.environ.get("HIRO_VEIL_NAME_PROBE", "1") != "0"


def name_from_probe(text, family):
    """点按读到的文字里认版本：按「+」多的先比（トレーニングの成果++ 先于 トレーニングの成果+），名字后面不能再跟「+」。"""
    text = norm(text or "")
    for name in sorted(family, key=lambda n: -n.count("+")):
        if re.search(re.escape(name) + r"(?!\+)", text):
            return name
    return None


def fix_plus_misread(hand, data, turn, deck, probe=None):
    """同名不同强化的牌（トレーニングの成果 / トレーニングの成果+）卡 ID 一样，只靠名字后面「+」的个数分，画面常读错个数
    （codex-20261108-1-b：R1 T3 把 + 读成基础版、R2 T7 读成 ++，账本判「抽到牌堆里没有的牌」，整场位置不明、进不了循环）。
    手里某个名字比它能来的地方多（或账本里根本没有这个名字），而同名的另一个版本那里还有富余，就按那个版本记。
    能来的地方：同回合 = 上一帧手牌 + 山札；换回合 = 山札（不够抽才算上捨札和上回合手牌）；位置不明的都算。
    候选不止一个时取「+」个数最接近的。只改记账用的名字，改了的记 veil_name_fix_log。"""
    previous = data.get("veil_piles")
    if previous is None:
        return hand
    siblings = {}
    for name in deck:
        siblings.setdefault(_base(name), []).append(name)
    if not any(len(siblings.get(_base(c.name), [])) > 1 or norm(c.name) not in deck for c in hand if c.card_id):
        return hand
    old_hand = Counter(c for c in previous["hand"] if c)
    piles = {k: Counter(previous.get(k) or {}) for k in ("deck", "grave", "unlocated")}
    sources = piles["deck"] + piles["unlocated"] + Counter(previous.get("born") or {})
    if turn == previous["turn"]:
        sources += old_hand
        if previous.get("swap"):
            sources += piles["grave"]
    elif piles["deck"].total() < len([c for c in hand if c.card_id]):
        sources += piles["grave"] + old_hand
    seen = Counter(norm(c.name) for c in hand if c.card_id)
    spare = sources - seen
    out = []
    for card in hand:
        name = norm(card.name) if card.card_id else None
        family = siblings.get(_base(card.name), []) if name else []
        wrong = name and (seen[name] > sources[name] or name not in deck) and len(family) >= (1 if name not in deck else 2)
        if wrong:
            options = [n for n in family if n != name and spare[n] > 0]
            pick = None
            # 点按一次约 2–3 秒（选中、等浮起、整屏 OCR、取消选中）：只在账本也定不下来时用（使用者 2026-11-08）——
            # 读数对不上、而同名能补上的版本不是正好一个（没有 / 好几个）。读数对得上就信读数，账本能唯一定下就按账本
            if probe is not None and len(options) != 1:
                cache = data.setdefault("veil_name_probe", {})
                if cache.get("turn") != turn:
                    cache.clear()
                    cache["turn"] = turn
                key = f"{card.slot}:{name}"
                if key not in cache:
                    text = probe(card)
                    cache[key] = name_from_probe(text, family)
                    data.setdefault("veil_name_probe_log", []).append({"turn": turn, "slot": card.slot, "read": name,
                                                                        "probe": text, "as": cache[key]})
                pick = cache[key]
                if pick == name:
                    out.append(card)
                    continue
            if pick is None and options:
                pick = min(options, key=lambda n: abs(n.count("+") - name.count("+")))
            if pick is not None and pick != name:
                seen[name] -= 1
                seen[pick] += 1
                spare[pick] -= 1
                data.setdefault("veil_name_fix_log", []).append({"turn": turn, "slot": card.slot, "read": name, "as": pick})
                card = replace(card, name=pick)
        out.append(card)
    return out


def track_piles(data, turn, slots, outside):
    """Reconcile known piles against the observed hand without reading pile UI."""
    previous = data.get("veil_piles")
    deck, grave, unknown = Counter(), Counter(), Counter()
    if previous is None:
        (deck if turn == 1 and not data.get("veil_legacy_piles") else unknown).update(outside)
    else:
        deck, grave, unknown = (Counter(previous[k]) for k in ("deck", "grave", "unlocated"))
        # 上一手生成的眠気（_commit 记在 born）：牌的效果是先抽牌、再把眠気生成进山札（フルスロットル+ 等，主数据文字顺序），
        # 同一回合这一帧的抽牌不会抽到它，抽完（含洗牌）再并进山札；跨回合时它早已在山札里
        # （codex-20261108-3 第 3 局 R1 T4：フルスロットル+ 抽 3 张时山札只剩 2 张，洗牌后抽到 ティーパーティ+，
        # 先并进去的眠気让严格洗牌判成「山札没抽完」，整副位置不明）
        born = Counter(previous.get("born") or {})
        old_hand = Counter(c for c in previous["hand"] if c)
        hand = Counter(c for c in slots if c)
        # 换手牌（ガタゴトすやすや+、仕切り直し+「手札をすべて入れ替える」）：原手牌整手先进捨札，再抽同样张数（仕切り直し+ 再多抽 2），
        # 山札不够照常洗牌，刚丢的原手牌也会被洗进去。不能按名字差算：新手牌里和原手牌同名的那张也是新抽的。
        # codex-20261108-3-c 第 1 局 R1 T4：山札 3 张时 ガタゴト 换 4 张，洗牌抽到刚丢的 フルスロットル+、之后又抽到 忍耐力+。
        # （1e7ac07 曾按 3-b 第 1 局 R2 改成「先抽后丢」，那是因为 T4 早先打的 仕切り直し+ 没被当成换手牌、账本已偏，结论错了）
        if turn != previous["turn"]:
            grave.update(old_hand)
            drawn = hand
            deck += born
            born = Counter()
        elif previous.get("swap"):
            grave.update(old_hand)
            drawn = hand
        else:
            grave.update(old_hand - hand)
            drawn = hand - old_hand
        # 洗牌：抽牌数大于牌堆剩余时，先把牌堆全部抽完，再把弃牌堆洗进牌堆，剩下的按概率抽（使用者 2026-11-07）。
        # 严格模式（HIRO_VEIL_STRICT_SHUFFLE=1）还要求牌堆里的牌都在这次抽到的牌里，否则不认洗牌、下面整副记成位置不明
        if drawn - deck and not unknown and deck.total() < drawn.total() and not (strict_shuffle() and deck - drawn):
            deck.update(grave)
            grave.clear()
        if drawn - deck or deck - drawn + born + grave + unknown != outside:
            deck.clear()
            grave.clear()
            unknown = outside.copy()
        else:
            deck = deck - drawn + born
    data["veil_piles"] = {"turn": turn, "hand": slots, "deck": dict(deck), "grave": dict(grave), "unlocated": dict(unknown)}
    return {"deck": list(deck.elements()), "grave": list(grave.elements()), "unlocated": list(unknown.elements())}


def read_position(state, memory, hand, read_flower, *, calibrate=False):
    """Use the last verified play until a turn boundary or resync."""
    data = memory.strategy_data
    position = data.get("veil_position", {})
    pending = data.get("veil_pending_play")
    new_turn = position.get("turn") != memory.turn
    observed = stance(state)
    observed_level = stance_level(state, observed)
    current = position.get("stance")
    level = position.get("stance_level")
    flower = position.get("flower")
    delayed = position.get("next_stance")
    delayed_level = position.get("next_stance_level")

    if pending:
        if pending["turn"] == memory.turn:
            if hand[pending["card"]] >= pending["count"]:
                return "veil_play_unverified"
            current, flower = pending["stance"], pending["flower"]
            level = pending.get("stance_level")
            if pending["next_stance"]:
                delayed = pending["next_stance"]
                delayed_level = pending.get("next_stance_level")
        else:
            # The next hand may redraw the card. Verify the digit if this
            # unverified play could have changed it.
            if pending["flower"] != flower:
                flower = None
        # Keep the pending record until any required resync also succeeds.
    predicted_flower = flower
    if calibrate or flower is None:
        flower = read_flower()
        if flower is None:
            return "veil_flower_unreadable"
    elif flower_screen() and (seen := read_flower()) is not None and seen != flower:
        # 账本优先（使用者 2026-11-07：任何时候都以账本为准）：画面和账本不一致只记一笔供审计，不改账本。
        # 1-d R2 的偏差是 インフルエンサー+ 客制化「強気追加」没进账本（已由 apply_customs 补上），不是该信画面
        log = data.setdefault("veil_flower_log", [])
        entry = {"turn": memory.turn, "ledger": flower, "screen": seen}
        if not log or log[-1] != entry:
            log.append(entry)
    if new_turn:
        # 画面读不出姿态（回合开头过渡帧）时按账本接着走：有预约的下回合姿态用它，否则沿用上回合结束时的姿态
        # （验证局 1-e R1 第 2 回合开头读不出就停机）。ledger 里本来就有 next_stance，没有时姿态不会自己变
        if pending and pending["next_stance"]:
            current, level = observed, observed_level  # 上一手没核对上、它又预约了下回合姿态：账本不确定，只能看画面
        else:
            scheduled = delayed if not keeps_nonbiri(current, delayed) else None
            predicted = scheduled or current
            predicted_level = level_after(current, level, scheduled, delayed_level) if scheduled else level
            # 一番星：回合开始不在温存 / のんびり 就切のんびり（1-h R1 第 2 回合：账本记強気，画面已是のんびり）
            predicted, star = start_turn(predicted, data.get("veil_star"))
            if star != data.get("veil_star"):
                data["veil_star"] = star
            if observed:
                level = observed_level
            else:
                level = predicted_level if predicted == (scheduled or current) else None
            current = observed or predicted
    elif calibrate:
        # On a resumed cycle a verified hand plus the expected flower digit
        # can corroborate the saved position even if the stance icon is hidden.
        current = observed or (current if flower == predicted_flower else None)
        if observed or current is None:
            level = observed_level
    elif current is None:
        current, level = observed, observed_level
    elif observed == current and observed_level is not None:
        level = observed_level
    if new_turn or calibrate:
        if current is None and position:
            return "veil_stance_unreadable"
        if new_turn:
            delayed = delayed_level = None

    # 一番星剩余次数以状态栏为准（画面读到数字时）：一番星可能不是本场打出的（回忆 / 开场带入），账本没记过、按 0 算，
    # 回合开头不切のんびり，姿态记错（codex-20261108-1 第 1 局 R1 attempt3 T6：画面のんびり、一番星 2→1，账本強気，进循环晚一回合）。
    # 回合开头那帧状态栏往往读不出：先按账本推（start_turn），之后读到的数字已是触发后的，直接覆盖
    seen_star = next((s.get("value") for s in state.statuses or [] if norm(s.get("name") or "").startswith(STAR)
                      and isinstance(s.get("value"), int)), None)
    if seen_star is not None and seen_star != data.get("veil_star"):
        data["veil_star"] = seen_star
    count_stance_change(data, position.get("stance"), current)
    data["veil_position"] = {"turn": memory.turn, "stance": current, "stance_level": level,
                             "flower": flower, "next_stance": delayed, "next_stance_level": delayed_level}
    data.pop("veil_pending_play", None)
    state.pitem_charges = flower
    state.statuses = [s for s in state.statuses or [] if norm(s.get("name") or "") not in STANCES]
    if current is not None:
        state.statuses.append({"name": current, "value": level})
    if observed is not None and current is not None and observed != current:
        # VEIL_STANCE_SCREEN：账本姿态仍是决策依据，画面读到别的姿态时记下来，体力估算取掉血多的（veil.hp_loss）
        from hiro.brain.veil import SCREEN_STANCE

        state.statuses.append({"name": SCREEN_STANCE, "value": observed, "level": observed_level})
        log = data.setdefault("veil_stance_screen_log", [])
        entry = {"turn": memory.turn, "ledger": current, "screen": observed}
        if not log or log[-1] != entry:
            log.append(entry)
    if delayed:
        state.statuses.append({"name": f"次のターン開始時、{delayed}に変更"})
    return None


ROUND_MISMATCH = 5  # 画面轮次（NPC 分数推）连续这么多帧不符才停
UNDO_AFTER = 2  # 同一帧序列里连续读到「上一手没落地」这么多次，就按出牌前的记忆撤销那一手


def undo_unlanded_play(memory, hand) -> str | None:
    """上一手记了账但手里同名牌没少（点击没落地 / 落地前进程退出）：把记忆恢复到出牌前，返回撤销的牌名。
    只在当前手牌与出牌前手牌完全相同时撤销（没有别的牌被打出或抽到，确定这一手没发生）；
    否则返回 None，保持原来的停机交代理。验证局 codex-20261107-1-b R2 剩 8 回合タフネス+ 就是这样停的。"""
    from hiro.brain.state import ExamMemory

    pending = memory.strategy_data.get("veil_pending_play") or {}
    undo, before = pending.get("undo"), pending.get("hand")
    if undo is None or before is None or pending.get("turn") != memory.turn:
        return None
    base = lambda names: Counter(n.rstrip("+") for n in names)  # noqa: E731 —— 回合中能力会给牌加「+」
    now, was = base(hand.elements()), base(before)
    gone = was - now
    if now - was or sum(gone.values()) > 1:
        return None
    restored = ExamMemory.from_dict(undo)
    restored.last_remaining = memory.last_remaining
    restored.last_score = memory.last_score
    memory.__dict__.update(restored.__dict__)
    memory.plan = []
    memory.expected_hand = None
    memory.strategy_data.setdefault("veil_undo_log", []).append({"turn": memory.turn, "card": pending.get("card"),
                                                                 "actually": sorted(gone) or None})
    if gone:
        # 点错了牌：记的那张还在手里，另一张不见了。撤销记的那一手，实际打出的牌没记账 → 牌库账本标「不知道」
        mark_uncertain(memory.strategy_data, memory, f"出牌点错：记的 {pending.get('card')} 还在手里，实际打出 {sorted(gone)[0]}")
    return pending.get("card")


def init_abilities(data, img, recorded, started=False):
    """一场考试开局：状态栏读回忆能力（最多 4 项）；读不全时用回忆栏记下的。结果放进考试记忆 veil_abilities（每项 left 次数）。
    started：这一轮已经打过牌（续跑旧记忆）。用过的能力图标会从状态栏消失，这时以画面为准，不按回忆栏把用过的补回来。"""
    from hiro.brain.memory_ability import fresh

    screen = []
    try:
        from hiro.vision.status import read_memory_abilities

        screen = read_memory_abilities(img) if img is not None else []
    except Exception:  # noqa: BLE001  识别失败不挡考试，退回回忆栏
        screen = []
    names = lambda items: sorted(norm(a.get("card") or "") for a in items)  # noqa: E731
    chosen = screen if started or len(screen) >= len(recorded or []) else recorded
    if screen and recorded and names(screen) != names(recorded):
        data["veil_abilities_mismatch"] = {"screen": names(screen), "recorded": names(recorded)}
    data["veil_abilities"] = fresh([{k: a.get(k) for k in ("card_id", "card", "kind", "count")} for a in chosen or []])
    data["veil_abilities_source"] = "screen" if chosen is screen and screen else "memory"


def apply_ability(data, memory, played):
    """打出 played 后：有对应回忆能力就触发一次（再动 +1 / 抽一张 / 从山札或捨札删一张眠気），次数减一。返回触发的能力。"""
    from hiro.brain.memory_ability import trigger

    hit = trigger(data.get("veil_abilities") or [], played)
    if hit is None:
        return None
    if hit["kind"] in ("draw", "play"):
        memory.actions_left += 1  # 抽到的牌由下一帧手牌与牌堆账（track_piles 同回合 drawn）记
        from hiro.brain import piles as PB

        PB.expect(data.get("veil_piles"), PB.moves_of_ability(hit["kind"]))
    elif hit["kind"] == "sleep":
        deck = data.get("veil_deck") or {}
        if deck.get("眠気", 0) > 0:
            deck["眠気"] -= 1
            piles = data.get("veil_piles")
            if piles is not None and piles.get("born", {}).get("眠気", 0) > 0 and not piles["grave"].get("眠気"):
                piles["born"]["眠気"] -= 1  # 刚生成、还没并进山札的那张也在山札里：捨札没有眠気时删的就是山札的
            elif piles is not None:
                where = [k for k in ("deck", "grave") if piles[k].get("眠気", 0) > 0]
                if len(where) == 1:
                    piles[where[0]]["眠気"] -= 1
                elif len(where) == 2:  # 山札、捨札都有：随机删一张，不知道哪边 → 两边的眠気都记成位置未知
                    total = piles["deck"].pop("眠気") + piles["grave"].pop("眠気") - 1
                    if total:
                        piles["unlocated"]["眠気"] = piles["unlocated"].get("眠気", 0) + total
                elif piles["unlocated"].get("眠気", 0) > 0:
                    piles["unlocated"]["眠気"] -= 1
    data.setdefault("veil_ability_log", []).append({"turn": memory.turn, "card": hit["card"], "kind": hit["kind"], "left": hit["left"]})
    return hit


# ---------------------------------------------------------------- 全力値（账本优先，画面只作参考）
# 画面上一直没识别全力値（8 份 trace 的状态栏 / buffs 里都没有），策略也不看它：清牌把全力値花在 アンダンテ+ / 巻き返し+ 上，
# 循环里 オーバードライブ+ 刚 +2 时画面还没刷新、巻き返し+ 显示灰 → 抛「循环下一张牌不可用」退出（codex-20261107-1 第 1 局 R2 剩 5 回合）。
# 使用者 2026-11-07：「全力値始终没被跟踪，加上他的跟踪」「记账优先识别，而不是识别优先记账」。
#
# 记忆字段：veil_fp（None = 不知道）、veil_fp_turn、veil_fp_boosts（全力値増加量増加 [{pct, until}]）、veil_fp_per_turn
# （以降、ターン開始時、全力値+N）、veil_fp_log、veil_fp_gap（账本作废的原因）、veil_log（[回合, 打/喝, 名字]，不截断的出牌记录）、
# veil_fp_cards（见过的牌 / 饮料的全力値效果，重建时查）、veil_last_commit（最后一次记账后的手牌，续跑时核对 exam 外有没有出牌）。
import math
import re as _re

from hiro.brain.veil import fp_delta, fp_effect  # noqa: F401  fp_delta 供测试 / 旧调用方从这里取

FULL_POWER_AT = 10  # 主数据：ターン終了時に全力値が10以上なら、次のターン開始時に全力値を10消費して、指針を全力に変更
FP_REREAD = 2  # 账本说付得起、画面还是灰：先换帧重读这么多次，再按账本当作可打
HISTORY_CAP = 30  # ExamMemory.history 只留最近 30 条（hiro.brain.state.ExamMemory._log）
_FP_PER_TURN = _re.compile(r"以降、ターン開始時、全力値\+(\d+)")
_HISTORY = _re.compile(r"T(\d+) (打|喝) (.+)")


def _fp_record(obj) -> dict:
    """牌 / 饮料 → 记账用的全力値效果（可存进 JSON）。"""
    eff = fp_effect(obj)
    per_turn = _FP_PER_TURN.search(norm(getattr(obj, "text", "") or ""))
    return {"gain": eff["gain"], "cost": eff["cost"], "boost": list(eff["boost"]) if eff["boost"] else None,
            "per_turn": int(per_turn[1]) if per_turn else 0}


def _fp_relevant(rec) -> bool:
    return rec is None or bool(rec["gain"] or rec["cost"] or rec["boost"] or rec["per_turn"])


def _master_fp(kind, name):
    """按名字查主数据牌面（ProduceCard 以名字为主键，名字带强化「+」）；查不到 / 没有主数据 → None。"""
    try:
        from hiro.brain.cards import description_text, select

        table = "ProduceCard" if kind == "打" else "ProduceDrink"
        row = select(f"SELECT produceDescriptions FROM {table} WHERE name = ?;", name)
    except Exception:  # noqa: BLE001  主数据不可用：当作查不到
        return None
    if not row:
        return None
    from types import SimpleNamespace

    return _fp_record(SimpleNamespace(text=description_text(row[0])))


def _fp_of(data, kind, name):
    """先查本场见过的牌面（veil_fp_cards），再查主数据。"""
    rec = (data.get("veil_fp_cards") or {}).get(f"{kind} {name}")
    return rec if rec is not None else _master_fp(kind, name)


def note_fp_cards(data, state) -> None:
    """每帧把手牌 / 饮料的全力値效果记下来（重建和续跑核对时按名字查，不依赖主数据）。"""
    cards = data.setdefault("veil_fp_cards", {})
    for kind, items in (("打", state.hand), ("喝", state.drinks)):
        for x in items:
            if getattr(x, "card_id", True) and getattr(x, "text", None):
                cards.setdefault(f"{kind} {norm(x.name)}", _fp_record(x))


def _fp_apply(fp, boosts, turn, rec):
    """一次出牌 / 喝饮料：先扣消費，再加获得（获得吃本回合生效的全力値増加量増加），最后挂上这张给的増加量増加。
    増加量増加的取整方式未在主数据里核实，按向上取整（宁高勿低会让账本偏乐观，所以 log 里记 pct 供实机核对）。"""
    pct = sum(b["pct"] for b in boosts if b["until"] >= turn)
    gain = math.ceil(rec["gain"] * (100 + pct) / 100) if rec["gain"] and pct else rec["gain"]
    fp = max(0, fp - rec["cost"]) + gain
    if rec.get("boost"):
        boosts = [*boosts, {"pct": rec["boost"][0], "until": turn + rec["boost"][1] - 1}]  # （Nターン）含本回合
    return fp, boosts, gain, pct


def _fp_next_turn(fp, boosts, per_turn, turn):
    """进入 turn：上回合结束 ≥10 → 扣 10（进全力）；再加「以降、ターン開始時、全力値+N」；去掉过期的増加量増加。"""
    ops = []
    if fp >= FULL_POWER_AT:
        fp -= FULL_POWER_AT
        ops.append("全力")
    if per_turn:
        fp += per_turn
        ops.append(f"开局+{per_turn}")
    return fp, [b for b in boosts if b["until"] >= turn], ops


def history_plays(memory):
    """ExamMemory.history → [(回合, 打/喝, 名字)]。history 只留最近 30 条：第一条是 T1、且不满 30 条才算完整，否则 None。"""
    hist = list(memory.history)
    if not hist or len(hist) >= HISTORY_CAP or not hist[0].startswith("T1 "):
        return None
    return [(int(m[1]), m[2], m[3]) for h in hist if (m := _HISTORY.fullmatch(h))]


def rebuild_full_power(plays, upto_turn, effect_of):
    """按出牌记录从 0 重算全力値。effect_of(kind, name) → 记账效果；有一张查不到就返回 (None, 那张的名字)。
    → ((fp, boosts, per_turn), None) 或 (None, 缺的名字)。"""
    fp, boosts, per_turn, turn = 0, [], 0, 1
    for t, kind, name in [*plays, (upto_turn, None, None)]:
        while turn < t:
            turn += 1
            fp, boosts, _ = _fp_next_turn(fp, boosts, per_turn, turn)
        if kind is None:
            break
        rec = effect_of(kind, name)
        if rec is None:
            return None, name
        fp, boosts, _, _ = _fp_apply(fp, boosts, t, rec)
        per_turn += rec.get("per_turn") or 0
    return (fp, boosts, per_turn), None


def track_full_power(data, memory) -> int | None:
    """每帧开头：建账 / 跨回合结算。新考试从 0 记；续跑旧记忆没有这本账时按出牌记录重建（veil_log，没有就用 history，
    history 被截断 / 有牌查不到牌面时记 None = 不知道，不拿它推翻画面）。跨回合：上回合结束时 ≥10 → 本回合开始扣 10（进全力）。"""
    log = data.setdefault("veil_fp_log", [])
    if "veil_fp" not in data:
        fp, boosts, per_turn = 0, [], 0
        if memory.history or memory.this_turn or memory.turn > 1:
            source, plays = ("veil_log", [tuple(x) for x in data["veil_log"]]) if "veil_log" in data else ("history", history_plays(memory))
            got, missing = rebuild_full_power(plays, memory.turn, lambda k, n: _fp_of(data, k, n)) if plays is not None else (None, None)
            if got is not None:
                fp, boosts, per_turn = got
                log.append({"turn": memory.turn, "op": "按记录重建", "source": source, "plays": len(plays), "fp": fp})
            else:
                fp = None
                why = f"牌面查不到：{missing}" if missing else "history 不完整（只留最近 30 条，或不从 T1 开始）"
                data["veil_fp_gap"] = why
                log.append({"turn": memory.turn, "op": "不知道", "why": why})
        data.update(veil_fp=fp, veil_fp_turn=memory.turn, veil_fp_boosts=boosts, veil_fp_per_turn=per_turn)
    fp = data["veil_fp"]
    while (data.get("veil_fp_turn") or memory.turn) < memory.turn:
        data["veil_fp_turn"] += 1
        if fp is not None:
            fp, data["veil_fp_boosts"], ops = _fp_next_turn(fp, data.get("veil_fp_boosts") or [], data.get("veil_fp_per_turn") or 0,
                                                             data["veil_fp_turn"])
            if ops:
                log.append({"turn": data["veil_fp_turn"], "op": "、".join(ops), "fp": fp})
    data["veil_fp"], data["veil_fp_turn"] = fp, memory.turn
    return fp


def commit_full_power(data, memory, card, kind="打") -> None:
    """打出一张牌 / 喝一瓶饮料后：记进不截断的出牌记录（veil_log），再按牌面记全力値（先扣消費、再加获得）。"""
    name = norm(card.name)
    data.setdefault("veil_log", []).append([memory.turn, kind, name])
    rec = _fp_record(card)
    data.setdefault("veil_fp_cards", {}).setdefault(f"{kind} {name}", rec)
    data["veil_fp_per_turn"] = (data.get("veil_fp_per_turn") or 0) + rec["per_turn"]
    fp = data.get("veil_fp")
    if fp is None or not _fp_relevant(rec):
        return
    data["veil_fp"], data["veil_fp_boosts"], gain, pct = _fp_apply(fp, data.get("veil_fp_boosts") or [], memory.turn, rec)
    entry = {"turn": memory.turn, "card": name, "gain": gain, "cost": rec["cost"], "fp": data["veil_fp"]}
    if pct:
        entry["boost_pct"] = pct
    if rec["boost"]:
        entry["boost_added"] = rec["boost"]
    data.setdefault("veil_fp_log", []).append(entry)


def note_commit(data, memory, state, decision) -> None:
    """每次记账后记下「此刻应在手里的牌」和剩余回合：下一次 exam 启动时用它核对 exam 外有没有人出过牌（check_outside_plays）。"""
    hand = [norm(c.name) for c in state.hand if c.card_id and not (decision.kind == "play" and c.slot == decision.slot)]
    played = next((c for c in state.hand if decision.kind == "play" and c.slot == decision.slot), None)
    swap = played is not None and swaps_hand(played)
    if swap and hand:
        data["veil_swap_wait"] = {"turn": memory.turn, "before": sorted(hand), "card": norm(played.name)}
    data["veil_last_commit"] = {"turn": memory.turn, "remaining": state.remaining_turns, "kind": decision.kind,
                                "hand": None if swap else hand,
                                "ended": decision.kind == "skip" or memory.actions_left <= 0}


SWAP_WAIT = 12  # 换手牌（手札をすべて入れ替える）后最多等这么多帧手牌变化，等不到就记一笔照常走
SWAP_FLOAT = 3  # 换手牌本身还在手里时只等这么多帧（多半没打出去）


def swap_pending(data, memory, hand) -> bool:
    """上一手是换手牌（ガタゴトすやすや+「手札をすべて入れ替える」），画面手牌还是出牌前剩下的那几张 → 换手动画没播完，
    这一帧不能决策。验证局 1-e R2 剩 8：打 ガタゴト 后在旧手牌帧上决定打 達成感+（槽 2），点下去时手牌已换，
    实际打出的是 トレーニングの成果++（全力値 +5、回忆能力演出），账本记成 達成感+，之后全力値账本一路少 5。"""
    wait = data.get("veil_swap_wait")
    if not wait:
        return False
    if wait["turn"] != memory.turn:
        data.pop("veil_swap_wait", None)
        return False
    base = lambda names: Counter(n.rstrip("+") for n in names)  # noqa: E731
    now = base(hand.elements())
    card = (wait.get("card") or "").rstrip("+")
    still = now.pop(card, None)  # 打出去的那张还飘在手牌位置上时也算旧手牌
    if now == base(wait["before"]):
        if still:
            # 换手牌本身一直还在手里：多半是没打出去（点了没落地），不是动画没播完。只等 SWAP_FLOAT 帧，
            # 之后交给「上一手没落地」的核对（read_position → 撤销重来）。codex-20261108-1-b R1 T9：仕切り直し+ 没落地，
            # 按换手动画等满 SWAP_WAIT 帧，白等约 40 秒
            wait["float"] = wait.get("float", 0) + 1
            if wait["float"] > SWAP_FLOAT:
                data.setdefault("veil_swap_log", []).append({"turn": memory.turn, "op": "换手牌还在手里，按没落地处理",
                                                            "card": wait.get("card")})
                data.pop("veil_swap_wait", None)
                return False
        return True
    data.pop("veil_swap_wait", None)
    return False


def check_outside_plays(data, memory, state, before_remaining) -> str | None:
    """exam 启动后的第一帧：画面和上次记账对不上 → exam 外有人出过牌（代理手动），全力値账本作废（记 None + 原因）。
    同一回合：上次记账后应在手里、现在不在的牌里有动全力値的 → 作废（只动元気 / 体力的牌不影响全力値，只记一笔）；
    过了 1 回合：上次不是跳过 / 次数用完结束的，且当时手里有动全力値的牌 → 作废；过了 2 回合以上 → 作废。"""
    last = data.get("veil_last_commit")
    now = state.remaining_turns
    if not last or now is None or last.get("remaining") is None or before_remaining is None:
        return None
    then = last["remaining"]
    screen = Counter(norm(c.name) for c in state.hand if c.card_id)
    risky = lambda names: sorted(n for n in names if _fp_relevant(_fp_of(data, "打", n)))  # noqa: E731
    why = None
    if now == then and last.get("hand") is not None:
        missing = Counter(last["hand"]) - screen
        if missing:
            data.setdefault("veil_fp_log", []).append({"turn": memory.turn, "op": "exam 外少了手牌", "cards": sorted(missing.elements())})
            if bad := risky(missing.elements()):
                why = f"同一回合 exam 外打掉了动全力値的牌：{'、'.join(bad)}"
    elif then - now == 1 and not last.get("ended"):
        if bad := risky(last.get("hand") or []):
            why = f"上回合（剩 {then}）没跳过就离开 exam，手里有动全力値的牌：{'、'.join(bad)}"
        elif last.get("hand") is None:
            why = f"上回合（剩 {then}）换过手牌后离开 exam，不知道 exam 外打了什么"
    elif then - now >= 2:
        why = f"剩余回合 {then}→{now}：exam 外过了 {then - now} 回合"
    if why is None:
        return None
    if data.get("veil_fp") is not None:
        data["veil_fp"] = None
        data["veil_fp_gap"] = why
        data.setdefault("veil_fp_log", []).append({"turn": memory.turn, "op": "不知道", "why": why})
    return why


def distrust_fp_after_undo(data, memory, undone) -> bool:
    """这回合按账本解灰的牌没打出去、被撤销：说明全力値账本错了（画面的灰是对的）。账本作废（记 None），
    下一帧按画面补账，不再解灰 → 不会「解灰→打不出→撤销→再解灰」循环（codex-20261108-3-b 第 1 局 R1 attempt2 T8 卡 2.5 分钟）。"""
    log = data.get("veil_fp_log") or []
    unlocked = next((e for e in reversed(log) if e.get("op") == "按账本解灰"), None)
    if not unlocked or unlocked.get("turn") != memory.turn or norm(undone) not in unlocked.get("cards", []):
        return False
    if data.get("veil_fp") is not None:
        why = f"按账本解灰的 {norm(undone)} 没打出去：账本全力値 {data['veil_fp']} 不可信，改用画面"
        data["veil_fp"] = None
        data["veil_fp_gap"] = why
        log.append({"turn": memory.turn, "op": "不知道", "why": why})
        data["veil_fp_log"] = log
    return True


def fp_grey_by_stale_frame(state, fp) -> list:
    """画面发灰、但账本说全力値付得起的牌：只看「打这张只要全力値」的牌（体力 / 元気 消耗为 0，没有「〜の場合、使用可」条件）。"""
    if fp is None:
        return []
    out = []
    for c in state.hand:
        gain, cost = fp_delta(c)
        text = norm(c.text or "")
        if (not c.available and c.card_id and cost and fp >= cost and not (c.cost or c.hp_cost)
                and "使用可" not in text and not c.tags.is_sleep):
            out.append(c)
    return out


STANCE_REREAD = 3  # 姿态条件按账本已满足、画面还是灰：换帧重读这么多次（出牌动画中途的帧），仍灰就照画面


def stance_grey_by_stale_frame(state) -> list:
    """画面发灰、但唯一的使用条件是姿态、账本姿态已满足的牌（「強気の場合、使用可」等）。
    1-h R1 剩 7：教えてあげる+ 切強気的动画中途截帧，一心不乱+ 还是温存下的灰，按「只剩复用牌」Skip 掉了。
    只认纯姿态条件：计数条件（〜回以上）、回合条件、没有条件的发灰（全力値 / 体力 / 元気 付不起）不算；姿态不明不算。"""
    current = stance(state)
    if current is None:
        return []
    out = []
    for c in state.hand:
        if c.available or not c.card_id:
            continue
        cond = play_condition(c)
        if cond and cond["kind"] == "stance" and condition_met(cond, state, None, {}, current) is True:
            out.append(c)
    return out


# ---------------------------------------------------------------- 牌库账本不确定（veil_uncertain）：原因、自愈、核对后清除
# 置真的地方只有 VeilBrain._commit：①打出的一次性牌不在牌组账本里；②打出会生成 / 变换牌的牌（眠気生成牌除外，单独记账）。
# 置真后 read() 每帧都跳过牌堆记账，手里同时有四张核心或已进循环就停；以前没有任何地方清它，只能代理手改记忆
# （codex-20261107-1 第 1 局 R2：T3 手里出现牌组外的 立ち回りの基本，T5 打出后置真，剩 5 回合进循环时停机）。
# 现在：①的常见来源（牌组外的一次性牌出现在手里）在看到时就收进账本（adopt_added_cards），打出时正常扣掉，不再置真；
# 其余情况记下原因（veil_uncertain_why），停机提示里写原因和清除命令；清除要给出「所持スキルカード」的张数，和账本对上才清。


def turn_carry():
    """HIRO_TURN_CARRY=1：状态栏、名次 / 分数、P 道具、属性只在回合开头读一次，回合内沿用（见 hiro.vision.hif.TURN_CARRY）；
    回合内姿态全靠账本。使用者 2026-11-08：识别每帧约省 1.2s。默认关。"""
    return os.environ.get("HIRO_TURN_CARRY", "0") == "1"


def flower_screen():
    """HIRO_VEIL_FLOWER_SCREEN=0 关掉「夜空计数和画面对账」（默认开：只记差异，不改账本）。"""
    return os.environ.get("HIRO_VEIL_FLOWER_SCREEN", "1") != "0"


def fp_screen():
    """HIRO_VEIL_FP_SCREEN=0 关掉「全力値和画面对账」（默认开：账本知道时只记差异，账本 None 时用画面补）。"""
    return os.environ.get("HIRO_VEIL_FP_SCREEN", "1") != "0"


def adopt_added():
    """HIRO_VEIL_ADOPT_ADDED=0 关掉「牌组外的一次性牌收进账本」（默认开：自愈，不改出牌规则）。"""
    return os.environ.get("HIRO_VEIL_ADOPT_ADDED", "1") != "0"


def mark_uncertain(data, memory, why) -> None:
    data["veil_uncertain"] = True
    data.setdefault("veil_uncertain_why", []).append({"turn": memory.turn, "why": why})


# H.I.F 本战 R2 的 P 道具「H.I.F応援棒」（主数据 pitem_03-3-270 / 271）：試験開始時、山札が 22 枚になるように、
# 名字带「基本」的随机技能卡生成到山札的随机位置（codex-20261108-3 第 1 局 R2：21 张牌组多出一张 魅せ方の基本，
# 账本不认识，T3 被 全身全霊+ 抽到后整副记成位置不明，trace 不可信）。名字开考前不知道，先记成占位「基本?」。
HIF_FILL_TO = 22
BASIC_SLOT = "基本?"


def hif_fill():
    """HIRO_VEIL_HIF_FILL=1：本战 R2 开考时按 H.I.F応援棒 把牌组账本补到 22 张（占位「基本?」，抽到时改成真名）。默认关。"""
    return os.environ.get("HIRO_VEIL_HIF_FILL") == "1"


def fill_count(round_no, deck) -> int:
    """这一轮开考时 H.I.F応援棒 生成几张「基本」牌：只有 R2，且开关打开。"""
    return max(0, HIF_FILL_TO - sum(deck.values())) if round_no == 2 and hif_fill() else 0


def resolve_basic(data, hand) -> dict:
    """手里出现账本外、名字带「基本」的牌，而账本还有占位「基本?」：就是应援棒生成的那张，占位改成真名（牌堆位置跟着改）。
    返回 {真名: 张数}；没有可改的返回空。"""
    deck = data["veil_deck"]
    resolved = {}
    for name, count in (hand - Counter({k: v for k, v in deck.items() if v > 0})).items():
        if "基本" not in name:
            continue
        n = min(count, deck.get(BASIC_SLOT, 0))
        if n <= 0:
            continue
        deck[BASIC_SLOT] -= n
        deck[name] = deck.get(name, 0) + n
        piles = data.get("veil_piles")
        if piles is not None:
            left = n
            for where in ("deck", "grave", "unlocated"):
                take = min(left, piles[where].get(BASIC_SLOT, 0))
                if take:
                    piles[where][BASIC_SLOT] -= take
                    piles[where][name] = piles[where].get(name, 0) + take
                    left -= take
        resolved[name] = n
    if resolved:
        data.setdefault("veil_fill_log", []).append({"cards": resolved})
    return resolved


def adopt_added_cards(data, memory, state, hand, deck):
    """手里出现牌组账本外的牌（事件 / P 道具 / 效果加进来的）：都是一次性牌（打出即除外）时收进账本——它此刻就在手里，
    数量确定；打出后照常从账本扣掉，牌组其余部分不受影响。有常驻牌（会回到捨札、循环里一直在）时不收，维持原来的暂停。"""
    extra = hand - deck
    by_name = {norm(c.name): c for c in state.hand if c.card_id}
    if not all(n in by_name and (by_name[n].tags.once or by_name[n].tags.is_sleep) for n in extra):
        return deck
    renamed = {}
    for name, count in list(extra.items()):
        # 同一张牌在考试里被强化（支援卡额外强化，「X+」→「X++」）：账本里有同基名、此刻不在手里的那张，就是它，改名不加张。
        # 验证局 1-f R2：ティーパーティ+ T1 收进账本，T4 变成 ティーパーティ++ 又收一张，牌组多一张幽灵牌（位置未知），
        # 五卡循环一直判不成小牌组，オーバードライブ 不打、全力値 0 死循环
        base = name.rstrip("+")
        old = next((n for n, k in data["veil_deck"].items() if k > 0 and n != name and n.rstrip("+") == base
                    and hand.get(n, 0) < k), None)
        if old is None:
            continue
        data["veil_deck"][old] -= 1
        data["veil_deck"][name] = data["veil_deck"].get(name, 0) + 1
        piles = data.get("veil_piles")
        if piles is not None:
            for where in ("deck", "grave", "unlocated"):
                if piles[where].get(old, 0) > 0:
                    piles[where][old] -= 1  # 现在它在手里：从原来的位置拿掉
                    break
        renamed[old] = name
        extra[name] -= 1
    extra = +extra
    for name, count in extra.items():
        data["veil_deck"][name] = data["veil_deck"].get(name, 0) + count
    entry = {"turn": memory.turn, "cards": dict(extra)}
    if renamed:
        entry["upgraded"] = renamed
    data.setdefault("veil_added", []).append(entry)
    return Counter(data["veil_deck"])


def clear_uncertain(trace_dir, *, hand, deck, grave, evidence, removed=None, hand_cards=None, dry_run=False) -> dict:
    """核对后清除 veil_uncertain。张数取自游戏里「所持スキルカード」（手札 / 山札 / 捨札 / 除外）。
    条件：手札 + 山札 + 捨札 = 账本 veil_deck 的总张数；给了 hand_cards（手札卡名）时还要都在账本里、张数等于 hand。
    对上了才清：先备份 exam_memory.before-clear-<时间>.json，再写 veil_notes / veil_uncertain_cleared（依据、张数、当时的原因），
    牌堆位置记账作废（veil_piles 删掉，下一帧按「位置未知」重建）。对不上抛 ValueError，不改文件。"""
    import json
    import shutil
    import time

    path = Path(trace_dir) / "exam_memory.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("finished"):
        raise ValueError("这份考试记忆已标记结束，不用清")
    data = raw["memory"]["strategy_data"]
    if not evidence or not str(evidence).strip():
        raise ValueError("须写明核对依据（看的是哪一屏 / 哪一帧）")
    ledger = Counter({k: v for k, v in (data.get("veil_deck") or {}).items() if v > 0})
    total = ledger.total()
    if hand + deck + grave != total:
        raise ValueError(f"所持スキルカード 手札{hand}+山札{deck}+捨札{grave}={hand + deck + grave}，账本 veil_deck 共 {total} 张：对不上，不清除")
    if hand_cards is not None:
        names = Counter(norm(c) for c in hand_cards)
        if names.total() != hand or names - ledger:
            raise ValueError(f"手札卡名 {sorted(names.elements())} 与张数 {hand} 或账本不符（账本外：{sorted((names - ledger).elements())}）")
    result = {"was": bool(data.get("veil_uncertain")), "why_was": data.get("veil_uncertain_why"), "ledger_total": total,
              "counts": {"hand": hand, "deck": deck, "grave": grave, "removed": removed}, "evidence": evidence}
    if dry_run:
        return {**result, "dry_run": True}
    backup = path.with_name(f"exam_memory.before-clear-{int(time.time())}.json")
    shutil.copyfile(path, backup)
    data["veil_uncertain"] = False
    data.pop("veil_piles", None)
    data["veil_legacy_piles"] = True
    data.setdefault("veil_uncertain_cleared", []).append({"at": time.time(), **result})
    data.setdefault("veil_notes", []).append(
        f"核对所持スキルカード：手札{hand} 山札{deck} 捨札{grave}" + (f" 除外{removed}" if removed is not None else "")
        + f"，与 veil_deck（{total} 张）一致，清除 veil_uncertain；依据：{evidence}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return {**result, "backup": str(backup)}


def _drink_by_name(name):
    from kaa.db.drink import Drink

    want = norm(name)
    return next((d for d in Drink.all() if norm(d.name) == want), None)


def variant_texts(info, lookup=None) -> list[str]:
    """一个版本的客制化应该在牌面上加的文字（主数据 / 关键词，同 apply_customs）。"""
    if not info.get("customs"):
        return []
    extra = resolve_customs([{"card": info["name"], "item": c} for c in info["customs"]], lookup)
    return list(extra.get(info["name"].rstrip("+"), []))


def match_variant(text, keys, variants, lookup=None) -> str | None:
    """点按一次读到的效果文字 → 是哪个版本：「レッスン中1回」有没有要和一次性对得上，客制化文字要都在。
    只有一个版本对得上才返回它，否则 None（不猜）。"""
    flat = norm(text or "")
    if not flat:
        return None
    hits = [k for k in keys if ("レッスン中1回" in flat) == variants[k]["once"]
            and all(t in flat for t in variant_texts(variants[k], lookup))]
    return hits[0] if len(hits) == 1 else None


PROBE_TEXT_BOX = (0, 560, 720, 1040)  # 选中手牌后效果说明出现的区域（手牌上方）。未实机校准：探测时把 OCR 全文记进 trace
PROBE_SETTLE = 0.4
PROBE_NAME = "卡名:"  # tap_probe 返回文字的第一行：选中后读到的完整卡名（带「+」）
PROBE_NAME_DX = 160  # 卡名标签与点按位置的横向容差
PROBE_NAME_Y = 900  # 卡名标签在这条线以下（手牌区）
PROBE_RETRIES = 2  # 点按读不出 / 分不出时最多再试几帧，之后按第一个可能的版本记、牌库标不确定


# 考试页右下角牌库按钮：图标上压着「保留0/2」角标，模板（选行动页的图标）只配到 0.48（codex-20261109-1-c R1 五次全没打开），
# 认不出时点这个固定位置；点了之后由 is_deck_list 核对一览真的打开
EXAM_DECK_BUTTON = (557, 1205)
PILE_VIEW_LAST: dict = {}  # 上次 read_pile_view 的情况（打开没有、问题），写进 trace


def read_pile_view() -> dict | None:
    """考试页右下角牌库按钮 → 一览切列表显示 → 按「保留 / 手札 / 山札 / 捨札 / 除外」分组读卡名 → 关闭。
    读全（每组张数和标题对上、总张数对上）才返回 {组: [卡名]}；读不全返回 None；一览没关掉就停机（不能在一览上出牌）。"""
    from kotonebot import device

    from hiro.produce import deck_review, deck_text
    from hiro.produce.ops import ocr_boxes

    def swipe(x1, y1, x2, y2, d):
        device.swipe(x1, y1, x2, y2, duration=d)

    def ocr_texts(img):
        return [(t, ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)) for t, _, b in ocr_boxes(img)]

    reader = lambda: deck_text.read_list(shoot=device.screenshot, click=device.click, swipe=swipe,  # noqa: E731
                                         ocr_boxes=ocr_boxes, view_diff=deck_review._view_diff, save=deck_review._save)
    r = deck_review.review_deck(shoot=device.screenshot, click=device.click, swipe=swipe, ocr_texts=ocr_texts,
                                text_reader=reader, button=EXAM_DECK_BUTTON)
    if r.get("opened") and not r.get("closed"):
        raise ResumeBlocked("VEIL 牌库一览打开后没关掉，停止考试操作")
    t = r.get("text") or {}
    PILE_VIEW_LAST.clear()
    PILE_VIEW_LAST.update(opened=r.get("opened"), problems=(r.get("problems") or [])[:5] + (t.get("problems") or [])[:5])
    if not t.get("complete"):
        return None
    return {g: [norm(n) for n in names] for g, names in (t.get("groups") or {}).items()}


def tap_probe(frame, card) -> str | None:
    """点按一次手牌（只选中，不打出），OCR 效果说明，点舞台空白取消选中。返回读到的文字；任何一步不对返回 None。
    已有别的牌在选中态时不点（第二下会把它打出去）；取消选中后还在选中态就再点一次空白，仍在就停机。"""
    from kotonebot import device

    from hiro.exec import exam as X
    from hiro.produce.ops import ocr_boxes

    cards = getattr(frame, "cards", None) or []
    rect = getattr(cards[card.slot], "rect", None) if 0 <= card.slot < len(cards) else None
    if rect is None or X._selects(device.screenshot()):
        return None
    x, y = int(rect.center.x), int(rect.center.y)
    device.click(x, y)
    deadline = time.monotonic() + X.PLAY_CONFIRM_TIMEOUT
    selected = False
    while time.monotonic() < deadline:
        time.sleep(X.PLAY_POLL)
        if X._select_at(device.screenshot(), x):
            selected = True
            break
    text = None
    if selected:
        time.sleep(PROBE_SETTLE)
        boxes = ocr_boxes(device.screenshot())
        x1, y1, x2, y2 = PROBE_TEXT_BOX
        text = "".join(t for t, _, (a, b, c, d) in boxes if x1 <= (a + c) // 2 <= x2 and y1 <= (b + d) // 2 <= y2)
        # 使用者 2026-11-09：「点一次就可以看到完整卡」——选中的牌浮到最前，卡名标签不再被旁边的牌盖住，「+」个数就是档位。
        # 卡名标签放第一行（PROBE_NAME 前缀），level_from_text 先按它定档，读不到再比说明文字
        label = next((t for t, _, (a, b, c, d) in sorted(boxes, key=lambda bx: abs((bx[2][0] + bx[2][2]) // 2 - x))
                      if abs((a + c) // 2 - x) <= PROBE_NAME_DX and (b + d) // 2 >= PROBE_NAME_Y
                      and X._name_match(t, card.name)), None)
        if label:
            text = f"{PROBE_NAME}{label}\n{text}"
    for _ in range(2):
        device.click(*X.DESELECT_TAP)
        time.sleep(X.DESELECT_WAIT)
        if not X._select_at(device.screenshot(), x):
            return text
    raise ResumeBlocked("VEIL 点按读效果后取消不了选中，停止考试操作以免误打出")


class VeilScreen(ScreenExamStateSource):
    def __init__(self, deck, once, ledger_drinks=None, abilities=None, customs=None, variants=None, probe=None, fill=0):
        super().__init__()
        self.fill = fill  # 开考时 H.I.F応援棒 生成的「基本」牌张数（fill_count）
        # 分版本记账（HIRO_VEIL_SIGNATURE=1 且账本里有同名不同版本）：deck / once 的键是记账键（见 instance_keys）
        self.variants = variants or None
        self.keys_by_name = defaultdict(list)
        for key, info in (self.variants or {}).items():
            self.keys_by_name[info["name"]].append(key)
        self.probe = probe or tap_probe
        self.probe_retries = 0
        # 本局客制化（账本 customize 记录）：画面 / 主数据的牌面不含客制化效果，按账本补进手牌文字
        self.customs = list(customs or [])
        self.deck = deck
        self.once = once
        # 批量账本里的饮料栏（1–4 格，开考前）：某格画面读不清（unknown）时按账本补，不因识别失手漏掉饮料
        self.ledger_drinks = list(ledger_drinks or [])
        # 回忆栏记下的回忆能力（开局状态栏读不全时用）；每场考试开局优先按画面读
        self.abilities = list(abilities or [])
        self.data = None
        self.calibrate = True
        self.position_retries = 0
        self.round_mismatch = 0
        self.fp_retries = 0
        self.stance_retries = 0
        self.swap_retries = 0
        self.resume_checked = False  # 本进程第一帧：核对 exam 外有没有出牌、把不确定的原因写进 events.jsonl
        self.rec = None
        self.names_by_base = defaultdict(list)
        self.copies = Counter()  # 去「+」卡名 → 牌组里几张（同名多张的客制化只补共有项）
        self.custom_uncertain = []
        for key in deck:
            name = name_of(key)
            if name not in self.names_by_base[name.rstrip("+")]:
                self.names_by_base[name.rstrip("+")].append(name)
            self.copies[name.rstrip("+")] += deck[key]

    def begin(self, context):
        super().begin(context)
        self.rec = (context or {}).get("rec")

    def _log(self, kind, **fields):
        if self.rec is not None:
            self.rec.log(kind, **fields)

    def trace_context(self, state, memory) -> dict:
        """每次决策写进 events.jsonl（battle_decision.context）和 steps.jsonl 的 notes：决策实际用的姿态 / 夜空计数 / 全力値账本 /
        再动层数 / 牌库是否可信。画面观测里的 pitem_charges 是通用 P 道具角标（VEIL 下恒为 None），夜空计数以这里为准。"""
        data = memory.strategy_data
        position = data.get("veil_position") or {}
        statuses = {norm(s.get("name") or ""): s.get("value") for s in state.statuses or []}
        return {"turn": memory.turn, "remaining": state.remaining_turns, "stance": stance(state),
                "stance_level": statuses.get("温存") if stance(state) in WARM else statuses.get("強気"),
                "next_stance": position.get("next_stance"), "flower": state.pitem_charges,
                "fp": data.get("veil_fp"), "fp_gap": data.get("veil_fp_gap"),
                "fp_boosts": data.get("veil_fp_boosts") or None, "actions_left": memory.actions_left,
                "cycle": data.get("veil_cycle"), "uncertain": bool(data.get("veil_uncertain")),
                "uncertain_why": (data.get("veil_uncertain_why") or [None])[-1],
                "carried": ((getattr(self, "last_raw", None) or {}).get("carried") or {}).get("fields"),  # HIRO_TURN_CARRY：沿用回合开头读数的字段
                **self.trace_board(state, memory)}

    def trace_board(self, state, memory) -> dict:
        """可信 trace 库（使用者 2026-11-07）：决策实际用的牌面。原来 steps.jsonl 只有规整前的手牌（OCR 漏 / 多「+」），
        牌堆 / 弃牌堆 / 除外区只在考试记忆里，回放只能从账本倒推。
        hand：规整 + 客制化后的手牌 [槽位, 名字, 卡 id, 强化, 能打, 一次性]；piles：牌堆 / 弃牌堆 / 位置不明（多重集合，不知道顺序）；
        excluded：开场牌组里已经打出离开的一次性牌；deck：当前牌组账本（含衍生的眠気）；
        undone：本回合撤回过的出牌次数（画面延迟、点错）；flags：VEIL 策略开关的实际取值。"""
        data = memory.strategy_data
        piles = state.piles or {}
        initial, deck = Counter(data.get("veil_initial") or {}), Counter(data.get("veil_deck") or {})
        if data.get("veil_fill"):
            # 应援棒生成的「基本」牌算开场牌组：占位已改成真名的按真名算，打出离开的一次性「基本」牌也进 excluded
            initial[BASIC_SLOT] += data["veil_fill"]
            for entry in data.get("veil_fill_log") or []:
                for name, n in entry["cards"].items():
                    initial[BASIC_SLOT] -= n
                    initial[name] += n
        return {
            "hand": [[c.slot, norm(c.name), c.card_id, c.upgrade, c.available, bool(c.tags.once)] for c in state.hand],
            "drinks": [d.name for d in state.drinks],
            "piles": None if not piles else {k: dict(Counter(norm(n) for n in piles.get(k) or [])) for k in ("deck", "grave", "unlocated", "doubt")},
            "next_draw": piles.get("next_draw"),
            "excluded": dict(initial - deck) or None,
            "deck": dict(deck) or None,
            "played": [a[2:] for a in memory.this_turn if a.startswith("打 ")],
            "undone": sum(1 for u in data.get("veil_undo_log") or [] if u.get("turn") == memory.turn),
            "flags": {name: _flag(name) for name in STRATEGY_FLAGS},
            **({"slot_keys": data.get("veil_slot_keys")} if signature_mode() else {}),
        }

    def _probe(self, frame, card, memory) -> str | None:
        """点按读一张牌，前后各截一帧比手牌（角标位置 / 卡面颜色 / 体力元気）。变了说明这一下把牌打出去了或局面在动
        （codex-20261109-1-c R1 T2：点按读 トレーニングの成果+ 后它从手里没了，没记账，之后一直当幽灵牌在山札里）：
        牌堆层标 need_view，本回合再开一次右下角牌库一览以画面为准重置。"""
        from hiro.exec import exam as X

        try:
            from kotonebot import device

            before = device.screenshot()
        except Exception:  # noqa: BLE001  测试替身没有设备
            return self.probe(frame, card)
        text = self.probe(frame, card)
        try:
            after = device.screenshot()
            a, b = X._lite_sig(before), X._lite_sig(after)
            changed = None if a is None or b is None else (
                "手牌 / 体力变了" if a != b else
                (f"槽 {m} 卡面变了" if (m := X._art_changed(before, after, a[1])) is not None else None))
        except Exception:  # noqa: BLE001
            changed = None
        if changed:
            data = memory.strategy_data
            self._log("veil_probe_changed", turn=memory.turn, card=norm(card.name), slot=card.slot, why=changed)
            piles = data.get("veil_piles")
            if piles is not None:
                piles["need_view"] = True
                data.pop("veil_pile_viewed", None)
        return text

    def probe_boosts(self, state, memory, frame, raw_hand) -> str | None:
        """5 张手牌：被盖住、头像数不出的牌，多一档会改变决策 / 记账时点开读一次说明定档位（使用者 2026-11-09）。
        循环状态（手里有核心 4 张）不读。读到的档位按 (回合, 卡名) 记在考试记忆里，本回合沿用；写回 raw_hand 的 boost。
        点过一张返回 "veil_boost_probe"（换帧重读），没要点的返回 None。"""
        data = memory.strategy_data
        seen = data.setdefault("veil_boost_seen", {})
        for key in [k for k in seen if not k.startswith(f"{memory.turn}:")]:
            seen.pop(key)
        by_slot = {e.get("slot"): e for e in raw_hand}
        names = {norm(c.name).rstrip("+") for c in state.hand}
        in_cycle = {n.rstrip("+") for n in CORE} <= names
        for c in state.hand:
            e = by_slot.get(c.slot)
            if e is None or not c.card_id or e.get("plus") is not None or e.get("boost") is not None:
                continue
            base = norm(c.name).rstrip("+")
            levels = [n.count("+") for n in (base, base + "+") if n in self.deck]
            if len(levels) != 1:
                continue
            key = f"{memory.turn}:{base}"
            if key in seen:
                e["boost"] = seen[key]
                continue
            if len(state.hand) < 5 or in_cycle or not boost_matters(c.card_id, levels[0]):
                continue
            text = self._probe(frame, c, memory)
            level = level_from_text(c.card_id, text, levels[0])
            self._log("veil_boost_probe", turn=memory.turn, card=base + "+" * levels[0], text=text, level=level)
            if level is not None:
                seen[key] = level - levels[0]
            else:
                seen[key] = None
            return "veil_boost_probe"
        return None

    def assign_keys(self, state, memory, frame) -> dict[int, str] | None:
        """每个手牌槽位的记账键。同名只有一个版本：键就是卡名。同名几个版本：牌库里只剩一个版本有牌就是它；
        本回合已点按分辨过（缓存按槽位，打出一张后剩下的都是同一版本也直接用）就用缓存；否则逐张点按读效果文字分辨，
        读完返回 None（这一帧作废，换新帧决策）。分辨不了 PROBE_RETRIES 次后按第一个可能的版本记，牌库标不确定。"""
        data = memory.strategy_data
        cache = data.get("veil_hand_keys")
        if not cache or cache.get("turn") != memory.turn:
            cache = {"turn": memory.turn, "by_name": {}}
        deck = Counter(data.get("veil_deck") or {})
        out = {}
        for name in sorted({norm(c.name) for c in state.hand if c.card_id}):
            cards = [c for c in state.hand if c.card_id and norm(c.name) == name]
            keys = self.keys_by_name.get(name) or [name]
            possible = [k for k in keys if deck.get(k, 0) > 0] or keys
            if len(possible) == 1:
                out.update({c.slot: possible[0] for c in cards})
                continue
            cached = cache["by_name"].get(name) or {}
            if set(cached) == {str(c.slot) for c in cards}:
                out.update({c.slot: cached[str(c.slot)] for c in cards})
                continue
            if len(cached) == len(cards) and len(set(cached.values())) == 1:  # 打出一张后槽位挪了，剩下的都是同一版本
                key = next(iter(cached.values()))
                cache["by_name"][name] = {str(c.slot): key for c in cards}
                out.update({c.slot: key for c in cards})
                continue
            got = {}
            for c in cards:
                text = self._probe(frame, c, memory)
                key = match_variant(text, possible, self.variants)
                self._log("veil_probe", turn=memory.turn, slot=c.slot, name=name, text=text, candidates=possible, key=key)
                if key is None:
                    break
                got[str(c.slot)] = key
            if len(got) == len(cards):
                cache["by_name"][name] = got
                self.probe_retries = 0
                data["veil_hand_keys"] = cache
                return None
            self.probe_retries += 1
            if self.probe_retries <= PROBE_RETRIES:
                data["veil_hand_keys"] = cache
                return None
            self.probe_retries = 0
            mark_uncertain(data, memory, f"同名「{name}」几个版本点按后仍分不出（{'、'.join(possible)}）")
            got = {str(c.slot): got.get(str(c.slot), possible[0]) for c in cards}
            cache["by_name"][name] = got
            out.update({int(k): v for k, v in got.items()})
        data["veil_hand_keys"] = cache
        return out

    def apply_variants(self, state, slot_keys):
        """分好版本的手牌：一次性按版本改（タグ），客制化文字按那一张补（不再只补同名共有项）。"""
        out = []
        for card in state.hand:
            info = (self.variants or {}).get(slot_keys.get(card.slot, ""))
            if info is not None and slot_keys.get(card.slot) != info["name"]:
                texts = list(variant_texts(info))
                add = [t for t in texts if t not in norm(card.text or "")]
                if add:
                    card = replace(card, text="".join(add) + (card.text or ""))
                if texts and card.fx is not None:
                    from hiro.brain import card_fx

                    index = custom_effect_ids(norm(card.name))
                    card = replace(card, fx=card_fx.add_effects(card.fx, [index.get(t) for t in texts]))
                card = replace(card, tags=replace(card.tags, once=info["once"]))
            out.append(card)
        state.hand = out

    def read(self, frame, memory):
        from hiro.vision.hif import read_hif_exam

        if getattr(frame, "raw", None) is None:
            carry = None
            if turn_carry():
                carry = memory.strategy_data.setdefault("veil_carry_id", uuid.uuid4().hex[:12])
            frame.raw = read_hif_exam(frame.img, batch_names=True, carry=carry)
        before_remaining = memory.last_remaining
        state, why = super().read(frame, memory)
        if state is None:
            return state, why
        if (self.last_raw or {}).get("carried"):
            # 状态栏是回合开头的读数：姿态 / 段階 交给账本（read_position 当作画面没读到姿态），其余状态照用
            state.statuses = [s for s in state.statuses or [] if norm(s.get("name") or "") not in STANCES]
        if support_boost():
            raw_hand = [dict(e) for e in (self.last_raw or {}).get("hand") or []]
            if (probing := self.probe_boosts(state, memory, frame, raw_hand)) is not None:
                return None, probing
            state.hand, boosted = apply_boosts(state.hand, raw_hand, self.deck)
            if boosted and boosted != getattr(self, "last_boosted", None):
                self._log("veil_support_boost", turn=memory.turn, cards=boosted)
            self.last_boosted = boosted
        uncertain = []
        if self.customs:
            state.hand = apply_customs(state.hand, self.customs, copies=self.copies, uncertain=uncertain)
        # 轮次以状态为准：NPC 分数推出来的轮次常读错（验证局 1-c R2 剩 5 回合读成 R1 停机），连续 ROUND_MISMATCH 帧都不符才停
        if self.last_raw.get("finals_round") not in (None, int(state.exam[-1])):
            self.round_mismatch += 1
            self._log("veil_round_mismatch", read=self.last_raw.get("finals_round"), streak=self.round_mismatch)
            if self.round_mismatch >= ROUND_MISMATCH:
                raise ResumeBlocked(f"VEIL 画面轮次连续 {self.round_mismatch} 帧读成 R{self.last_raw.get('finals_round')}，停止考试操作")
        else:
            self.round_mismatch = 0
        track_extra(memory.strategy_data, memory, keep_warm())
        state.actions_left = memory.actions_left
        from hiro.brain.cards import drink_view
        from kaa.db.drink import Drink

        data = memory.strategy_data
        self.data = data
        if "veil_abilities" not in data:
            init_abilities(data, frame.img, self.abilities,
                           started=bool(memory.history or memory.this_turn or memory.turn > 1))  # fmt: skip
        physical = (self.last_raw or {}).get("drink_slots")
        if physical is not None:
            frame.fixed_drink_slots = True
            state.drinks = sync_bar(data, self.ledger_drinks, physical, memory.turn)
        if "veil_deck" not in data:
            if memory.this_turn or memory.history or memory.used_once:
                raise ResumeBlocked("无法从旧考试记忆建立 VEIL 牌库，请核对后交回代理")
            data["veil_deck"] = dict(self.deck)
            if self.fill:
                data["veil_deck"][BASIC_SLOT] = self.fill
                data["veil_fill"] = self.fill
            data["veil_initial"] = dict(self.deck)
            data["veil_initial_once"] = sorted(self.once)
        if self.deck != data.get("veil_initial") or sorted(self.once) != data.get("veil_initial_once"):
            raise ResumeBlocked("本轮续跑的牌组清单与开场清单不一致")
        if "veil_piles" not in data and memory.history:
            data["veil_legacy_piles"] = True
        deck = Counter(data["veil_deck"])
        state.hand = remap_hand(state.hand, self.names_by_base, self.customs, copies=self.copies, uncertain=uncertain)
        if uncertain and sorted(set(uncertain)) != self.custom_uncertain:
            self.custom_uncertain = sorted(set(uncertain))
            self._log("veil_custom_uncertain", items=self.custom_uncertain)  # 同名多张、只有部分做过这项：版本不确定，没补
        state.hand = fix_plus_misread(state.hand, data, memory.turn, deck,
                                      probe=(lambda c: self.probe(frame, c)) if name_probe() else None)
        names = Counter(norm(c.name) for c in state.hand if c.card_id)
        if len(state.hand) != names.total():
            raise ResumeBlocked("VEIL 手牌未读全，暂停以免错牌")
        slot_keys = {c.slot: norm(c.name) for c in state.hand}
        if self.variants:
            slot_keys = self.assign_keys(state, memory, frame)
            if slot_keys is None:
                return None, "veil_probe"
            self.apply_variants(state, slot_keys)
        data["veil_slot_keys"] = {str(k): v for k, v in slot_keys.items()}
        hand = Counter(slot_keys[c.slot] for c in state.hand if c.card_id)
        if swap_pending(data, memory, names):
            self.swap_retries += 1
            if self.swap_retries <= SWAP_WAIT:
                return None, "veil_swap_wait"
            data.setdefault("veil_swap_log", []).append({"turn": memory.turn, "op": "等不到换手", "hand": sorted(names.elements())})
            data.pop("veil_swap_wait", None)
        self.swap_retries = 0
        if data.get("veil_deck", {}).get(BASIC_SLOT) and hand - deck and resolve_basic(data, hand):
            deck = Counter(data["veil_deck"])
        if adopt_added() and hand - deck:
            deck = adopt_added_cards(data, memory, state, hand, deck)
        problem = read_position(state, memory, hand, lambda: flower_count(frame.img), calibrate=self.calibrate)
        if problem == "veil_play_unverified" and self.position_retries >= UNDO_AFTER - 1:
            undone = undo_unlanded_play(memory, names)
            self._log("veil_play_undone", turn=memory.turn, card=undone, hand=sorted(names.elements()))
            if undone is not None:
                self.position_retries = 0
                distrust_fp_after_undo(memory.strategy_data, memory, undone)  # 撤销换了记忆，旧的 data 引用已过时
                return None, "veil_play_undone"
        if problem:
            self.position_retries += 1
            if self.position_retries >= 3:
                raise ResumeBlocked(f"VEIL 无法确认姿态、夜空或上一次出牌：{problem}")
            return None, problem
        self.calibrate = False
        self.position_retries = 0
        note_fp_cards(data, state)
        if not self.resume_checked:
            self.resume_checked = True
            outside_why = check_outside_plays(data, memory, state, before_remaining) if memory.history else None
            self._log("veil_resume_check", turn=memory.turn, remaining=state.remaining_turns, outside_plays=outside_why,
                      fp=data.get("veil_fp"), fp_gap=data.get("veil_fp_gap"), uncertain=bool(data.get("veil_uncertain")),
                      uncertain_why=data.get("veil_uncertain_why"))
        # 全力値：账本优先。账本说付得起、画面发灰 → 先换帧重读几次（多半是 オーバードライブ+ 的 +2 还没刷到画面），仍灰就按账本当作可打
        fp = track_full_power(data, memory)
        if fp_screen() and (seen := full_power_count(frame.img)) is not None and seen != fp:
            # 账本优先（使用者 2026-11-07）：账本知道时只记差异；账本不知道（None，读不清 / 作废）时才用画面补上
            log = data.setdefault("veil_fp_log", [])
            entry = {"turn": memory.turn, "op": "画面不符" if fp is not None else "按画面补账", "ledger": fp, "fp": seen}
            if not log or {k: v for k, v in log[-1].items()} != entry:
                log.append(entry)
            if fp is None:
                data["veil_fp"] = fp = seen
                data.pop("veil_fp_gap", None)
        stale = fp_grey_by_stale_frame(state, fp)
        if stale:
            self.fp_retries += 1
            if self.fp_retries <= FP_REREAD:
                return None, "veil_fullpower_grey"
            state.hand = [replace(c, available=True) if c in stale else c for c in state.hand]
            data.setdefault("veil_fp_log", []).append({"turn": memory.turn, "op": "按账本解灰", "cards": [norm(c.name) for c in stale],
                                                       "fp": fp})
        self.fp_retries = 0
        if grey := stance_grey_by_stale_frame(state):
            self.stance_retries += 1
            if self.stance_retries <= STANCE_REREAD:
                self._log("veil_stance_grey", turn=memory.turn, stance=stance(state), cards=[norm(c.name) for c in grey],
                          retry=self.stance_retries)
                return None, "veil_stance_grey"
        self.stance_retries = 0
        if blocked := play_condition_blocked(state, memory, data):
            # 主数据「…の場合、使用可」按账本判定不满足：当作发灰，不点（画面没灰也不点）。
            # codex-20261108-1-b R1 T2：タフネス+（温存になった回数が2回以上）画面没灰，点了两次都没打出，撤回后账本位置不明
            state.hand = [replace(c, available=False) if c.slot in blocked else c for c in state.hand]
            log = data.setdefault("veil_condition_log", [])
            entry = {"turn": memory.turn, "blocked": {norm(c.name): blocked[c.slot] for c in state.hand if c.slot in blocked}}
            if not log or log[-1] != entry:
                log.append(entry)
        outside = deck - hand
        if data.get("veil_uncertain") or hand - deck:
            if CORE <= names.keys() or data.get("veil_cycle"):
                why_list = data.get("veil_uncertain_why") or []
                detail = why_list[-1]["why"] if why_list else (f"手牌里有牌组账本外的牌：{sorted((hand - deck).elements())}"
                                                               if hand - deck else "旧记忆没记原因")
                raise ResumeBlocked(f"VEIL 牌库记账与当前画面无法互证（{detail}），暂停核对；核对所持スキルカード后用 "
                                    f"python -m hiro.produce.veil_exam clear-uncertain 清除")
            return state, why
        played = {norm(a[2:]) for a in memory.this_turn if a.startswith("打 ")}
        next_draw = 3 + 2 * (TEACH in played) + int(memory.turn < data.get("veil_soda_until", 0))
        # Only the combined multiset is known, so don't expose a guessed draw order.
        known_once = set(data.get("veil_known_once", [])) | self.once
        known_once.update(slot_keys[c.slot] for c in state.hand if c.tags.once and norm(c.name) != REBOUND)
        data["veil_known_once"] = sorted(known_once)
        slots = [None] * 5
        for card in state.hand:
            if not 0 <= card.slot < 5 or slots[card.slot] is not None:
                raise ResumeBlocked("VEIL 手牌槽位无法核对")
            slots[card.slot] = slot_keys[card.slot]
        from hiro.brain import piles as PB

        if PB.enabled():
            piles = PB.observe(data, memory.turn, slots, outside)
            if data["veil_piles"].get("need_view") and data.get("veil_pile_viewed") != memory.turn:
                # 对账层兜底（使用者 2026-11-09：「将点进右下角牌库做成所有幽灵牌出现时的兜底」）：打开牌库一览按分组读一次
                data["veil_pile_viewed"] = memory.turn
                groups = read_pile_view()
                diff = PB.apply_view(data, groups) if groups else None
                self._log("veil_pile_view", turn=memory.turn, ok=diff is not None, diff=diff, **PILE_VIEW_LAST)
                if diff is not None:
                    piles = PB._view(data["veil_piles"])
        else:
            piles = track_piles(data, memory.turn, slots, outside)
        once_view = data["veil_known_once"]
        if self.variants:
            # 决策层只认卡名：牌堆按卡名给；once 列出牌堆里还有一次性版本的卡名（small_deck 等按「有没有一次性的」判）
            piles = {k: [name_of(n) for n in v] for k, v in piles.items()}
            once_view = sorted({name_of(k) for k in known_once})
        state.piles = {**piles, "next_draw": next_draw, "once": once_view}
        return state, why

    # ---------------------------------------------------------------- 循环连打（VEIL_CYCLE_TAP，执行侧见 hiro.exec.cycle_tap）
    def burst_begin(self, state, memory, decision):
        """刚记完账的一手循环牌之后，不读画面推出「这张牌离手后」的局面：手牌按首读的顺序删掉打出的那张、槽位重排，
        记忆字段（本回合动作、剩余出牌次数）刷新。姿态 / 夜空等到 burst_settle 核对过画面张数再结算。
        不满足连打条件（开关关、分版本记账、牌库不确定、不在本回合的循环里、出牌次数用完、打出的牌会抽牌 / 换手）返回 None。"""
        from hiro.brain.veil import cycle_tap

        if not cycle_tap() or decision.kind != "play" or self.variants:
            return None
        data = memory.strategy_data
        cycle = data.get("veil_cycle")
        pending = data.get("veil_pending_play")
        if (not cycle or cycle.get("turn") != state.turn or data.get("veil_uncertain") or memory.actions_left < 1
                or not pending or pending.get("turn") != memory.turn or memory.expected_hand is None):
            return None
        played = next((c for c in state.hand if c.slot == decision.slot), None)
        if played is None or played.tags.draws or "入れ替える" in norm(played.text or ""):
            return None
        rest = [c for c in state.hand if c.slot != decision.slot]
        if not rest or any(not c.card_id for c in rest):
            return None
        return replace(state, hand=[replace(c, slot=i) for i, c in enumerate(rest)], actions_left=memory.actions_left,
                       this_turn=list(memory.this_turn), history=list(memory.history), blocked=list(memory.blocked))

    def burst_settle(self, state, memory, greys) -> bool:
        """连打：画面核对过张数和位置（上一手确实离手）后，按账本结算上一手，同 read_position 回合内的分支：
        姿态 / 段階 / 夜空 / 预约的下回合姿态，牌堆记录里的手牌格挤紧，发灰按卡名首字母角标（greys，每格一个），
        使用条件按账本。结算不了返回 False（退回整帧读取，pending 留给 read_position 核对）。"""
        from hiro.brain.veil import SCREEN_STANCE

        data = memory.strategy_data
        pending = data.get("veil_pending_play")
        position = data.get("veil_position") or {}
        if (not pending or pending.get("turn") != memory.turn or position.get("turn") != memory.turn
                or len(greys) != len(state.hand) or pending.get("stance") is None or pending.get("flower") is None):
            return False
        current, level, flower = pending["stance"], pending.get("stance_level"), pending["flower"]
        delayed, delayed_level = position.get("next_stance"), position.get("next_stance_level")
        if pending.get("next_stance"):
            delayed, delayed_level = pending["next_stance"], pending.get("next_stance_level")
        count_stance_change(data, position.get("stance"), current)
        data["veil_position"] = {"turn": memory.turn, "stance": current, "stance_level": level,
                                 "flower": flower, "next_stance": delayed, "next_stance_level": delayed_level}
        data.pop("veil_pending_play", None)
        state.pitem_charges = flower
        state.statuses = [s for s in state.statuses or [] if s.get("name") != SCREEN_STANCE
                          and norm(s.get("name") or "") not in STANCES and not norm(s.get("name") or "").startswith("次のターン開始時")]
        state.statuses.append({"name": current, "value": level})
        if delayed:
            state.statuses.append({"name": f"次のターン開始時、{delayed}に変更"})
        names = [norm(c.name) for c in state.hand]
        data["veil_slot_keys"] = {str(c.slot): norm(c.name) for c in state.hand}
        piles = data.get("veil_piles")
        if piles is not None:
            piles["hand"] = names + [None] * (5 - len(names))
            played = {norm(a[2:]) for a in memory.this_turn if a.startswith("打 ")}
            state.piles = {**(state.piles or {}),
                           **{k: list(Counter(piles.get(k) or {}).elements()) for k in ("deck", "grave", "unlocated")},
                           "next_draw": 3 + 2 * (TEACH in played) + int(memory.turn < data.get("veil_soda_until", 0))}
        state.hand = [replace(c, available=not grey) for c, grey in zip(state.hand, greys)]
        if blocked := play_condition_blocked(state, memory, data):
            state.hand = [replace(c, available=False) if c.slot in blocked else c for c in state.hand]
        track_full_power(data, memory)
        state.actions_left = memory.actions_left
        return True


def count_stance_change(data, before, after):
    """使用条件要的计数（这场考试内）：指針を変更した回数、温存になった回数、全力になった回数。往多里算（宁多勿少）：
    のんびり 也算进温存、上一帧姿态不明时也算一次，只用来判「肯定不满足」（play_condition_blocked）。"""
    if after is None or after == before:
        return
    counts = data.setdefault("veil_stance_counts", {"changes": 0, "warm": 0, "full": 0})
    if before is not None:
        counts["changes"] += 1
    if after in WARM:
        counts["warm"] += 1
    if after == "全力":
        counts["full"] += 1


_CONDITIONS = (
    (re.compile(r"温存になった回数が(\d+)回以上の場合、使用可"), "warm"),
    (re.compile(r"指針を変更した回数が(\d+)回以上の場合、使用可"), "changes"),
    (re.compile(r"全力になった回数が(\d+)回以上の場合、使用可"), "full"),
)


_TEXT_STANCE_CONDITIONS = (
    (re.compile(r"強気(\d)段階目の場合、使用可"), "強気"),
    (re.compile(r"強気の場合、使用可"), "強気"),
    (re.compile(r"温存の場合、使用可"), "温存"),
    (re.compile(r"全力の場合、使用可"), "全力"),
)


def play_condition(card) -> dict | None:
    """这张牌的使用条件，格式同 card_fx.trigger：{kind, value, not, text[, level]}。结构化效果为准（使用者 2026-11-09），
    没有结构化效果时按牌面文字认（认得的几种），都没有返回 None。"""
    from hiro.brain import card_fx

    if (fx := card_fx.of(card)) is not None:
        return fx.get("condition")
    text = norm(card.text or "")
    if "使用可" not in text:
        return None
    for pattern, key in _CONDITIONS:
        if m := pattern.search(text):
            return {"kind": key, "value": int(m.group(1)), "not": False, "text": m.group(0)}
    if m := re.search(r"(\d+)ターン目以降の場合、使用可", text):
        return {"kind": "turn", "value": int(m.group(1)), "not": False, "text": m.group(0)}
    for pattern, value in _TEXT_STANCE_CONDITIONS:
        if m := pattern.search(text):
            out = {"kind": "stance", "value": value, "not": False, "text": m.group(0)}
            if m.groups():
                out["level"] = int(m.group(1))
            return out
    if "いずれかの指針の場合、使用可" in text:
        return {"kind": "stance", "value": "なし", "not": True, "text": "いずれかの指針の場合、使用可"}
    return {"kind": "other", "value": None, "not": False, "text": text}


def condition_met(cond, state, memory, counts, current) -> bool | None:
    """使用条件按账本 / 画面成不成立：True / False；判断不了（姿态不明、计数没记、认不出的条件）None。"""
    if not cond:
        return True
    kind, value = cond["kind"], cond.get("value")
    if kind in ("warm", "changes", "full", "strong"):
        if kind not in counts and kind == "strong":
            return None
        ok = counts.get(kind, 0) >= int(value or 0)
    elif kind == "turn":
        ok = memory.turn >= int(value or 0)
    elif kind == "stance":
        if current is None:
            return None
        if value == "なし":
            ok = current == NEUTRAL
        elif value == "温存":
            ok = current in WARM
        else:
            ok = current == value
            if ok and cond.get("level") in (1, 2):
                level = stance_level(state, current)
                ok = level in (None, cond["level"])
    else:
        return None
    return not ok if cond.get("not") else ok


def play_condition_blocked(state, memory, data) -> dict:
    """手牌里按主数据使用条件肯定打不了的牌 {槽位: 原因}。只判账本 / 画面确定的（计数往多里算、姿态不明不判），
    宁可漏判（照旧按画面）也不误拦。使用条件以结构化效果为准（card_fx），没有时按牌面文字。"""
    counts = data.get("veil_stance_counts") or {}
    current = stance(state)
    out = {}
    for card in state.hand:
        if not card.available:
            continue
        cond = play_condition(card)
        if condition_met(cond, state, memory, counts, current) is False:
            seen = counts.get(cond["kind"], 0) if cond["kind"] in ("warm", "changes", "full", "strong") else None
            where = f"账本 {seen} 次" if seen is not None else (f"第 {memory.turn} 回合" if cond["kind"] == "turn" else f"现在 {current}")
            out[card.slot] = f"{cond['text']}（{where}）"
    return out


def star_count(card) -> int:
    """一番星打出后装上的「回合开始切のんびり」附魔次数（主数据 effectCount，以降3回まで）；查不到按 3。"""
    if _flag("ENCHANT_ENGINE"):
        try:
            from hiro.brain.pitems import PHASE_START_TURN, card_enchants

            for en, count in card_enchants(card):
                if count and en.trigger is not None and any(p == PHASE_START_TURN for p, _ in en.trigger.phases):
                    return count
        except Exception:  # noqa: BLE001
            pass
    return 3


class VeilBrain(BattleBrain):
    def commit(self, state, memory, decision):
        before = memory.actions_left
        undo = json.loads(json.dumps(memory.to_dict())) if decision.kind == "play" else None
        self._commit(state, memory, decision)
        pending = memory.strategy_data.get("veil_pending_play")
        if undo is not None and pending is not None:
            # 出牌前的整份记忆：牌没落地（手里同名牌没少）时按它撤销这一手，见 undo_unlanded_play
            pending["undo"] = undo
            pending["hand"] = sorted(norm(c.name) for c in state.hand if c.card_id)
        note_extra(memory.strategy_data, memory, decision, before)
        note_commit(memory.strategy_data, memory, state, decision)

    def _commit(self, state, memory, decision):
        super().commit(state, memory, decision)
        data = memory.strategy_data
        if decision.kind == "drink":
            drink = next(d for d in state.drinks if d.slot == decision.slot)
            bar = data.get("veil_bar")
            if bar is not None and 0 <= decision.slot < len(bar):
                bar.pop(decision.slot)  # 喝掉：后面的往左挤（实机 1-d R2：3 瓶喝掉第 2 格后第 3 瓶挪到第 2 格）
                data["veil_drinks"] = bar_record(bar)
            if drink.name == SODA:
                data["veil_soda_until"] = memory.turn + 5
            from hiro.brain import piles as PB

            PB.expect(data.get("veil_piles"), PB.moves_of_drink(norm(drink.name)))
            commit_full_power(data, memory, drink, "喝")  # ジンジャーエール 全力値+1、ほうじ茶 +2、厳選初星チャイ 每回合 +1
            return
        if decision.kind != "play":
            return
        card = next(c for c in state.hand if c.slot == decision.slot)
        slot_keys = data.get("veil_slot_keys") or {}
        name = slot_keys.get(str(decision.slot)) or norm(card.name)  # 记账键（分版本记账时带版本后缀，否则就是卡名）
        variant = name != norm(card.name)
        before = stance(state)
        if before is None and memory.turn == 1 and (data.get("veil_position") or {}).get("stance") in (None, NEUTRAL):
            before = NEUTRAL  # 第 1 回合还没切过姿态：切強気不推夜空（原来记成 None、夜空不明）
        current, flower = after_play(before, state.pitem_charges, card)
        level = (level_after(before, stance_level(state, before), current, stance_change_level(card))
                 if destination(card) == current else None)
        if current == before and destination(card) != current:
            level = stance_level(state, before)
        if norm(card.name).startswith(STAR):
            data["veil_star"] = star_count(card)
        delayed, delayed_level = next_stance_change(card)
        data["veil_pending_play"] = {
            "turn": memory.turn, "card": name,
            "count": sum((slot_keys.get(str(c.slot)) or norm(c.name)) == name for c in state.hand),
            "stance": current, "stance_level": level, "flower": flower,
            "next_stance": delayed, "next_stance_level": delayed_level,
        }
        if destination(card) == "強気" and stance(state) in WARM:
            memory.actions_left += 1
        commit_full_power(data, memory, card)
        deck = data["veil_deck"]
        if variant:  # 分版本：一次性按这一张的版本（账本记的），不按同名
            lost = name in data.get("veil_initial_once", ())
        else:
            lost = (name in data.get("veil_initial_once", ()) if name == REBOUND else
                    card.tags.once or name == "眠気" or name in data.get("veil_known_once", ()))
        cache = (data.get("veil_hand_keys") or {}).get("by_name", {}).get(norm(card.name))
        if cache is not None:
            cache.pop(str(decision.slot), None)  # 打出去的那张不再在手里
        if lost:
            if deck.get(name, 0) == 0:
                mark_uncertain(data, memory, f"打出的一次性牌不在牌组账本里：{name}")
            else:
                deck[name] -= 1
        piles = data.get("veil_piles")
        if piles is not None:
            piles["hand"][decision.slot] = None
            if not lost:
                piles["grave"][name] = piles["grave"].get(name, 0) + 1
            if swaps_hand(card):
                piles["swap"] = True  # 下一次记账：原手牌整手进捨札，新手牌全部算新抽的（track_piles）
            from hiro.brain import piles as PB

            PB.expect(piles, PB.moves_of_card(card))  # 这一手应该抽几张：回合结束都没出现的记成暗抽（VEIL_PILE_ENGINE）
        if norm(card.name) in SLEEP_GENERATORS:
            deck["眠気"] = deck.get("眠気", 0) + 1
            if piles is not None:
                piles.setdefault("born", {})
                piles["born"]["眠気"] = piles["born"].get("眠気", 0) + 1  # 抽完这一手的牌再并进山札（track_piles）
        elif card.tags.makes_sleep or "生成" in card.text or "変換" in card.text:
            mark_uncertain(data, memory, f"打出会生成 / 变换牌的牌，牌组账本跟不上：{name}")
        # 回忆能力在牌的效果之后结算（「〜使用後」）：再动 / 抽牌 / 删眠気，每项按次数触发后取否
        apply_ability(data, memory, norm(card.name))


OPEN_WAIT_S = 30.0  # 开场演出（回忆能力发动、ターン開始）期间不是出牌页，等它播完再接管


def wait_exam_page(shoot, ok, *, timeout=OPEN_WAIT_S, every=1.0, clock=time.monotonic, sleep=time.sleep):
    """首次接管：开场演出帧不是出牌页时等，出牌页出现就返回该帧；超时返回 None。
    验证局 codex-20261107-1-b：exam 首次启动遇回忆能力演出帧直接退出。"""
    start = clock()
    while True:
        img = shoot()
        if ok(img):
            return img
        if clock() - start >= timeout:
            return None
        sleep(every)


def round_attempt(directory, exam, *, first_turn):
    """选这一轮当前要写的记录目录，返回 (目录, 第几次再挑戦或 None, 续跑用的考试记忆或 None)。
    再挑戦的唯一证据是最近一份考试记忆标了 finished（strict 模式只在认到结算页时才标）且画面回到首回合：
    这时开新目录 <名>-attemptN，旧记录原样保留。首回合已出牌 / 喝饮料后中断（剩余回合仍是 9 / 12）是正常续跑，
    沿用未结束的记忆，不改名、不恢复饮料。证据矛盾（记忆已结束但画面不是首回合、画面回到首回合但记忆停在后面回合
    且没有追加回合）时抛 ResumeBlocked 交给使用者核对，不重置。"""
    from hiro.exec.exam import MEMORY_FILE, load_exam_memory

    base = directory
    pattern = re.compile(rf"{re.escape(base.name)}-attempt([1-9]\d*)")
    attempts = [int(m[1]) for d in directory.parent.glob(f"{directory.name}-attempt*")
                if d.is_dir() and (m := pattern.fullmatch(d.name))]
    retry = max(attempts, default=0)
    if retry:
        directory = directory.with_name(f"{directory.name}-attempt{retry}")

    checkpoints = []
    for path in [directory, *directory.parent.glob(f"{directory.name}-r*")]:
        if path != directory and not re.fullmatch(rf"{re.escape(directory.name)}-r\d+", path.name):
            continue
        checkpoint = path / MEMORY_FILE
        if not checkpoint.exists():
            continue
        try:
            data = json.loads(checkpoint.read_text(encoding="utf-8"))
            if data["label"] != exam:
                raise ValueError("考试轮次不符")
            checkpoints.append((data["saved_at"], path, data["finished"]))
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ResumeBlocked(f"无法核对考试记忆：{checkpoint}") from e
    if not checkpoints:
        return directory, retry or None, None

    _, path, finished = max(checkpoints, key=lambda entry: entry[0])
    if finished:
        if not first_turn:
            raise ResumeBlocked("上一次考试已结束，未确认画面是再挑戦首回合")
        retry += 1
        return base.with_name(f"{base.name}-attempt{retry}"), retry, None

    restored = load_exam_memory(directory, exam)
    if restored is None or restored[1] != path:
        raise ResumeBlocked("未结束的考试记忆无法续跑，请核对原记录")
    if first_turn and restored[0].turn > 1 and not restored[0].extra_turns:
        raise ResumeBlocked("画面回到首回合，但考试记忆未标结束，请核对是否已再挑戦")
    return directory, retry or None, restored


def save_failure(trace_dir, shoot, why: str) -> Path | None:
    """考试停机（ResumeBlocked）时把当时的画面和原因存进 <trace>/failures/（使用者 2026-11-08：读失败的帧原来不进 trace，
    算不出真实停机率）。存不了不影响停机本身。"""
    import cv2

    try:
        d = Path(trace_dir) / "failures"
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        img = shoot()
        if img is not None:
            cv2.imwrite(str(d / f"{stamp}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        with (d / "failures.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 2), "frame": f"{stamp}.jpg" if img is not None else None, "why": why},
                               ensure_ascii=False) + "\n")
        return d
    except Exception:  # noqa: BLE001
        return None


def run_round(round_no, deck_path, trace_name, drinks=None, abilities=None, retry_drinks=None):
    from kaa.config.const import HifScenario
    from kotonebot import device
    from kotonebot.backend.context import init_context, manual_context

    from hiro.contracts import TraceWriter
    from hiro.device import connect
    from hiro.exec.exam import run_exam
    from hiro.kaa_env import init_kaa
    from hiro.loop import pause_flag
    from hiro.loop.repeat_guard import watchdog
    from hiro.loop.session import Session
    from hiro.recorder import Recorder
    from hiro.vision.hif import is_hif_exam, read_hif_exam
    from hiro.vision.pages import classify_page, dialog_header
    from kotonebot.config.config import conf

    deck, once = read_deck(deck_path)
    customs = read_customs(deck_path)
    variants = read_variants(deck_path)
    if variants:
        deck = {k: v["count"] for k, v in variants.items()}
        once = {k for k, v in variants.items() if v["once"]}
    init_kaa(HifScenario.MAIN, idol=IDOL, require_sets=False)
    pause_flag.wait_until_recording()
    connection = connect("maatools")
    try:
        init_context(target_device=connection)
        manual_context().begin()
        from kotonebot.backend.context import vars as kvars

        img = wait_exam_page(device.screenshot, lambda f: (f is not None and f.shape[:2] == (1280, 720)
                                                           and dialog_header(f) is None and is_hif_exam(f)))
        if img is None:
            raise ResumeBlocked(f"实机画面未确认是本战 R{round_no} 出牌页（等了 {OPEN_WAIT_S:.0f}s）")
        observed = read_hif_exam(img)
        exam = f"r{round_no}"
        directory = Path("traces") / trace_name
        # 轮次以状态为准（--round + 同一轮的考试记忆）；画面只用来发现矛盾：明确读成别的轮次才拦，读不出不拦
        # （使用者 2026-11-07：「轮次直接写在状态机里」。验证局 R2 切屏后读不出「本戦 R2」，续跑被拦）
        read_round = observed.get("finals_round") if observed.get("finals") else None
        if read_round is not None and read_round != round_no:
            raise ResumeBlocked(f"实机读到本战 R{read_round}，与要接管的 R{round_no} 不符")
        first_turn = observed.get("remaining_turns") == {1: 9, 2: 12}[round_no]
        directory, retry, restored = round_attempt(directory, exam, first_turn=first_turn)
        if retry and restored is None:
            # 再挑戦：上一次的 trace 原样留着，这一次写进 <名>-attemptN（只按「上一次考试记忆已标结束」判，不按有没有出牌历史）
            print(f"再挑戦：R{round_no} 第 {retry} 次重试，记录写到 {directory}", file=sys.stderr)
        if restored is None and not first_turn:
            raise ResumeBlocked("缺少同一轮的考试记忆，请从首回合接管")
        if read_round is None and restored is None:
            # 新开一轮（剩余回合正好是这一轮的首回合）：按 --round 接管（验证局 1-f R1 开考读不出轮次被拦，原样重跑才过）
            print(f"实机轮次读不出，剩余回合 {observed.get('remaining_turns')} 是 R{round_no} 首回合，按 --round 接管", file=sys.stderr)
        elif read_round is None:
            print(f"实机轮次读不出，按考试记忆接管 R{round_no}（{directory}）", file=sys.stderr)
        result = None

        def ended():
            nonlocal result
            frame = device.screenshot()
            if frame is None or frame.shape[:2] != (1280, 720) or is_hif_exam(frame):
                return False
            page = classify_page(frame)
            if not page or page[0] != "HIF_FINALS_RANKING":
                return False
            fields = page[1]
            if not ((fields.get("kind") == "round_result" and fields.get("finals_round") == round_no)
                    or (fields.get("kind") == "final" and round_no == 2)):
                return False
            result = fields
            session.record(session.observe(scene="HIF_FINALS_RANKING", img=frame, fields=fields, save_frame=True))
            return True

        trace = TraceWriter(root=directory.parent, name=directory.name, meta={"mode": "main", "idol": IDOL, "advisor": "veil", "round": round_no})
        session = Session(trace)
        watch = pause_flag.Watcher(kvars.flow, session=session).start()
        guard = watchdog(session)
        callbacks = conf().loop.loop_callbacks
        callbacks.append(guard)
        rec = Recorder(trace.dir / "battle")
        if retry and retry_drinks is not None:
            drinks = retry_drinks  # 再挑戦：游戏把饮料栏恢复到这一轮开考前，账本也用开考前的快照
        source = VeilScreen(deck, once, drinks, abilities, customs=customs, variants=variants, fill=fill_count(round_no, deck))
        try:
            run_exam(VeilBrain(None, None), exam, rec, ended, session=session, source=source,
                     advisor=VeilAdvisor(), round_no=round_no, strict=True)
        except ResumeBlocked as e:
            save_failure(trace.dir, device.screenshot, str(e))
            raise
        finally:
            callbacks.remove(guard)
            watch.stop()
            trace.close()
        if result is None:
            raise ResumeBlocked("VEIL 考试结束页未确认，请查看实机与 trace")
        return {"ok": True, "exam": exam, "trace": str(trace.dir), "retry": retry,
                "round_score": result.get("round_score"), "rank": result.get("rank"),
                "drinks": (source.data or {}).get("veil_drinks"),
                "abilities": (source.data or {}).get("veil_abilities"),
                "abilities_source": (source.data or {}).get("veil_abilities_source"),
                "abilities_mismatch": (source.data or {}).get("veil_abilities_mismatch")}
    finally:
        connection.stop()


def main(argv=None):
    """离线命令（只改 trace 里的考试记忆，不碰游戏）：
    python -m hiro.produce.veil_exam clear-uncertain traces/<run> --hand 5 --deck 0 --grave 0 --removed 18 --evidence "帧 …"
    python -m hiro.produce.veil_exam show traces/<run>"""
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="python -m hiro.produce.veil_exam")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clear-uncertain", help="核对所持スキルカード张数后清除 veil_uncertain")
    c.add_argument("trace_dir")
    for k in ("hand", "deck", "grave"):
        c.add_argument(f"--{k}", type=int, required=True)
    c.add_argument("--removed", type=int)
    c.add_argument("--hand-cards", help="手札卡名，逗号分隔（可选，多一层核对）")
    c.add_argument("--evidence", required=True)
    c.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("show", help="打印牌库账本、不确定原因和全力値账本")
    s.add_argument("trace_dir")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        data = json.loads((Path(a.trace_dir) / "exam_memory.json").read_text(encoding="utf-8"))["memory"]["strategy_data"]
        keys = ("veil_uncertain", "veil_uncertain_why", "veil_added", "veil_deck", "veil_fp", "veil_fp_gap", "veil_fp_log")
        print(json.dumps({k: data.get(k) for k in keys}, ensure_ascii=False, indent=1))
        return 0
    try:
        out = clear_uncertain(a.trace_dir, hand=a.hand, deck=a.deck, grave=a.grave, removed=a.removed, evidence=a.evidence,
                              hand_cards=a.hand_cards.split(",") if a.hand_cards else None, dry_run=a.dry_run)
    except ValueError as e:
        print(f"未清除：{e}")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
