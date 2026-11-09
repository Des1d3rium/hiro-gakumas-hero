# 来源：digital-hiro src/hiro/sim/probe.py（develop 864e037）
"""局面探查：把审计记录重放到某个决策点，用更多世界比较几条完整的动作序列。

    .venv/bin/python -m hiro.sim.probe reports/sim/<run> --seed 1 --turn 3 \\
        --seq "drink:特製ハツボシエキス,play:デイドリーミング+" --seq "play:エウレカ！+" --worlds 64

每条序列在同一批确定化世界里执行，执行完再用启发式推演到终局；输出均值、相对第一条的配对差与标准误。
序列里的动作键与审计记录相同（play:卡名 / drink:饮料名 / end_turn），在当前局面找不到对应合法动作时报错。
"""

import argparse
import json
import random
import statistics
from pathlib import Path

from hiro.brain.state import CAMPUS_HIRO
from hiro.sim.bench import EXAMS
from hiro.sim.env import SimExam, fast_apply
from hiro.sim.policies import action_key, determinize, rollout


def find(env, key: str) -> dict:
    for a in env.legal_actions():
        if action_key(a, env) == key:
            return a
    raise SystemExit(f"当前局面没有合法动作 {key}；合法：{sorted({action_key(a, env) for a in env.legal_actions()})}")


def reach(exam: SimExam, records: list[dict], seed: int, turn: int, at: int = 0):
    """重放 seed 这局的已选动作，停在第 turn 回合的第 at 个决策点之前。"""
    env = exam.episode(seed).env
    mine = [r for r in records if r["seed"] == seed]
    target = [i for i, r in enumerate(mine) if r["turn"] == turn]
    if len(target) <= at:
        raise SystemExit(f"种子 {seed} 第 {turn} 回合只有 {len(target)} 个决策点")
    for r in mine[: target[at]]:
        fast_apply(env, find(env, r["chosen"]))
    rec = mine[target[at]]
    st = rec["state"]
    if (env.hp, env.shield) != (st.get("hp", env.hp), st.get("genki", env.shield)):
        raise SystemExit(f"重放不一致：引擎 hp{env.hp}/元气{env.shield}，记录 hp{st.get('hp')}/元气{st.get('genki')}")
    return env, rec


def compare(env, sequences: list[list[str]], worlds: int, meta: dict, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    world_seeds = [rng.randrange(2**31) for _ in range(worlds)]
    results = []
    for seq in sequences:
        scores = []
        for ws in world_seeds:
            r = random.Random(ws)
            w = determinize(env, meta, r)
            for key in seq:
                fast_apply(w, find(w, key))
            scores.append(rollout(w, r))
        results.append({"seq": seq, "scores": scores, "mean": statistics.fmean(scores)})
    base = results[0]["scores"]
    for res in results:
        diffs = [a - b for a, b in zip(res["scores"], base)]
        res["diff"] = statistics.fmean(diffs)
        res["se"] = statistics.stdev(diffs) / len(diffs) ** 0.5 if len(diffs) > 1 else 0.0
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="reports/sim/<run> 目录")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--turn", type=int, required=True)
    ap.add_argument("--at", type=int, default=0, help="该回合第几个决策点（从 0 起）")
    ap.add_argument("--seq", action="append", required=True)
    ap.add_argument("--worlds", type=int, default=64)
    args = ap.parse_args()

    run = Path(args.run)
    exam_name = run.name.rsplit("-", 1)[-1]
    fixture, label = EXAMS[exam_name]
    exam = SimExam(fixture, label, CAMPUS_HIRO)
    records = [json.loads(line) for line in (run / "decisions.jsonl").read_text().splitlines() if line.strip()]
    env, rec = reach(exam, records, args.seed, args.turn, args.at)
    st = rec["state"]
    print(f"种子 {args.seed} 第 {args.turn} 回合：hp{env.hp} 元气{env.shield} 干劲{env.motivation} 分{env.score:,}")
    print(f"  手牌 {[env.definition(c)['name'] for c in env.zones['hand']]}  饮料 {[env.drink_definitions[d]['name'] for d in env.drinks.values()]}")
    print(f"  当时选择 {rec['chosen']}（{rec['source']}）：{rec['reason'][:120]}")
    for res in compare(env, [s.split(",") for s in args.seq], args.worlds, exam.exam.meta):
        print(f"  {' → '.join(res['seq']):60s} 均分 {res['mean']:>10,.0f}  相对第一条 {res['diff']:>+10,.0f} ± {res['se']:,.0f}")
    _ = st


if __name__ == "__main__":
    main()
