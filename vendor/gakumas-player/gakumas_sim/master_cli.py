"""Offline data inspection, calendar export and real-card sandbox evaluation."""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics
import time
from .catalog import Catalog,ROOT
from .core import Battle,Unsupported
from .master_adapter import MasterAdapter,sandbox
from .schedule import HIFSchedule
from .evaluate import run_battle,summarize,implementation_manifest


def compact(value):
    if isinstance(value,dict):return {k:compact(v) for k,v in value.items() if 'Descriptions' not in k}
    if isinstance(value,list):return [compact(v) for v in value]
    return value


def parse_card(spec):
    parts=spec.rsplit('@',1)
    return dict(card_id=parts[0],upgrade=int(parts[1]) if len(parts)==2 else 0)


def parse_custom(value):
    parts=value.rsplit('@',1)
    if len(parts)!=2:raise ValueError('Customization needs exact ID@count')
    return dict(id=parts[0],count=int(parts[1]))


DECKS={
    'common':['p_card-00-act-0_001','p_card-00-act-0_002','p_card-00-men-0_003','p_card-00-act-2_009','p_card-00-men-2_012','p_card-00-sup-3_025'],
    'focus':['p_card-00-act-0_001','p_card-00-act-0_002','p_card-00-men-2_012','p_card-01-act-0_006','p_card-01-act-1_001','p_card-01-act-1_022','p_card-01-men-0_008','p_card-01-men-1_024'],
    'motivation':['p_card-00-act-0_001','p_card-00-act-0_002','p_card-00-men-2_012','p_card-02-act-0_010','p_card-02-act-1_004','p_card-02-men-0_012','p_card-02-men-2_008','p_card-02-act-2_047'],
}


def benchmark(seeds_count,output):
    if seeds_count<1:raise ValueError('Positive seed count required')
    started=time.perf_counter();catalog=Catalog();adapter=MasterAdapter(catalog)
    path=Path(output) if output else ROOT/'simulator/runs'/('master-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    if path.exists() and any(path.iterdir()):raise ValueError('Choose an empty output folder')
    path.mkdir(parents=True,exist_ok=True)
    coverage=adapter.coverage();verification=catalog.verify();references=catalog.audit_references()
    (path/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+'\n')
    (path/'verification.json').write_text(json.dumps(dict(source=verification,references=references),indent=2)+'\n')
    samples=random.Random(34190).sample(range(2**32),seeds_count)
    records=[];groups={};documents={};regressions=[]
    for name,card_ids in DECKS.items():
        document=sandbox([dict(card_id=k) for k in card_ids],drinks=['pdrink_00-1-001','pdrink_00-1-004'])
        document['id']='master-'+name;documents[name]=document
        for index,seed in enumerate(samples):
            for policy in ('naive','skill'):
                record=run_battle(document,policy,seed,capture=index==0)
                if index==0:(path/f'{name}-{policy}-{seed}.replay.json').write_text(json.dumps(record,ensure_ascii=False)+'\n')
                record.pop('events',None);record.pop('scenario_document',None);record.pop('implementation_manifest',None)
                records.append(record)
        group={p:summarize([r for r in records if r['scenario']==document['id'] and r['policy']==p]) for p in ('naive','skill')}
        differences=[]
        for seed in samples:
            pair={r['policy']:r for r in records if r['scenario']==document['id'] and r['seed']==seed}
            if all(r['status']=='ok' for r in pair.values()):
                delta=pair['skill']['raw_score']-pair['naive']['raw_score'];differences.append(delta)
                if delta<0:regressions.append(dict(deck=name,seed=seed,delta=delta))
        group['paired_delta']=dict(better=sum(v>0 for v in differences),equal=sum(v==0 for v in differences),worse=sum(v<0 for v in differences),mean=statistics.mean(differences) if differences else None)
        groups[name]=group
    (path/'episodes.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    (path/'regressions.json').write_text(json.dumps(regressions,indent=2)+'\n')
    for name in DECKS:
        for row in [r for r in regressions if r['deck']==name][:3]:
            for policy in ('naive','skill'):
                record=run_battle(documents[name],policy,row['seed'],capture=True)
                (path/f"failure-{name}-{policy}-{row['seed']}.replay.json").write_text(json.dumps(record,ensure_ascii=False)+'\n')
    # Exercise every admitted raw variant, even those not in the three decks.
    smoke=[]
    for row in coverage['cards']:
        if row['status']!='executable':continue
        document=sandbox([row],turns=1);document['state'].update(hp=100,max_hp=100,shield=1000,focus=1000,motivation=1000,impression=1000)
        env=Battle(document);env.reset(1);action=dict(kind='play',id='c0')
        if action not in env.legal_actions():smoke.append(dict(card_id=row['card_id'],upgrade=row['upgrade'],status='blocked-by-condition'));continue
        env.step(action,'master:admitted-effect-smoke')
        while not env.ended:env.step(dict(kind='end_turn'))
        smoke.append(dict(card_id=row['card_id'],upgrade=row['upgrade'],status='ok',result=env.result()))
    (path/'admitted-variants.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in smoke))
    trainings=[];calendars={}
    state=dict(hp=30,max_hp=36,p_points=0,stats=dict(Vo=600,Da=1200,Vi=500),owned={},drinks=[])
    bindings=dict(sp_flags=dict(Vo=True,Da=False,Vi=True),sub_stats=dict(Vo='Da',Da='Vi',Vi='Vo'),
                  parameter_bonus_permil=dict(Vo=1000,Da=1000,Vi=1000),star_bonus_permil=1000,
                  stat_targets=dict(Vo=1000,Da=1000,Vi=1000),hp_reserve=6)
    for mode in ('produce-007','produce-008'):
        schedule=HIFSchedule(mode,catalog);calendars[mode]=schedule.export()
        for stage in schedule.mode['stages']:
            if 'training' in stage['options']:trainings.append(dict(produce_id=mode,**schedule.choose_training(stage['position'],state,bindings)))
    (path/'calendar.json').write_text(json.dumps(calendars,ensure_ascii=False,indent=2)+'\n')
    (path/'training-decisions.json').write_text(json.dumps(trainings,ensure_ascii=False,indent=2)+'\n')
    result=dict(scope='Real master cards in neutral sandboxes; NOT native full HIF runs or win rates',
                commit=catalog.info['commit'],card_base_ids=coverage['card_base_ids'],card_variants=coverage['card_variants'],
                executable_variants=coverage['executable_variants'],smoke=dict(total=len(smoke),passed=sum(r['status']=='ok' for r in smoke)),
                calendar_steps={k:len(v['stages']) for k,v in calendars.items()},training_decisions=len(trainings),
                episodes=len(records),seeds=samples,elapsed_seconds=time.perf_counter()-started,groups=groups,
                illegal_actions=sum(r['illegal_actions'] for r in records),blocked=sum(r['status']=='unsupported' for r in records),
                regression_pairs=len(regressions),
                manifest=implementation_manifest(),database_sha256=catalog.pointer['database_sha256'],output_dir=str(path.resolve()),
                verified=verification['matches'] and references['matches'])
    (path/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# 真实数据接入验证','',f"固定提交 `{result['commit']}`。468 个卡牌 ID、1,788 个强化版本全部入库。",'',
           f"{coverage['executable_variants']} 个版本属于当前可执行效果子集，逐版本烟测 {result['smoke']['passed']}/{len(smoke)}。其余保留原始效果并明确阻断；定制仅支持已声明的部分原始成长操作。",'',
           f"H.I.F 选拔 20 步、本战 9 步完整日历已接入；8 个公开训练位置已运行策略选择。日历来自社区观察，基础训练数值来自 master。",'',
           f"{len(records)} 次真实卡牌沙盒评测，耗时 {result['elapsed_seconds']:.3f} 秒（含全快照校验、引用检查和逐版本烟测）。",'',
           '| 沙盒 | 简单策略均分 | skill 均分 | 更好/相同/更差 |','|---|---:|---:|---|']
    for name,g in groups.items():
        a=g['paired_delta'];lines.append(f"| {name} | {g['naive']['mean_score']} | {g['skill']['mean_score']} | {a['better']}/{a['equal']}/{a['worse']} |")
    lines += ['', '[指标](summary.json) · [逐版本覆盖](coverage.json) · [来源/引用验证](verification.json) · [完整日历](calendar.json) · [训练选择](training-decisions.json)', '',
              f"有 {len(regressions)} 个配对局面低于简单策略，见 [反例清单](regressions.json)；各组最多三个反例保存双方完整回放。", '',
              '真实卡牌数值已用于执行；沙盒使用显式中性倍率、无支援/道具触发环境。完整日历收录不代表 29 步均可自动结算；动态候选、支援/道具触发、全部原生状态、属性倍率和服务端结果仍需补齐。']
    (path/'report.md').write_text('\n'.join(lines)+'\n')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description='Pinned offline master catalog and real-card sandbox')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('stats');sub.add_parser('verify');sub.add_parser('modes')
    search=sub.add_parser('search');search.add_argument('query',nargs='?',default='');search.add_argument('--limit',type=int,default=30)
    card=sub.add_parser('card');card.add_argument('card_id');card.add_argument('--upgrade',type=int,default=0);card.add_argument('--raw',action='store_true');card.add_argument('--custom',action='append',type=parse_custom,default=[],help='Exact customization ID@count, repeat per branch')
    schedule=sub.add_parser('schedule');schedule.add_argument('--mode',choices=['produce-007','produce-008'],default='produce-007');schedule.add_argument('--position',type=int);schedule.add_argument('--idol-card')
    sub.add_parser('coverage')
    passive=sub.add_parser('passives');passive.add_argument('--loadout',type=Path,required=True)
    run=sub.add_parser('run');run.add_argument('--card',action='append');run.add_argument('--deck-file',type=Path,help='JSON array of card_id/upgrade/customizations specs');run.add_argument('--deck',choices=list(DECKS),default='common');run.add_argument('--seed',type=int,default=34190);run.add_argument('--policy',choices=['skill','naive'],default='skill')
    run.add_argument('--loadout',type=Path);run.add_argument('--drink',action='append',default=[]);run.add_argument('--turns',type=int,default=6)
    bench=sub.add_parser('benchmark');bench.add_argument('--seeds',type=int,default=20);bench.add_argument('--output',type=Path)
    # Each data command may save a full machine-readable artifact.
    for p in [search,card,schedule,run,passive]:p.add_argument('--output',type=Path)
    args=parser.parse_args(argv);c=Catalog();adapter=MasterAdapter(c)
    if args.command=='stats':result=c.info
    elif args.command=='verify':result=dict(source=c.verify(),references=c.audit_references())
    elif args.command=='modes':result=c.modes()
    elif args.command=='search':result=c.search(args.query,args.limit)
    elif args.command=='coverage':result=adapter.coverage()
    elif args.command=='passives':
        from .passives import compile_loadout
        result=compile_loadout(c,json.loads(args.loadout.read_text()))
    elif args.command=='card':
        result=c.inspect_card(args.card_id,args.upgrade)
        choices=args.custom
        try:result['resolved_definition']=adapter.card(args.card_id,args.upgrade,choices);result['execution_status']='executable'
        except Unsupported as error:result.update(execution_status='blocked',reason=str(error))
        if not args.raw:result=compact(result)
    elif args.command=='schedule':
        s=HIFSchedule(args.mode,c)
        result=s.exam_options(args.position,args.idol_card) if args.idol_card else s.stage(args.position) if args.position else s.export()
    elif args.command=='run':
        if args.deck_file and args.card:raise Unsupported('--deck-file and --card cannot be combined')
        specs=json.loads(args.deck_file.read_text()) if args.deck_file else [parse_card(value) for value in (args.card or DECKS[args.deck])]
        if not isinstance(specs,list) or not specs:raise Unsupported('Need a nonempty card spec list')
        loadout=json.loads(args.loadout.read_text()) if args.loadout else None
        result=run_battle(sandbox(specs,turns=args.turns,drinks=args.drink,loadout=loadout),args.policy,args.seed,capture=True)
    else:
        result=benchmark(args.seeds,args.output)
        print(json.dumps({k:result[k] for k in ['episodes','elapsed_seconds','executable_variants','smoke','calendar_steps','illegal_actions','blocked','output_dir']},indent=2))
        return 0 if result['verified'] and not result['illegal_actions'] and not result['blocked'] and result['smoke']['passed']==result['smoke']['total'] else 1
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if getattr(args,'output',None):args.output.write_text(text)
    else:print(text,end='')
    return 0 if not isinstance(result,dict) or result.get('status','ok')=='ok' else 2
