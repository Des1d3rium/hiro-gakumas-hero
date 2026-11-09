"""Selection-memory inheritance plus explicit server-start progress boundary.

The card switch is a source-based active/inactive test, never a card rewrite.
Startup grants/counters are supplied results, not a fabricated loot generator.
"""
from copy import deepcopy

from .catalog import Catalog
from .core import Unsupported
from .finals import exact, source, text, digest, FinalsSegment
from .growth_scaling import checked, validate_modifiers, parameter_caps

MEMORY_FIELDS = {'id', 'produce_id', 'idol_card_id', 'max_hp', 'parameters', 'parameter_caps',
                 'star', 'cards', 'card_order', 'from_memory', 'items', 'memories',
                 'drink_capacity_addition', 'modifiers', 'unmodelled_effects'}
SCOPE = ('Source-normalized HIF selection-memory projection and supplied startup progress; '
         'not protobuf decoding, server startup grant production, full account admission or live verification')


def conversion_index(catalog):
    by_card = {}
    for row in catalog.rows('ProduceCardConversion'):
        for field, side in [('beforeProduceCardId', 'before'), ('afterProduceCardId', 'after')]:
            key = row[field]
            if key in by_card:
                raise Unsupported('Ambiguous conversion card reference')
            catalog.card(key, 0)
            by_card[key] = (row, side)
    return by_card


def inactive(card_id, conversions, by_card):
    """Native IsDisabledConversion(after first, then before); missing pref=None."""
    if card_id not in by_card:
        return False
    row, side = by_card[card_id]
    value = conversions[row['beforeProduceCardId']]
    if side == 'after':
        return not value['unlocked'] or value['enabled'] is not True
    return value['enabled'] is True


def project_memory(document, catalog=None):
    own = catalog is None
    c = catalog or Catalog()
    try:
        exact(document, {'schema_version', 'provenance', 'memory', 'conversions'}, 'Memory projection input')
        if type(document['schema_version']) is not int or document['schema_version'] != 1:
            raise Unsupported('Memory projection schema version')
        source(document['provenance'])
        memory = document['memory']
        if not isinstance(memory,dict) or not MEMORY_FIELDS<=set(memory) or set(memory)-MEMORY_FIELDS-{'customize_item_ids'}:
            raise Unsupported('Normalized selection memory requires known fields')
        text(memory['id'], 'Selection memory ID')
        if memory['produce_id'] != 'produce-007':
            raise Unsupported('Only HIF selection -> finals inheritance is supported')
        if memory['unmodelled_effects'] != []:
            raise Unsupported('Unmodelled selection memory effects cannot be discarded')
        validate_modifiers(memory['modifiers'])
        checked(memory['max_hp'], 'Memory maximum HP')
        if memory['max_hp'] < 1:
            raise Unsupported('Memory maximum HP must be positive')
        checked(memory['star'], 'Memory star')
        checked(memory['drink_capacity_addition'], 'Memory drink capacity addition')
        exact(memory['parameters'], {'Vo', 'Da', 'Vi'}, 'Memory parameters')
        caps = parameter_caps(memory, c)
        for a, v in memory['parameters'].items():
            if checked(v, 'Memory parameter') > caps[a]:
                raise Unsupported('Memory parameter exceeds current cap')
        if not isinstance(memory['items'], list) or not isinstance(memory['memories'], list):
            raise Unsupported('Memory items and abilities must be explicit lists')
        seen = set()
        for item in memory['items']:
            exact(item, {'id', 'fire_count', 'reaction_count'}, 'Inherited item')
            c.one('ProduceItem', item['id'])
            if item['id'] in seen:
                raise Unsupported('Duplicate inherited item')
            seen.add(item['id'])
            for k in ('fire_count', 'reaction_count'): checked(item[k], 'Inherited item count')
        idol = c.one('IdolCard', memory['idol_card_id'])
        custom_ids=memory.get('customize_item_ids',[])
        if not isinstance(custom_ids,list) or any(not isinstance(k,str) for k in custom_ids) or len(custom_ids)!=len(set(custom_ids)):
            raise Unsupported('Need exact distinct inherited custom item IDs')
        from .growth_items import compile_item
        for key in custom_ids:compile_item(c,key,memory['idol_card_id'])
        if not isinstance(memory['cards'], dict) or not memory['cards']:
            raise Unsupported('Need selection memory physical cards')
        order = memory['card_order']
        if (not isinstance(order, list) or any(not isinstance(k, str) or not k for k in order)
            or len(order) != len(set(order)) or set(order) != set(memory['cards'])):
            raise Unsupported('Memory cards need exact physical order')
        exact(memory['from_memory'], set(order), 'Selection card origin flags')
        if any(type(v) is not bool for v in memory['from_memory'].values()):
            raise Unsupported('Selection card origin must be boolean')
        index = conversion_index(c)
        relevant = set()
        for spec in memory['cards'].values():
            exact(spec, {'card_id', 'upgrade', 'customizations'}, 'Selection card')
            checked(spec['upgrade'], 'Card upgrade')
            raw = c.card(spec['card_id'], spec['upgrade'])
            if raw['planType'] not in ('ProducePlanType_Common', idol['planType']):
                raise Unsupported('Selection card belongs to another plan')
            if not isinstance(spec['customizations'], list):
                raise Unsupported('Selection customizations must be explicit')
            for custom in spec['customizations']:
                exact(custom, {'id', 'count'}, 'Selection card customization')
                if custom['id'] not in raw['produceCardCustomizeIds'] or not c.by_id('ProduceCardCustomize', custom['id']):
                    raise Unsupported('Customization does not belong to selection card')
                if checked(custom['count'], 'Customization count') < 1:
                    raise Unsupported('Customization count must be positive')
            if spec['card_id'] in index:
                relevant.add(index[spec['card_id']][0]['beforeProduceCardId'])
        switches = document['conversions']; exact(switches, {'provenance', 'preferences'}, 'Conversion snapshot')
        source(switches['provenance'])
        prefs = switches['preferences']
        before_ids = {r['beforeProduceCardId'] for r, side in index.values()}
        if not isinstance(prefs, dict) or not relevant <= set(prefs) or set(prefs) - before_ids:
            raise Unsupported('Need explicit preferences for every relevant conversion; unknown keys refused')
        for value in switches['preferences'].values():
            exact(value, {'unlocked', 'enabled'}, 'Conversion preference')
            if type(value['unlocked']) is not bool or (value['enabled'] is not None and type(value['enabled']) is not bool):
                raise Unsupported('Conversion requires boolean unlock and bool/null preference')
        kept = [k for k in order if not inactive(memory['cards'][k]['card_id'], switches['preferences'], index)]
        excluded = [dict(instance=k, card=deepcopy(memory['cards'][k]), reason='disabled_conversion') for k in order if k not in kept]
        base = {k: deepcopy(memory[k]) for k in ('idol_card_id', 'max_hp', 'parameters', 'parameter_caps', 'star',
                'items', 'memories', 'drink_capacity_addition', 'unmodelled_effects')}
        base.update(hp=memory['max_hp'], produce_points=c.one('ProduceSetting', 'p_setting-8')['initialProducePoint'],
                    cards={k: deepcopy(memory['cards'][k]) for k in kept}, card_order=kept, drinks={}, drink_order=[],
                    star_gain_permil=memory['modifiers']['star_bonus_permil'])
        # A projection may have no executable cards; start progress must add a
        # valid deck before GrowthSession admission, never auto-fill an embed table.
        embed = [r for r in c.by_id('ExamContestEmbedProduceCard', c.one('Produce', 'produce-008')['selectionMemoryEmbedProduceCardId'])
                 if r['examEffectType'] == idol['examEffectType']]
        if len(embed) != 1:
            raise Unsupported('Ambiguous HIF embed reference')
        result=dict(memory_digest=digest(document), conversion_digest=digest(switches),
                    before_start_effects=base, excluded=excluded, from_memory=deepcopy(memory['from_memory']),
                    embed_reference=embed[0], master_commit=c.info['commit'],
                    requires_start_progress=True, scope=SCOPE)
        if custom_ids:result['inherited_custom_item_ids']=deepcopy(custom_ids)
        return result
    finally:
        if own: c.db.close()


def bind_start(document, progress):
    projection = project_memory(document)
    exact(progress, {'provenance', 'memory_digest', 'conversion_digest', 'state'}, 'Startup progress')
    source(progress['provenance'])
    for k in ('memory_digest', 'conversion_digest'):
        if progress[k] != projection[k]:
            raise Unsupported('Startup progress bound to different memory or conversion state')
    base = projection['before_start_effects']; current = deepcopy(progress['state'])
    FinalsSegment._validate_state(current)
    from .growth_items import CONTEXT_FIELDS
    if not set(base)<=set(current) or set(current)-set(base)-CONTEXT_FIELDS-{'supports','support_scope','support_exam'}:
        raise Unsupported('Startup progress cannot drop inherited growth context')
    if [i['id'] for i in current.get('customize_items',[])]!=document['memory'].get('customize_item_ids',[]):
        raise Unsupported('Startup custom items differ from selection memory IDs')
    unchanged = ('idol_card_id', 'max_hp', 'parameters', 'parameter_caps', 'memories',
                 'drink_capacity_addition', 'star_gain_permil', 'unmodelled_effects')
    if any(current[k] != base[k] for k in unchanged):
        raise Unsupported('Unported startup alteration of inherited parameters/abilities')
    if current['hp'] != base['max_hp']:
        raise Unsupported('Supported HIF startup requires full HP; startup HP costs are not ported')
    if current['star'] < base['star']:
        raise Unsupported('Inherited star cannot decrease at startup')
    for instance, spec in base['cards'].items():
        if current['cards'].get(instance) != spec:
            raise Unsupported('Startup changed or removed an inherited active card')
    excluded = {e['instance'] for e in projection['excluded']}
    if excluded & set(current['cards']):
        raise Unsupported('Disabled selection card reappeared in startup progress')
    if [k for k in current['card_order'] if k in base['cards']] != base['card_order']:
        raise Unsupported('Startup changed inherited physical order')
    c = Catalog()
    try:
        index = conversion_index(c)
        prefs = document['conversions']['preferences']
        for spec in current['cards'].values():
            if spec['card_id'] in index:
                key = index[spec['card_id']][0]['beforeProduceCardId']
                if key not in prefs:
                    raise Unsupported('Startup introduced a conversion with no explicit preference')
                if inactive(spec['card_id'], prefs, index):
                    raise Unsupported('Startup contains a disabled conversion card')
        items = {i['id']: i for i in current['items']}
        if [i['id'] for i in current['items'] if i['id'] in {v['id'] for v in base['items']}] != [i['id'] for i in base['items']]:
            raise Unsupported('Startup changed inherited item order')
        for old in base['items']:
            new = items.get(old['id'])
            if new is None or any(new[k] < old[k] for k in ('fire_count', 'reaction_count')):
                raise Unsupported('Startup discarded or reset inherited item counts')
        setting = c.one('ProduceSetting', 'p_setting-8')
        if len(current['cards']) < setting['selectionMemoryNeedProduceCardCount']:
            raise Unsupported('Startup needs an explicitly supplied executable deck')
    finally:
        c.db.close()
    refs = [document['provenance'], document['conversions']['provenance'], progress['provenance']]
    provenance = dict(kind='observed-progress' if all(r['kind']=='observed-progress' for r in refs) else 'local-fixture',
                      reference='HIF memory/start bindings ' + projection['memory_digest'] + '; ' + '; '.join(r['reference'] for r in refs))
    result = dict(schema_version=1, provenance=provenance, state=current,
                  modifiers=deepcopy(document['memory']['modifiers']))
    from .growth import GrowthSession
    GrowthSession(result)
    return dict(growth_document=result, projection=projection,
                explicit_start_additions=dict(cards=[k for k in current['card_order'] if k not in base['cards']],
                                              produce_points=current['produce_points'], drinks=current['drink_order'],
                                              star_delta=current['star']-base['star']), scope=SCOPE)


def record_start(document, progress):
    from .evaluate import implementation_manifest
    return dict(schema_version=1, input=deepcopy(document), progress=deepcopy(progress),
                result=bind_start(document, progress), implementation_manifest=implementation_manifest())


def replay_start(record):
    from .evaluate import implementation_manifest
    if record['implementation_manifest'] != implementation_manifest():
        return dict(matches=False, error='Memory-start implementation changed')
    actual = record_start(record['input'], record['progress'])
    diff = [k for k in sorted(set(actual)|set(record)) if digest(actual.get(k)) != digest(record.get(k))]
    return dict(matches=not diff, differences=diff)
