"""Reconstructed actual T1 Main; exchangeable research model with masked future.

Initialization is already settled. This is neither a permanent deck import nor
native RNG reconstruction; sampled future prefix attributes are private.
"""
from collections import Counter
from copy import deepcopy
from fractions import Fraction
import hashlib,json,math,random
from .core import Battle,Unsupported,public_definition
from .exam_snapshot import canonical
from .exam_history import analyze
from .exam_resume import MainPhaseSession
from .catalog import Catalog
from .master_adapter import MasterAdapter
from .support_upgrades import variant_key,restore_public
from .support_posterior import infer_batch
from .passives import PassiveCompiler,runtime_hooks
from .turn_order import allocate


def card_key(row,grade,c):
    raw=c.card(row['card_id'],grade);counts=row['customize_counts'];ids=raw['produceCardCustomizeIds']
    if len(counts) not in (0,len(ids)) or any(type(n) is not int or n<0 for n in counts):raise Unsupported('Opening customization branches disagree')
    rule=MasterAdapter(c).card(row['card_id'],grade,[dict(id=k,count=n) for k,n in zip(ids,counts) if n])
    return variant_key(rule['provenance'],grade)


def restore(opening,observation,boundary,history):
    expected={'schema_version','scope','source_public_sha256','history_sha256','initial_card_specs','initial_total','hand','startup','memories','drinks',
        'resources','bonus_permil','ordinary_draw','hp_witness','sources','basis','native_posterior_admitted','permanent_deck_recovered','actual_clear','opening_sha256'}
    if not isinstance(opening,dict) or set(opening)!=expected or opening['schema_version']!=1:
        raise Unsupported('Exact reconstructed opening fields required')
    if any(opening[k] is not False for k in ('native_posterior_admitted','permanent_deck_recovered','actual_clear')):
        raise Unsupported('Opening reconstruction cannot certify private state or a clear')
    source=deepcopy(opening);claimed=source.pop('opening_sha256')
    if hashlib.sha256(canonical(source).encode()).hexdigest()!=claimed:raise Unsupported('Opening fingerprint mismatch')
    if (opening['scope']!='reconstructed-actual-hif-t1-main' or opening['source_public_sha256']!=observation['public_sha256']
        or opening['history_sha256']!=history['history_sha256']):raise Unsupported('Opening is bound to another actual source')
    proof=analyze(history,observation,boundary);session=MainPhaseSession(observation,boundary)
    first_play=next(e['index'] for e in history['events'] if e['cell_type']==3)
    if [(e['cell_type'],e['source_id']) for e in opening['startup']]!=[(e['cell_type'],e['source_id']) for e in history['events'] if e['index']<first_play and e['cell_type']!=1]:
        raise Unsupported('Initial startup sources differ from frozen history')
    if opening['initial_total']!=len(opening['initial_card_specs']) or Counter(r['card_id'] for r in opening['initial_card_specs'])!=Counter(proof['initial_card_id_composition']):
        raise Unsupported('Opening inventory differs from the conserved initial composition')
    if opening['hand']!=history['headers'][0]['incoming'][:opening['ordinary_draw']]:raise Unsupported('Initial ordinary hand differs from recorded entries')
    changes=[x for r in opening['startup'] if r['cell_type']==14 for x in r['changes']]
    if changes!=[dict(kind=4,effect=e,before=b,after=a) for b,a in [(0,3),(3,6)] for e in (31,42)]:
        raise Unsupported('Unrestored initial custom item effects')
    v=opening['resources'];w=opening['hp_witness']
    if any(type(n) is not int for n in v.values()) or v!=dict(hp=17,max_hp=36,shield=0,score=0,motivation=6,impression=6,actions=2) or w['recorded_start']!=17 or (w['after_first_card'],w['after_soda'])!=(14,12):
        raise Unsupported('Pinned opening resource witnesses required')
    item=next((r for r in opening['startup'] if r['source_id']=='pitem_00-3-010-0'),None)
    if item is None or item['changes']!=[dict(kind=4,effect=14,before=0,after=1)]:raise Unsupported('Initial extra action witness missing')
    if opening['drinks']!=[e['source_id'] for e in history['events'] if e['cell_type']==4]+[r['_id'] for r in observation['drinks']]:
        raise Unsupported('Initial drink multiset differs from consumed and remaining bottles')
    c=Catalog()
    try:
        if opening['ordinary_draw']!=c.one('ExamSetting',observation['scalars']['settingId'])['turnStartDistribute']:
            raise Unsupported('Opening ordinary draw count differs from pinned setting')
        library=session.initial_observation['public_model']['definition_library'];bases=[]
        for r in opening['initial_card_specs']:
            matches=[x for x in observation['cards'].values() if x['card_id']==r['card_id'] and x['customize_counts']==r['customize_counts'] and x['native']['_baseUpgradeCount']==r['base_upgrade']]
            if not matches:raise Unsupported('Initial card has no base-grade/customization witness')
            bases.append(card_key(r,r['base_upgrade'],c))
        hand=[];hand_bases=[]
        for r in opening['hand']:
            target=card_key(r,r['upgrade'],c)
            candidates=[k for k in bases if k.split('@')[0]==r['card_id'] and k.split(':')[1:]==target.split(':')[1:]]
            if not candidates:raise Unsupported('Initial hand lacks a conserved base instance')
            base=candidates[0];bases.remove(base);hand_bases.append(base);hand.append(target)
        s=observation['scalars'];bonus=dict(Vo=s['vocalBonusPermil'],Da=s['danceBonusPermil'],Vi=s['visualBonusPermil'])
        attrs=[k for k,b in bonus.items() if b==opening['bonus_permil']]
        if len(attrs)!=1:raise Unsupported('Ambiguous observed opening attribute')
        weights=dict(Vo=s['vocalConfigParameter'],Da=s['danceConfigParameter'],Vi=s['visualConfigParameter'])
        order=allocate(s['limitTurn'],weights);rules=session.initial_observation['public_model']['support_upgrades']
        posterior=infer_batch(rules,hand_bases,hand,attrs[0])
        hooks=deepcopy(session.runtime['values']['hooks'])
        mode=c.one('Produce',s['produceId']);group=next(g for g in c.rows('ProduceGroup') if s['produceId'] in g['produceIds']);idol=c.one('IdolCard',s['idolCardId'])
        memories=[]
        for r in opening['memories']:
            bundle,_=PassiveCompiler(c).memory(r['id'],r['level'],group,mode,idol);memories.append(bundle)
        # Only the actually observed first-turn installation is admitted.
        startup_mem=[r for r in opening['startup'] if r['cell_type']==8]
        if [b['provenance']['references'][0]['id'] for b in memories]!=[r['id'] for r in opening['memories']]:
            raise Unsupported('Memory provenance identity mismatch')
        if len(memories)!=len(startup_mem) or any(c.one('MemoryAbility',r['id'])['skillId']!=e['source_id'] or r['level']!=e['level'] for r,e in zip(opening['memories'],startup_mem)):
            raise Unsupported('Memory installation differs from observed source')
        hooks.extend(runtime_hooks(memories))
        drinks={str(i):public_definition(MasterAdapter(c).drink(k)) for i,k in enumerate(opening['drinks'])}
    finally:c.db.close()
    result=dict(schema_version=1,scope='actual opening, exchangeable independent-trial research model',source_opening_sha256=claimed,
        resources=deepcopy(v),deck_definitions=sorted(bases),hand_definitions=hand,hand_bases=hand_bases,
        definition_library={k:public_definition(v) for k,v in library.items()},
        supports=deepcopy(rules['supports']),next_definitions=deepcopy(rules['next_definitions']),
        exam_upgrades=deepcopy(session.initial_observation['public_model']['exam_upgrades']),
        support_posterior=posterior,drinks=drinks,hooks=hooks,allocation=order,weights=weights,bonus=bonus,
        current_attribute=attrs[0],base_turns=s['limitTurn'],ordinary_draw=opening['ordinary_draw'],hand_limit=session.initial_observation['hand_limit'],
        skip_recovery=session.initial_observation['skip_recovery'],native_posterior_admitted=False,permanent_deck_recovered=False,actual_clear=False)
    result['packet_sha256']=hashlib.sha256(canonical(result).encode()).hexdigest();return result


class OpeningBattle(Battle):
    def observe(self):
        obs=super().observe()
        if not hasattr(self,'opening_public'):return obs
        p=self.opening_public;prefix=p['base_turns']-3
        if self.turn>=prefix-1:return obs # Only the fixed tail remains.
        attrs=self.support_config['attributes'];left=Counter(p['allocation']['random_prefix_counts'])-Counter(attrs[:self.turn+1])
        average=sum(p['bonus'][a]*n for a,n in left.items())//sum(left.values())
        for i,t in enumerate(obs['public_model']['turns']):
            if self.turn+i<prefix and i:t['bonus_permil']=average
        obs['remaining_bonus_permil']=[t['bonus_permil'] for t in obs['public_model']['turns']]
        obs['public_model']['support_upgrades']['attributes']=[attrs[self.turn]]
        # Explicitly reject legacy rollout's complete-public-schedule contract.
        obs['public_model']['unobserved_attribute_order']=dict(remaining_prefix_counts=dict(left),
            marginal_bonus_permil=average,fixed_tail=p['allocation']['fixed_tail'],scope='marginals only, not sampled future')
        return obs


def sample(packet,particle,*,hp_override=None,omit_drinks=()):
    expected={'schema_version','scope','source_opening_sha256','resources','deck_definitions','hand_definitions','hand_bases','definition_library',
        'supports','next_definitions','exam_upgrades','support_posterior','drinks','hooks','allocation','weights','bonus','current_attribute','base_turns',
        'ordinary_draw','hand_limit','skip_recovery','native_posterior_admitted','permanent_deck_recovered','actual_clear','packet_sha256'}
    if not isinstance(packet,dict) or set(packet)!=expected or any(packet[k] is not False for k in ('native_posterior_admitted','permanent_deck_recovered','actual_clear')):
        raise Unsupported('Exact model-only opening packet required')
    p=deepcopy(packet);claimed=p.pop('packet_sha256')
    if hashlib.sha256(canonical(p).encode()).hexdigest()!=claimed:raise Unsupported('Opening packet fingerprint mismatch')
    if type(particle) is not int or particle<0:raise Unsupported('Nonnegative opening particle required')
    rng=random.Random(int.from_bytes(hashlib.sha256((claimed+'/'+str(particle)).encode()).digest(),'big'))
    pool=[a for a,n in p['allocation']['random_prefix_counts'].items() for _ in range(n)];pool.remove(p['current_attribute']);rng.shuffle(pool)
    attrs=[p['current_attribute']]+pool+p['allocation']['fixed_tail']
    deck=list(p['deck_definitions']);rng.shuffle(deck)
    cards={'opening-hand-'+str(i):d for i,d in enumerate(p['hand_definitions'])}
    zones=dict(hand=list(cards),deck=[])
    for i,d in enumerate(deck):
        name='opening-deck-'+str(i);cards[name]=d;zones['deck'].append(name)
    v=deepcopy(p['resources'])
    if hp_override is not None:
        if type(hp_override) is not int or not 0<=hp_override<=v['max_hp']:raise Unsupported('Bounded explicit HP counterfactual required')
        v['hp']=hp_override
    turns=[dict(draw=0 if i==0 else p['ordinary_draw'],actions=v['actions'] if i==0 else 1,bonus_permil=p['bonus'][a]) for i,a in enumerate(attrs)]
    doc=dict(schema_version=1,id='actual-opening-research-model',provenance=dict(kind='synthetic'),cards=cards,zones=zones,
        definitions=p['definition_library'],turns=turns,state={k:v[k] for k in ('hp','max_hp','shield','score','motivation','impression')},
        hand_limit=p['hand_limit'],skip_recovery=p['skip_recovery'],shuffle=False,opening_initial=False,
        drink_definitions=p['drinks'],drinks={k:k for k in p['drinks']},goal=dict(target_score=0),exam_upgrades=p['exam_upgrades'],
        support_upgrades=dict(kind='public-support-upgrade',supports=p['supports'],next_definitions=p['next_definitions'],attributes=attrs))
    b=OpeningBattle(doc);b.opening_public=p;b.reset(rng.getrandbits(32));b.hooks=deepcopy(p['hooks'])
    states=p['support_posterior']['states'];common=math.lcm(*(s['probability']['denominator'] for s in states))
    weights=[s['probability']['numerator']*(common//s['probability']['denominator']) for s in states];pick=rng.randrange(sum(weights))
    for s,weight in zip(states,weights):
        if pick<weight:chosen=s;break
        pick-=weight
    upgraded={i:dict(base_definition=base,support_ids=ids) for i,base,ids in zip(zones['hand'],p['hand_bases'],chosen['witness_support_ids']) if ids}
    restore_public(b,dict(used=chosen['used'],upgraded=upgraded))
    if len(set(omit_drinks))!=len(omit_drinks) or any(k not in b.drinks for k in omit_drinks):
        raise Unsupported('Omitted drinks must be unique owned model identities')
    for key in omit_drinks:del b.drinks[key]
    b.extra_turn_template=dict(draw=p['ordinary_draw'],actions=1,bonus_permil=p['bonus'][p['allocation']['fixed_tail'][-1]])
    b._validate_zones();return b
