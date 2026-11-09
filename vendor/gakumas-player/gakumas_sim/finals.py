"""HIF calendar positions 7 -> 8 -> 9 with explicit progress boundaries.

No selection-memory reconstruction, server loot generator, or star-to-protocol
formula is inferred. Quotes and post-R1 progress are environment inputs, kept
separate from locally computed card battles and purchases.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json

from .catalog import Catalog
from .core import Unsupported
from .hif import HIFPlanner, integer
from .master_adapter import MasterAdapter, _sandbox_from_adapter
from .audition import bind_native_audition_battle
from .evaluate import run_battle, implementation_manifest
from .growth_scaling import star_gain,checked
from .supports import validate_supports,apply_phase,phase_pending
from .permanent_upgrade import upgrade_target
from .interval_customize import validate_service as validate_customize_service, select_target as select_customize_target

PATCH = 'pitem_00-3-265-0'
PRODUCT_FIELDS = {'positionNumber', 'purchased', 'resourceType', 'resourceId',
                  'upgradeCount', 'staminaRecoverValue', 'price', 'nextPrice'}
STATE_FIELDS = {'idol_card_id', 'hp', 'max_hp', 'produce_points', 'star', 'cards',
                'card_order', 'drinks', 'drink_order', 'items', 'memories',
                'drink_capacity_addition', 'unmodelled_effects'}
OPTIONAL_STATE_FIELDS = {'parameters', 'parameter_caps', 'star_gain_permil', 'customize_items',
                         'next_audition_enchants', 'permanent_audition_enchants','supports','support_scope','support_exam',
                         'customize_item_history','custom_shop_discounts'}
SCOPE = ('HIF R1/interval/R2 component with supplied progress/quotes/protocol values; '
         'not full growth, account admission, server reconciliation, or a win probability')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def exact(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise Unsupported(name + ' requires exactly ' + ','.join(sorted(fields)))


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise Unsupported(name + ' needs a nonempty string')


def source(value):
    exact(value, {'kind', 'reference'}, 'Progress provenance')
    if value['kind'] not in ('local-fixture', 'observed-progress'):
        raise Unsupported('Progress must distinguish fixture from observed input')
    text(value['reference'], 'Progress reference')


def patch_rule(catalog):
    """Compile the exact pinned growth branch; descriptions are not executed."""
    item = catalog.one('ProduceItem', PATCH)
    trigger = catalog.one('ProduceTrigger', item['produceTriggerId'])
    effects = [catalog.one('ProduceItemEffect', k) for k in item['produceItemEffectIds']]
    if (len(effects) != 1
        or trigger != {'id': item['produceTriggerId'], 'phaseType': 'ProducePhaseType_GetProduceCard'}
        or item['produceTriggerIds'] or item['fireInterval'] or item['fireLimit'] != 20
        or item['skills'] != [dict(produceTriggerId='', produceItemEffectId=effects[0]['id'])]):
        raise Unsupported('HIF patch trigger/limit changed')
    effect = effects[0]
    if (effect['effectType'] != 'ProduceItemEffectType_ProduceEffect'
        or effect['effectTurn'] or effect['effectCount'] or effect['produceExamStatusEnchantId']):
        raise Unsupported('HIF patch payload changed')
    leaf = catalog.one('ProduceEffect', effect['produceEffectId'])
    expected = dict(id=leaf['id'], produceEffectType='ProduceEffectType_StarAddition',
                    effectValueMin=10, effectValueMax=10, produceResourceType='ProduceResourceType_Unknown',
                    produceRewards=[], produceCardSearchId='', produceExamStatusEnchantId='',
                    produceStepEventDetailId='', pickRangeType='ProducePickRangeType_Unknown',
                    pickCountMin=0, pickCountMax=0, isResearch=False)
    if leaf != expected:
        raise Unsupported('HIF patch StarAddition branch changed')
    return dict(value=leaf['effectValueMin'], limit=item['fireLimit'],
                master_commit=catalog.info['commit'], references=[item, trigger, effect, leaf])


class FinalsSegment:
    """A bounded event host, starting immediately before the first finals exam."""
    def __init__(self, document):
        exact(document, {'schema_version', 'provenance', 'state'}, 'Finals input')
        if document['schema_version'] != 1:
            raise Unsupported('Finals schema_version must be 1')
        source(document['provenance'])
        self.initial_document = deepcopy(document)
        self.state = deepcopy(document['state'])
        self._validate_state(self.state)
        self.position = 7
        self.phase = 'round1'
        self.quote = None
        self.seen_quotes = set()
        self.bought_positions = set()
        self.events = []
        self.battles = []
        self.first_settled = None
        self.support_trace=[]
        self.interval_upgrade_count = None
        self.interval_upgrade_reset = False
        self.interval_customizing = None
        self.interval_customize_reset = False
        self.interval_customize_recheck = set()

    @staticmethod
    def _validate_state(state, produce_id='produce-008'):
        if produce_id not in ('produce-007', 'produce-008'):
            raise Unsupported('Persistent state requires an explicit HIF mode')
        if (not isinstance(state, dict) or not STATE_FIELDS <= set(state)
            or set(state) - STATE_FIELDS - OPTIONAL_STATE_FIELDS):
            raise Unsupported('Persistent produce state requires known fields and supported growth context')
        if 'parameters' in state:
            exact(state['parameters'], {'Vo', 'Da', 'Vi'}, 'Persistent parameters')
            for name, value in state['parameters'].items():
                integer(value, name + ' parameter')
        if 'parameter_caps' in state:
            exact(state['parameter_caps'], {'Vo', 'Da', 'Vi'}, 'Persistent parameter caps')
            for name, value in state['parameter_caps'].items():
                checked(value, name + ' cap')
                if state.get('parameters', {}).get(name, 0) > value:
                    raise Unsupported('Parameter exceeds supplied current cap')
        if 'star_gain_permil' in state: checked(state['star_gain_permil'], 'star gain permil', 1000000)
        for key in ('hp', 'max_hp', 'produce_points', 'star', 'drink_capacity_addition'):
            integer(state[key], key)
        if state['max_hp'] < 1 or state['hp'] > state['max_hp']:
            raise Unsupported('Invalid persistent stamina')
        if state['unmodelled_effects'] != []:
            raise Unsupported('Unmodelled growth/support modifiers cannot be silently discarded')
        if not isinstance(state['cards'], dict) or not state['cards']:
            raise Unsupported('Need permanent physical cards')
        if not isinstance(state['drinks'], dict) or not isinstance(state['items'], list):
            raise Unsupported('Need drink instances and item counters')
        for inventory, order in [('cards', 'card_order'), ('drinks', 'drink_order')]:
            if (not isinstance(state[order], list) or any(not isinstance(k, str) for k in state[order])
                or len(state[order]) != len(set(state[order])) or set(state[order]) != set(state[inventory])):
                raise Unsupported('Need explicit exact physical order: ' + order)
        c = Catalog()
        try:
            adapter = MasterAdapter(c)
            from .growth_items import validate_context
            validate_context(state,c)
            validate_supports(state,c)
            setting = c.one('ProduceSetting', c.one('Produce', produce_id)['produceSettingId'])
            if len(state['drinks']) > setting['produceDrinkPossessLimit'] + state['drink_capacity_addition']:
                raise Unsupported('Drink capacity exceeded')
            for instance, spec in state['cards'].items():
                text(instance, 'Card instance')
                exact(spec, {'card_id', 'upgrade', 'customizations'}, 'Permanent card')
                adapter.card(spec['card_id'], spec['upgrade'], spec['customizations'])
            counts = Counter(s['card_id'] for s in state['cards'].values())
            for spec in state['cards'].values():
                if c.card(spec['card_id'], spec['upgrade'])['noDeckDuplication'] and counts[spec['card_id']] > 1:
                    raise Unsupported('Master forbids duplicate permanent card: ' + spec['card_id'])
            legend_count = sum(c.card(s['card_id'], s['upgrade'])['rarity'] == 'ProduceCardRarity_Legend'
                               for s in state['cards'].values())
            if legend_count > setting['maxLegendProduceCardCount']:
                raise Unsupported('Legend capacity exceeded; extra-limit modifiers are not ported')
            for instance, key in state['drinks'].items():
                text(instance, 'Drink instance'); adapter.drink(key)
            seen = set()
            for item in state['items']:
                exact(item, {'id', 'fire_count', 'reaction_count'}, 'Produce item')
                text(item['id'], 'Item ID')
                for field in ('fire_count', 'reaction_count'):
                    integer(item[field], field)
                if item['id'] in seen:
                    raise Unsupported('Duplicate persistent item')
                seen.add(item['id'])
                if item['id'] == PATCH:
                    rule = patch_rule(c)
                    if item['fire_count'] > rule['limit']:
                        raise Unsupported('HIF patch count exceeds pinned limit')
                elif item['fire_count'] or item['reaction_count']:
                    # Exam counters are installed afresh; no unsupported per-produce
                    # growth counters may be hidden in the exam-only compiler.
                    raise Unsupported('Persistent counters of this exam item are not ported')
            loadout = FinalsSegment._loadout(state, produce_id)
            # Compile all items/memories and possible generated outcomes now, not
            # after observing a favorable draw or purchase.
            _sandbox_from_adapter(adapter, [state['cards'][k] for k in state['card_order']], 9,
                                  [state['drinks'][k] for k in state['drink_order']], loadout)
        finally:
            c.db.close()

    @staticmethod
    def _loadout(state, produce_id='produce-008'):
        return dict(mode=produce_id, idol_card_id=state['idol_card_id'],
                    items=[i['id'] for i in state['items'] if i['id'] != PATCH],
                    memories=deepcopy(state['memories']))

    def resource_digest(self):
        return digest(self.state)

    def play_round(self, protocol, seed, policy='skill', policy_config=None):
        if self.phase not in ('round1', 'round2'):
            raise Unsupported('No exam is offered in current phase')
        is_display=isinstance(protocol,dict) and 'display_bonus' in protocol
        exact(protocol, {'resource_digest', 'display_bonus', 'source'} if is_display else {'resource_digest', 'progress', 'audition', 'source'}, 'Exam progress input')
        if protocol['resource_digest'] != self.resource_digest():
            raise Unsupported('Exam progress is bound to a different persistent state')
        c = Catalog()
        try:
            count = HIFPlanner(self.state['idol_card_id'], c).configuration('produce-008', self.position)['battle_config']['turn']
            doc = _sandbox_from_adapter(MasterAdapter(c), [self.state['cards'][k] for k in self.state['card_order']], count,
                                       [self.state['drinks'][k] for k in self.state['drink_order']], self._loadout(self.state))
        finally:
            c.db.close()
        doc['id'] = 'hif-finals-position-' + str(self.position)
        doc['cards'] = dict(zip(self.state['card_order'], doc['cards'].values()))
        doc['zones'] = dict(deck=list(doc['cards']))
        doc['state'] = dict(hp=self.state['hp'], max_hp=self.state['max_hp'])
        doc['drinks'] = {k: self.state['drinks'][k] for k in self.state['drink_order']}
        doc['goal'] = dict(target_score=0)
        doc['round_adjustment'] = dict(numerator=6 if self.position == 7 else 1,
                                       denominator=5 if self.position == 7 else 1)
        doc['completed_adjusted_score'] = self.first_settled or 0
        c=Catalog()
        try:
            from .growth_items import bind_battle_start
            doc=bind_battle_start(doc,self.state,c)
        finally:c.db.close()
        if is_display:
            from .audition import bind_displayed_audition_battle
            doc=bind_displayed_audition_battle(doc,'produce-008',self.position,protocol['display_bonus'],protocol['source'],self.state['idol_card_id'])
        else:
            doc = bind_native_audition_battle(doc, 'produce-008', self.position, protocol['progress'],
                                             protocol['audition'], protocol['source'], self.state['idol_card_id'])
        if self.state.get('support_scope')=='growth-and-exam':
            from .support_upgrades import bind
            doc=bind(doc,self.state['support_exam'])
        record = run_battle(doc, policy, seed, capture=True, policy_config=policy_config)
        if record['status'] != 'ok':
            raise Unsupported('Exam did not complete: ' + record.get('error', record['status']))
        after = deepcopy(self.state)
        if 'next_audition_enchants' in after:after['next_audition_enchants']=[]
        after['hp'] = record['hp']
        used = {e['action']['id'] for e in record['events'] if e['action']['kind'] == 'drink'}
        after['drinks'] = {k: v for k, v in self.state['drinks'].items() if k not in used}
        after['drink_order'] = [k for k in self.state['drink_order'] if k not in used]
        if len(after['drinks']) != record['drinks_left']:
            raise Unsupported('Battle drink reconciliation failed')
        c=Catalog()
        try:after,support_events=apply_phase(after,'EndAudition',{},c)
        finally:c.db.close()
        self._validate_state(after)
        event = dict(kind='exam', position=self.position, before=self.resource_digest(),
                     after=digest(after), input=deepcopy(protocol), seed=seed, policy=policy,
                     policy_config=deepcopy(policy_config), result={k: record[k] for k in
                     ('raw_score', 'adjusted_score', 'final_total', 'hp', 'drinks_left')})
        self.state = after
        self.support_trace.extend(support_events)
        self.battles.append(record)
        self.events.append(event)
        if self.position == 7:
            self.first_settled = record['adjusted_score']
            self.phase = 'await_interval_progress'
        else:
            self.phase = 'complete'
        return deepcopy(record)

    def open_interval(self, progress):
        if self.phase != 'await_interval_progress':
            raise Unsupported('Interval progress is only accepted after R1')
        exact(progress, {'provenance', 'state', 'quote'}, 'Post-R1 progress')
        source(progress['provenance']); self._validate_state(progress['state'])
        after = deepcopy(progress['state'])
        # This component admits no intervening reward-selection event. Changed
        # inventory/HP must go through a future explicit event implementation.
        if set(after) != set(self.state):
            raise Unsupported('Post-R1 progress cannot add/drop persistent context')
        for key in set(self.state) - {'produce_points', 'star', 'items'}:
            if after[key] != self.state[key]:
                raise Unsupported('Unimplemented post-R1 change: ' + key)
        old = self.state['items']
        if after['items'][:len(old)] != old:
            raise Unsupported('Post-R1 progress cannot reset/drop existing item counters')
        if any(i['fire_count'] or i['reaction_count'] for i in after['items'][len(old):]):
            raise Unsupported('Newly received interval items require zero prior counters')
        self._validate_quote(progress['quote'], after, set(), set())
        before = self.resource_digest()
        self.state = after; self.position = 8; self.phase = 'interval'
        self.quote = deepcopy(progress['quote']); self.seen_quotes.add(self.quote['id'])
        if 'upgrade_service' in self.quote:
            self.interval_upgrade_count = self.quote['upgrade_service']['used_count']
        if 'customize_service' in self.quote:
            self.interval_customizing = list(self.quote['customize_service']['active_instances'])
        self.bought_positions = {p['positionNumber'] for p in self.quote['products'] if p['purchased']}
        self.events.append(dict(kind='interval_progress', before=before, after=self.resource_digest(),
                                input=deepcopy(progress), origin='external; rewards/grants not locally inferred'))

    @staticmethod
    def _validate_quote(quote, state, seen, bought, upgrade_count=None, customizing=None, customize_recheck=()):
        base = {'id', 'provenance', 'produce_points', 'reroll_price', 'products'}
        services = {'change_sources', 'upgrade_service', 'customize_service'}
        optional = {'state_digest'} | services
        if (not isinstance(quote, dict) or not base <= set(quote) or set(quote) - base - optional
            or ('state_digest' in quote and not set(quote) & services)
            or (set(quote) & services and 'state_digest' not in quote)):
            raise Unsupported('Interval quote requires base fields or state-bound service selectors')
        if 'state_digest' in quote and quote['state_digest'] != digest(state):
            raise Unsupported('Service quote belongs to another persistent state')
        text(quote['id'], 'Quote ID'); source(quote['provenance'])
        if quote['id'] in seen:
            raise Unsupported('Stale quote ID')
        if integer(quote['produce_points'], 'quoted balance') != state['produce_points']:
            raise Unsupported('Quoted balance disagrees with current progress')
        integer(quote['reroll_price'], 'reroll price')
        if not isinstance(quote['products'], list):
            raise Unsupported('Quote products must be a list')
        positions = set()
        for product in quote['products']:
            exact(product, PRODUCT_FIELDS, 'Interval product')
            for key in PRODUCT_FIELDS - {'resourceType', 'resourceId', 'purchased'}:
                integer(product[key], key)
            if type(product['purchased']) is not bool:
                raise Unsupported('Purchased must be boolean')
            text(product['resourceType'], 'Resource type')
            if not isinstance(product['resourceId'], str):
                raise Unsupported('Resource ID must be string')
            pos = product['positionNumber']
            if pos in positions:
                raise Unsupported('Duplicate product position')
            if pos in bought and not product['purchased']:
                raise Unsupported('Purchased slot revived without reroll')
            positions.add(pos)
        changes = {str(p['positionNumber']) for p in quote['products']
                   if p['resourceType'] == 'ProduceResourceType_CardChange'}
        if changes or 'change_sources' in quote:
            if quote.get('state_digest') != digest(state):
                raise Unsupported('Change quote belongs to another persistent state')
            exact(quote.get('change_sources'), changes, 'Current change source selectors')
            for values in quote['change_sources'].values():
                if (not isinstance(values, list) or any(not isinstance(i, str) for i in values)
                    or len(values) != len(set(values)) or set(values) - set(state['cards'])):
                    raise Unsupported('Change selector needs current unique physical instances')
        if 'upgrade_service' in quote:
            service = quote['upgrade_service']
            exact(service, {'used_count', 'eligible'}, 'Current upgrade service')
            used = checked(service['used_count'], 'interval upgrade used count', 2147483647)
            if upgrade_count is not None and used != upgrade_count:
                raise Unsupported('Upgrade count changed without a service or refresh')
            positions = {str(p['positionNumber']) for p in quote['products']
                         if p['resourceType'] == 'ProduceResourceType_CardUpgrade'}
            exact(service['eligible'], positions, 'Upgrade source selectors')
            for values in service['eligible'].values():
                if (not isinstance(values, list) or any(not isinstance(i, str) for i in values)
                    or len(values) != len(set(values)) or set(values) - set(state['cards'])):
                    raise Unsupported('Upgrade selector needs current unique physical instances')
        if 'customize_service' in quote:
            positions = {str(p['positionNumber']) for p in quote['products']
                         if p['resourceType'] == 'ProduceResourceType_CardCustomize'}
            validate_customize_service(quote['customize_service'], positions, state, customizing, customize_recheck)

    def install_quote(self, quote):
        if self.phase != 'interval' or self.quote is not None:
            raise Unsupported('A fresh quote is needed only after a transaction')
        expected = 0 if self.interval_upgrade_reset else self.interval_upgrade_count
        customizing = [] if self.interval_customize_reset else self.interval_customizing
        self._validate_quote(quote, self.state, self.seen_quotes, self.bought_positions, expected,
                             customizing, self.interval_customize_recheck)
        self.quote = deepcopy(quote); self.seen_quotes.add(quote['id'])
        if 'upgrade_service' in quote:
            self.interval_upgrade_count = quote['upgrade_service']['used_count']
            self.interval_upgrade_reset = False
        if 'customize_service' in quote:
            self.interval_customizing = list(quote['customize_service']['active_instances'])
            self.interval_customize_reset = False
            self.interval_customize_recheck.clear()
        self.bought_positions.update(p['positionNumber'] for p in quote['products'] if p['purchased'])
        self.events.append(dict(kind='quote', input=deepcopy(quote)))

    def observe_interval(self):
        if self.phase != 'interval':
            raise Unsupported('No interval event')
        result = dict(position=8, state=deepcopy(self.state), quote=deepcopy(self.quote), scope=SCOPE)
        if self.interval_upgrade_count is not None: result['upgrade_used_count'] = self.interval_upgrade_count
        if self.interval_customizing is not None:
            result['customize_progress'] = dict(active_instances=deepcopy(self.interval_customizing),
                needs_progress_for=sorted(self.interval_customize_recheck))
        return result

    def transact(self, action):
        if self.phase != 'interval' or self.quote is None:
            raise Unsupported('Interval action needs fresh current progress quote')
        if not isinstance(action, dict) or action.get('quote_id') != self.quote['id']:
            raise Unsupported('Action uses a stale/different quote')
        kind = action.get('kind')
        fields = {'kind', 'quote_id'} if kind == 'reroll' else {'kind', 'quote_id', 'position'}
        if kind in ('change', 'upgrade', 'customize'): fields.add('instance')
        if kind == 'customize': fields.add('customize_id')
        exact(action, fields, 'Interval action')
        after = deepcopy(self.state); bought = set(self.bought_positions); detail = {}; support_events = []
        upgrade_count = self.interval_upgrade_count; upgrade_reset = self.interval_upgrade_reset
        customizing = deepcopy(self.interval_customizing); customize_reset = self.interval_customize_reset
        customize_recheck = set(self.interval_customize_recheck)
        if kind == 'reroll':
            price = self.quote['reroll_price']; bought.clear()
            if upgrade_count is not None: upgrade_count = 0
            upgrade_reset = True
            if customizing is not None: customizing = []
            customize_reset = True; customize_recheck.clear()
        elif kind in ('buy', 'change', 'upgrade', 'customize'):
            integer(action['position'], 'product position')
            matches = [p for p in self.quote['products'] if p['positionNumber'] == action['position']]
            if len(matches) != 1:
                raise Unsupported('Product is not offered')
            product = matches[0]; price = product['price']
            if product['purchased'] or action['position'] in bought:
                raise Unsupported('Product already purchased')
            resource = product['resourceType']
            instance = 'interval:' + self.quote['id'] + ':' + str(action['position'])
            if kind == 'customize':
                if (resource != 'ProduceResourceType_CardCustomize' or product['resourceId']
                    or product['upgradeCount'] or product['staminaRecoverValue']):
                    raise Unsupported('Customize action requires a plain card-customize service')
                if 'customize_service' not in self.quote or customizing is None:
                    raise Unsupported('Need current customize branch offers and card flags')
                if self.quote['state_digest'] != self.resource_digest():
                    raise Unsupported('Customize quote belongs to another persistent state')
                text(action['instance'], 'Customize instance'); text(action['customize_id'], 'Customize branch')
                if action['instance'] not in after['cards']:
                    raise Unsupported('Customize instance is not currently owned')
                c = Catalog()
                try:
                    old = deepcopy(after['cards'][action['instance']])
                    new, customizing, price, detail['customize'] = select_customize_target(c, MasterAdapter(c),
                        self.quote['customize_service'], action['position'], action['instance'], action['customize_id'], old)
                    # The product contains no selected branch. Its generic
                    # preview price is not an additional fee for each choice.
                    # Audition interval customization is not a calendar
                    # StartCustomize visit. Only the per-operation trigger
                    # below applies, even if a guidance-only support is ready.
                    after['cards'][action['instance']] = new
                finally: c.db.close()
            if price > after['produce_points']:
                raise Unsupported('Not enough produce points')
            if kind == 'customize':
                pass
            elif kind == 'upgrade':
                if (resource != 'ProduceResourceType_CardUpgrade' or product['resourceId']
                    or product['upgradeCount'] or product['staminaRecoverValue']):
                    raise Unsupported('Upgrade action requires a plain card-upgrade service')
                if 'upgrade_service' not in self.quote or upgrade_count is None:
                    raise Unsupported('Need current upgrade eligibility and used count')
                if self.quote['state_digest'] != self.resource_digest():
                    raise Unsupported('Upgrade quote belongs to another persistent state')
                text(action['instance'], 'Upgrade source instance')
                if action['instance'] not in self.quote['upgrade_service']['eligible'][str(action['position'])]:
                    raise Unsupported('Source instance is outside current upgrade selector')
                c = Catalog()
                try:
                    limit = c.one('ProduceSetting', 'p_setting-8')['stepIntervalUpgradeProduceCardCount']
                    if upgrade_count >= limit: raise Unsupported('No interval upgrade uses remain before refresh')
                    old = deepcopy(after['cards'][action['instance']])
                    after['cards'][action['instance']] = upgrade_target(c, old)
                finally: c.db.close()
                upgrade_count += 1
                detail['upgrade'] = dict(instance=action['instance'], before=old,
                    after=deepcopy(after['cards'][action['instance']]), used_count=upgrade_count, limit=limit)
            elif kind == 'change':
                if (resource != 'ProduceResourceType_CardChange' or product['staminaRecoverValue']
                    or not product['resourceId']):
                    raise Unsupported('Change action requires a current card-change product')
                if self.quote.get('state_digest') != self.resource_digest():
                    raise Unsupported('Change quote belongs to another persistent state')
                text(action['instance'], 'Change source instance')
                if action['instance'] not in self.quote['change_sources'][str(action['position'])]:
                    raise Unsupported('Source instance is not offered by current change selector')
                old = deepcopy(after['cards'][action['instance']])
                # Product upgradeCount is the displayed preview, not the grade
                # chosen for the actual replacement. The source determines it.
                grade = 0 if old['card_id'] == 'p_card-00-acc-0_002' else old['upgrade']
                replacement = dict(card_id=product['resourceId'], upgrade=grade, customizations=[])
                after['cards'][action['instance']] = replacement
                c = Catalog()
                try:
                    target = c.card(replacement['card_id'], grade)
                    # The interval button checks Legend capacity before the
                    # source selector opens (30743a4..3074544), even if one
                    # intended to replace the currently owned Legend itself.
                    setting = c.one('ProduceSetting', 'p_setting-8')
                    legends = sum(c.card(s['card_id'], s['upgrade'])['rarity'] == 'ProduceCardRarity_Legend'
                                  for s in self.state['cards'].values())
                    if target['rarity'] == 'ProduceCardRarity_Legend' and legends >= setting['maxLegendProduceCardCount']:
                        raise Unsupported('Interval Legend product disabled at current capacity')
                    # Validate the replacement before effects/counters. No Get
                    # or BuyShop phase is synthesized for this distinct entry.
                    self._validate_state(after)
                    after, support_events = apply_phase(after, 'ChangeProduceCard', dict(card=old), c)
                finally: c.db.close()
                detail['change'] = dict(instance=action['instance'], before=old, after=deepcopy(replacement))
                if customizing is not None and action['instance'] in customizing:
                    customize_recheck.add(action['instance'])
                if support_events: detail['support_events'] = deepcopy(support_events)
                bought.add(action['position'])
            elif resource == 'ProduceResourceType_CardUpgrade':
                raise Unsupported('Card-upgrade product requires a selected source and current service progress')
            elif resource == 'ProduceResourceType_CardChange':
                raise Unsupported('Card-change product requires a selected source instance')
            elif resource == 'ProduceResourceType_CardCustomize':
                raise Unsupported('Card-customize product requires current branch offers and selected instance')
            elif resource == 'ProduceResourceType_ProduceCard':
                if product['staminaRecoverValue']:
                    raise Unsupported('Mixed card/heal product')
                if instance in after['cards']:
                    raise Unsupported('Card instance collision')
                after['cards'][instance] = dict(card_id=product['resourceId'], upgrade=product['upgradeCount'], customizations=[])
                after['card_order'].append(instance)
                c = Catalog()
                try:
                    for item in after['items']:
                        if item['id'] == PATCH:
                            rule = patch_rule(c)
                            if item['fire_count'] < rule['limit']:
                                gain=star_gain(after,rule['value'])
                                item['fire_count'] += 1; after['star'] += gain
                                detail['growth_trigger'] = dict(item=PATCH, star_added=gain, fire_count=item['fire_count'])
                finally:
                    c.db.close()
                bought.add(action['position'])
            elif resource == 'ProduceResourceType_ProduceDrink':
                if product['upgradeCount'] or product['staminaRecoverValue']:
                    raise Unsupported('Mixed drink product')
                if instance in after['drinks']:
                    raise Unsupported('Drink instance collision')
                after['drinks'][instance] = product['resourceId']; bought.add(action['position'])
                after['drink_order'].append(instance)
            elif resource == 'ProduceResourceType_Stamina':
                if (product['resourceId'] or product['upgradeCount'] or product['staminaRecoverValue'] < 1
                    or after['hp'] == after['max_hp']):
                    raise Unsupported('Invalid/full stamina recovery')
                after['hp'] = min(after['max_hp'], after['hp'] + product['staminaRecoverValue'])
            else:
                raise Unsupported('Interval resource/service not ported: ' + resource)
        else:
            raise Unsupported('Unknown interval transaction')
        if price > after['produce_points']:
            raise Unsupported('Not enough produce points')
        after['produce_points'] -= price
        if kind in ('upgrade', 'customize'):
            c = Catalog()
            try: after, support_events = apply_phase(after,
                'UpgradeProduceCard' if kind == 'upgrade' else 'CustomizeProduceCard', dict(card=old), c)
            finally: c.db.close()
            if support_events: detail['support_events'] = deepcopy(support_events)
        if kind=='buy' and resource in ('ProduceResourceType_ProduceCard','ProduceResourceType_ProduceDrink'):
            # Ordinary-shop triggers cannot silently be assumed identical in
            # the distinct audition-mid-shop endpoint. Preserve this boundary.
            phases=(('BuyShopItemProduceCard','GetProduceCard') if resource=='ProduceResourceType_ProduceCard'
                    else ('BuyShopItemProduceDrink','GetProduceDrink'))
            c=Catalog()
            try:
                if any(phase_pending(after,p,c) for p in phases):raise Unsupported('Support trigger timing in audition-mid-shop is not ported')
            finally:c.db.close()
        self._validate_state(after)
        event = dict(kind='transaction', input=deepcopy(action), quote=deepcopy(self.quote),
                     price=price, before=self.resource_digest(), after=digest(after), details=detail)
        self.state = after; self.bought_positions = bought
        self.interval_upgrade_count = upgrade_count; self.interval_upgrade_reset = upgrade_reset
        self.interval_customizing = customizing; self.interval_customize_reset = customize_reset
        self.interval_customize_recheck = customize_recheck
        self.support_trace.extend(support_events)
        self.quote = None  # No guessed NextPrice/replenishment after a mutation.
        self.events.append(event)
        return deepcopy(event)

    def end_interval(self):
        if self.phase != 'interval':
            raise Unsupported('Not in interval')
        self.events.append(dict(kind='end_interval', state_digest=self.resource_digest()))
        self.position = 9; self.phase = 'round2'; self.quote = None

    def record(self):
        result=dict(schema_version=1, initial=deepcopy(self.initial_document), events=deepcopy(self.events),
                    battles=deepcopy(self.battles), state=deepcopy(self.state), phase=self.phase,
                    position=self.position, first_settled=self.first_settled,
                    total=self.battles[-1]['final_total'] if self.phase == 'complete' else None,
                    implementation_manifest=implementation_manifest(), scope=SCOPE)
        if 'supports' in self.state:result['support_trace']=deepcopy(self.support_trace)
        if self.interval_upgrade_count is not None: result['upgrade_used_count'] = self.interval_upgrade_count
        if self.interval_customizing is not None:
            result['customize_progress'] = dict(active_instances=deepcopy(self.interval_customizing),
                needs_progress_for=sorted(self.interval_customize_recheck))
        return result


def replay_segment(record):
    if record['implementation_manifest'] != implementation_manifest():
        return dict(matches=False, error='Implementation changed since segment recording')
    env = FinalsSegment(record['initial'])
    for event in record['events']:
        if event['kind'] == 'exam':
            env.play_round(event['input'], event['seed'], event['policy'], event['policy_config'])
        elif event['kind'] == 'interval_progress':
            env.open_interval(event['input'])
        elif event['kind'] == 'quote':
            env.install_quote(event['input'])
        elif event['kind'] == 'transaction':
            env.transact(event['input'])
        elif event['kind'] == 'end_interval':
            env.end_interval()
        else:
            raise Unsupported('Unknown segment replay event')
    actual = env.record()
    # The battle engine already records elapsed wall time separately from its
    # deterministic trajectory. Segment replay compares everything else.
    expected = deepcopy(record)
    for result in (actual, expected):
        for battle in result['battles']:
            battle.pop('elapsed_seconds', None)
    differences = [k for k in sorted(set(actual) | set(expected)) if digest(actual.get(k)) != digest(expected.get(k))]
    return dict(matches=not differences, differences=differences, exams=len(env.battles), events=len(env.events))
