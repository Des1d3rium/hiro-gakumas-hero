"""抽牌概率校准：用 VEIL trace 检验「牌堆账 + hiro.brain.play.draws」对下回合开局抽牌的预测。

    PYTHONPATH=src .venv/bin/python tools/audit/draw_calibration.py traces/batch-…-exam1 [...] [--json 输出.json]

对每个回合交界：取第 T 回合最后一个决策帧的牌堆账（context.piles）、手牌和这一手，推出回合末进捨札的手牌；
对牌堆里每个卡名算「下回合开局在手里」的概率区间，和第 T+1 回合第一个决策帧的手牌比。

输出：
- 校准表：按预测概率分档，每档实际抽到的比例（准的话两者接近）；
- Brier 分数（越小越好），和「不管洗牌、按全部牌均匀抽」的朴素估计对照；
- 不可能事件：预测 0% 却抽到了 / 预测 100% 却没抽到（多半是牌堆账错了，列出来查）。
trace 里没记暗抽张数（ghost），按 0 算；区间宽度单列。
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "src")]

from hiro.brain.play.draws import next_turn  # noqa: E402

BINS = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001]


def _piles(c):
    p = c.get("piles")
    if not p:
        return None
    lst = lambda d: [n for n, k in (d or {}).items() for _ in range(k)]  # noqa: E731
    return {"deck": lst(p.get("deck")), "grave": lst(p.get("grave")), "unlocated": lst(p.get("unlocated")),
            "doubt": lst(p.get("doubt")), "next_draw": c.get("next_draw")}


def frames(trace):
    ev = sorted(glob.glob(os.path.join(trace, "battle", "*", "events.jsonl")))
    if not ev:
        return []
    out = []
    for line in open(ev[0]):
        d = json.loads(line)
        if d.get("kind") == "battle_decision" and "hand" in (d.get("context") or {}):
            out.append(d)
    return out


def calibrate(trace):
    rows, impossible = [], []
    fs = frames(trace)
    by_turn = defaultdict(list)
    for d in fs:
        by_turn[d["context"]["turn"]].append(d)
    for turn in sorted(by_turn):
        nxt = by_turn.get(turn + 1)
        if not nxt:
            continue
        last, first = by_turn[turn][-1], nxt[0]
        c = last["context"]
        piles = _piles(c)
        if piles is None or piles["next_draw"] is None:
            continue
        hand = {h[0]: h for h in c["hand"]}
        a = last["contract"]["action"]
        end = dict(hand)
        if a["kind"] == "PlayCard":
            played = end.pop(a["args"].get("slot"), None)
            if played is not None and not played[5]:
                piles["grave"].append(played[1])  # 打出的复用牌进捨札；一次性的离开牌库
        hand_end = [h[1] for h in end.values()]
        got = Counter(h[1] for h in first["context"]["hand"])
        pool = set(piles["deck"]) | set(piles["grave"]) | set(piles["unlocated"]) | set(hand_end)
        size = len(piles["deck"]) + len(piles["grave"]) + len(piles["unlocated"]) + len(hand_end)
        n = int(piles["next_draw"])
        for name in sorted(pool):
            odds = next_turn(piles, hand_end, [name], draws=n)
            k = sum(1 for x in piles["deck"] + piles["grave"] + piles["unlocated"] + hand_end if x == name)
            naive = 1.0 - _naive_miss(size, k, n)
            hit = got[name] > 0
            rows.append({"turn": turn, "name": name, "lo": odds.lo, "hi": odds.hi, "naive": naive, "hit": hit})
            if (odds.hi == 0.0 and hit) or (odds.lo == 1.0 and not hit):
                impossible.append({"seq": last["seq"], "turn": turn, "name": name, "lo": odds.lo, "hi": odds.hi, "hit": hit,
                                   "deck": piles["deck"], "grave": piles["grave"], "unlocated": piles["unlocated"],
                                   "hand_end": hand_end, "next_hand": sorted(got.elements()), "next_draw": n})
    return rows, impossible


def _naive_miss(size, k, n):
    import math

    n = min(n, size)
    if k <= 0:
        return 1.0
    return math.comb(size - k, n) / math.comb(size, n) if size - k >= n else 0.0


def report(rows):
    bins = defaultdict(lambda: [0, 0, 0.0])
    brier = naive = 0.0
    wide = 0
    for r in rows:
        p = (r["lo"] + r["hi"]) / 2
        wide += r["hi"] - r["lo"] > 0.005
        for i in range(len(BINS) - 1):
            if BINS[i] <= p < BINS[i + 1]:
                b = bins[i]
                b[0] += 1
                b[1] += r["hit"]
                b[2] += p
        brier += (p - r["hit"]) ** 2
        naive += (r["naive"] - r["hit"]) ** 2
    n = max(1, len(rows))
    lines = [f"样本 {len(rows)}（区间不唯一 {wide}）  Brier {brier / n:.3f}  朴素 {naive / n:.3f}"]
    for i in sorted(bins):
        cnt, hits, ps = bins[i]
        lines.append(f"  预测 {BINS[i]:.0%}–{min(BINS[i + 1], 1):.0%}：{cnt:4d} 个，平均预测 {ps / cnt:.0%}，实际 {hits / cnt:.0%}")
    return "\n".join(lines)


def main(argv):
    out = None
    if "--json" in argv:
        i = argv.index("--json")
        out, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    all_rows, all_bad, per = [], [], {}
    for t in argv:
        rows, bad = calibrate(t)
        if not rows:
            continue
        all_rows += rows
        all_bad += [{**b, "trace": os.path.basename(t)} for b in bad]
        per[os.path.basename(t)] = {"rows": len(rows), "impossible": len(bad)}
    print(report(all_rows))
    print(f"不可能事件 {len(all_bad)}：")
    for b in all_bad[:15]:
        print(f"  {b['trace'][6:]} seq {b['seq']} T{b['turn']} {b['name']} 预测 {b['lo']:.0%}–{b['hi']:.0%} 实际{'抽到' if b['hit'] else '没抽到'}")
    if out:
        json.dump({"per_trace": per, "impossible": all_bad, "rows": all_rows}, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
