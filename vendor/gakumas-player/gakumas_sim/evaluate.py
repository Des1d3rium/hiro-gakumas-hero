"""Reproducible paired evaluation and JSONL action traces."""
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
import math
import platform
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
import statistics
import time

from .core import Battle, IllegalAction, Unsupported, totals
from .decisions import Decision
from .policies import ROOT, make_policy
from .episode import run_episode


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def implementation_manifest():
    paths = list((ROOT/'gakumas_sim').glob('*.py')) + [ROOT/'strategy/hif-policy.json']
    paths += list((ROOT/'strategy/skills').rglob('SKILL.md'))
    paths += [p for p in [ROOT/'data/master/current.json',ROOT/'data/schedules/hif.json',ROOT/'data/community/hif-wiki-tables.json'] if p.exists()]
    return {str(p.relative_to(ROOT)):sha(p) for p in sorted(paths)}


def wilson(wins,n):
    if not n:
        return None
    z=1.959963984540054
    p=wins/n;den=1+z*z/n
    mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-half), min(1,mid+half)]


def run_battle(document, policy_name, seed, capture=False,policy_config=None, *, episode_start=None):
    started=time.perf_counter();env=None;policy=None
    record=dict(scenario=document['id'],policy=policy_name,seed=seed,status='ok',illegal_actions=0)
    if policy_config is not None:record['policy_config']=deepcopy(policy_config)
    if episode_start is not None:record['episode_start']=deepcopy(episode_start)
    try:
        env=Battle(document);env.reset(seed);policy=make_policy(policy_name,policy_config,episode_start=episode_start)
        while not env.ended:
            obs,legal=env.observe(),env.legal_actions()
            action,reason=policy.choose(deepcopy(obs),deepcopy(legal))
            env.step(action,reason)
        record.update(env.result())
    except IllegalAction as error:
        record.update(status='invalid',error=str(error),illegal_actions=1)
    except Unsupported as error:
        record.update(status='unsupported',error=str(error))
    record['elapsed_seconds']=time.perf_counter()-started
    if policy is not None and hasattr(policy,'statistics'):record['policy_statistics']=deepcopy(policy.statistics)
    if policy is not None and hasattr(policy,'hp_reserve_gate_state'):
        record['hp_reserve_gate_state']=deepcopy(policy.hp_reserve_gate_state)
    if capture and env is not None and hasattr(env,'events'):
        record['events']=env.events
        record['scenario_document']=deepcopy(document)
        record['implementation_manifest']=implementation_manifest()
        if hasattr(policy,'diagnostics'):record['policy_diagnostics']=deepcopy(policy.diagnostics)
    return record


def replay(record):
    if record['implementation_manifest'] != implementation_manifest():
        return dict(matches=False, error='Implementation/skill changed since recording; rerun the benchmark')
    actual=run_battle(record['scenario_document'],record['policy'],record['seed'],capture=True,policy_config=record.get('policy_config'),episode_start=record.get('episode_start'))
    # Artifacts are JSON: tuple/list distinctions inside an in-memory producer
    # must not make a persisted, numerically identical replay fail.
    def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    # Wall time is observational, not part of the deterministic trajectory.
    keys=set(record)-{'elapsed_seconds'}
    differences=[key for key in sorted(keys) if encoded(record[key]) != encoded(actual.get(key))]
    return dict(matches=not differences,differences=differences,events=len(actual.get('events',[])),
                scenario=record['scenario'],seed=record['seed'],policy=record['policy'])


def decision_cases(cases, policy_name):
    result=[];policy=make_policy(policy_name)
    for case in cases:
        env=Decision(case);obs=env.reset()
        legal=env.legal_actions()
        action,reason=policy.choose(obs,legal)
        env.step(action,reason)
        result.append(dict(id=case['id'],module=case['module'],action=action,reason=reason,
                           expected=case['expected_actions'],passed=action['id'] in case['expected_actions'],
                           after=env.state))
    return dict(policy=policy_name,passed=sum(x['passed'] for x in result),cases=len(result),results=result,
                scope='Hand-authored synthetic decision rubric; not real-game win probability')


def history_checks():
    path=ROOT/'run2/state.json';history=json.loads(path.read_text())
    a=history['main_round1_attempt1'];first=history['main_round1_final']
    rows=[dict(name='round1-adjustment',calculated=a['raw']*6//5,recorded=a['score'])]
    for key in ('round2_attempt1_result','round2_attempt2_result'):
        rows.append(dict(name=key,calculated=first['score']+history[key]['score'],recorded=history[key]['total']))
    rank=totals([[dict(participant_id=i,score=s) for i,s in enumerate([first['score']]+first['NPC'])]])
    rows.append(dict(name='round1-rank',calculated=next(r['rank'] for r in rank if r['participant_id']==0),recorded=first['rank']))
    return dict(source=str(path.relative_to(ROOT)),sha256=sha(path),checks=[dict(**r,matches=r['calculated']==r['recorded']) for r in rows],
                scope='Historical arithmetic only; no full historical RNG/effect trajectory replay',
                gate_correction=history['round2_rules']['HIF_red_baton'])


def summarize(records):
    ok=[r for r in records if r['status']=='ok'];scores=sorted(r['final_total'] for r in ok)
    n=len(ok)
    wins=[r['conditional_win'] for r in ok if r['conditional_win'] is not None]
    def quantile(p):return scores[max(0,math.ceil(len(scores)*p)-1)] if scores else None
    return dict(episodes=len(records),completed=n,blocked=sum(r['status']=='unsupported' for r in records),
                invalid=sum(r['status']=='invalid' for r in records),mean_score=statistics.mean(scores) if scores else None,
                p10=quantile(.1),p50=quantile(.5),p90=quantile(.9),unique_score_values=len(set(scores)),
                target_met_rate=sum(r['target_met'] for r in ok)/n if n else None,
                conditional_win_rate=sum(wins)/len(wins) if wins else None,
                win_rate_wilson_95=wilson(sum(wins),len(wins)) if wins else None)


def benchmark(seed_count, output):
    if seed_count<1:raise ValueError('Positive seed count required')
    output=Path(output) if output else ROOT/'simulator/runs'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory is nonempty; choose a new run directory')
    output.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter();folder=ROOT/'simulator/scenarios'
    # Consecutive small xorshift states bias the first multiply-high draw.
    # Spread test inputs across uint32, with a reproducible external sampler.
    seeds=random.Random(34190).sample(range(2**32),seed_count)
    scenarios=[json.loads(p.read_text()) for p in sorted(folder.glob('*.json')) if p.name!='decision-cases.json']
    cases=json.loads((folder/'decision-cases.json').read_text())
    inputs=list(folder.glob('*.json'))+list((ROOT/'simulator/episodes').glob('*.json'))+[ROOT/'strategy/hif-policy.json']
    inputs+=list((ROOT/'strategy/skills').rglob('SKILL.md'))+list((ROOT/'gakumas_sim').glob('*.py'))
    inputs+=list((ROOT/'simulator/tests').glob('*.py'))+[ROOT/'simulator/SPEC.md']
    manifest={str(p.relative_to(ROOT)):sha(p) for p in sorted(inputs)}
    records=[]
    with (output/'episodes.jsonl').open('w') as stream:
        for document in scenarios:
            for index,seed in enumerate(seeds):
                for policy in ('naive','skill'):
                    r=run_battle(document,policy,seed,capture=(index==0))
                    if index==0:
                        (output/(document['id']+'-'+policy+f'-seed{seed}.replay.json')).write_text(json.dumps(r,ensure_ascii=False)+'\n')
                    trace=r.pop('events',None)
                    r.pop('scenario_document',None);r.pop('implementation_manifest',None)
                    if trace is not None:
                        with (output/(document['id']+'-'+policy+f'-seed{seed}.trace.jsonl')).open('w') as log:
                            for e in trace:log.write(json.dumps(e,ensure_ascii=False)+'\n')
                    records.append(r);stream.write(json.dumps(r,ensure_ascii=False)+'\n')
    groups={}
    for document in scenarios:
        id=document['id'];group={p:summarize([r for r in records if r['scenario']==id and r['policy']==p]) for p in ('naive','skill')}
        delta=[]
        for seed in seeds:
            pair={r['policy']:r for r in records if r['scenario']==id and r['seed']==seed}
            if all(v['status']=='ok' for v in pair.values()):
                delta.append(pair['skill']['final_total']-pair['naive']['final_total'])
        group['paired_delta']=dict(pairs=len(delta),mean=statistics.mean(delta) if delta else None,
                                  better=sum(x>0 for x in delta),equal=sum(x==0 for x in delta),worse=sum(x<0 for x in delta))
        groups[id]=group
    regressions=[]
    for document in scenarios:
        for seed in seeds:
            pair={r['policy']:r for r in records if r['scenario']==document['id'] and r['seed']==seed}
            if all(r['status']=='ok' for r in pair.values()) and pair['skill']['final_total']<pair['naive']['final_total']:
                regressions.append(dict(scenario=document['id'],seed=seed,
                                        delta=pair['skill']['final_total']-pair['naive']['final_total']))
    (output/'regressions.json').write_text(json.dumps(regressions,indent=2)+'\n')
    # Keep executable counterexamples, including cases beyond the seed-1 trace.
    saved=Counter()
    for row in regressions:
        if saved[row['scenario']]>=3:continue
        saved[row['scenario']]+=1
        document=next(d for d in scenarios if d['id']==row['scenario'])
        for policy in ('naive','skill'):
            record=run_battle(document,policy,row['seed'],capture=True)
            (output/(f"failure-{row['scenario']}-{policy}-{row['seed']}.replay.json")).write_text(json.dumps(record,ensure_ascii=False)+'\n')
    elapsed=time.perf_counter()-start
    route=json.loads((ROOT/'simulator/episodes/five-stage-route.json').read_text())
    route_start=time.perf_counter()
    route_records=[run_episode(route,p,s) for s in seeds for p in ('naive','skill')]
    with (output/'route-episodes.jsonl').open('w') as stream:
        for record in route_records:stream.write(json.dumps(record,ensure_ascii=False)+'\n')
    for policy in ('naive','skill'):
        (output/(policy+f'-route-seed{seeds[0]}.json')).write_text(json.dumps(run_episode(route,policy,seeds[0],capture=True),ensure_ascii=False)+'\n')
    route_metrics=dict(episodes=len(route_records),elapsed_seconds=time.perf_counter()-route_start,
                       groups={p:summarize([r for r in route_records if r['policy']==p]) for p in ('naive','skill')},
                       scope='Synthetic coupled decisions -> battle; no native schedule/stat-to-score formula')
    result=dict(schema_version=1,scope='Synthetic offline policy/interpreter benchmark; NOT actual HIF clear rates',
                policy_status='Executable hand-coded adapters to text skills; no trained model and no global optimality claim',
                seed_prior='Uniform uint32 without replacement; Python Random(34190); not a measured game seed prior',
                caveat='Fixed visible microcases repeat the same initial hand; Wilson intervals there do not imply game uncertainty.',
                seeds=seed_count,seed_states=seeds,episodes=len(records),elapsed_seconds=elapsed,episodes_per_second=len(records)/elapsed,
                output_dir=str(output.resolve()),timestamp_utc=datetime.now(timezone.utc).isoformat(),
                runtime=dict(python=sys.version,system=platform.platform(),machine=platform.machine()),
                illegal_actions=sum(r['illegal_actions'] for r in records),blocked=sum(r['status']=='unsupported' for r in records),
                regression_pairs=len(regressions),
                route=route_metrics,
                groups=groups,decisions={p:decision_cases(cases,p) for p in ('naive','skill')},
                history=history_checks(),manifest=manifest)
    result['total_episodes']=len(records)+len(route_records)
    result['total_elapsed_seconds']=time.perf_counter()-start
    result['quality'] = dict(
        contracts_passed=(result['decisions']['skill']['passed']==len(cases)
                          and all(r['matches'] for r in result['history']['checks'])
                          and not any(r['status']!='ok' for r in records+route_records)),
        battle_regression_pairs=len(regressions),
        scope='Contracts passing is not evidence of global optimality or real-game win rate')
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with (output/'comparison.csv').open('w') as f:
        fields=['scenario','policy','episodes','completed','blocked','invalid','mean_score','p10','p50','p90','conditional_win_rate']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for name,group in groups.items():
            for policy in ('naive','skill'):writer.writerow(dict(scenario=name,policy=policy,**group[policy]))
    lines=['# 离线策略评测', '', f"日期：{result['timestamp_utc']}。运行环境：{platform.machine()} / Python {platform.python_version()}。", '',
           f"{seed_count} 个 uint32 测试状态；5 个战斗场景 × 2 个策略，共 {len(records)} 局；另有 {len(route_records)} 条串联路线。",
           f"总耗时 {result['total_elapsed_seconds']:.3f} 秒。战斗批次 {elapsed:.3f} 秒，{result['episodes_per_second']:.1f} 局/秒。", '',
           '场景均为合成数据；三个固定可见局面重复运行，只有两个战斗场景和串联路线产生抽牌差异。此处不是真实 H.I.F 胜率，也不与屏幕操作耗时直接比较。', '',
           '| 场景 | 简单策略均分 | skill 均分 | skill 更好/相同/更差 |',
           '|---|---:|---:|---|']
    for name,g in groups.items():
        d=g['paired_delta'];lines.append(f"| {name} | {g['naive']['mean_score']:.2f} | {g['skill']['mean_score']:.2f} | {d['better']}/{d['equal']}/{d['worse']} |")
    lines += ['', f"局外规则检查：skill {result['decisions']['skill']['passed']}/{len(cases)}。简单策略固定选择首个合法选项；预设用例有意包含该方法的反例，不能代替强策略对照。",
              f"历史算术核对：{sum(r['matches'] for r in result['history']['checks'])}/4；不包含历史抽牌/完整战斗回放。",
              f"串联路线均分：简单策略 {route_metrics['groups']['naive']['mean_score']:.2f}，skill {route_metrics['groups']['skill']['mean_score']:.2f}。", '',
              f"战斗出现 {len(regressions)} 个 skill 低于简单策略的配对局面，见 [反例清单](regressions.json)；每个场景最多保留三个反例的双方回放。", '',
              '## 日志', '', '[完整指标与输入指纹](summary.json) · [比较表](comparison.csv) · [战斗逐局结果](episodes.jsonl) · [串联路线结果](route-episodes.jsonl)', '',
              '首个种子每种战斗/策略均保存 `.trace.jsonl` 与 `.replay.json`，后者可用 `python3 -m gakumas_sim replay <文件>` 核对所有事件。', '',
              '## 边界', '', '只移植了声明的效果子集。策略是人工编码的 skill 适配器，搜索深度/叶子价值仍为启发式，没有训练或全局最优保证。未知效果、同键固定排序和第二轮自动补基础卡会阻断；尚无完整真实 master、全日程与服务端校验。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    return result
