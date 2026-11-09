"""用回合推演器（hiro.brain.play.sim.TurnState）回放 VEIL trace：从每个决策帧记下的局面出发，按这一手推演下一帧，
和下一帧的账本（决策 context）、画面读数（夜空环旁的「N回」、全力値药丸）逐项比。

    PYTHONPATH=src:vendor/kaa .venv/bin/python tools/audit/sim_replay.py traces/batch-…-exam1 [...] [--json 输出.json]

比的项：
- 同一回合内打一张牌：姿态、夜空、全力値、剩余出牌次数（账本）；夜空、全力値（画面，读不出的帧不算）。
- 喝ホエイ：出牌次数 +1；其他饮料不动这几项（ソーダ 只抽牌）。
- 换回合：预约姿态生效、全力値满 10 扣 10、出牌次数回到 1（一番星附魔次数 trace 里没逐帧记，姿态只在账本有预约时比）。
跳过：撤回出牌 / 没落地 / 过期帧之后的那一对、回合内连打中间没有决策帧的牌（下一帧的 played 多出不止一张）。
回忆能力（再动 / 抽牌）trace 里没逐帧记，推演按「没有」算，出牌次数可能少算 1，单列为 ability? 不计入不一致。
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter, defaultdict

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "vendor", "kaa")]

from hiro.brain.cards import card_view, kaa_cards  # noqa: E402
from hiro.brain.play.profile import WHEY  # noqa: E402
from hiro.brain.play.sim import TurnState  # noqa: E402
from hiro.brain.veil import flower_count, full_power_count  # noqa: E402

_master = kaa_cards()
SKIP_KINDS = {"veil_play_undone", "battle_play_not_landed", "exam_stale_frame"}


def _card(row):
    slot, name, cid, up, avail, _once = row
    obj = None
    for u in (([up, up - 1, up - 2] if up is not None else [0, 1, 2]) if cid else []):  # 档位没记：从 0 档起找
        if u >= 0 and (obj := _master.get((cid, u))) is not None:
            break
    v = card_view(slot, obj, available=bool(avail))
    v.name = name
    return v


def _state(c) -> TurnState:
    return TurnState(stance=c.get("stance"), flower=c.get("flower"), acts=c.get("actions_left") or 1, fp=c.get("fp"),
                     level=c.get("stance_level"), next_stance=c.get("next_stance"), turn=c.get("turn") or 0)


def _action(contract):
    a = contract["action"]
    if a["kind"] == "PlayCard":
        return "play", a["args"].get("slot")
    if a["kind"] == "UseDrink":
        return "drink", a["args"].get("slot")
    return "skip", None


def replay(trace):
    ev = sorted(glob.glob(os.path.join(trace, "battle", "*", "events.jsonl")))
    if not ev:
        return None
    base = os.path.dirname(ev[0])
    rows = [json.loads(line) for line in open(ev[0])]
    stats = defaultdict(Counter)
    bad = []
    prev = None  # (event, predicted TurnState or None, label)
    for d in rows:
        if d["kind"] in SKIP_KINDS:
            prev = None
            continue
        if d["kind"] != "battle_decision" or "hand" not in (d.get("context") or {}):
            continue
        c = d["context"]
        if prev is not None:
            p_ev, pred, label = prev
            pc = p_ev["context"]
            same_turn = pc["turn"] == c["turn"]
            grew = len(c.get("played") or []) - len(pc.get("played") or [])
            if pred is not None and ((same_turn and grew == (label == "play")) or not same_turn):
                if not same_turn:
                    pred = pred.new_turn() if c["turn"] == pc["turn"] + 1 else None
                if pred is not None:
                    _compare(stats, bad, d, c, pred, label, same_turn, base)
        kind, slot = _action(d["contract"])
        ts = _state(c)
        if kind == "play":
            row = next((h for h in c["hand"] if h[0] == slot), None)
            prev = (d, ts.step(_card(row)) if row else None, "play")
        elif kind == "drink":
            name = (c.get("drinks") or [None] * (slot + 1))[slot] if slot is not None and slot < len(c.get("drinks") or []) else None
            prev = (d, ts.drink() if name == WHEY else ts, "drink")
        else:
            prev = (d, ts, "skip")
    return stats, bad


def _compare(stats, bad, d, c, pred, label, same_turn, base):
    where = "turn" if same_turn else "new_turn"
    checks = [("flower", pred.flower, c.get("flower"))]
    if pred.fp is not None and c.get("fp") is not None:
        checks.append(("fp", pred.fp, c.get("fp")))
    if same_turn:
        checks.append(("stance", pred.stance, c.get("stance")))
        if label != "skip":
            checks.append(("acts", pred.acts, c.get("actions_left")))
    elif pred.stance is not None and c.get("stance") is not None and pred.next_stance is None:
        pass  # 回合开始还可能有一番星切のんびり（次数没逐帧记），姿态不比
    for key, want, got in checks:
        if want is None or got is None:
            stats[f"账本·{key}"]["unknown"] += 1
            continue
        ok = want == got
        if key == "acts" and want == got - 1:
            ok = "ability?"  # 回忆能力（再动）trace 里没记，推演少算 1
        stats[f"账本·{key}"][ok if isinstance(ok, str) else ("ok" if ok else "bad")] += 1
        if ok is False:
            bad.append({"seq": d["seq"], "where": where, "item": f"账本·{key}", "sim": want, "ledger": got,
                        "turn": c.get("turn")})
    img = cv2.imread(os.path.join(base, d["frame"])) if d.get("frame") else None
    for key, want, read in (("flower", pred.flower, flower_count), ("fp", pred.fp, full_power_count)):
        seen = read(img) if img is not None else None
        if want is None or seen is None:
            stats[f"画面·{key}"]["unread"] += 1
            continue
        stats[f"画面·{key}"]["ok" if want == seen else "bad"] += 1
        if want != seen:
            bad.append({"seq": d["seq"], "where": where, "item": f"画面·{key}", "sim": want, "screen": seen,
                        "turn": c.get("turn")})


def chain_fp(trace):
    """连续推演全力値：从开考第一帧的账本值出发，只按出牌 / 饮料 / 换回合推演（不再用账本重置），每帧和画面「★ N」比。
    没落地 / 撤回的那一手按出牌前的值恢复。返回 Counter(ok / bad / unread) 和不一致的帧。"""
    from types import SimpleNamespace

    from hiro.brain.cards import description_text, select
    from hiro.brain.play.sim import fp_apply, fp_next_turn
    from hiro.produce.veil_exam import _fp_record

    ev = sorted(glob.glob(os.path.join(trace, "battle", "*", "events.jsonl")))
    if not ev:
        return None
    base = os.path.dirname(ev[0])
    stats, bad = Counter(), []
    fp = boosts = per_turn = turn = None
    snap = None
    for line in open(ev[0]):
        d = json.loads(line)
        if d["kind"] in ("veil_play_undone", "battle_play_not_landed") and snap is not None:
            fp, boosts, per_turn = snap
            snap = None
            continue
        if d["kind"] != "battle_decision" or "hand" not in (d.get("context") or {}):
            continue
        c = d["context"]
        if fp is None:
            if c.get("fp") is None:
                continue
            fp, boosts, per_turn, turn = c["fp"], [], 0, c["turn"]
        while turn < c["turn"]:
            turn += 1
            fp, boosts, _ = fp_next_turn(fp, boosts, per_turn, turn)
        img = cv2.imread(os.path.join(base, d["frame"])) if d.get("frame") else None
        seen = full_power_count(img) if img is not None else None
        if seen is None:
            stats["unread"] += 1
        else:
            stats["ok" if seen == fp else "bad"] += 1
            if seen != fp:
                bad.append({"seq": d["seq"], "turn": c["turn"], "sim": fp, "screen": seen})
        kind, slot = _action(d["contract"])
        rec = None
        if kind == "play":
            row = next((h for h in c["hand"] if h[0] == slot), None)
            rec = _fp_record(_card(row), c.get("stance")) if row else None
        elif kind == "drink" and slot is not None and slot < len(c.get("drinks") or []):
            row = select("SELECT produceDescriptions FROM ProduceDrink WHERE name = ?;", c["drinks"][slot])
            rec = _fp_record(SimpleNamespace(text=description_text(row[0]))) if row else None
        if rec is not None:
            snap = (fp, list(boosts), per_turn)
            fp, boosts, _, _ = fp_apply(fp, boosts, turn, rec)
            per_turn += rec.get("per_turn") or 0
    return stats, bad


def main(argv):
    out = None
    if "--json" in argv:
        i = argv.index("--json")
        out, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    total = defaultdict(Counter)
    report = {}
    for t in argv:
        r = replay(t)
        if r is None:
            continue
        stats, bad = r
        chained = chain_fp(t)
        if chained is not None:
            stats["连续推演·fp"] = chained[0]
            bad = bad + [{**b, "item": "连续推演·fp"} for b in chained[1]]
        for k, v in stats.items():
            total[k].update(v)
        report[os.path.basename(t)] = {"stats": {k: dict(v) for k, v in stats.items()}, "bad": bad}
        line = "  ".join(f"{k} {v.get('ok', 0)}/{v.get('ok', 0) + v.get('bad', 0)}" for k, v in sorted(stats.items()))
        print(f"{os.path.basename(t)[6:]}: {line}" + (f"  不一致 {len(bad)}" if bad else ""))
    print("合计：" + "  ".join(f"{k} {v.get('ok', 0)}/{v.get('ok', 0) + v.get('bad', 0)}"
                              + (f"（另 ability? {v['ability?']}）" if v.get("ability?") else "")
                              for k, v in sorted(total.items())))
    if out:
        json.dump({"total": {k: dict(v) for k, v in total.items()}, "traces": report}, open(out, "w"),
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
