"""Client support hand-upgrade planning/execution, with supplied effective rates.

Native loop: HandAdd.AddList order, then SupportCardList order. Every eligible
pair consumes the shared RNG, even at 0/1000; successes reserve support IDs
immediately, and the upgrade command executes after planning the batch.
"""
from copy import deepcopy
import hashlib,json

from .catalog import Catalog
from .core import Unsupported
from .customize import nondefault
from .growth_scaling import checked

ATTRS={'ProduceParameterType_Vocal':'Vo','ProduceParameterType_Dance':'Da','ProduceParameterType_Visual':'Vi','ProduceParameterType_Unknown':None}
NATIVE='native-support-upgrade';PUBLIC='public-support-upgrade'


def exact(value,keys,label):
    if not isinstance(value,dict) or set(value)!=set(keys):raise Unsupported('Exact '+label+' fields required')


def packet_supports(packet,c):
    exact(packet,{'provenance','cards'},'exam support packet')
    p=packet['provenance'];exact(p,{'kind','reference'},'exam support source')
    if p['kind'] not in ('local-fixture','observed-progress') or not isinstance(p['reference'],str) or not p['reference'].strip():raise Unsupported('Explicit exam support source required')
    if not isinstance(packet['cards'],list) or not 1<=len(packet['cards'])<=6:raise Unsupported('Need one to six exam support cards')
    seen=set();result=[]
    for row in packet['cards']:
        exact(row,{'supportCardId','produceCardUpgradePermil'},'effective exam support')
        key=row['supportCardId']
        if not isinstance(key,str) or not key or key in seen:raise Unsupported('Invalid/duplicate exam support identity')
        seen.add(key);checked(row['produceCardUpgradePermil'],'Effective support upgrade permil',1000000)
        card=c.one('SupportCard',key);search=c.one('ProduceCardSearch',card['upgradeProduceCardSearchId'])
        if search['id']!='p_card_search-hand' or search['cardPositionType']!='ProduceCardPositionType_Hand' or any(nondefault(v) for k,v in search.items() if k not in {'id','cardPositionType','produceDescriptions'}):
            raise Unsupported('Support card search is not the pinned plain-hand rule')
        if card['produceCardUpgradeLessonParameterType'] not in ATTRS:raise Unsupported('Unknown support parameter filter')
        result.append(dict(support_card_id=key,parameter=ATTRS[card['produceCardUpgradeLessonParameterType']],
            card_search_id=search['id'],upgrade_permil=row['produceCardUpgradePermil']))
    return result


def variant_key(source,grade):
    key=source['card_id']+'@'+str(grade);choices=source.get('customization',{}).get('customizations',[])
    if choices:key+=':'+hashlib.sha256(json.dumps(choices,sort_keys=True).encode()).hexdigest()
    return key


def expand_definitions(document,c):
    from .master_adapter import MasterAdapter
    adapter=MasterAdapter(c);definitions=deepcopy(document['definitions']);graph={};pending=list(definitions)
    for key in pending:
        source=definitions[key].get('provenance',{})
        if source.get('table')!='ProduceCard':raise Unsupported('Exam support versions require exact master cards')
        grade=source['upgrade'];choices=source.get('customization',{}).get('customizations',[])
        rows=[r for r in c.by_id('ProduceCard',source['card_id']) if r['upgradeCount']==grade+1]
        if not rows:continue
        target=variant_key(source,grade+1);expected=adapter.card(source['card_id'],grade+1,choices)
        if target in definitions and definitions[target]!=expected:raise Unsupported('Support upgrade variant identity collision')
        if target not in definitions:definitions[target]=expected;pending.append(target)
        graph[key]=target
    return definitions,graph


def bind(document,packet,c=None):
    own=c is None;c=c or Catalog()
    try:
        if document.get('provenance',{}).get('kind')!='master-derived' or document.get('exam_bonus',{}).get('kind') not in ('native-audition','displayed-audition'):
            raise Unsupported('Native support upgrades require sourced audition order')
        result=deepcopy(document);supports=packet_supports(packet,c);definitions,graph=expand_definitions(result,c)
        result['definitions']=definitions
        result['support_upgrades']=dict(kind=NATIVE,master_commit=c.info['commit'],input=deepcopy(packet),supports=supports,next_definitions=graph)
        validate_support_plan(result,supports,c)
        return result
    finally:
        if own:c.db.close()


def validate_support_plan(document,supports,c):
    plan=c.one('IdolCard',document['exam_bonus']['idol_card_id'])['planType']
    if any(c.one('SupportCard',s['support_card_id'])['planType'] not in ('ProducePlanType_Common',plan) for s in supports):
        raise Unsupported('Exam support belongs to another idol plan')


def validate(document,c=None):
    config=document.get('support_upgrades')
    if config is None:return
    own=c is None;c=c or Catalog()
    try:
        if config.get('kind')==NATIVE:
            exact(config,{'kind','master_commit','input','supports','next_definitions'},'native support binding')
            if document.get('provenance',{}).get('kind')!='master-derived' or document.get('exam_bonus',{}).get('kind') not in ('native-audition','displayed-audition') or config['master_commit']!=c.info['commit']:
                raise Unsupported('Unbound native support configuration')
            if any(document['zones'].get(k) for k in ('hand','grave','lost','hold','playing')):
                raise Unsupported('Native support binding requires a pre-deal deck; use explicit public restoration mid-exam')
            definitions,graph=expand_definitions(document,c)
            if definitions!=document['definitions'] or graph!=config['next_definitions'] or packet_supports(config['input'],c)!=config['supports']:
                raise Unsupported('Support rules/versions diverge from master and supplied progress')
            validate_support_plan(document,config['supports'],c)
        elif config.get('kind')==PUBLIC:
            exact(config,{'kind','supports','next_definitions','attributes'},'public support projection')
            # 合成的公开样本世界，或 campus.py 从实机开考快照构造、逐局给定回合属性的 master 派生对局
            if document.get('provenance',{}).get('kind') not in ('synthetic','master-derived'):raise Unsupported('Public support projection requires a sampled world')
            if not isinstance(config['attributes'],list) or not config['attributes'] or any(a not in ('Vo','Da','Vi') for a in config['attributes']):raise Unsupported('Explicit public support attributes required')
        else:raise Unsupported('Unknown support upgrade binding')
        if not isinstance(config['supports'],list) or len(config['supports'])>6:raise Unsupported('At most six support rules required')
        ids=set()
        for s in config['supports']:
            exact(s,{'support_card_id','parameter','card_search_id','upgrade_permil'},'support execution rule')
            if not isinstance(s['support_card_id'],str) or not s['support_card_id'] or s['support_card_id'] in ids or s['parameter'] not in (None,'Vo','Da','Vi') or s['card_search_id']!='p_card_search-hand':raise Unsupported('Invalid support execution rule')
            ids.add(s['support_card_id']);checked(s['upgrade_permil'],'Support permil',1000000)
        graph=config['next_definitions']
        if not isinstance(graph,dict) or any(k not in document['definitions'] or v not in document['definitions'] for k,v in graph.items()):raise Unsupported('Incomplete support version graph')
        for node in graph:
            seen=set();current=node
            while current in graph:
                if current in seen:raise Unsupported('Cyclic support version graph')
                seen.add(current);current=graph[current]
    finally:
        if own:c.db.close()


def reset(battle):
    battle.support_config=deepcopy(battle.document.get('support_upgrades'))
    battle.support_used=[];battle.support_ids={};battle.support_bases=dict(battle.cards);battle.support_events=[]


def attribute(battle):
    if battle.support_config['kind']==NATIVE:
        values=battle.turn_order_trace['turn_attributes']
    else:values=battle.support_config['attributes']
    return values[min(battle.turn,len(values)-1)] # Native clamps extra turns to the final configured slot.


def process_batch(battle,added):
    if not battle.support_config or not added:return
    graph=battle.support_config['next_definitions'];planned=[];attempts=[];parameter=attribute(battle)
    for instance in added:
        if instance not in battle.zones['hand']:raise Unsupported('Support HandAdd target is not in hand')
        if battle.cards[instance] not in graph:continue
        for support in battle.support_config['supports']:
            key=support['support_card_id']
            if key in battle.support_used or support['parameter'] not in (None,parameter):continue
            before=battle.rng.state;value=battle.rng.integer(0,1000);success=value<support['upgrade_permil']
            attempts.append(dict(instance=instance,support_card_id=key,roll=value,permil=support['upgrade_permil'],success=success,rng_before=before,rng_after=battle.rng.state))
            if success:battle.support_used.append(key);planned.append((instance,key))
    applied=[]
    for instance,key in planned:
        old=battle.cards[instance];target=graph.get(old)
        if target is not None:
            battle.support_bases.setdefault(instance,old);battle.cards[instance]=target
            battle.support_ids.setdefault(instance,[]).append(key)
        applied.append(dict(instance=instance,support_card_id=key,before=old,after=battle.cards[instance],applied=target is not None))
    battle.support_events.append(dict(turn=battle.turn,attribute=parameter,hand_added=list(added),attempts=attempts,applied=applied))


def reset_card(battle,instance):
    if battle.support_ids.pop(instance,None):battle.cards[instance]=battle.support_bases[instance]


def public_state(battle):
    return dict(used=list(battle.support_used),upgraded={k:dict(base_definition=battle.support_bases[k],support_ids=list(v)) for k,v in battle.support_ids.items() if k in battle.zones['hand']})


def public_rules(battle):
    result=dict(supports=deepcopy(battle.support_config['supports']),next_definitions=deepcopy(battle.support_config['next_definitions']),current_attribute=attribute(battle))
    if battle.support_config['kind']==NATIVE:result['bonus_by_attribute']=deepcopy(battle.document['exam_bonus']['preview']['parameter_bonus_permil'])
    else:
        # Sampled/visible worlds already have an admitted public attribute schedule.
        result['attributes']=deepcopy(battle.support_config['attributes'][battle.turn:]) or [attribute(battle)]
    return result


def sampled_config(rules,turns):
    if 'attributes' in rules:attrs=list(rules['attributes'])
    else:
        attrs=[rules['current_attribute']]
        for turn in turns[1:]:
            matches=[a for a,b in rules['bonus_by_attribute'].items() if b==turn['bonus_permil']]
            if len(matches)!=1:raise Unsupported('Ambiguous public future support attribute prior is not ported')
            attrs.append(matches[0])
    return dict(kind=PUBLIC,supports=deepcopy(rules['supports']),next_definitions=deepcopy(rules['next_definitions']),attributes=attrs)


def restore_public(battle,state):
    exact(state,{'used','upgraded'},'public support state')
    allowed={s['support_card_id'] for s in battle.support_config['supports']}
    if not isinstance(state['used'],list) or len(state['used'])!=len(set(state['used'])) or set(state['used'])-allowed:raise Unsupported('Invalid public used supports')
    battle.support_used=list(state['used']);battle.support_ids={}
    for instance,row in state['upgraded'].items():
        exact(row,{'base_definition','support_ids'},'public upgraded card')
        if instance not in battle.zones['hand'] or row['base_definition'] not in battle.definitions or not isinstance(row['support_ids'],list) or set(row['support_ids'])-allowed:raise Unsupported('Invalid public support upgrade identity')
        current=row['base_definition']
        for key in row['support_ids']:
            if current not in battle.support_config['next_definitions']:raise Unsupported('Public support upgrade exceeds available versions')
            current=battle.support_config['next_definitions'][current]
        if current!=battle.cards[instance]:raise Unsupported('Public effective card differs from upgrade history')
        battle.support_bases[instance]=row['base_definition'];battle.support_ids[instance]=list(row['support_ids'])
