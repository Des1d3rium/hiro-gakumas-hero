"""Bounded HIF custom growth items; separate from support-card abilities."""
from copy import deepcopy

from .core import Unsupported
from .customize import nondefault
from .finals import exact
from .growth_scaling import checked

CONTEXT_FIELDS={'customize_items','next_audition_enchants','permanent_audition_enchants'}
ITEM_FIELDS={'number','id','trigger_count','reaction_count'}
ENCHANT_FIELDS={'item_id','number','activation','enchant_id'}
HISTORY_FIELD='customize_item_history'


def plain(row,allowed,label):
    if any(nondefault(v) for k,v in row.items() if k not in allowed and 'Description' not in k):
        raise Unsupported('Unported custom item '+label)


def compile_item(c,item_id,idol_id):
    r=c.one('ProduceCustomizeItem',item_id);idol=c.one('IdolCard',idol_id)
    plain(r,{'id','name','effectType','planType','assetId1','assetId2','assetId3','assetId4','assetId5',
             'isBase','isTerminal','produceEffectTriggerCount','produceEffectTriggerInterval',
             'produceTriggerId','produceEffectIds','effectGroupIds'},'root')
    trigger=c.one('ProduceTrigger',r['produceTriggerId'])
    if (r['planType']!=idol['planType'] or r['effectType']!='ProduceItemEffectType_ProduceEffect'
        or not (trigger=={'id':trigger['id'],'phaseType':'ProducePhaseType_EndLesson'} or
                trigger=={'id':'p_trigger-start_shop','phaseType':'ProducePhaseType_StartShop'})
        or r['produceEffectTriggerInterval'] or r['produceEffectTriggerCount']<1):
        raise Unsupported('Custom item plan/trigger/interval not ported')
    effects=[];refs=[r,trigger]
    for key in r['produceEffectIds']:
        e=c.one('ProduceEffect',key);refs.append(e);typ=e['produceEffectType']
        if e['isResearch']:raise Unsupported('Research custom effect not ported')
        common={'id','produceEffectType','effectValueMin','effectValueMax','isResearch'}
        if typ in ('ProduceEffectType_ProducePointAddition','ProduceEffectType_StaminaRecoverFix'):
            plain(e,common,'scalar')
            if e['effectValueMin']!=e['effectValueMax']:raise Unsupported('Random custom scalar not ported')
            value=checked(e['effectValueMin'],'Custom scalar')
            effects.append(dict(kind='point' if typ.endswith('ProducePointAddition') else 'recover_fix',value=value,source_id=key))
        elif typ=='ProduceEffectType_ProduceRewardSet':
            plain(e,common|{'produceResourceType','pickRangeType','pickCountMin','pickCountMax'},'reward')
            if (e['produceResourceType']!='ProduceResourceType_ProduceDrink' or e['effectValueMin'] or e['effectValueMax']
                or e['pickCountMin']!=e['pickCountMax'] or e['pickCountMin'] not in (1,2)
                or e['pickCountMin']==2 and e['pickRangeType']!='ProducePickRangeType_Random'
                or e['pickRangeType'] not in ('ProducePickRangeType_Select','ProducePickRangeType_Random')):
                raise Unsupported('Custom item reward not ported')
            if e['pickCountMin']==2:effects.append(dict(kind='random_drink_batch',count=2,source_id=key))
            else:effects.append(dict(kind='drink' if e['pickRangeType'].endswith('_Select') else 'random_drink',source_id=key))
        elif typ in {'ProduceEffectType_ShopProduceDrinkPriceDiscountMultiple',
                     'ProduceEffectType_ShopProduceCardUpgradePriceDiscountMultiple',
                     'ProduceEffectType_ShopProduceCardDeletePriceDiscountMultiple',
                     'ProduceEffectType_ShopProduceCardPriceDiscountMultiple'}:
            card=typ=='ProduceEffectType_ShopProduceCardPriceDiscountMultiple'
            plain(e,common|({'produceCardSearchId'} if card else set()),'shop discount')
            if e['effectValueMin']!=e['effectValueMax']:raise Unsupported('Random custom discount not ported')
            if card:
                search=c.one('ProduceCardSearch',e['produceCardSearchId']);refs.append(search)
                plain(search,{'id','cardPositionType','produceDescriptions'},'discount card search')
                if search['cardPositionType']!='ProduceCardPositionType_DeckAll':raise Unsupported('Filtered card discount not ported')
            resources={'ProduceEffectType_ShopProduceDrinkPriceDiscountMultiple':'ProduceResourceType_ProduceDrink',
                       'ProduceEffectType_ShopProduceCardUpgradePriceDiscountMultiple':'ProduceResourceType_CardUpgrade',
                       'ProduceEffectType_ShopProduceCardDeletePriceDiscountMultiple':'ProduceResourceType_CardDelete',
                       'ProduceEffectType_ShopProduceCardPriceDiscountMultiple':'ProduceResourceType_ProduceCard'}
            effects.append(dict(kind='shop_discount',resource=resources[typ],rate=checked(e['effectValueMin'],'Custom discount',1000),source_id=key))
        elif typ in ('ProduceEffectType_ExamStatusEnchant','ProduceEffectType_ExamPermanentAuditionStatusEnchant'):
            plain(e,common|{'produceExamStatusEnchantId'},'exam enchant')
            if e['effectValueMin'] or e['effectValueMax']:raise Unsupported('Custom enchant scalar not ported')
            start=compile_enchant(c,e['produceExamStatusEnchantId'])
            refs.extend(start['references'])
            effects.append(dict(kind='permanent_audition' if 'PermanentAudition' in typ else 'next_audition',
                                enchant_id=e['produceExamStatusEnchantId'],source_id=key))
        else:raise Unsupported('Custom item effect not ported: '+typ)
    if not effects:raise Unsupported('Custom item has no effects')
    result=dict(id=item_id,limit=r['produceEffectTriggerCount'],effects=effects,references=refs,master_commit=c.info['commit'])
    if trigger['phaseType']=='ProducePhaseType_StartShop':result['phase']='StartShop'
    return result


def compile_enchant(c,key):
    row=c.one('ProduceExamStatusEnchant',key)
    plain(row,{'id','assetId','produceExamTriggerId','produceExamEffectIds'},'start enchant')
    trigger=c.one('ProduceExamTrigger',row['produceExamTriggerId'])
    plain(trigger,{'id','phaseTypes'},'start trigger')
    if trigger['phaseTypes']!=['ProduceExamPhaseType_ExamStartExam']:
        raise Unsupported('Custom start enchant timing not ported')
    values=dict(motivation=0,impression=0);refs=[row,trigger]
    for key in row['produceExamEffectIds']:
        e=c.one('ProduceExamEffect',key);refs.append(e)
        plain(e,{'id','effectType','effectValue1','effectGroupIds'},'start scalar')
        attr={'ProduceExamEffectType_ExamCardPlayAggressive':'motivation','ProduceExamEffectType_ExamReview':'impression'}.get(e['effectType'])
        if attr is None:raise Unsupported('Custom start resource not ported')
        values[attr]=checked(values[attr]+checked(e['effectValue1'],'Start scalar'),'Start resource')
    return dict(enchant_id=row['id'],values=values,references=refs)


def validate_context(state,c):
    if 'customize_items' not in state and any(state.get(k) for k in CONTEXT_FIELDS-{'customize_items'}):
        raise Unsupported('Start enchants require explicit source custom items/counters')
    items=state.get('customize_items',[])
    if not isinstance(items,list):raise Unsupported('Custom items need ordered list')
    compiled={};seen=set()
    for item in items:
        exact(item,ITEM_FIELDS,'Persistent custom item')
        number=checked(item['number'],'Custom item number')
        if number in compiled or item['id'] in seen:raise Unsupported('Duplicate custom item number or ID')
        seen.add(item['id']);r=compile_item(c,item['id'],state['idol_card_id'])
        if checked(item['trigger_count'],'Custom trigger count')>r['limit']:
            raise Unsupported('Custom item trigger limit exceeded')
        if checked(item['reaction_count'],'Custom reaction count'):
            raise Unsupported('Custom reaction-count semantics not ported')
        compiled[number]=(item,r)
    sources={(number,item['id']):(item,r) for number,(item,r) in compiled.items()}
    history=state.get(HISTORY_FIELD,[])
    if not isinstance(history,list):raise Unsupported('Custom item history requires an ordered list')
    edges={(r['parentProduceCustomizeItemId'],r['childProduceCustomizeItemId'])
           for r in c.rows('ProduceCustomizeItemRelationship')} if history else set()
    links={}
    for old in history:
        exact(old,ITEM_FIELDS|{'replaced_by'},'Historical custom item')
        if not isinstance(old['id'],str) or not isinstance(old['replaced_by'],str):raise Unsupported('Historical custom item IDs must be strings')
        number=checked(old['number'],'Historical custom item number');identity=(number,old['id'])
        if number not in compiled or identity in sources or (old['id'],old['replaced_by']) not in edges:
            raise Unsupported('Historical custom item is not a unique direct predecessor')
        r=compile_item(c,old['id'],state['idol_card_id'])
        if checked(old['trigger_count'],'Historical custom trigger count')>r['limit'] or checked(old['reaction_count'],'Historical reaction count'):
            raise Unsupported('Invalid historical custom item counters')
        sources[identity]=(old,r);links[identity]=old['replaced_by']
    for number,item_id in links:
        current=item_id;visited=set()
        while current!=compiled[number][0]['id']:
            if current in visited or (number,current) not in links:
                raise Unsupported('Historical custom item chain does not reach current item')
            visited.add(current);current=links[(number,current)]
    from .custom_discounts import validate_sources
    validate_sources(state,sources)
    seen=set()
    for field,kind in [('next_audition_enchants','next_audition'),('permanent_audition_enchants','permanent_audition')]:
        entries=state.get(field,[])
        if not isinstance(entries,list):raise Unsupported('Audition enchant list required')
        for entry in entries:
            exact(entry,ENCHANT_FIELDS,'Persistent custom enchant')
            checked(entry['number'],'Enchant source number');checked(entry['activation'],'Enchant source activation')
            identity=(entry['number'],entry['activation'],entry['enchant_id'])
            if identity in seen:raise Unsupported('Duplicate custom enchant activation')
            seen.add(identity)
            pair=sources.get((entry['number'],entry['item_id']))
            if pair is None or not 1<=entry['activation']<=pair[0]['trigger_count']:
                raise Unsupported('Enchant source item/count does not match')
            if not any(e['kind']==kind and e.get('enchant_id')==entry['enchant_id'] for e in pair[1]['effects']):
                raise Unsupported('Enchant differs from source item effect/lifetime')
    return compiled


def phase_effects(state,c,phase):
    compiled=validate_context(state,c);pending=[];fired=[]
    for item in state.get('customize_items',[]):
        r=compiled[item['number']][1]
        if r.get('phase','EndLesson')!=phase:continue
        if item['trigger_count']>=r['limit']:continue
        item['trigger_count']+=1
        for effect in r['effects']:
            pending.append(dict(deepcopy(effect),custom_source=dict(item_id=item['id'],number=item['number'],activation=item['trigger_count'])))
        fired.append(dict(number=item['number'],id=item['id'],activation=item['trigger_count']))
    return pending,fired


def end_lesson(state,c):return phase_effects(state,c,'EndLesson')


def phase_pending(state,c,phase):
    compiled=validate_context(state,c)
    return any(r.get('phase','EndLesson')==phase and item['trigger_count']<r['limit'] for item,r in compiled.values())


def start_projection(state,c):
    validate_context(state,c);total=dict(motivation=0,impression=0);sources=[]
    for field in ('next_audition_enchants','permanent_audition_enchants'):
        for entry in state.get(field,[]):
            compiled=compile_enchant(c,entry['enchant_id'])
            for attr,value in compiled['values'].items():total[attr]=checked(total[attr]+value,'Combined start resource')
            sources.append(dict(entry=deepcopy(entry),lifetime=field,compiled=compiled))
    return dict(values=total,sources=sources,master_commit=c.info['commit'])


def bind_battle_start(document,state,c):
    projection=start_projection(state,c)
    if not projection['sources']:return document
    result=deepcopy(document)
    if any(result['state'].get(k,0) for k in projection['values']):raise Unsupported('Cannot apply custom start twice')
    context={k:deepcopy(state.get(k,[])) for k in CONTEXT_FIELDS};context['idol_card_id']=state['idol_card_id']
    if state.get(HISTORY_FIELD):context[HISTORY_FIELD]=deepcopy(state[HISTORY_FIELD])
    result['growth_start']=dict(context=context,projection=projection)
    result['state'].update(projection['values'])
    return result


def validate_battle_start(document,c):
    binding=document.get('growth_start')
    if binding is None:return
    exact(binding,{'context','projection'},'Growth start binding')
    context=binding['context'];exact(context,CONTEXT_FIELDS|{'idol_card_id'}|({HISTORY_FIELD} if HISTORY_FIELD in context else set()),'Growth start context')
    if (document.get('loadout',{}).get('mode') not in ('produce-007','produce-008')
        or document['loadout']['idol_card_id']!=context['idol_card_id']):
        raise Unsupported('Custom start needs matching HIF loadout')
    expected=start_projection(context,c)
    if expected!=binding['projection'] or any(document['state'].get(k,0)!=v for k,v in expected['values'].items()):
        raise Unsupported('Custom start diverges from source projection')
