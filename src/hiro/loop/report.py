"""从 trace 产出度量报告：考试内截图到点击的耗时分布、LLM 调用次数与 token、故障恢复次数、页面切换、每场考试结果。

    python -m hiro.loop.report traces/<run> [traces/<run2> …] [--out reports/runs/<名>.json]
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from hiro.contracts import Step, read_steps
from hiro.loop.session import RECOVER_KINDS


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, round((len(xs) - 1) * p)))
    return xs[k]


def summarize(steps: list[Step], run_meta: dict | None = None) -> dict:
    decisions = [s for s in steps if s.decision is not None]
    exam_lat = [s.timings_ms["shot_to_tap"] for s in steps if "shot_to_tap" in s.timings_ms]
    decide_s = [s.decision.seconds for s in decisions if s.decision.seconds is not None]
    llm_calls = [c for s in decisions for c in s.decision.llm]
    recov = Counter()
    for s in steps:
        for n in s.notes:
            if n.startswith("recover:"):
                recov[n.split(":")[1]] += 1
    page_changes = sum(1 for a, b in zip(steps, steps[1:]) if a.state.page.name != b.state.page.name)
    exams: dict[str, dict] = {}
    for s in steps:
        label = s.state.exam_session.label
        if s.state.page.name == "HIF_EXAM" and label:
            e = exams.setdefault(label, {"decisions": 0, "turns": 0, "score": None})
            if s.decision is not None:
                e["decisions"] += 1
            e["turns"] = max(e["turns"], s.state.exam_session.turn)
            if s.state.exam and s.state.exam.get("score") is not None:
                e["score"] = s.state.exam["score"]
    execs = [s.execution for s in steps if s.execution is not None]
    return {
        "run": run_meta or {},
        "steps": len(steps),
        "decisions": len(decisions),
        "decisions_by_source": dict(Counter(s.decision.source for s in decisions)),
        "decisions_by_decider": dict(Counter(s.decision.decider or "?" for s in decisions)),
        "page_changes": page_changes,
        "final_page": steps[-1].state.page.name if steps else None,
        "exam_shot_to_tap_ms": {"n": len(exam_lat), "median": _pct(exam_lat, 0.5), "p90": _pct(exam_lat, 0.9), "max": max(exam_lat) if exam_lat else None},
        "decide_seconds": {"n": len(decide_s), "median": _pct(decide_s, 0.5), "p90": _pct(decide_s, 0.9)},
        "llm": {
            "calls": len(llm_calls),
            "errors": sum(1 for c in llm_calls if c.error),
            "usage_limit": sum(1 for c in llm_calls if c.error and c.error.startswith("usage_limit")),
            "input_tokens": sum(c.input_tokens or 0 for c in llm_calls),
            "output_tokens": sum(c.output_tokens or 0 for c in llm_calls),
            "seconds_median": _pct([c.seconds for c in llm_calls], 0.5),
            "adapters": dict(Counter(f"{c.adapter}:{c.model}" for c in llm_calls)),
        },
        "recoveries": {k: recov.get(k, 0) for k in RECOVER_KINDS},
        "execution": {
            "n": len(execs), "ok": sum(1 for e in execs if e.ok), "retries": sum(e.retries for e in execs),
            "unexpected_transition": sum(1 for e in execs if e.expected_transition is False),
        },
        "exams": exams,
    }  # fmt: skip


def report(trace_dir: Path) -> dict:
    meta = {}
    run_json = trace_dir / "run.json"
    if run_json.exists():
        meta = json.loads(run_json.read_text(encoding="utf-8"))
    out = summarize(list(read_steps(trace_dir)), meta)
    out["trace"] = str(trace_dir)
    return out


def markdown(r: dict) -> str:
    lat = r["exam_shot_to_tap_ms"]
    llm = r["llm"]
    lines = [
        f"# 运行报告 {r.get('trace', '')}",
        "",
        f"- 步数 {r['steps']}，决策 {r['decisions']}（来源 {r['decisions_by_source']}；决策器 {r['decisions_by_decider']}）",
        f"- 页面切换 {r['page_changes']} 次，终止页面 {r['final_page']}",
        f"- 考试内截图→点击：n={lat['n']}，中位 {lat['median']} ms，p90 {lat['p90']} ms，最大 {lat['max']} ms",
        f"- LLM：{llm['calls']} 次调用（失败 {llm['errors']}，其中用量上限 {llm.get('usage_limit', 0)}），输入 {llm['input_tokens']} / 输出 {llm['output_tokens']} token，单次中位 {llm['seconds_median']} s，适配器 {llm['adapters']}",
        f"- 故障恢复：{r['recoveries']}",
        f"- 执行：{r['execution']}",
        f"- 考试：{r['exams']}",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rows = [report(t) for t in args.traces]
    out = args.out or Path("reports/runs") / (args.traces[0].name + ("-batch" if len(rows) > 1 else "") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows if len(rows) > 1 else rows[0], ensure_ascii=False, indent=1), encoding="utf-8")
    for r in rows:
        print(markdown(r))
        print()
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
