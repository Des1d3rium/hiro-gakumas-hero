import argparse
import json
from pathlib import Path
import sys
import unittest
from .evaluate import ROOT,benchmark,run_battle,history_checks,replay
from .episode import run_episode
from .core import Unsupported


def main():
    if len(sys.argv)>1 and sys.argv[1]=='master':
        from .master_cli import main as master_main
        return master_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='hif':
        from .hif_cli import main as hif_main
        return hif_main(sys.argv[2:])
    parser=argparse.ArgumentParser(description='Local Gakumas strategy lab (no LLM/game access)')
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run');run.add_argument('scenario');run.add_argument('--seed',type=int,default=34190)
    run.add_argument('--policy',choices=['skill','naive','rollout'],default='skill');run.add_argument('--output',type=Path)
    bench=sub.add_parser('benchmark');bench.add_argument('--seeds',type=int,default=20);bench.add_argument('--output',type=Path)
    sub.add_parser('validate');sub.add_parser('history')
    again=sub.add_parser('replay');again.add_argument('record',type=Path)
    route=sub.add_parser('episode',help='Synthetic module integration only; not a game route',
                         description='Run a synthetic module integration fixture; does not validate game flow or clear rate.')
    route.add_argument('--seed',type=int,default=34190)
    route.add_argument('--policy',choices=['skill','naive'],default='skill');route.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.command=='validate':
        suite=unittest.defaultTestLoader.discover(str(ROOT/'simulator/tests'))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if args.command=='history':result=history_checks()
    elif args.command=='replay':
        result=replay(json.loads(args.record.read_text()))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['matches'] else 1
    elif args.command=='run':
        path=Path(args.scenario)
        if not path.exists():path=ROOT/'simulator/scenarios'/(args.scenario+'.json')
        result=run_battle(json.loads(path.read_text()),args.policy,args.seed,capture=True)
    elif args.command=='episode':
        result=run_episode(json.loads((ROOT/'simulator/episodes/five-stage-route.json').read_text()),args.policy,args.seed,capture=True)
    else:
        result=benchmark(args.seeds,args.output)
        print(json.dumps({k:result[k] for k in ('episodes','elapsed_seconds','episodes_per_second','illegal_actions','blocked')},indent=2))
        print('Report: '+str(Path(result['output_dir'])/'summary.json'))
        return 0 if result['quality']['contracts_passed'] else 1
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if getattr(args,'output',None):args.output.write_text(text)
    else:print(text,end='')
    return 0 if result.get('status','ok')=='ok' else 2


if __name__=='__main__':
    try:sys.exit(main())
    except (Unsupported,ValueError) as error:
        print(json.dumps(dict(status='unsupported',error=str(error)),ensure_ascii=False))
        sys.exit(2)
