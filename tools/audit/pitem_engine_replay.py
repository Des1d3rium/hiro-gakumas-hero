"""通用 P 道具引擎（VEIL_PITEM_ENGINE）对姿态账本的影响：按 trace 逐手回放，比较「原账本 / 引擎改过的账本」和画面姿态。

每个出牌决策帧：打出前姿态 = 本帧 context.stance（账本），原账本出牌后姿态 = 同一回合下一个决策帧的 context.stance；
引擎姿态 = pitem_engine.after_play(…) 在原账本上再结算持有道具的附魔。换回合时再走 end_turn / start_turn。
画面姿态取下一帧观测的状态栏（沿用回合开头读数的帧不算）。道具按第一帧画面道具列 + 偶像档案 pitem_assets 装；
--hint 图标=道具id 可以换成别的版本看看（例：--hint img_general_pitem_3-258=pitem_00-3-258-0 = 「温存に変更」那版 ゼミパーカー）。

用法：PYTHONPATH=src:vendor/kaa .venv/bin/python tools/audit/pitem_engine_replay.py [traces/batch-…-exam1 …] [--hint 图标=id]
不给 trace 时跑 traces/batch-codex-*-exam*。只读 traces/。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter

STANCE_NAMES = ("温存", "強気", "全力", "のんびり", "指針解除")


def _ctx(row):
    for n in row.get("notes") or []:
        if n.startswith("context "):
            try:
                return json.loads(n[8:])
            except ValueError:
                return {}
    return {}


def _screen(row, ctx):
    if "statuses" in (ctx.get("carried") or []):
        return None
    f = (row.get("observation") or {}).get("fields") or {}
    names = [s.get("name") for s in f.get("statuses") or [] if s.get("name") in STANCE_NAMES]
    return names[0] if len(names) == 1 else None


def _card(h):
    from hiro.brain import card_fx
    from hiro.brain.cards import CardView, parse_tags

    slot, name, cid, up = h[0], h[1], h[2], h[3] or 0
    fx = card_fx.card_fx(cid, up) if cid else None
    return CardView(slot, name, cid, up, True, "active", 0, 0, None, "", parse_tags(name, "", once=bool(h[5])), fx=fx)


def replay(trace, hints, stats):
    from hiro.brain import pitem_engine as E
    from hiro.brain.state import BattleState, ExamMemory
    from hiro.brain.veil import IDOL

    path = os.path.join(trace, "steps.jsonl")
    if not os.path.exists(path):
        return
    rows = [json.loads(l) for l in open(path)]
    dec = [r for r in rows if (r.get("decision") or {}).get("action") and _ctx(r).get("turn") is not None]
    if not dec:
        return
    data = {}
    first = next((((r.get("observation") or {}).get("fields") or {}).get("pitems") for r in rows
                  if ((r.get("observation") or {}).get("fields") or {}).get("pitems")), None)
    st = E.observe(data, first or [], IDOL, hints)
    stats["traces"] += 1
    stats["installed:" + ("+".join(sorted(e["name"] for e in st["items"])) or "none")] += 1
    for u in st["unsure"]:
        stats["unsure:" + (u.get("name") or u.get("asset"))] += 1
    mem = ExamMemory("r1", turn=_ctx(dec[0])["turn"])
    override = None  # (turn, stance)：引擎改过、本回合后续帧账本应是这个
    prev_turn = None
    for a, b in zip(dec, dec[1:] + [None]):
        ca = _ctx(a)
        f = a["observation"]["fields"]
        state = BattleState("r1", ca["turn"], f.get("remaining_turns"), f.get("hp"), None, 0, [], statuses=[],
                            pitem_charges=None)
        state.genki = f.get("genki")
        mem.turn = ca["turn"]
        ledger = ca.get("stance")
        if prev_turn is not None and ca["turn"] != prev_turn:
            override = None
            ended, _ = E.end_turn(data, mem, state, prev_turn, ledger, ca.get("stance_level"))
            new, _, changed = E.start_turn(data, mem, state, ledger, ca.get("stance_level"))
            if changed or ended != ledger:
                stats["boundary_changed"] += 1
        elif prev_turn is None:
            E.start_turn(data, mem, state, ledger, ca.get("stance_level"))
        prev_turn = ca["turn"]
        engine_now = override[1] if override and override[0] == ca["turn"] else ledger
        seen = _screen(a, ca)
        if seen is not None and ledger is not None:
            stats["frames"] += 1
            stats["old_bad"] += seen != ledger
            stats["new_bad"] += seen != engine_now
        act = a["decision"]["action"]
        if act["kind"] != "PlayCard" or b is None:
            continue
        cb = _ctx(b)
        if cb.get("turn") != ca["turn"]:
            continue
        h = next((h for h in ca.get("hand") or [] if h[0] == act["args"]["slot"]), None)
        if h is None or not h[2]:
            continue
        before = engine_now
        after_old = cb.get("stance")
        if override and override[0] == ca["turn"]:
            # 引擎改过本回合姿态：这张牌不改姿态时延续引擎的，改姿态时以牌为准
            from hiro.brain.veil import destination

            card = _card(h)
            after_old = destination(card) or override[1]
        mem.actions_left = 1
        fired_before = sum(st["fired"].values())
        new, _ = E.after_play(data, mem, state, _card(h), before, after_old, cb.get("stance_level"))
        stats["plays"] += 1
        stats["fired_on_play"] += sum(st["fired"].values()) - fired_before
        if new != after_old:
            stats["play_changed"] += 1
            override = (ca["turn"], new)
        elif override and override[0] == ca["turn"]:
            override = (ca["turn"], new)
    for x in st["log"]:
        if x["what"] == "发动":
            stats[f"fired:{x['name']}:{x['phase']}"] += 1
    unknown = sum(1 for x in st["log"] if "判断不了" in x["what"] or "不知道" in x["what"])
    stats["unknown"] += unknown


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("traces", nargs="*")
    ap.add_argument("--hint", action="append", default=[], help="图标=道具id，覆盖偶像档案 pitem_assets")
    args = ap.parse_args(argv)
    from hiro.brain import pitem_engine as E

    hints = E.profile_hints()
    for h in args.hint:
        k, v = h.split("=", 1)
        hints[E._asset_id(k)] = v
    traces = args.traces or sorted(glob.glob("traces/batch-codex-*-exam*"))
    stats = Counter()
    for t in traces:
        replay(t, hints, stats)
    for k in sorted(stats):
        print(f"{k}: {stats[k]}")


if __name__ == "__main__":
    sys.exit(main())
