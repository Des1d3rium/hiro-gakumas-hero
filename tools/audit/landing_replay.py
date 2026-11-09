"""出牌落地核对（hiro.exec.landing，按卡图认牌）离线回放。

trace 里每次决策存一帧：出牌那一步的帧 = 点之前，下一步的帧 = 结算稳定后（同一回合内）。对每对帧跑 classify，和实际结果比：
下一步备注里有 veil_play_undone / veil_play_unverified，或这一步备注里有「没打出」= 实际没打出，否则 = 打出了。连打（cycle_tap，同一帧打几张）和跨回合的跳过。

用法：PYTHONPATH=src:vendor/kaa .venv/bin/python tools/audit/landing_replay.py traces/batch-codex-20261109-1-*-exam*
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def pairs(trace: Path):
    rows = [json.loads(line) for line in (trace / "steps.jsonl").open(encoding="utf-8")]
    by_seq: dict[int, list] = {}
    for r in rows:
        by_seq.setdefault(r["seq"], []).append(r)
    seqs = sorted(by_seq)
    for a, b in zip(seqs, seqs[1:]):
        ra, rb = by_seq[a], by_seq[b]
        if len(ra) != 1:
            continue  # 连打：同一帧打了几张
        act = ((ra[0].get("decision") or {}).get("action") or {})
        if act.get("kind") != "PlayCard":
            continue
        oa, ob = ra[0]["observation"], rb[0]["observation"]
        if not oa.get("screenshot") or not ob.get("screenshot"):
            continue
        if (oa.get("fields") or {}).get("remaining_turns") != (ob.get("fields") or {}).get("remaining_turns"):
            continue
        # 实际没打出：下一步撤回 / 没核对上（veil_play_undone / veil_play_unverified），或这一步执行层自己认出没打出
        undone = (any(w in n for n in (rb[0].get("notes") or []) for w in ("veil_play_undone", "veil_play_unverified"))
                  or any("没打出" in n for n in (ra[0].get("notes") or [])))
        yield a, act["args"]["slot"], trace / oa["screenshot"], trace / ob["screenshot"], ("none" if undone else "played")


def main(args: list[str]) -> int:
    import cv2

    from hiro.context import ensure_offline_context
    from hiro.exec import landing

    ensure_offline_context()
    table: Counter = Counter()
    wrong = []
    for arg in args:
        trace = Path(arg)
        if not (trace / "steps.jsonl").exists():
            continue
        for seq, slot, fa, fb, truth in pairs(trace):
            h0, h1 = landing.read_ids(cv2.imread(str(fa))), landing.read_ids(cv2.imread(str(fb)))
            state, why = landing.classify(h0, h1, slot)
            table[(truth, state)] += 1
            if (truth, state) in (("played", "none"), ("none", "played")):
                wrong.append({"trace": trace.name, "seq": seq, "slot": slot, "truth": truth, "state": state, "why": why,
                              "before": h0.ids if h0 else None, "after": h1.ids if h1 else None})
            elif truth == "none" or state in ("unclear", "moving"):
                print(json.dumps({"trace": trace.name, "seq": seq, "truth": truth, "state": state, "why": why}, ensure_ascii=False))
    print(json.dumps({f"{t}→{s}": n for (t, s), n in sorted(table.items())}, ensure_ascii=False))
    for w in wrong:
        print("判反：", json.dumps(w, ensure_ascii=False))
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
