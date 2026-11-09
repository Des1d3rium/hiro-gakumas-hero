"""cycle 引擎（hiro.brain.veil）离线逐步回放：按 trace 每个 veil 决策步的 context 重建局面，用当前代码重新决策。

重构验收（TODO S3）：重构前后各跑一次，逐行 diff，每一步的动作和理由都应完全相同。

    PYTHONPATH=src .venv/bin/python tools/audit/veil_replay.py traces/batch-codex-* > after.jsonl
    diff before.jsonl after.jsonl

--flags default（默认）= 偶像档案的 default_on；all = 全部开关打开；live = default_on + 睡眠流程测试局打开的出牌开关
（LIVE_FLAGS）；可重复给，逐组输出。组名后可以用「+开关」再打开几个（如 --flags live+FULL_PLAN）；
--on FLAG 在每一组上再打开一个开关（可重复）。
全力过渡的规划（veil_full）trace 的 context 里没有：同一组开关下按上一步重放的决策带到下一步（同一回合才用得上）。
只读 traces/，不连游戏。改写自会话 scratchpad 的 replay_veil.py。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))


def _okey(a: dict) -> str:
    k, g = a.get("kind"), a.get("args") or {}
    return {"PlayCard": f"play:{g.get('slot')}", "UseDrink": f"drink:{g.get('slot')}"}.get(k, "skip" if k in ("Skip", "EndTurn", "Rest") else str(k))


def _build(s, ctx, played, sd, star_seen):
    from test_veil import case

    from hiro.brain.cards import CardView, parse_tags
    from hiro.brain.veil import STANCES, norm

    def canon(name):
        base = norm(name).rstrip("+")
        v = [k for k in (sd.get("veil_deck") or {}) if k.rstrip("+") == base]
        return v[0] if len(v) == 1 else norm(name)

    f = s["state"].get("exam") or s["observation"]["fields"]
    hand = [CardView(h["slot"], canon(h["name"]), h["card_id"], h["upgrade"], h["available"], h["category"], h["cost"],
                     h["hp_cost"], h.get("extra_cost"), h["text"], parse_tags(h["name"], h["text"], once=h["tags"].get("once", False)))
            for h in (f["hand"] or [])]
    state, mem = case(*hand, exam=s["state"]["exam_session"]["label"], status=ctx["stance"], flower=ctx["flower"],
                      drinks=[d["name"] for d in (f["drinks"] or [])], actions_left=ctx["actions_left"])
    state.hp, state.genki, state.remaining_turns, state.score = f["hp"], f["genki"], f["remaining_turns"], f.get("score")
    state.turn = mem.turn = ctx["turn"]
    state.statuses = [{"name": ctx["stance"], "value": ctx.get("stance_level")}] + \
        [x for x in f["statuses"] if x.get("name") and x["name"] not in STANCES]
    seen = next((x for x in (s["observation"]["fields"].get("statuses") or []) if x.get("name") in STANCES), None)
    if seen and seen["name"] != ctx["stance"]:
        state.statuses.append({"name": "_screen_stance", "value": seen["name"], "level": seen.get("value")})
    mem.this_turn = [f"打 {p}" for p in played]
    if ctx.get("piles"):
        pl = ctx["piles"]
        state.piles = {k: [n for n, c in (pl.get(k) or {}).items() for _ in range(c)] for k in ("deck", "grave", "unlocated")}
        state.piles["next_draw"] = ctx.get("next_draw") or 3
        state.piles["once"] = sd.get("veil_known_once", [])
    mem.strategy_data["veil_fp"] = ctx["fp"]
    for x in (f["statuses"] or []):
        if (x.get("name") or "").startswith("星々を見下ろす一番星") and isinstance(x.get("value"), int):
            star_seen[0] = x["value"]
    if star_seen[0] is not None:
        mem.strategy_data["veil_star"] = star_seen[0]
    if ctx.get("fp_boosts") is not None:
        mem.strategy_data["veil_fp_boosts"] = ctx["fp_boosts"]
    if ctx.get("cycle"):
        mem.strategy_data["veil_cycle"] = ctx["cycle"]
    for k in ("veil_abilities", "veil_abilities_source", "veil_initial_once", "veil_known_once", "veil_fp_cards"):
        if k in sd:
            mem.strategy_data[k] = json.loads(json.dumps(sd[k]))
    return state, mem


# 睡眠流程测试局一直打开的出牌决策开关（记忆 test-switches，2026-11-09）
LIVE_FLAGS = frozenset({"CYCLE_PLAN", "IDOL_CLEAR", "STRONG_TEACH", "CYCLE_WHEY", "DRIVE_CHECK", "STANCE_LATER", "HP_ZERO",
                        "STANCE_SCREEN", "CYCLE_TAP", "IDOL_FP_GATE", "STANCE_UNLOCK"})


def _setflags(on: set[str], known) -> None:
    for f in known:
        os.environ[f"VEIL_{f}"] = "1" if f in on else "0"
        os.environ.pop(f"HIRO_VEIL_{f}", None)


def replay(run: Path, flag_sets: dict[str, set[str]], known):
    from hiro.brain.veil import VeilAdvisor
    from hiro.contracts import Action

    sd = json.loads((run / "exam_memory.json").read_text())["memory"]["strategy_data"] if (run / "exam_memory.json").exists() else {}
    turn, played, star = None, [], [None]
    carried = {name: {} for name in flag_sets}  # 每组开关重放出的 veil_full（全力过渡规划）
    for line in (run / "steps.jsonl").read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        d = s.get("decision") or {}
        ctxn = next((n for n in s.get("notes") or [] if n.startswith("context ")), None)
        if d.get("decider") != "veil" or not ctxn:
            continue
        ctx = json.loads(ctxn[8:])
        if ctx["turn"] != turn:
            turn, played = ctx["turn"], []
        for name, on in flag_sets.items():
            _setflags(on, known)
            os.environ.pop("VEIL_HP_FLOOR", None)
            try:
                state, mem = _build(s, ctx, played, sd, star)
                if carried[name].get("turn") == ctx["turn"]:
                    mem.strategy_data["veil_full"] = dict(carried[name])
                r = VeilAdvisor().decide(state, mem, None, [Action.from_legal(a) for a in state.legal_actions()])
                got = [r.action.legal_key(), r.reason]
                carried[name] = dict(mem.strategy_data.get("veil_full") or {})
            except Exception as e:  # noqa: BLE001
                got = ["ERR", f"{type(e).__name__}: {e}"[:160]]
            yield {"run": run.name, "seq": s["seq"], "flags": name, "decision": got}
        x, a = s.get("execution") or {}, d.get("action") or {}
        f = s["state"].get("exam") or s["observation"]["fields"]
        if x.get("ok") and a.get("kind") == "PlayCard":
            c = next((h for h in f["hand"] if h["slot"] == a["args"]["slot"]), None)
            if c:
                from hiro.brain.veil import norm

                base = norm(c["name"]).rstrip("+")
                v = [k for k in (sd.get("veil_deck") or {}) if k.rstrip("+") == base]
                played.append(v[0] if len(v) == 1 else norm(c["name"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--flags", action="append", help="default / all / none / live，可加「+开关」")
    ap.add_argument("--on", action="append", default=[], metavar="FLAG", help="每组开关上再打开它（可重复）")
    args = ap.parse_args(argv)
    from hiro.brain import veil

    known = tuple(veil.STRATEGY_FLAGS)
    sets = {"default": set(veil.DEFAULT_ON), "all": set(known), "none": set(), "live": set(veil.DEFAULT_ON) | LIVE_FLAGS}
    chosen = {}
    for spec in args.flags or ["default"]:
        base, *more = spec.split("+")
        extra = set(args.on) | set(more)
        if base not in sets or extra - set(known):
            ap.error(f"不认识的开关组 / 开关：{spec}")
        chosen[spec] = sets[base] | extra
    n = 0
    for run in args.runs:
        if not (run / "steps.jsonl").is_file():
            continue
        for row in replay(run, chosen, known):
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
            n += 1
    print(f"{n} 步", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
