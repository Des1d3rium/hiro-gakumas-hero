"""Command-line access to HIF-specific planning evidence."""
import argparse
import json
from pathlib import Path
from .catalog import Catalog
from .hif import HIFPlanner,dispatch,round2_budget


def main(argv=None):
    p=argparse.ArgumentParser(description='HIF-only objective and event advisor; not a full growth runner')
    s=p.add_subparsers(dest='command',required=True)
    goals=s.add_parser('targets');goals.add_argument('--idol-card',default='i_card-shro-3-000')
    goals.add_argument('--selection',type=int,choices=[1,2,3])
    goals.add_argument('--round1',type=int);goals.add_argument('--score-kind',choices=['raw','settled'])
    goals.add_argument('--observed-npcs',type=Path,help='JSON mapping identity to {value,kind}, round1 only')
    goals.add_argument('--output',type=Path)
    gate=s.add_parser('baton');gate.add_argument('item_id');gate.add_argument('--draw-count',type=int)
    gate.add_argument('--output',type=Path)
    creation=s.add_parser('baton-generate',help='Standalone creation at an explicit ExamStartExam boundary')
    creation.add_argument('input',type=Path);creation.add_argument('--output',type=Path)
    baton_bind=s.add_parser('bind-baton',help='Install an explicit baton into an existing source-bound loadout')
    baton_bind.add_argument('input',type=Path);baton_bind.add_argument('--output',type=Path,required=True)
    finals_check=s.add_parser('finals-admit',help='Validate explicit current finals state; not selection-memory reconstruction')
    finals_check.add_argument('input',type=Path);finals_check.add_argument('--output',type=Path)
    finals_replay=s.add_parser('finals-replay',help='Replay both exams and quoted interval transactions')
    finals_replay.add_argument('input',type=Path);finals_replay.add_argument('--output',type=Path)
    growth_check=s.add_parser('growth-admit',help='Validate explicit main pre-exam state and modifiers')
    growth_check.add_argument('input',type=Path);growth_check.add_argument('--output',type=Path)
    growth_replay=s.add_parser('growth-replay',help='Replay actual pre-exam schedule and nested reward choices')
    growth_replay.add_argument('input',type=Path);growth_replay.add_argument('--output',type=Path)
    for name in ('selection-admit','selection-replay'):
        command=s.add_parser(name,help='Explicit HIF selection event host; not actual qualification or memory generation')
        command.add_argument('input',type=Path);command.add_argument('--output',type=Path)
    growth_profile=s.add_parser('growth-profile',help='Project explicit dearness/panel sources; not current account initialization')
    growth_profile.add_argument('input',type=Path);growth_profile.add_argument('--output',type=Path)
    supports=s.add_parser('support-profile',help='Resolve exact support levels with explicit current skill counters')
    supports.add_argument('input',type=Path);supports.add_argument('--output',type=Path)
    for command,help_text in [('memory-preview','Project HIF selection memory before startup effects'),
                              ('memory-start','Bind memory projection to supplied startup progress'),
                              ('memory-replay','Replay a serialized memory/start binding')]:
        sub=s.add_parser(command,help=help_text);sub.add_argument('input',type=Path);sub.add_argument('--output',type=Path)
    event=s.add_parser('dispatch');event.add_argument('input',type=Path);event.add_argument('--output',type=Path)
    budget=s.add_parser('budget');budget.add_argument('input',type=Path);budget.add_argument('--output',type=Path)
    attributes=s.add_parser('attributes');attributes.add_argument('--mode',choices=['produce-007','produce-008'],required=True)
    attributes.add_argument('--position',type=int,required=True);attributes.add_argument('--idol-card',default='i_card-shro-3-000')
    for name in ('vo','da','vi'):attributes.add_argument('--'+name,type=int,required=True)
    attributes.add_argument('--output',type=Path)
    bind=s.add_parser('bind-battle');bind.add_argument('input',type=Path)
    bind.add_argument('--output',type=Path,required=True,help='New output file; existing files are not overwritten')
    audition=s.add_parser('audition-bonus');audition.add_argument('input',type=Path);audition.add_argument('--output',type=Path)
    bind_audition=s.add_parser('bind-audition');bind_audition.add_argument('input',type=Path)
    bind_audition.add_argument('--output',type=Path,required=True,help='New output file; existing files are not overwritten')
    order=s.add_parser('turn-order');order.add_argument('--mode',choices=['produce-007','produce-008'],required=True)
    order.add_argument('--position',type=int,required=True);order.add_argument('--seed',type=int,required=True)
    order.add_argument('--idol-card',default='i_card-shro-3-000');order.add_argument('--output',type=Path)
    native=s.add_parser('bind-native-audition');native.add_argument('input',type=Path)
    native.add_argument('--output',type=Path,required=True,help='New output file; existing files are not overwritten')
    displayed=s.add_parser('display-bonus',help='Normalize three explicit final displayed multipliers; no producer inference')
    displayed.add_argument('input',type=Path);displayed.add_argument('--output',type=Path)
    bind_display=s.add_parser('bind-displayed-audition',help='Use final displayed multipliers with restored native order')
    bind_display.add_argument('input',type=Path);bind_display.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    c=Catalog()
    try:
        if a.command=='turn-order':
            from .turn_order import hif_order
            result=hif_order(a.mode,a.position,a.seed,a.idol_card)
        elif a.command=='display-bonus':
            from .audition import displayed_bonus
            result=displayed_bonus(json.loads(a.input.read_text()))
        elif a.command=='bind-displayed-audition':
            from .audition import bind_displayed_audition_battle
            from .core import Battle
            if a.output.exists():p.error('Output already exists')
            result=bind_displayed_audition_battle(**json.loads(a.input.read_text()));Battle(result)
        elif a.command=='bind-native-audition':
            from .audition import bind_native_audition_battle
            from .core import Battle
            if a.output.exists():p.error('Output already exists')
            result=bind_native_audition_battle(**json.loads(a.input.read_text()));Battle(result)
        elif a.command=='audition-bonus':
            from .audition import audition_preview
            result=audition_preview(**json.loads(a.input.read_text()))
        elif a.command=='bind-audition':
            from .audition import bind_audition_battle
            from .core import Battle
            if a.output.exists():p.error('Output already exists')
            result=bind_audition_battle(**json.loads(a.input.read_text()));Battle(result)
        elif a.command=='attributes':
            from .attributes import hif_preview
            result=hif_preview(a.mode,a.position,dict(Vo=a.vo,Da=a.da,Vi=a.vi),a.idol_card,c)
        elif a.command=='bind-battle':
            from .attributes import bind_battle
            from .core import Battle
            if a.output.exists():p.error('Output already exists')
            result=bind_battle(**json.loads(a.input.read_text()))
            Battle(result) # Revalidate source cards, loadout and formula before writing.
        elif a.command=='targets':
            if (a.round1 is None)!=(a.score_kind is None):p.error('--round1 and --score-kind must be provided together')
            planner=HIFPlanner(a.idol_card,c)
            if a.selection:
                if a.round1 is not None or a.observed_npcs:p.error('Finals scores cannot be supplied for selection')
                result=planner.selection(a.selection)
            else:
                score=None if a.round1 is None else dict(value=a.round1,kind=a.score_kind)
                observed=json.loads(a.observed_npcs.read_text()) if a.observed_npcs else None
                result=planner.finals(score,observed)
        elif a.command=='bind-baton':
            from .baton import bind_baton
            if a.output.exists():p.error('Output already exists')
            result=bind_baton(**json.loads(a.input.read_text()))
        elif a.command=='baton-generate':
            from .baton import generate
            from .core import NativeRandom,Unsupported
            data=json.loads(a.input.read_text())
            if not isinstance(data,dict) or set(data)-{'item_id','draw_pile','rng_state','occupied_ids'}:
                raise Unsupported('Unknown baton component input fields')
            result=generate(data['item_id'],data['draw_pile'],NativeRandom(data['rng_state']),
                            occupied_ids=data.get('occupied_ids'),catalog=c)
        elif a.command=='baton':
            result=HIFPlanner(catalog=c).baton(a.item_id)
            from .baton import coverage
            result['execution_coverage']=coverage(c,result['pool_model'])
            if a.draw_count is not None:
                from .hif import integer
                integer(a.draw_count,'draw-count')
                result['generated_count']=max(0,result['minimum_draw_pile']-a.draw_count)
        elif a.command in ('memory-preview','memory-start','memory-replay'):
            from .selection_start import project_memory,record_start,replay_start
            if a.output and a.output.exists():p.error('Output already exists')
            values=json.loads(a.input.read_text())
            if a.command=='memory-preview':result=project_memory(values,c)
            elif a.command=='memory-start':
                from .finals import exact
                exact(values,{'memory_input','progress'},'Memory-start binding input')
                result=record_start(values['memory_input'],values['progress'])
            else:
                result=replay_start(values)
                if not result['matches']:
                    print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        elif a.command=='support-profile':
            from .supports import profile
            result=profile(json.loads(a.input.read_text()),c)
        elif a.command=='growth-profile':
            from .growth_scaling import dearness_projection,hif_panel_projection
            from .finals import exact
            values=json.loads(a.input.read_text())
            exact(values,{'character_id','dearness_level','before_exam_panel_level','cap_panel_level'},'Growth profile query')
            result=dict(dearness=dearness_projection(c,values['character_id'],values['dearness_level']),
                        panels=hif_panel_projection(c,values['before_exam_panel_level'],values['cap_panel_level']))
        elif a.command=='growth-admit':
            from .growth import GrowthSession
            env=GrowthSession(json.loads(a.input.read_text()));result=env.observe()
            result['state_digest']=env.state_digest()
        elif a.command=='selection-admit':
            from .selection import SelectionSession
            env=SelectionSession(json.loads(a.input.read_text()));result=env.observe();result['state_digest']=env.state_digest()
        elif a.command=='selection-replay':
            from .selection import replay_selection
            result=replay_selection(json.loads(a.input.read_text()))
            if not result['matches']:
                print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        elif a.command=='growth-replay':
            from .growth import replay_growth
            result=replay_growth(json.loads(a.input.read_text()))
            if not result['matches']:
                print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        elif a.command=='finals-admit':
            from .finals import FinalsSegment,SCOPE
            env=FinalsSegment(json.loads(a.input.read_text()))
            result=dict(admitted=True,position=env.position,phase=env.phase,resource_digest=env.resource_digest(),scope=SCOPE)
        elif a.command=='finals-replay':
            from .finals import replay_segment
            result=replay_segment(json.loads(a.input.read_text()))
            if not result['matches']:
                print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        elif a.command=='budget':
            result=round2_budget(**json.loads(a.input.read_text()))
        else:result=dispatch(**json.loads(a.input.read_text()),catalog=c)
    finally:c.db.close()
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.output:a.output.write_text(text)
    else:print(text,end='')
    return 0
