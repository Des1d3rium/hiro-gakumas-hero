"""Pinned support skill resolution and a bounded deterministic event evaluator.

Master ProduceTrigger has no predicate AST. Exact accepted IDs below are
cross-checked against the referenced skill descriptions, never parsed as code.
"""
from copy import deepcopy
from functools import lru_cache

from .catalog import Catalog
from .core import Unsupported
from .customize import nondefault
from .growth_scaling import checked,parameter_caps

TABLES={t:'SupportCardProduceSkillLevel'+t for t in ('Dance','Vocal','Visual','Assist')}
EXECUTION_SCOPE='growth-only-counterfactual'
EXAM_EXECUTION_SCOPE='growth-and-exam'
ATTR={'Vocal':'Vo','Dance':'Da','Visual':'Vi'}
SCALARS={'ProduceEffectType_'+a+'Addition':v for a,v in ATTR.items()}
SIMPLE={
    'p_trigger-start_customize','p_trigger-customize_produce_card',
    'p_trigger-start_present','p_trigger-start_shop','p_trigger-start_shop-no_description',
    'p_trigger-end_step_event_activity','p_trigger-end_step_event_school','p_trigger-start_refresh',
    'p_trigger-buy_shop_item_produce_card','p_trigger-buy_shop_item_produce_drink',
    'p_trigger-get_produce_drink','p_trigger-end_audition','p_trigger-change_produce_card',
    'p_trigger-get_produce_card-0000_0000-p_card_search-deck_all',
    'p_trigger-upgrade_produce_card-0000_0000-p_card_search-deck_all',
    'p_trigger-delete_produce_card-0000_0000-p_card_search-deck_all',
    'p_trigger-end_lesson_before_present-lesson','p_trigger-end_lesson-lesson_sp'}
LESSONS={}
for label,attribute in [('vocal','Vo'),('dance','Da'),('visual','Vi')]:
    for suffix,sp in [('',None),('_sp',True),('_normal',False)]:
        LESSONS['p_trigger-end_lesson-lesson_'+label+suffix]=(attribute,sp)
    LESSONS['p_trigger-end_lesson_before_present-lesson_'+label+'_sp']=(attribute,True)
CATEGORIES={}
for action in ('get','delete','upgrade'):
    for label,category in [('mental','MentalSkill'),('active','ActiveSkill')]:
        CATEGORIES['p_trigger-'+action+'_produce_card-0000_0000-p_card_search-'+label+'_skill-deck_all']='ProduceCardCategory_'+category


def exact(value,fields,label):
    if not isinstance(value,dict) or set(value)!=fields:raise Unsupported('Exact '+label+' fields required')


@lru_cache(maxsize=4)
def level_rows(commit):
    c=Catalog()
    try:
        if c.info['commit']!=commit:raise Unsupported('Support master changed')
        index={}
        for table in TABLES.values():
            for r in c.rows(table):index.setdefault((table,r['supportCardId']),[]).append(r)
        return index
    finally:c.db.close()


def compile_card(c,card_id,level,limit_rank,plan):
    checked(level,'Support level');card=c.one('SupportCard',card_id)
    if card['planType'] not in ('ProducePlanType_Common',plan):raise Unsupported('Support belongs to another plan')
    limits=[r for r in c.by_id('SupportCardLevelLimit',card['supportCardLevelLimitId']) if r['rank']==limit_rank]
    if len(limits)!=1 or not 1<=level<=limits[0]['levelLimit']:raise Unsupported('Support level exceeds actual limit rank')
    suffix=card['type'].replace('SupportCardType_','')
    if suffix not in TABLES:raise Unsupported('Support type not ported')
    active={}
    for row in level_rows(c.info['commit']).get((TABLES[suffix],card_id),[]):
        if row['supportCardLevel']>level:continue
        prior=active.get(row['order'])
        if prior and prior['supportCardLevel']==row['supportCardLevel']:raise Unsupported('Ambiguous support skill tier')
        if not prior or row['supportCardLevel']>prior['supportCardLevel']:active[row['order']]=row
    skills=[]
    for order,row in sorted(active.items()):
        raw=[r for r in c.by_id('ProduceSkill',row['produceSkillId']) if r['level']==row['produceSkillLevel']]
        if len(raw)!=1:raise Unsupported('Missing exact support skill level')
        skill=raw[0];entries=[]
        for i in (1,2,3):
            effect_id=skill['produceEffectId'+str(i)];trigger_id=skill['produceTriggerId'+str(i)]
            if bool(effect_id)!=bool(trigger_id):raise Unsupported('Partial support trigger/effect')
            if effect_id:entries.append(dict(effect=c.one('ProduceEffect',effect_id),trigger=c.one('ProduceTrigger',trigger_id),rate=skill['activationRatePermil'+str(i)]))
        if len(entries)!=1 or entries[0]['rate'] or skill['planType'] not in ('ProducePlanType_Common',plan) or nondefault(skill['produceType']) or nondefault(skill['produceSplitType']):
            raise Unsupported('Support skill shape/plan/random activation not ported')
        skills.append(dict(order=order,id=skill['id'],level=skill['level'],limit=checked(skill['activationCount'],'Support activation limit'),
            tier=deepcopy(row),description=deepcopy(skill['produceDescriptions']),**entries[0]))
    return dict(card=deepcopy(card),skills=skills,master_commit=c.info['commit'])


def validate_supports(state,c,require_scope=True):
    if 'supports' not in state:
        if 'support_scope' in state:raise Unsupported('Support execution scope without support slots')
        return []
    if require_scope and state.get('support_scope') not in (EXECUTION_SCOPE,EXAM_EXECUTION_SCOPE):
        raise Unsupported('Support exam upgrades require a bound packet or explicit growth-only-counterfactual scope')
    if require_scope and state.get('support_scope')==EXAM_EXECUTION_SCOPE:
        from .support_upgrades import packet_supports
        if 'support_exam' not in state:raise Unsupported('Effective per-card exam support packet required')
        actual=packet_supports(state['support_exam'],c)
        if [r['support_card_id'] for r in actual]!=[r['card_id'] for r in state['supports']]:raise Unsupported('Exam support packet differs from current ordered support slots')
    elif require_scope and 'support_exam' in state:raise Unsupported('Exam support packet contradicts execution scope')
    rows=state['supports'];seen=set();positions=set();rental=0;compiled=[]
    if not isinstance(rows,list) or len(rows)>6:raise Unsupported('Need at most six explicit support slots')
    plan=c.one('IdolCard',state['idol_card_id'])['planType']
    for s in rows:
        exact(s,{'position','card_id','level','limit_rank','is_rental','skills'},'support slot')
        if not isinstance(s['card_id'],str) or not s['card_id'] or not isinstance(s['limit_rank'],str):raise Unsupported('Support identity/rank must be strings')
        checked(s['position'],'Support position',5)
        if s['position'] in positions or s['card_id'] in seen:raise Unsupported('Duplicate support slot/card')
        if type(s['is_rental']) is not bool:raise Unsupported('Support rental flag must be boolean')
        rental+=s['is_rental'];positions.add(s['position']);seen.add(s['card_id'])
        card=compile_card(c,s['card_id'],s['level'],s['limit_rank'],plan)
        if not isinstance(s['skills'],list) or len(s['skills'])!=len(card['skills']):raise Unsupported('Need every current support skill and explicit counter')
        for actual,expected in zip(s['skills'],card['skills']):
            exact(actual,{'order','id','level','trigger_count'},'current support skill')
            if any(actual[k]!=expected[k] for k in ('order','id','level')):raise Unsupported('Support skills differ from exact active tiers')
            count=checked(actual['trigger_count'],'Support trigger count')
            if expected['limit'] and count>expected['limit']:raise Unsupported('Support counter exceeds limit')
            if expected['trigger']['phaseType']=='ProducePhaseType_ProduceStart' and count!=expected['limit']:
                raise Unsupported('Need explicit already-applied startup support counters; no automatic initial production')
        compiled.append(card)
    if rental>1:raise Unsupported('Only one rental support is allowed')
    return compiled


def trigger_matches(trigger,context,c):
    key=trigger['id']
    if key=='p_trigger-end_lesson-lesson_sp':return context.get('sp') is True
    if key in SIMPLE:return True
    if key in LESSONS:
        attribute,sp=LESSONS[key]
        return context.get('attribute')==attribute and (sp is None or context.get('sp') is sp)
    if key in CATEGORIES:
        spec=context.get('card')
        if not isinstance(spec,dict):raise Unsupported('Support card trigger needs exact affected card')
        return c.card(spec['card_id'],spec['upgrade'])['category']==CATEGORIES[key]
    raise Unsupported('Support trigger condition not ported: '+key)


def phase_pending(state,phase,c):
    return any(sk['trigger']['phaseType']=='ProducePhaseType_'+phase and (not sk['limit'] or raw['trigger_count']<sk['limit'])
        for slot,card in zip(state.get('supports',[]),validate_supports(state,c)) for raw,sk in zip(slot['skills'],card['skills']))


def apply_phase(state,phase,context,c):
    cards=validate_supports(state,c);after=deepcopy(state);trace=[]
    for slot,card in zip(after.get('supports',[]),cards):
        for raw,sk in zip(slot['skills'],card['skills']):
            if sk['trigger']['phaseType']!='ProducePhaseType_'+phase or sk['limit'] and raw['trigger_count']>=sk['limit']:continue
            if not trigger_matches(sk['trigger'],context,c):continue
            e=sk['effect'];kind=e['produceEffectType'];value=checked(e['effectValueMin'],'Support scalar')
            allowed={'id','produceEffectType','effectValueMin','effectValueMax'}
            if e['effectValueMax']!=value or any(nondefault(v) for k,v in e.items() if k not in allowed):raise Unsupported('Support effect payload not ported')
            before=deepcopy({k:after.get(k) for k in ('parameters','hp','produce_points')})
            if kind in SCALARS:
                attribute=SCALARS[kind]
                if 'parameters' not in after:raise Unsupported('Support parameter effect needs current parameters')
                cap=parameter_caps(after,c)[attribute];after['parameters'][attribute]=min(cap,after['parameters'][attribute]+value)
            elif kind=='ProduceEffectType_StaminaRecoverFix':after['hp']=min(after['max_hp'],after['hp']+value)
            elif kind=='ProduceEffectType_ProducePointAdditionDisableTrigger':after['produce_points']+=value
            else:raise Unsupported('Support phase effect not ported: '+kind)
            raw['trigger_count']+=1
            trace.append(dict(card_id=slot['card_id'],position=slot['position'],skill_id=sk['id'],level=sk['level'],
                trigger_id=sk['trigger']['id'],phase=phase,context=deepcopy(context),effect=deepcopy(e),
                trigger_count=raw['trigger_count'],before=before,after={k:deepcopy(after.get(k)) for k in before}))
    return after,trace


class SupportMixin:
    def _supports(self,phase,context=None):
        if 'supports' not in self.state:return
        c=Catalog()
        try:after,trace=apply_phase(self.state,phase,context or {},c)
        finally:c.db.close()
        self.state=after;self.support_trace.extend(trace)


def profile(document,c):
    from .finals import source
    exact(document,{'provenance','idol_card_id','supports'},'support profile');source(document['provenance'])
    compiled=validate_supports(document,c,require_scope=False)
    return dict(provenance=deepcopy(document['provenance']),master_commit=c.info['commit'],
        input=deepcopy(document),resolved=compiled,unexecuted_mechanisms=['Exam hand upgrades need a separately bound current per-card probability packet','Server support-event scheduling','Startup totals/unknown predicates'],
        scope='Exact active support tiers and explicit current counters; startup totals are already applied, not summed again. '
              'Unknown conditions/effects, server support-event scheduling, HIF counter resets and owned availability are not inferred.')
