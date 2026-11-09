"""Soft fixed-HP reserve from visible costs and known modifier timers.

This reserves once for each remaining one-use scoring core, not a claim that
it will be drawn or paid on a particular turn. It never changes legality.
"""
from collections import Counter
from copy import deepcopy
import math
from .core import Unsupported,ratio
from .passives import matching_hooks


def validate(settings):
    if settings is None:return
    if (not isinstance(settings,dict) or set(settings)!={'weight','lookahead_turns','include_finisher','include_passive_loss'}
        or type(settings['weight']) not in (int,float) or not math.isfinite(settings['weight']) or not 0<settings['weight']<=1000
        or type(settings['lookahead_turns']) is not int or not 1<=settings['lookahead_turns']<=6
        or any(type(settings[k]) is not bool for k in ('include_finisher','include_passive_loss'))):
        raise Unsupported('Explicit bounded future HP reserve settings required')


def fee_schedule(cost,modifiers,steps):
    """Current fee then subsequent starts; same end-turn lifetime as core."""
    modifiers=deepcopy(modifiers);fees=[]
    for offset in range(steps+1):
        multiplier=1000
        for mod in modifiers:multiplier=multiplier*mod['permil']//1000
        fees.append(ratio(cost,multiplier))
        for mod in modifiers:
            if mod['fresh']:mod['fresh']=False
            else:mod['remaining']-=1
        modifiers=[mod for mod in modifiers if mod['remaining']>0]
    return fees


def scoring_delay(definition,include_finisher):
    if not definition.get('once'):return None
    delays=[]
    def walk(effects):
        for e in effects:
            if e['op']=='conditional':walk(e['effects'])
            elif e['op']=='shield_tick':delays.append(1)
            elif e['op']=='delayed_shield_score':delays.append(e['delay'])
            elif include_finisher and e['op']=='shield_score' and e.get('consume_permil',0):delays.append(0)
    walk(definition['effects'])
    return min(delays) if delays else None


def estimate(observation,model,remaining,settings):
    """Estimate a payment buffer, not an exact future score/probability."""
    validate(settings);window=min(remaining,settings['lookahead_turns']);rows=[]
    hidden=observation.get('public_model',{});library=hidden.get('definition_library',{})
    # Current hand is represented by the hypothetical visible state. Hidden
    # composition remains the original public multiset; draws are not guessed.
    sources=[]
    if hasattr(model,'fetch_projection_piles'):
        unseen=[key for rows in model.fetch_projection_piles.values() for key,identity in rows if identity is None]
        unseen.extend(hidden.get('hold_definition_ids',[]))
    else:unseen=sum((hidden.get(zone,[]) for zone in ('deck_definition_ids','grave_definition_ids','hold_definition_ids')),[])
    for key,count in Counter(unseen).items():
        sources.append((key,library[key],library[key],count,1))
    for instance in model.zones['hand']:
        key=model.cards[instance];definition=model.definition(instance)
        future=model.definitions[model.support_bases.get(instance,key)]
        sources.append((key,definition,future,1,0 if model.actions>0 else 1))
    for zone in ('grave','hold','deck'):
        for instance in model.zones[zone]:
            key=model.cards[instance];definition=model.definition(instance)
            sources.append((key,definition,definition,1,1))
    for key,current,future,count,earliest in sources:
        delay=scoring_delay(current,settings['include_finisher'])
        if delay is None or remaining<earliest+delay:continue
        last=min(window,remaining-delay)
        offsets=list(range(earliest,last+1))
        if not offsets:continue
        now=current.get('cost',{}).get('hp',0);later=future.get('cost',{}).get('hp',0)
        if not now and not later:continue
        schedule=fee_schedule(later,model.stamina_modifiers,last)
        if 0 in offsets:schedule[0]=fee_schedule(now,model.stamina_modifiers,0)[0]
        cost=sum(schedule[i] for i in offsets)/len(offsets)*count
        rows.append(dict(definition_id=key,count=count,offsets=offsets,fees=[schedule[i] for i in offsets],reserved_hp=cost))
    core=sum(r['reserved_hp'] for r in rows);passive_loss=0
    if core and settings['include_passive_loss']:
        hooks=deepcopy(model.hooks);hp=model.hp+model.skip_recovery_amount()
        # Only already-installed fixed losses. No future card/drink supply,
        # guessed future health recovery, or future draws are budgeted here.
        for offset in range(1,remaining+1):
            values=dict(hp_min=hp,shield_min=model.shield,focus_min=model.focus,motivation_min=model.motivation,
                        impression_min=max(0,model.impression-offset),hand_trouble_min=0)
            selected=matching_hooks(hooks,'turn_start',values,model.remaining_turns-offset)
            for hook in selected:
                hook['uses']+=1
                for e in hook['effects']:
                    if e['op']=='hp_loss':
                        lost=min(hp,e['value']);passive_loss+=lost;hp-=lost
    available=model.hp+model.skip_recovery_amount();needed=core+passive_loss;shortfall=max(0,needed-available)
    return dict(core_hp=core,installed_passive_loss=passive_loss,available_hp=available,shortfall=shortfall,
        penalty=settings['weight']*shortfall*shortfall,cores=rows,
        scope='Unweighted feasible-window cost buffer; no draw probabilities, exact payment plan or native score claim')
