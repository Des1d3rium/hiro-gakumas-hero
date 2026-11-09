"""CalcTurnParameterType's supported positive-weight, T>=3 branch."""
from .core import Unsupported,NativeRandom,f32
from .attributes import ATTRIBUTES,CONFIG_COLUMNS,checked
import math


def allocate(turns,parameters):
    checked(turns,'base turns',100)
    if turns<3:raise Unsupported('Native allocation branch requires at least three turns')
    if set(parameters)!=set(ATTRIBUTES):raise Unsupported('Need exactly Vo/Da/Vi config weights')
    for attr in ATTRIBUTES:
        checked(parameters[attr],attr+' config weight')
        if not parameters[attr]:raise Unsupported('Zero config weight is outside the restored branch')
    ordered=sorted(ATTRIBUTES,key=lambda a:-parameters[a])
    p0,p1,p2=(parameters[a] for a in ordered);n=turns-3
    first=math.ceil(f32(f32(f32(n)*f32(p0))/f32(p0+p1+p2)))
    rest=n-first
    second=round(f32(f32(f32(rest)*f32(p1))/f32(p1+p2)))
    counts=[first,second,rest-second]
    if min(counts)<0:raise Unsupported('Invalid native allocation')
    return dict(ordered_attributes=ordered,random_prefix_counts=dict(zip(ordered,counts)),
                counts={a:dict(zip(ordered,counts))[a]+1 for a in ATTRIBUTES},fixed_tail=list(reversed(ordered)))


def generate(turns,parameters,rng):
    proof=allocate(turns,parameters);before=rng.state;calls=rng.calls
    pool=[a for a in proof['ordered_attributes'] for _ in range(proof['random_prefix_counts'][a])]
    prefix=[]
    while pool:prefix.append(pool.pop(rng.integer(0,len(pool))))
    return dict(**proof,turn_attributes=prefix+proof['fixed_tail'],rng_before=before,rng_after=rng.state,rng_calls=rng.calls-calls)


def config_parameters(config):
    return dict(zip(ATTRIBUTES,(config[k] for k in CONFIG_COLUMNS)))


def hif_order(mode,position,seed,idol_card_id='i_card-shro-3-000'):
    from .catalog import Catalog
    from .hif import HIFPlanner
    c=Catalog()
    try:
        config=HIFPlanner(idol_card_id,c).configuration(mode,position)['battle_config']
        result=generate(config['turn'],config_parameters(config),NativeRandom(seed))
        return dict(mode=mode,position=position,idol_card_id=idol_card_id,master_commit=c.info['commit'],
                    battle_config=config,**result,scope='restored turn-order branch only; not full native battle setup/RNG alignment')
    finally:c.db.close()
