# 来源：digital-hiro src/hiro/produce/formation_check.py（99092bc）（GPL-3.0-or-later）
"""开局编成核对（hiro.brain.formation_plan 的方案 vs 游戏画面）：对不上就停，不开局。

- 第 2 步「サポート選択」：read_support_formation 读 5 格自己的卡，和方案的 own 比。认出来的卡不在方案里、或全部认出但缺方案里的卡、
  或编成号读出来和方案不同 → 不一致；有格子认不出时只记警告（素材库认不全不该卡住开局）。
- 第 3 步「メモリー選択」：KAA 选完编成后读左侧「N/20」，编成号和方案不同 → 不一致。
  回忆卡本身还核对不了：第 3 步的技能卡小图标用现有卡面库匹配分只有 0.3–0.5（2026-11-06 用 11-04 截图试过），要实机标注帧再做。
"""

from __future__ import annotations

import re


class FormationMismatch(RuntimeError):
    """开局编成和方案对不上：停在培育准备页，不开局。"""


def check_support_step2(read: dict | None, plan: dict) -> tuple[list[str], list[str]]:
    """read：vision.support_screens.read_support_formation 的结果；plan：开局编成方案。返回 (不一致, 警告)。"""
    sup = (plan or {}).get("support") or {}
    own = [o["support_card_id"] for o in sup.get("own") or []]
    problems, warnings = [], []
    if not own:
        return problems, ["方案里没有支援卡，跳过核对"]
    if not read or read.get("view") != "step2":
        return problems, ["没认出第 2 步支援卡页面，跳过核对"]
    if sup.get("set") and read.get("number") and read["number"] != sup["set"]:
        problems.append(f"编成号 {read['number']} 不是方案的 {sup['set']}")
    slots = read.get("slots") or []
    seen = [s.get("support_card_id") for s in slots if s.get("support_card_id")]
    unknown = len(slots) - len(seen)
    extra = [s.get("name") or s["support_card_id"] for s in slots if s.get("support_card_id") and s["support_card_id"] not in own]
    if extra:
        problems.append(f"编成里有方案外的卡：{'、'.join(extra)}")
    names = {o["support_card_id"]: o.get("name") or o["support_card_id"] for o in sup.get("own") or []}
    missing = [names[c] for c in own if c not in seen]
    if missing and unknown == 0:
        problems.append(f"编成里缺方案的卡：{'、'.join(missing)}")
    elif missing:
        warnings.append(f"{unknown} 格认不出，方案里的 {'、'.join(missing)} 没看到（可能就在认不出的格子里）")
    main = plan.get("main_attr")
    if main and unknown == 0 and not problems:
        want = sum(o.get("attr") == main for o in sup.get("own") or [])
        got = sum(s.get("attr") == main for s in slots)
        if got != want:
            problems.append(f"主属性 {main} 有 {got} 张，方案是 {want} 张")
    return problems, warnings


SET_NUMBER = re.compile(r"^(\d{1,2})\s*/\s*20$")


def memory_set_number(texts: list[tuple[str, tuple]] | list[str]) -> int | None:
    """第 3 步 OCR → 左侧编成号「N/20」。texts：[(文字, (x1, y1, x2, y2))] 或纯文字列表（没有坐标时取第一个 N/20）。"""
    for t in texts:
        text, box = (t, None) if isinstance(t, str) else (t[0], t[1])
        m = SET_NUMBER.match(text.replace(" ", ""))
        if m and (box is None or box[0] < 300):  # 右上「親愛度 37/37」不是 /20，这里再限定左半边
            return int(m.group(1))
    return None


def check_memory_step3(number: int | None, plan: dict) -> tuple[list[str], list[str]]:
    mem = (plan or {}).get("memory") or {}
    if not mem.get("set") or not mem.get("cards"):
        return [], ["方案里没有回忆编成号，跳过核对"]
    if number is None:
        return [], ["第 3 步编成号没读出来，跳过核对"]
    if number != mem["set"]:
        return [f"メモリー编成号 {number} 不是方案的 {mem['set']}"], []
    return [], []
