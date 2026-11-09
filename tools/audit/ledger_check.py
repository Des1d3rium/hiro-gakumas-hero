"""账本 / 回忆按主数据模块核对：每张牌查得到卡 id 和结构化效果（card_fx），一次性和主数据一致，
客制化项在主数据里有、追加效果能对上效果 id（否则考试里这张牌退回文字读法），P 道具能编译。

用法：PYTHONPATH=src:vendor/kaa .venv/bin/python tools/audit/ledger_check.py [回忆名或批次 ID …]
不给参数时核对 reports/memories/ 下全部回忆。只读，不改账本。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa"), str(ROOT / "tools")]

LIFT = "レッスン中1回の制限削除"


def _nfkc(text: str) -> str:
    import unicodedata

    return "".join(unicodedata.normalize("NFKC", text).split())


def card_row(name: str):
    """账本卡名是 NFKC 规整过的（「輝け!+」），主数据是全角（「輝け！+」）：两边都规整后比。"""
    from hiro.state.catalog import catalog

    db = catalog().db
    row = db.execute("SELECT id, upgradeCount, playMovePositionType FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    if row is not None:
        return row
    want = _nfkc(name)
    for r in db.execute("SELECT id, upgradeCount, playMovePositionType, name FROM ProduceCard"):
        if _nfkc(r[3] or "") == want:
            return r[:3]
    return None


def check_cards(cards: list[dict]) -> list[str]:
    """cards = [{name, once, customs}]。返回问题列表。"""
    from hiro.brain import card_fx
    from hiro.produce.deck_customs import master_items
    from hiro.produce.veil_exam import custom_effect_ids

    problems = []
    for c in cards:
        name, customs = c["name"], list(c.get("customs") or [])
        row = card_row(name)
        if row is None:
            problems.append(f"{name}：主数据查不到")
            continue
        fx = card_fx.card_fx(row[0], row[1])
        if fx is None:
            problems.append(f"{name}：没有结构化效果（card_fx）")
        elif fx.get("unknown"):
            problems.append(f"{name}：结构化效果里有没认的类型 {fx['unknown']}（只记、不影响）")
        master_once = "Lost" in str(row[2])
        want_once = master_once and LIFT not in customs
        if c.get("once") is not None and bool(c["once"]) != want_once:
            problems.append(f"{name}：账本一次性={c['once']}，主数据 + 客制化应为 {want_once}")
        if not customs:
            continue
        from hiro.produce.deck_customs import NUMERIC

        items = {}
        for it in master_items(name):
            for lv in it:
                items[lv["desc"]] = lv
                if lv["type"] in NUMERIC and lv["value"]:
                    items[NUMERIC[lv["type"]][1].format(v=lv["value"])] = lv
        index = custom_effect_ids(name)
        for item in customs:
            hit = items.get(item)
            if hit is None and item != LIFT:
                problems.append(f"{name}：客制化「{item}」主数据里没有这项")
                continue
            for text in (hit or {}).get("texts") or []:
                if text not in index:
                    problems.append(f"{name}：客制化「{item}」的追加效果「{text}」对不上效果 id（考试里退回文字读法）")
    return problems


def memory_cards(memory: dict) -> list[dict]:
    import batch_ledger as L

    led = L.new_ledger({**memory, "evidence": memory.get("evidence") or "audit"})
    return led["deck"]


def ledger_cards(batch: dict) -> list[tuple[str, list[dict]]]:
    out = []
    for r in batch.get("completed", []) + ([batch["active"]] if batch.get("active") else []):
        led = r.get("ledger")
        if led and led.get("deck"):
            out.append((f"第 {r.get('run')} 局", led["deck"]))
    return out


def check_pitems() -> list[str]:
    from hiro.brain import pitems as P

    problems = []
    for name in ("夜空を従える花+", "夜空を従える花"):
        for item in P.find_pitems(name):
            if item.missing or any(e.missing for e in item.enchants):
                problems.append(f"P 道具 {name}（{item.id}）引用断链：{item.missing}")
            if not item.play_counters:
                problems.append(f"P 道具 {name}（{item.id}）没有「每 N 张」计数触发")
    found = P.card_start_turn_enchant("星々を見下ろす一番星")
    if found is None:
        problems.append("一番星：主数据里找不到「回合开始」附魔")
    return problems


def main(args: list[str]) -> int:
    targets = args or [p.stem for p in sorted((ROOT / "reports" / "memories").glob("*.json"))]
    bad = 0
    for t in targets:
        mem = ROOT / "reports" / "memories" / f"{t}.json"
        batch = ROOT / "reports" / "batches" / f"{t}.json"
        if mem.exists():
            groups = [("回忆", memory_cards(json.loads(mem.read_text())))]
        elif batch.exists():
            groups = ledger_cards(json.loads(batch.read_text()))
        else:
            print(f"{t}：找不到回忆或批次")
            bad += 1
            continue
        for label, cards in groups:
            problems = check_cards(cards)
            bad += bool(problems)
            print(f"{t} {label}：{len(cards)} 张，" + ("全部对上" if not problems else f"{len(problems)} 处问题"))
            for p in problems:
                print("  -", p)
    pi = check_pitems()
    print("P 道具 / 附魔：" + ("全部对上" if not pi else ""))
    for p in pi:
        print("  -", p)
    return 1 if bad or pi else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
