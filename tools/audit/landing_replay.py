"""出牌落地核对（hiro.exec.landing，按卡图认牌）离线回放。

trace 里每次决策存一帧：出牌那一步的帧 = 点之前，下一步的帧 = 结算稳定后（同一回合内）。对每对帧跑 classify，和实际结果比：
下一步备注里有 veil_play_undone / veil_play_unverified，或这一步备注里有「没打出」= 实际没打出，否则 = 打出了。连打（cycle_tap，同一帧打几张）和跨回合的跳过。

用法：PYTHONPATH=src:vendor/kaa .venv/bin/python tools/audit/landing_replay.py traces/batch-codex-20261109-1-*-exam* [--jobs N] [--no-cache | --cache-only]

多份 trace 按进程并行；每帧的认牌结果（read_ids）缓存在 cache/replay/landing.json，第二次起不读图，
改了认牌代码（landing / hif / card_art）或卡图索引就自动重读（见 replay_common.py）。
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_common as RC  # noqa: E402

CACHE_NAME = "landing"
VERSION_SOURCES = ["src/hiro/exec/landing.py", "src/hiro/vision/hif.py", "src/hiro/vision/card_art.py", "cache/card_art_index.npz"]


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


_CACHE: RC.FrameCache | None = None
_READY = False


def _read_frame(frame: Path):
    """读一帧的整手牌卡图身份（JSON 化的 HandIds，读不到 None）。第一次真读图时才初始化离线上下文。"""
    global _READY
    import cv2

    from hiro.exec import landing

    if not _READY:
        from hiro.context import ensure_offline_context

        ensure_offline_context()
        _READY = True
    h = landing.read_ids(cv2.imread(str(frame)))
    return dataclasses.asdict(h) if h else None


def _hand(d):
    from hiro.exec.landing import HandIds

    return HandIds(**d) if d else None


def replay_trace(trace: str, version: str, mode: str) -> dict:
    """一份 trace：[(seq, slot, truth, state, why, before, after)] + 本进程新读的缓存项 + 命中统计。"""
    global _CACHE
    from hiro.exec import landing

    if _CACHE is None:
        _CACHE = RC.FrameCache(CACHE_NAME, version, mode=mode)
    _CACHE.new, _CACHE.hits, _CACHE.misses = {}, 0, 0
    out = []
    t = Path(trace)
    if (t / "steps.jsonl").exists():
        for seq, slot, fa, fb, truth in pairs(t):
            h0, h1 = _hand(_CACHE.get(fa, _read_frame)), _hand(_CACHE.get(fb, _read_frame))
            state, why = landing.classify(h0, h1, slot)
            out.append((seq, slot, truth, state, why, h0.ids if h0 else None, h1.ids if h1 else None))
    return {"trace": t.name, "rows": out, "new": _CACHE.new, "hits": _CACHE.hits, "misses": _CACHE.misses}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("traces", nargs="+")
    RC.add_args(ap)
    args = ap.parse_args(argv)
    version, mode = RC.code_version(VERSION_SOURCES), RC.cache_mode(args)
    results = RC.run_traces(functools.partial(replay_trace, version=version, mode=mode), args.traces, args.jobs)

    cache = RC.FrameCache(CACHE_NAME, version, mode=mode)
    table: Counter = Counter()
    wrong = []
    hits = misses = 0
    for r in results:
        cache.merge(r["new"])
        hits, misses = hits + r["hits"], misses + r["misses"]
        for seq, slot, truth, state, why, before, after in r["rows"]:
            table[(truth, state)] += 1
            if (truth, state) in (("played", "none"), ("none", "played")):
                wrong.append({"trace": r["trace"], "seq": seq, "slot": slot, "truth": truth, "state": state, "why": why,
                              "before": before, "after": after})
            elif truth == "none" or state in ("unclear", "moving"):
                print(json.dumps({"trace": r["trace"], "seq": seq, "truth": truth, "state": state, "why": why}, ensure_ascii=False))
    cache.save()
    print(json.dumps({f"{t}→{s}": n for (t, s), n in sorted(table.items())}, ensure_ascii=False))
    for w in wrong:
        print("判反：", json.dumps(w, ensure_ascii=False))
    print(f"读图缓存：命中 {hits} 帧，{'未读' if mode == 'ro' else '新读'} {misses} 帧", file=sys.stderr)
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
