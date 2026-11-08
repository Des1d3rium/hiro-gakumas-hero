"""可信 trace 自检（使用者 2026-11-07）：整段考试 trace 逐步核对决策局面能不能接上，结论写进 <trace>/trust.json。

    PYTHONPATH=src python tools/audit/trace_trust.py traces/<run> [<run> ...]

可信的条件：
- run.json 有 config（开关、提交号），且代码没有未提交的改动；
- 每个 VEIL 决策步的 context 带牌面（hand / piles / deck，2026-11-07 起才记）；
- 每步牌守恒：手牌 + 牌堆 + 弃牌堆 + 位置不明 == 牌组账本；
- 账本可信（uncertain=false）、没有位置不明的牌、本回合没撤回过出牌；
- 跨回合：新回合抽到的牌 ⊆ 上一回合末的牌堆；抽牌数大于牌堆剩余时按洗牌规则——牌堆先全部抽完，
  再把弃牌堆（含上回合末的手牌）洗进牌堆，多出来的牌只能来自弃牌堆。
不可信的步骤逐条列原因；整段都过才 trusted=true。旧 trace 没有牌面，判为不可信（只当参考）。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def _contexts(trace: Path):
    for line in (trace / "steps.jsonl").open(encoding="utf-8"):
        if not line.strip():
            continue
        s = json.loads(line)
        note = next((n for n in s.get("notes") or [] if n.startswith("context ")), None)
        if note and ((s.get("decision") or {}).get("decider") == "veil"):
            yield s["seq"], json.loads(note[8:])


def check(trace: Path) -> dict:
    trace = Path(trace)
    run = json.loads((trace / "run.json").read_text(encoding="utf-8")) if (trace / "run.json").exists() else {}
    problems = []
    configs = [run.get("config"), *[seg.get("config") for seg in run.get("segments") or []]]
    if any(c is None for c in configs):
        problems.append({"seq": None, "why": "run.json 没记开关 / 提交号（2026-11-07 之前的 trace）"})
    elif any(c.get("dirty") for c in configs):
        problems.append({"seq": None, "why": "运行时代码有未提交的改动"})
    elif len({c.get("commit") for c in configs}) > 1:
        problems.append({"seq": None, "why": "续跑的几段不是同一版代码"})
    steps = 0
    prev = None  # (turn, piles)
    for seq, ctx in _contexts(trace):
        steps += 1
        why = []
        if "hand" not in ctx or ctx.get("deck") is None:
            why.append("context 没有牌面")
        else:
            piles = ctx.get("piles")
            hand = Counter(h[1] for h in ctx["hand"])
            if ctx.get("uncertain"):
                why.append(f"账本不可信：{(ctx.get('uncertain_why') or {}).get('why')}")
            if piles is None:
                why.append("没有牌堆账")
            else:
                if piles.get("unlocated"):
                    why.append(f"有位置不明的牌：{piles['unlocated']}")
                total = hand + Counter(piles.get("deck")) + Counter(piles.get("grave")) + Counter(piles.get("unlocated"))
                if total != Counter(ctx["deck"]):
                    why.append(f"牌不守恒：多 {dict(total - Counter(ctx['deck']))}，少 {dict(Counter(ctx['deck']) - total)}")
                if prev is not None and ctx["turn"] != prev[0] and prev[1] is not None:
                    # 上回合最后一次决策之后生成的衍生牌（眠気）进了牌堆：按账本增量补进可抽的牌
                    # 只认眠気：别的生成 / 变换牌会把账本标成不可信（veil_exam._commit），不在这里放行
                    born = Counter({"眠気": (Counter(ctx["deck"]) - prev[3])["眠気"]})
                    pool = Counter(prev[1].get("deck")) + born
                    drawn = hand - prev[2]
                    grave = Counter(prev[1].get("grave")) + prev[2]  # 上回合末手牌回合结束进弃牌堆
                    if drawn - pool:
                        # 洗牌（使用者 2026-11-07）：抽牌数大于牌堆剩余时，先把牌堆全部抽完，再把弃牌堆洗进牌堆，剩下的按概率抽。
                        # 所以牌堆里的牌必须全在新手牌里，多出来的只能来自弃牌堆
                        if pool - drawn:
                            why.append(f"新回合抽到上回合牌堆里没有的牌 {dict(drawn - pool)}，牌堆却没抽完（剩 {dict(pool - drawn)}）")
                        elif (drawn - pool) - grave:
                            why.append(f"洗牌后抽到弃牌堆里也没有的牌：{dict((drawn - pool) - grave)}")
            if ctx.get("undone"):
                why.append(f"本回合撤回过 {ctx['undone']} 次出牌")
            prev = (ctx["turn"], piles, Counter() if piles is None else hand, Counter(ctx["deck"]))
        if why:
            problems.append({"seq": seq, "turn": ctx.get("turn"), "why": "；".join(why)})
    result = {"trusted": steps > 0 and not problems, "steps": steps, "bad_steps": sum(1 for p in problems if p["seq"]),
              "config": run.get("config"), "problems": problems}
    (trace / "trust.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    for arg in argv:
        r = check(Path(arg))
        print(json.dumps({"trace": arg, "trusted": r["trusted"], "steps": r["steps"], "bad_steps": r["bad_steps"],
                          "first": r["problems"][:3]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
