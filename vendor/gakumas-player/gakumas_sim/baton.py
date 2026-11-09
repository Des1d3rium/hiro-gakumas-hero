"""Source-bound HIF creation component, called at an EXPLICIT ExamStartExam boundary.

This is not automatic Battle initialization. In particular, it does not assume
that the old after-opening fixture gate is the native trigger time.
"""
from copy import deepcopy
from .core import NativeRandom, Unsupported
from .hif import HIFPlanner, integer
from .catalog import Catalog
from .passives import plain


BATONS = ('pitem_01-3-266-0', 'pitem_01-3-267-0', 'pitem_02-3-268-0',
          'pitem_02-3-269-0', 'pitem_03-3-270-0', 'pitem_03-3-271-0')


def pool_model(catalog, report):
    """Validate the exact supported shape; retain un-reduced, ordered copies."""
    e=report['evidence']
    plain(e['produce_effect'], {'effectType','effectTurn','produceExamStatusEnchantId'}, 'Baton item effect')
    if e['produce_effect']['effectType']!='ProduceItemEffectType_ExamStatusEnchant' or e['produce_effect']['effectTurn']!=-1:
        raise Unsupported('Baton outer installation not ported')
    plain(e['enchant'], {'produceExamTriggerId','produceExamEffectIds'}, 'Baton enchant')
    plain(e['trigger'], {'phaseTypes'}, 'Baton trigger')
    effect=e['effect']
    plain(effect, {'effectType','effectValue1','produceCardSearchId','movePositionType','pickRangeType',
                   'pickCountReferenceProduceCardSearchId','pickCountType','pickCountMin','pickCountMax'}, 'Baton creation')
    if effect['effectValue1']!=1 or effect['pickRangeType']!='ProducePickRangeType_Random':
        raise Unsupported('Baton multiplicity/range not ported')
    plain(e['count_search'], {'cardPositionType'}, 'Baton count search')
    source=e['source_search']
    plain(source, {'cardPositionType','orderType','produceCardRandomPoolId','produceCardPoolId'}, 'Baton pool search')
    if source['orderType']!='ProduceCardOrderType_Random' or source['produceCardPoolId']!=report['random_pool']:
        raise Unsupported('Baton pool/order mismatch')
    pool=catalog.one('ProduceCardPool',source['produceCardPoolId'])
    if set(pool)!={'id','produceCardRatios'} or not pool['produceCardRatios']:
        raise Unsupported('Unsupported ProduceCardPool shape')
    rows=deepcopy(pool['produceCardRatios'])
    for row in rows:
        if set(row)!={'id','upgradeCount','ratio'} or not isinstance(row['id'],str) or not row['id']:
            raise Unsupported('Invalid baton pool row')
        integer(row['upgradeCount'],'pool upgrade');integer(row['ratio'],'pool ratio',1)
        catalog.card(row['id'],row['upgradeCount']) # exact variant must exist
    total=sum(row['ratio'] for row in rows)
    if total>10000:raise Unsupported('Baton candidate budget exceeded')
    return dict(table='ProduceCardPool',id=pool['id'],rows=rows,expanded_count=total,
                commit=catalog.info['commit'],
                algorithm='order-random stable sort; quantity; pick-random stable sort/take; batch insert',
                timing='explicit ExamStartExam boundary; not automatically bound to Battle.reset')


def coverage(catalog, model):
    """Generating identities does not imply their effects are executable."""
    from .master_adapter import MasterAdapter
    adapter=MasterAdapter(catalog);result=[]
    for row in model['rows']:
        key=row['id']+'@'+str(row['upgradeCount'])
        try:
            adapter.card(row['id'],row['upgradeCount'])
            result.append(dict(variant=key,executable=True))
        except Unsupported as error:
            result.append(dict(variant=key,executable=False,reason=str(error)))
    return result


def candidate_definitions(adapter,passives):
    """Admit ALL outcomes before reset, never only the cards actually sampled."""
    result={}
    for bundle in passives:
        for init in bundle.get('initializers',[]):
            for row in init['pool_model']['rows']:
                key=row['id']+'@'+str(row['upgradeCount'])
                result[key]=deepcopy(adapter.card(row['id'],row['upgradeCount']))
    return result


def bind_baton(document,item_id):
    """Add an explicitly supplied item to a source-checked existing HIF loadout."""
    from .master_adapter import MasterAdapter,validate_master_document
    from .passives import compile_loadout
    from .core import Battle
    if item_id not in BATONS:raise Unsupported('Need an exact HIF baton item')
    if document.get('provenance',{}).get('kind')!='master-derived' or 'loadout' not in document:
        raise Unsupported('Baton binding requires an explicit master-derived HIF loadout')
    validate_master_document(document)
    result=deepcopy(document)
    if item_id in result['loadout']['items']:raise Unsupported('Baton already installed')
    result['loadout']['items'].append(item_id)
    adapter=MasterAdapter()
    try:
        result['passives']=compile_loadout(adapter.catalog,result['loadout'])
        result['definitions'].update(candidate_definitions(adapter,result['passives']))
    finally:adapter.catalog.db.close()
    Battle(result) # Recheck complete possible outcome admission and phase guards.
    return result


def generate(item_id, draw_pile, rng, *, occupied_ids=None, catalog=None):
    """Return a new draw pile without mutating the input; consume the shared RNG.

    draw_pile: opaque unique physical instance IDs in draw order. The caller must
    supply the actual zone AT effect execution. All other owned instance IDs can
    be supplied to prevent generated-instance collisions.
    """
    if not isinstance(draw_pile,list) or any(not isinstance(c,str) or not c for c in draw_pile) or len(set(draw_pile))!=len(draw_pile):
        raise Unsupported('Baton needs unique explicit draw-pile instance IDs')
    if not isinstance(rng,NativeRandom):raise Unsupported('Baton needs caller-owned NativeRandom')
    occupied=list(draw_pile) if occupied_ids is None else occupied_ids
    if (not isinstance(occupied,list) or any(not isinstance(c,str) or not c for c in occupied)
        or len(set(occupied))!=len(occupied) or not set(draw_pile)<=set(occupied)):
        raise Unsupported('Invalid complete occupied instance IDs')
    owned=catalog is None;c=catalog or Catalog()
    try:
        report=HIFPlanner(catalog=c).baton(item_id)
        model=report['pool_model']
        candidates=[]
        for row in model['rows']:
            for _ in range(row['ratio']):
                candidates.append(dict(pool_copy=len(candidates),card_id=row['id'],upgrade=row['upgradeCount']))
        shortage=max(0,report['minimum_draw_pile']-len(draw_pile))
        count=min(shortage,len(candidates))
        new_ids=['baton:'+item_id+':'+str(i) for i in range(count)]
        # All admission checks precede RNG mutation, including every new identity.
        if set(new_ids)&set(occupied):raise Unsupported('Generated baton instance ID collision')
        before=dict(state=rng.state,calls=rng.calls)
        ordered=sorted(((rng.signed(),card) for card in candidates),key=lambda pair:pair[0])
        after_order=dict(state=rng.state,calls=rng.calls)
        picked_count=rng.integer(shortage,shortage+1)
        ranked=sorted(((rng.signed(),card) for _,card in ordered),key=lambda pair:pair[0])
        chosen=[card for _,card in ranked[:min(picked_count,len(ranked))]]
        after_pick=dict(state=rng.state,calls=rng.calls)
        generated=[dict(card,instance=identity) for card,identity in zip(chosen,new_ids)]
        # Lazy Select is forced by OrderBy before the first Insert: all bounds
        # therefore see the SAME original count, including the empty interval.
        inserts=[dict(instance=card['instance'],index=rng.integer(0,len(draw_pile))) for card in generated]
        result=list(draw_pile)
        for row in sorted(inserts,key=lambda row:row['index']):result.insert(row['index'],row['instance'])
        return dict(item_id=item_id,phase='ExamStartExam',draw_pile_before=list(draw_pile),draw_pile_after=result,
                    generated=generated,pool_model=model,
                    trace=dict(before=before,after_order=after_order,after_pick=after_pick,
                               after_insert=dict(state=rng.state,calls=rng.calls),insert_indices=inserts),
                    scope='ID generation/movement only; does not execute card effects, advance turns or settle rounds')
    finally:
        if owned:c.db.close()
