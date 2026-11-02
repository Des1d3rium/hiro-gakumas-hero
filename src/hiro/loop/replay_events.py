"""H1 验收回放：python -m hiro.loop.replay_events <trace 基名>…

按 -rN 段接起一局，逐步走主循环同一路径（reduce_checked → 由已执行决策推出事件 → apply_event），统计：
每步的事件 / 互验数、按 verdict 计数、全部 read_error（字段、读数、规则），以及决策点看到的状态里是否还残留被拦的读数。
旧 trace（契约 1.1，没有 Step.events）由决策推出事件。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from hiro.contracts import GameState, Step
from hiro.loop.events import apply_event, events_from_step, reduce_checked, settle


def segments(base: str, root: Path = Path("traces")) -> list[Path]:
    return sorted((d for d in root.glob(base + "*") if re.fullmatch(re.escape(base) + r"(-r\d+)?", d.name) and (d / "steps.jsonl").exists()),
                  key=lambda d: int(re.search(r"-r(\d+)$", d.name).group(1)) if re.search(r"-r(\d+)$", d.name) else 0)  # fmt: skip


def replay(base: str, root: Path = Path("traces")) -> dict:
    s, preds = GameState(), []
    verdicts: Counter = Counter()
    read_errors, steps, with_events, with_checks = [], 0, 0, 0
    for seg in segments(base, root):
        for line in (seg / "steps.jsonl").open(encoding="utf-8"):
            if not line.strip():
                continue
            st = Step.from_json(line)
            steps += 1
            step_checks = 0
            for o in [*st.observations_before, st.observation, *st.observations_after]:
                s, cs = reduce_checked(s, o, preds)
                preds = settle(preds, cs)
                step_checks += len(cs)
                for c in cs:
                    verdicts[c.verdict] += 1
                    if c.verdict == "read_error":
                        read_errors.append({"trace": seg.name, "seq": o.seq, "field": c.field, "observed": c.observed, "rule": c.rule})
            evs = list(st.events)
            if not evs and st.decision is not None and st.execution is not None:
                evs = events_from_step(st.observation.page.name, st.decision.action.to_dict(), bool(st.execution.ok), st.state.page_fields,
                                       st.state.produce, seq=st.seq)  # fmt: skip
            for ev in evs:
                s, p = apply_event(s, ev)
                preds.extend(p)
            with_events += bool(evs)
            with_checks += bool(step_checks)
    return {"base": base, "steps": steps, "steps_with_events": with_events, "steps_with_checks": with_checks, "verdicts": dict(verdicts),
            "read_errors": read_errors}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bases", nargs="+")
    ap.add_argument("--out", type=Path, default=Path("reports/replay/h1-events.json"))
    args = ap.parse_args()
    rows = [replay(b) for b in args.bases]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
    for r in rows:
        print(f"{r['base']}: steps {r['steps']}，有事件 {r['steps_with_events']}，有互验 {r['steps_with_checks']}，{r['verdicts']}")
        for e in r["read_errors"]:
            print(f"   read_error {e['trace']} #{e['seq']} {e['field']}={e['observed']!r}（{e['rule']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
