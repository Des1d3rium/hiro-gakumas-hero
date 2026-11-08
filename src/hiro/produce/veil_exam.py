"""Screen adapter and single-round runner for the VEIL batch route."""

from __future__ import annotations

import json
import os
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
    stance, stance_level, stance_change_level, start_turn,
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


def validate_deck(data):
    if not isinstance(data, dict) or not isinstance(data.get("evidence"), str) or not data["evidence"].strip():
        raise ValueError("牌组清单须记录证据路径")
    if not isinstance(data.get("cards"), list) or not all(isinstance(c, str) and c.strip() for c in data["cards"]):
        raise ValueError("牌组清单须列出完整卡名及强化，每张单列")
    names = Counter(norm(c) for c in data["cards"])
    if any(names[c] != 1 for c in CORE) or not isinstance(data.get("rebound_repeatable"), bool):
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


def apply_customs(hand, customs, lookup=None, copies=None, uncertain=None):
    """手牌里客制化过的牌：按账本把客制化效果补进牌面文字（同名按去「+」比；已含该文字的不重复加）。
    立即结算的文字加在原文前；「次のターン / ターン開始 / 以降」这类延后的加在原文后
    （destination / fp_effect 只看第一个「次のターン」之前的部分，加在前面会把原文的立即效果挤到延后段里）。"""
    from hiro.brain.veil import _DELAYED

    extra = resolve_customs(customs, lookup, copies, uncertain)
    if not any(extra.values()):
        return hand
    out = []
    for card in hand:
        add = [t for t in dict.fromkeys(extra.get(norm(card.name).rstrip("+"), [])) if t not in norm(card.text or "")]
        if add:
            before = "".join(t for t in add if not _DELAYED.search(t))
            after = "".join(t for t in add if _DELAYED.search(t))
            card = replace(card, text=before + (card.text or "") + after)
        out.append(card)
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


def track_piles(data, turn, slots, outside):
    """Reconcile known piles against the observed hand without reading pile UI."""
    previous = data.get("veil_piles")
    deck, grave, unknown = Counter(), Counter(), Counter()
    if previous is None:
        (deck if turn == 1 and not data.get("veil_legacy_piles") else unknown).update(outside)
    else:
        deck, grave, unknown = (Counter(previous[k]) for k in ("deck", "grave", "unlocated"))
        old_hand = Counter(c for c in previous["hand"] if c)
        hand = Counter(c for c in slots if c)
        if turn != previous["turn"]:
            grave.update(old_hand)
            drawn = hand
        else:
            grave.update(old_hand - hand)
            drawn = hand - old_hand
        if drawn - deck and not unknown and deck.total() < drawn.total():
            deck.update(grave)
            grave.clear()
        if drawn - deck or deck - drawn + grave + unknown != outside:
            deck.clear()
            grave.clear()
            unknown = outside.copy()
        else:
            deck -= drawn
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
            predicted_level = delayed_level if scheduled else level
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

    data["veil_position"] = {"turn": memory.turn, "stance": current, "stance_level": level,
                             "flower": flower, "next_stance": delayed, "next_stance_level": delayed_level}
    data.pop("veil_pending_play", None)
    state.pitem_charges = flower
    state.statuses = [s for s in state.statuses or [] if norm(s.get("name") or "") not in STANCES]
    if current is not None:
        state.statuses.append({"name": current, "value": level})
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
    elif hit["kind"] == "sleep":
        deck = data.get("veil_deck") or {}
        if deck.get("眠気", 0) > 0:
            deck["眠気"] -= 1
            piles = data.get("veil_piles")
            if piles is not None:
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
    swap = played is not None and "手札をすべて入れ替える" in norm(played.text or "")
    if swap and hand:
        data["veil_swap_wait"] = {"turn": memory.turn, "before": sorted(hand), "card": norm(played.name)}
    data["veil_last_commit"] = {"turn": memory.turn, "remaining": state.remaining_turns, "kind": decision.kind,
                                "hand": None if swap else hand,
                                "ended": decision.kind == "skip" or memory.actions_left <= 0}


SWAP_WAIT = 12  # 换手牌（手札をすべて入れ替える）后最多等这么多帧手牌变化，等不到就记一笔照常走


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
    now.pop((wait.get("card") or "").rstrip("+"), None)  # 打出去的那张还飘在手牌位置上时也算旧手牌
    if now == base(wait["before"]):
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


# ---------------------------------------------------------------- 牌库账本不确定（veil_uncertain）：原因、自愈、核对后清除
# 置真的地方只有 VeilBrain._commit：①打出的一次性牌不在牌组账本里；②打出会生成 / 变换牌的牌（眠気生成牌除外，单独记账）。
# 置真后 read() 每帧都跳过牌堆记账，手里同时有四张核心或已进循环就停；以前没有任何地方清它，只能代理手改记忆
# （codex-20261107-1 第 1 局 R2：T3 手里出现牌组外的 立ち回りの基本，T5 打出后置真，剩 5 回合进循环时停机）。
# 现在：①的常见来源（牌组外的一次性牌出现在手里）在看到时就收进账本（adopt_added_cards），打出时正常扣掉，不再置真；
# 其余情况记下原因（veil_uncertain_why），停机提示里写原因和清除命令；清除要给出「所持スキルカード」的张数，和账本对上才清。


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


def adopt_added_cards(data, memory, state, hand, deck):
    """手里出现牌组账本外的牌（事件 / P 道具 / 效果加进来的）：都是一次性牌（打出即除外）时收进账本——它此刻就在手里，
    数量确定；打出后照常从账本扣掉，牌组其余部分不受影响。有常驻牌（会回到捨札、循环里一直在）时不收，维持原来的暂停。"""
    extra = hand - deck
    by_name = {norm(c.name): c for c in state.hand if c.card_id}
    if not all(by_name[n].tags.once or by_name[n].tags.is_sleep for n in extra):
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


class VeilScreen(ScreenExamStateSource):
    def __init__(self, deck, once, ledger_drinks=None, abilities=None, customs=None):
        super().__init__()
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
        self.swap_retries = 0
        self.resume_checked = False  # 本进程第一帧：核对 exam 外有没有出牌、把不确定的原因写进 events.jsonl
        self.rec = None
        self.names_by_base = defaultdict(list)
        self.copies = Counter()  # 去「+」卡名 → 牌组里几张（同名多张的客制化只补共有项）
        self.custom_uncertain = []
        for name in deck:
            self.names_by_base[name.rstrip("+")].append(name)
            self.copies[name.rstrip("+")] += deck[name]

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
                "uncertain_why": (data.get("veil_uncertain_why") or [None])[-1], **self.trace_board(state, memory)}

    def trace_board(self, state, memory) -> dict:
        """可信 trace 库（使用者 2026-11-07）：决策实际用的牌面。原来 steps.jsonl 只有规整前的手牌（OCR 漏 / 多「+」），
        牌堆 / 弃牌堆 / 除外区只在考试记忆里，回放只能从账本倒推。
        hand：规整 + 客制化后的手牌 [槽位, 名字, 卡 id, 强化, 能打, 一次性]；piles：牌堆 / 弃牌堆 / 位置不明（多重集合，不知道顺序）；
        excluded：开场牌组里已经打出离开的一次性牌；deck：当前牌组账本（含衍生的眠気）；
        undone：本回合撤回过的出牌次数（画面延迟、点错）；flags：VEIL 策略开关的实际取值。"""
        data = memory.strategy_data
        piles = state.piles or {}
        initial, deck = Counter(data.get("veil_initial") or {}), Counter(data.get("veil_deck") or {})
        return {
            "hand": [[c.slot, norm(c.name), c.card_id, c.upgrade, c.available, bool(c.tags.once)] for c in state.hand],
            "drinks": [d.name for d in state.drinks],
            "piles": None if not piles else {k: dict(Counter(norm(n) for n in piles.get(k) or [])) for k in ("deck", "grave", "unlocated")},
            "next_draw": piles.get("next_draw"),
            "excluded": dict(initial - deck) or None,
            "deck": dict(deck) or None,
            "played": [a[2:] for a in memory.this_turn if a.startswith("打 ")],
            "undone": sum(1 for u in data.get("veil_undo_log") or [] if u.get("turn") == memory.turn),
            "flags": {name: _flag(name) for name in STRATEGY_FLAGS},
        }

    def read(self, frame, memory):
        from hiro.vision.hif import read_hif_exam

        if getattr(frame, "raw", None) is None:
            frame.raw = read_hif_exam(frame.img, batch_names=True)
        before_remaining = memory.last_remaining
        state, why = super().read(frame, memory)
        if state is None:
            return state, why
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
        hand = Counter(norm(c.name) for c in state.hand if c.card_id)
        if len(state.hand) != hand.total():
            raise ResumeBlocked("VEIL 手牌未读全，暂停以免错牌")
        if swap_pending(data, memory, hand):
            self.swap_retries += 1
            if self.swap_retries <= SWAP_WAIT:
                return None, "veil_swap_wait"
            data.setdefault("veil_swap_log", []).append({"turn": memory.turn, "op": "等不到换手", "hand": sorted(hand.elements())})
            data.pop("veil_swap_wait", None)
        self.swap_retries = 0
        if adopt_added() and hand - deck:
            deck = adopt_added_cards(data, memory, state, hand, deck)
        problem = read_position(state, memory, hand, lambda: flower_count(frame.img), calibrate=self.calibrate)
        if problem == "veil_play_unverified" and self.position_retries >= UNDO_AFTER - 1:
            undone = undo_unlanded_play(memory, hand)
            self._log("veil_play_undone", turn=memory.turn, card=undone, hand=sorted(hand.elements()))
            if undone is not None:
                self.position_retries = 0
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
        outside = deck - hand
        if data.get("veil_uncertain") or hand - deck:
            if CORE <= hand.keys() or data.get("veil_cycle"):
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
        known_once.update(norm(c.name) for c in state.hand if c.tags.once and norm(c.name) != REBOUND)
        data["veil_known_once"] = sorted(known_once)
        slots = [None] * 5
        for card in state.hand:
            if not 0 <= card.slot < 5 or slots[card.slot] is not None:
                raise ResumeBlocked("VEIL 手牌槽位无法核对")
            slots[card.slot] = norm(card.name)
        state.piles = {**track_piles(data, memory.turn, slots, outside),
                       "next_draw": next_draw, "once": data["veil_known_once"]}
        return state, why


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
            commit_full_power(data, memory, drink, "喝")  # ジンジャーエール 全力値+1、ほうじ茶 +2、厳選初星チャイ 每回合 +1
            return
        if decision.kind != "play":
            return
        card = next(c for c in state.hand if c.slot == decision.slot)
        name = norm(card.name)
        before = stance(state)
        if before is None and memory.turn == 1 and (data.get("veil_position") or {}).get("stance") in (None, NEUTRAL):
            before = NEUTRAL  # 第 1 回合还没切过姿态：切強気照算夜空（原来记成 None、夜空不明）
        current, flower = after_play(before, state.pitem_charges, card)
        level = stance_change_level(card) if destination(card) == current else None
        if current == before and destination(card) != current:
            level = stance_level(state, before)
        if name.startswith(STAR):
            data["veil_star"] = 3
        delayed, delayed_level = next_stance_change(card)
        data["veil_pending_play"] = {
            "turn": memory.turn, "card": name, "count": sum(norm(c.name) == name for c in state.hand),
            "stance": current, "stance_level": level, "flower": flower,
            "next_stance": delayed, "next_stance_level": delayed_level,
        }
        if destination(card) == "強気" and stance(state) in WARM:
            memory.actions_left += 1
        commit_full_power(data, memory, card)
        deck = data["veil_deck"]
        lost = (name in data.get("veil_initial_once", ()) if name == REBOUND else
                card.tags.once or name == "眠気" or name in data.get("veil_known_once", ()))
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
        if name in SLEEP_GENERATORS:
            deck["眠気"] = deck.get("眠気", 0) + 1
            if piles is not None:
                piles["deck"]["眠気"] = piles["deck"].get("眠気", 0) + 1
        elif card.tags.makes_sleep or "生成" in card.text or "変換" in card.text:
            mark_uncertain(data, memory, f"打出会生成 / 变换牌的牌，牌组账本跟不上：{name}")
        # 回忆能力在牌的效果之后结算（「〜使用後」）：再动 / 抽牌 / 删眠気，每项按次数触发后取否
        apply_ability(data, memory, name)


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
        source = VeilScreen(deck, once, drinks, abilities, customs=customs)
        try:
            run_exam(VeilBrain(None, None), exam, rec, ended, session=session, source=source,
                     advisor=VeilAdvisor(), round_no=round_no, strict=True)
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
