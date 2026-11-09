"""Synthetic route runner: decision deltas feed the next stage and battle.

No day/lesson/master rewards are inferred. This is a configurable integration
fixture, not a recreation of HIF's full schedule or native stat multipliers.
"""
from copy import deepcopy
import time
from .core import Battle, Unsupported, IllegalAction
from .decisions import Decision
from .policies import make_policy


def run_episode(document, policy_name, seed, capture=False):
    start=time.perf_counter()
    result=dict(scenario=document['id'],policy=policy_name,seed=seed,status='ok',illegal_actions=0,
                scope='synthetic-module-integration',validates_game_flow=False,validates_clear_rate=False)
    if document.get('provenance',{}).get('kind')!='synthetic':
        raise Unsupported('Route requires synthetic provenance')
    state=deepcopy(document['initial_state']);events=[];policy=make_policy(policy_name)
    try:
        for n,stage in enumerate(document['stages']):
            scene=dict(schema_version=1,id=f"stage-{n}",provenance=dict(kind='synthetic'),
                       definitions=document['definitions'],state=state,**deepcopy(stage))
            env=Decision(scene);obs=env.reset()
            action,reason=policy.choose(obs,env.legal_actions());env.step(action,reason)
            state=deepcopy(env.state)
            events.append(dict(stage=n,module=stage['module'],**env.events[0]))
        battle=deepcopy(document['battle'])
        battle.update(schema_version=1,id=document['id']+'-battle',provenance=dict(kind='synthetic'),
                      definitions=document['definitions'],cards=state['owned'],zones=dict(deck=list(state['owned'])),
                      state=dict(hp=state['hp'],max_hp=state['max_hp']),drink_definitions=document['drink_definitions'],
                      drinks={str(i):d for i,d in enumerate(state['drinks'])})
        env=Battle(battle);env.reset(seed)
        while not env.ended:
            action,reason=policy.choose(env.observe(),env.legal_actions());env.step(action,reason)
        result.update(env.result(),post_route_state=state,route_actions=[dict(stage=e['stage'],action=e['action'],reason=e['reason']) for e in events])
        if capture:result.update(route_events=events,battle_events=env.events)
    except IllegalAction as error:
        result.update(status='invalid',error=str(error),illegal_actions=1)
    except Unsupported as error:
        result.update(status='unsupported',error=str(error))
    result['elapsed_seconds']=time.perf_counter()-start
    return result
