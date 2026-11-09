"""HIF finals pre-exam event host: actual school/change and open lessons.

Open lessons have no card battle. Non-neutral growth/support production and
server candidate generation are not inferred. Selection is a nested event,
not a synthetic five-stage route.
"""
from copy import deepcopy

from .catalog import Catalog
from .core import Unsupported
from .customize import nondefault
from .finals import FinalsSegment, PATCH, digest, exact, source, text, patch_rule
from .hif import integer
from .master_adapter import MasterAdapter
from .schedule import HIFSchedule, STATS
from .growth_scaling import NEUTRAL,validate_modifiers,scale_floor,parameter_caps,star_gain,pre_exam_recovery
from .market import MarketMixin
from .supports import SupportMixin,phase_pending

ATTR_TYPES = {'ProduceEffectType_' + value + 'Addition': key for key, value in STATS.items()}
SCOPE = ('HIF finals pre-exam event subset; supplied supported growth coefficients, current candidates/SP/substats; '
         'remaining cost/recovery/support production, candidate probability and server reconciliation not inferred')


def plain(row, allowed, label):
    if any(nondefault(value) for key, value in row.items() if key not in allowed):
        raise Unsupported('Unported ' + label + ' field')


def compile_event(catalog, event_id, attribute, stage, idol_card_id, produce_id='produce-008'):
    root = catalog.one('ProduceStepEventDetail', event_id)
    idol = catalog.one('IdolCard', idol_card_id)
    if root['eventType'] == 'ProduceEventType_School':
        if 'class' not in stage['options'] or attribute not in STATS:
            raise Unsupported('School event is not offered here')
        # Exact root bindings, not deriving numeric rules from the ID string.
        plan = {'ProducePlanType_Plan1': 'plan1', 'ProducePlanType_Plan2': 'plan2',
                'ProducePlanType_Plan3': 'plan3'}[idol['planType']]
        attr = {'Vo': 'vocal', 'Da': 'dance', 'Vi': 'visual'}[attribute]
        if produce_id == 'produce-008':
            roots = [f'event-detail-school-{attr}-003-produce_008-{plan}-{i:02}' for i in (1, 2)]
        elif produce_id == 'produce-007':
            families = ['before_1st'] if stage['class_base_parameter'] == 120 else ['before_3rd']
            roots = [f'event-detail-school-{attr}-003-produce_007-{plan}-{family}-{i:02}'
                     for family in families for i in ((1, 2) if stage['class_base_parameter']==120 else (1, 2, 3, 4))]
        else: raise Unsupported('Only explicit HIF school modes are supported')
        if event_id not in roots:
            raise Unsupported('School root differs from current mode/plan/attribute binding')
    elif root['eventType'] == 'ProduceEventType_Activity':
        roots = (['event-detail-activity-003-produce_008-01', 'event-detail-activity-003-produce_008-02']
                 if produce_id == 'produce-008' else
                 [f'event-detail-activity-003-produce_007-{family}-{i:02}'
                  for family in ['before_2nd', 'before_3rd'] for i in range(1, 10)] if produce_id == 'produce-007' else [])
        if ('outing' not in stage['options'] or attribute is not None or event_id not in roots):
            raise Unsupported('Outing root is not offered here')
    else:
        raise Unsupported('Pre-exam event type not ported')
    plain(root, {'id', 'suggestionType', 'produceStoryId', 'produceStoryGroupId',
                 'produceStepEventSuggestionIds', 'eventType', 'produceDescriptions'}, 'event root')
    if root['suggestionType'] != 'ProduceEventSuggestionType_Primary':
        raise Unsupported('Event suggestion phase not ported')
    options = []
    for key in root['produceStepEventSuggestionIds']:
        row = catalog.one('ProduceStepEventSuggestion', key)
        plain(row, {'id', 'producePoint', 'stamina', 'produceCardId', 'produceCardUpgradeCount',
                    'produceEffectIds', 'alwaysSuccessful', 'produceDescriptions'}, 'suggestion')
        if not row['alwaysSuccessful']:
            raise Unsupported('Random event branch is not ported')
        if row['produceCardId'] not in ('', 'p_card-00-acc-0_002') or row['produceCardUpgradeCount']:
            raise Unsupported('Unported event card cost')
        effects = [compile_effect(catalog, k) for k in row['produceEffectIds']]
        if root['eventType'] == 'ProduceEventType_School':
            if (len(effects) != 2 or effects[0] != dict(kind='parameter', attribute=attribute,
                    value=stage['class_base_parameter'], source_id=row['produceEffectIds'][0])
                or effects[1]['kind'] not in ({'card', 'change'} if produce_id == 'produce-007' and stage['class_base_parameter']==120 else {'change'})
                or row['stamina'] not in (0, stage['class_hp_cost'])):
                raise Unsupported('School effects differ from sourced HIF calendar')
        options.append(dict(id=key, hp_cost=row['stamina'], point_cost=row['producePoint'],
                            trouble_cost=row['produceCardId'], effects=effects))
    return dict(event_id=event_id, attribute=attribute, options=options,
                master_commit=catalog.info['commit'])


def compile_effect(catalog, key):
    row = catalog.one('ProduceEffect', key); kind = row['produceEffectType']
    base = {'id', 'produceEffectType', 'effectValueMin', 'effectValueMax', 'isResearch'}
    if row['isResearch']:
        raise Unsupported('Research effect in HIF event')
    if kind in ATTR_TYPES or kind == 'ProduceEffectType_StaminaRecoverMultiple':
        plain(row, base, 'scalar effect')
        if row['effectValueMin'] != row['effectValueMax']:
            raise Unsupported('Random growth scalar is not ported')
        value = integer(row['effectValueMin'], 'growth effect')
        if kind in ATTR_TYPES:
            return dict(kind='parameter', attribute=ATTR_TYPES[kind], value=value, source_id=key)
        return dict(kind='recover_permil', value=value, source_id=key)
    if kind in ('ProduceEffectType_ProduceRewardSet', 'ProduceEffectType_ProduceCardChangeSelect'):
        plain(row, base | {'produceResourceType', 'pickRangeType', 'pickCountMin', 'pickCountMax',
                           'produceCardSearchId'}, 'selection effect')
        if row['pickRangeType'] != 'ProducePickRangeType_Select' or (row['pickCountMin'], row['pickCountMax']) != (1, 1):
            raise Unsupported('Only explicit one-choice reward events are ported')
        resource = {'ProduceResourceType_ProduceCard': 'card', 'ProduceResourceType_ProduceDrink': 'drink'}.get(row['produceResourceType'])
        if resource is None:
            raise Unsupported('Reward resource not ported')
        if kind == 'ProduceEffectType_ProduceCardChangeSelect':
            if resource != 'card' or (row['effectValueMin'], row['effectValueMax']) != (1, 1):
                raise Unsupported('Card-change count not ported')
            search = catalog.one('ProduceCardSearch', row['produceCardSearchId'])
            plain(search, {'id', 'cardCategories', 'cardPositionType', 'produceDescriptions'}, 'change source search')
            if (search['cardPositionType'] != 'ProduceCardPositionType_DeckAll'
                or set(search['cardCategories']) != {'ProduceCardCategory_ActiveSkill', 'ProduceCardCategory_MentalSkill'}):
                raise Unsupported('Change source search not ported')
            return dict(kind='change', source_id=key, search_id=search['id'])
        if row['effectValueMin'] or row['effectValueMax'] or row['produceCardSearchId']:
            raise Unsupported('Reward-set semantics not ported')
        return dict(kind=resource, source_id=key)
    raise Unsupported('Pre-exam effect not ported: ' + kind)


class GrowthSession(MarketMixin,SupportMixin):
    produce_id = 'produce-008'
    def __init__(self, document):
        exact(document, {'schema_version', 'provenance', 'state', 'modifiers'}, 'Growth input')
        if document['schema_version'] != 1:
            raise Unsupported('Growth schema version')
        source(document['provenance'])
        validate_modifiers(document['modifiers'])
        if document['modifiers']['star_bonus_permil'] != document['state'].get('star_gain_permil', 1000):
            raise Unsupported('Effective star coefficient must persist consistently into finals')
        self.initial = deepcopy(document); self.state = deepcopy(document['state'])
        self.modifiers = deepcopy(document['modifiers'])
        self._validate(self.state)
        self.position = 1; self.phase = 'schedule'
        self.pending = []; self.offered = None; self.selected = None
        self.events = []; self.instance_counter = 0
        self.market=None;self.continuation=None
        self.custom_market_start=None
        self.support_trace=[];self.end_support_phase=None

    @classmethod
    def _validate(cls,state):
        if 'parameters' not in state:
            raise Unsupported('Growth input requires explicit parameters')
        FinalsSegment._validate_state(state,cls.produce_id)
        c = Catalog()
        try:
            caps = parameter_caps(state,c,cls.produce_id)
            if any(v > caps[a] for a,v in state['parameters'].items()):
                raise Unsupported('Parameter exceeds current cap')
        finally:
            c.db.close()

    def state_digest(self):
        return digest(self.state)

    def observe(self):
        result=dict(position=self.position, phase=self.phase, state=deepcopy(self.state),
                    modifiers=deepcopy(self.modifiers),
                    pending_effect=deepcopy(self.pending[0]) if self.pending else None,
                    offered=deepcopy(self.offered), selected=deepcopy(self.selected), scope=SCOPE)
        if self.market is not None:result['market']=deepcopy(self.market)
        if self.custom_market_start is not None:result['custom_market_start']=deepcopy(self.custom_market_start)
        return result

    def _stage(self):
        if self.phase != 'schedule' or not 1 <= self.position <= 6:
            raise Unsupported('No pre-exam schedule choice is currently offered')
        c = Catalog()
        try:
            return HIFSchedule('produce-008', c).stage(self.position)
        finally:
            c.db.close()

    def _advance_schedule(self):
        self.position += 1
        self.phase = 'schedule' if self.position <= 6 else 'before_round1'

    def event_options(self, event_id, attribute=None):
        c = Catalog()
        try:
            return compile_event(c, event_id, attribute, self._stage(), self.state['idol_card_id'], self.produce_id)
        finally:
            c.db.close()

    def _new_instance(self):
        while True:
            instance = 'growth:' + str(self.instance_counter); self.instance_counter += 1
            if instance not in self.state['cards'] and instance not in self.state['drinks']:
                return instance

    def _acquire_card(self, spec):
        instance = self._new_instance(); self.state['cards'][instance] = deepcopy(spec)
        self.state['card_order'].append(instance)
        c = Catalog()
        try:
            for item in self.state['items']:
                if item['id'] == PATCH:
                    rule = patch_rule(c)
                    if item['fire_count'] < rule['limit']:
                        item['fire_count'] += 1; self.state['star'] += star_gain(self.state,rule['value'])
        finally:
            c.db.close()
        self._supports('GetProduceCard',dict(card=deepcopy(spec)))

    def _drain(self):
        c = Catalog()
        try:
            caps = parameter_caps(self.state,c,self.produce_id)
            while self.pending and self.pending[0]['kind'] in ('parameter', 'recover_permil', 'point', 'recover_fix',
                                                             'next_audition', 'permanent_audition','grant_card','grant_drink',
                                                             'settled_parameter','settled_star','shop_discount'):
                effect = self.pending.pop(0)
                if effect['kind'] == 'parameter':
                    a = effect['attribute']; self.state['parameters'][a] = min(caps[a], self.state['parameters'][a] + effect['value'])
                elif effect['kind']=='settled_parameter':
                    a=effect['attribute'];value=self.state['parameters'][a]+effect['value']
                    if value>caps[a]:raise Unsupported('Settled parameter reward exceeds current cap')
                    self.state['parameters'][a]=value
                elif effect['kind']=='settled_star':
                    from .growth_scaling import checked
                    self.state['star']=checked(self.state['star']+effect['value'],'Settled star total')
                elif effect['kind']=='recover_permil':
                    self.state['hp'] = min(self.state['max_hp'], self.state['hp'] + self.state['max_hp'] * effect['value'] // 1000)
                elif effect['kind']=='point':self.state['produce_points']+=effect['value']
                elif effect['kind']=='recover_fix':self.state['hp']=min(self.state['max_hp'],self.state['hp']+effect['value'])
                elif effect['kind']=='grant_card':self._acquire_card(effect['spec'])
                elif effect['kind']=='grant_drink':
                    if not self._take_drink(effect['drink_id']):
                        self.pending.insert(0,effect);self._validate(self.state);return
                elif effect['kind']=='shop_discount':
                    self.state.setdefault('custom_shop_discounts',[]).append(dict(effect['custom_source'],
                        source_id=effect['source_id'],resource=effect['resource'],rate=effect['rate']))
                else:
                    field='next_audition_enchants' if effect['kind']=='next_audition' else 'permanent_audition_enchants'
                    self.state.setdefault(field,[]).append(dict(effect['custom_source'],enchant_id=effect['enchant_id']))
        finally:
            c.db.close()
        if self.pending:
            self.phase = 'await_offer'
        elif self.continuation=='custom_market_start':
            self.continuation=None;self.phase='shop_await_snapshot'
        elif self.continuation is not None:
            self._market_rewards_finished()
        else:
            if self.end_support_phase is not None:
                self._supports(self.end_support_phase);self.end_support_phase=None
            self._advance_schedule()
        self._validate(self.state)

    def choose_event(self, event_id, option_id, attribute=None):
        compiled = self.event_options(event_id, attribute)
        matches = [r for r in compiled['options'] if r['id'] == option_id]
        if len(matches) != 1:
            raise Unsupported('Suggestion is not part of the actual event')
        choice = matches[0]; trial = deepcopy(self); before = self.state_digest()
        if choice['hp_cost'] > trial.state['hp'] or choice['point_cost'] > trial.state['produce_points']:
            raise Unsupported('Cannot pay event cost')
        trial.state['hp'] -= choice['hp_cost']; trial.state['produce_points'] -= choice['point_cost']
        trial.end_support_phase='EndStepEventSchool' if attribute is not None else 'EndStepEventActivity'
        if choice['trouble_cost']:
            trial._acquire_card(dict(card_id=choice['trouble_cost'], upgrade=0, customizations=[]))
        trial.pending = deepcopy(choice['effects']); trial._drain()
        trial.events.append(dict(kind='event', event_id=event_id, option_id=option_id, attribute=attribute,
                                 before=before, after=trial.state_digest(), compiled=compiled))
        self.__dict__.update(trial.__dict__)

    def receive_offer(self, offer):
        if self.phase != 'await_offer' or self.offered is not None:
            raise Unsupported('No pending reward accepts an offer')
        if not self.pending or self.pending[0]['kind'] not in ('card','drink','change'):
            raise Unsupported('Random reward needs its actual result, not selectable candidates')
        exact(offer, {'provenance', 'state_digest', 'effect_id', 'candidates'}, 'Current reward offer')
        source(offer['provenance'])
        if offer['state_digest'] != self.state_digest() or offer['effect_id'] != self.pending[0]['source_id']:
            raise Unsupported('Reward offer belongs to another state/effect')
        if not isinstance(offer['candidates'], list) or not offer['candidates']:
            raise Unsupported('Need actual nonempty candidates')
        seen = set(); c = Catalog()
        try:
            adapter = MasterAdapter(c); kind = self.pending[0]['kind']
            for option in offer['candidates']:
                field = {'change': 'card_id', 'card': 'spec', 'drink': 'drink_id'}[kind]
                exact(option, {'id', field}, 'Reward candidate'); text(option['id'], 'Candidate ID')
                if option['id'] in seen:
                    raise Unsupported('Duplicate candidate identity')
                seen.add(option['id'])
                if kind == 'card':
                    exact(option['spec'], {'card_id', 'upgrade', 'customizations'}, 'Reward card spec')
                    adapter.card(option['spec']['card_id'], option['spec']['upgrade'], option['spec']['customizations'])
                elif kind == 'drink':
                    adapter.drink(option['drink_id'])
                else:
                    # No grade is selected yet: the source card will determine it.
                    text(option['card_id'], 'Change candidate card ID')
                    plan = c.one('IdolCard', self.state['idol_card_id'])['planType']
                    raw = c.card(option['card_id'], 0)
                    if raw['planType'] not in ('ProducePlanType_Common', plan):
                        raise Unsupported('Replacement candidate plan differs from current idol')
                    grades = {spec['upgrade'] for spec in self.state['cards'].values()
                              if c.card(spec['card_id'], spec['upgrade'])['category'] in
                              ('ProduceCardCategory_ActiveSkill', 'ProduceCardCategory_MentalSkill')}
                    if not grades:
                        raise Unsupported('No source cards match actual search')
                    for grade in grades:
                        adapter.card(option['card_id'], grade, [])
        finally:
            c.db.close()
        self.offered = deepcopy(offer); self.phase = 'choose_reward'
        self.events.append(dict(kind='offer', input=deepcopy(offer)))

    def choose_reward(self, option_id):
        if self.phase != 'choose_reward':
            raise Unsupported('No reward candidates are currently visible')
        matches = [r for r in self.offered['candidates'] if r['id'] == option_id]
        if len(matches) != 1:
            raise Unsupported('Reward candidate is not offered')
        trial = deepcopy(self); selected = matches[0]; kind = trial.pending[0]['kind']
        if kind == 'change':
            trial.selected = deepcopy(selected); trial.phase = 'choose_source'
        else:
            if kind == 'card':
                trial._acquire_card(selected['spec'])
            else:
                if not trial._take_drink(selected['drink_id']):
                    trial.offered=None
                    trial.events.append(dict(kind='reward_choice',option_id=option_id,after=trial.state_digest()))
                    self.__dict__.update(trial.__dict__);return
            trial.offered = None; trial.pending.pop(0); trial._drain()
        trial._validate(trial.state)
        trial.events.append(dict(kind='reward_choice', option_id=option_id, after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def _take_drink(self,drink_id):
        c=Catalog()
        try:
            capacity=c.one('ProduceSetting','p_setting-8')['produceDrinkPossessLimit']+self.state['drink_capacity_addition']
            if len(self.state['drinks'])>=capacity and phase_pending(self.state,'GetProduceDrink',c):
                raise Unsupported('Get-drink support timing during forced overflow is not ported')
        finally:c.db.close()
        if len(self.state['drinks'])>=capacity:
            self.selected=dict(drink_id=drink_id);self.phase='drink_overflow';return False
        instance=self._new_instance();self.state['drinks'][instance]=drink_id;self.state['drink_order'].append(instance)
        self._supports('GetProduceDrink')
        return True

    def receive_drink_grant(self,grant):
        if self.phase!='await_offer' or not self.pending or self.pending[0]['kind']!='random_drink':
            raise Unsupported('No random drink result is pending')
        exact(grant,{'provenance','state_digest','effect_id','drink_id'},'Random drink result');source(grant['provenance'])
        if grant['state_digest']!=self.state_digest() or grant['effect_id']!=self.pending[0]['source_id']:
            raise Unsupported('Random drink result belongs to another state/effect')
        c=Catalog()
        try:MasterAdapter(c).drink(grant['drink_id'])
        finally:c.db.close()
        trial=deepcopy(self)
        if trial._take_drink(grant['drink_id']):trial.pending.pop(0);trial._drain()
        trial._validate(trial.state);trial.events.append(dict(kind='drink_grant',input=deepcopy(grant),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def receive_drink_batch(self,grant):
        if self.phase!='await_offer' or not self.pending or self.pending[0]['kind']!='random_drink_batch':
            raise Unsupported('No current random drink batch is pending')
        exact(grant,{'provenance','state_digest','effect_id','drink_ids','unmodelled_effects'},'Random drink batch')
        source(grant['provenance']);effect=self.pending[0];ids=grant['drink_ids']
        if grant['state_digest']!=self.state_digest() or grant['effect_id']!=effect['source_id'] or grant['unmodelled_effects']!=[]:
            raise Unsupported('Stale or unmodelled drink batch')
        if (not isinstance(ids,list) or len(ids)!=effect['count'] or any(not isinstance(i,str) for i in ids)
            or len(set(ids))!=len(ids)):
            raise Unsupported('Need the complete distinct drink batch')
        c=Catalog()
        try:
            for key in ids:MasterAdapter(c).drink(key)
        finally:c.db.close()
        trial=deepcopy(self);trial.pending.pop(0)
        trial.pending[:0]=[dict(kind='grant_drink',drink_id=key,source_id=effect['source_id']) for key in ids]
        trial._drain();trial._validate(trial.state)
        trial.events.append(dict(kind='drink_batch',input=deepcopy(grant),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def resolve_drink_overflow(self,action):
        if self.phase!='drink_overflow':raise Unsupported('No drink overflow choice is pending')
        if not isinstance(action,dict) or action.get('kind') not in ('discard_new','discard_owned'):
            raise Unsupported('Need explicit discard-new or discard-owned choice')
        exact(action,{'kind'} if action['kind']=='discard_new' else {'kind','instance'},'Drink discard choice')
        trial=deepcopy(self)
        if action['kind']=='discard_owned':
            if action['instance'] not in trial.state['drinks']:raise Unsupported('Drink to discard is not owned')
            del trial.state['drinks'][action['instance']];trial.state['drink_order'].remove(action['instance'])
            if not trial._take_drink(trial.selected['drink_id']):raise Unsupported('Drink capacity did not clear')
        trial.selected=None;trial.pending.pop(0);trial._drain();trial._validate(trial.state)
        trial.events.append(dict(kind='drink_discard',action=deepcopy(action),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def change_sources(self):
        if self.phase != 'choose_source':
            raise Unsupported('Choose a visible replacement candidate before choosing its source')
        c = Catalog()
        try:
            return [i for i in self.state['card_order'] if c.card(self.state['cards'][i]['card_id'],
                    self.state['cards'][i]['upgrade'])['category'] in
                    ('ProduceCardCategory_ActiveSkill', 'ProduceCardCategory_MentalSkill')]
        finally:
            c.db.close()

    def choose_source(self, instance):
        if instance not in self.change_sources():
            raise Unsupported('Source card is outside actual search; trouble cards cannot be changed by school')
        trial = deepcopy(self); old = trial.state['cards'][instance]
        trial.state['cards'][instance] = dict(card_id=trial.selected['card_id'], upgrade=old['upgrade'], customizations=[])
        trial._supports('ChangeProduceCard',dict(card=deepcopy(old)))
        trial.offered = None; trial.selected = None; trial.pending.pop(0); trial._drain()
        trial.events.append(dict(kind='change_source', instance=instance, before_card=deepcopy(old),
                                 after_card=deepcopy(trial.state['cards'][instance]), after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def open_lesson(self, primary, bindings):
        stage = self._stage()
        if 'training' not in stage['options'] or primary not in STATS:
            raise Unsupported('No such public lesson is offered')
        exact(bindings, {'provenance', 'state_digest', 'sp_flags', 'sub_stats'}, 'Open-lesson binding')
        source(bindings['provenance'])
        if bindings['state_digest'] != self.state_digest():
            raise Unsupported('Stale open-lesson input')
        exact(bindings['sp_flags'], set(STATS), 'SP flags'); exact(bindings['sub_stats'], set(STATS), 'Substats')
        if any(type(v) is not bool for v in bindings['sp_flags'].values()):
            raise Unsupported('Observed SP flags must be booleans')
        if any(a not in STATS or a == k for k, a in bindings['sub_stats'].items()):
            raise Unsupported('Observed substat differs from primary')
        c = Catalog()
        try:
            sub = bindings['sub_stats'][primary]; sp = bindings['sp_flags'][primary]
            rows = [c.one('ProduceStepOpenLesson', k) for k in stage['open_lesson_ids']]
            rows = [r for r in rows if r['subParameterType'] == 'ProduceParameterType_' + STATS[sub]
                    and ('-sp-' in r['id']) == sp]
            if len(rows) != 1:
                raise Unsupported('Ambiguous actual open-lesson row')
            row = rows[0]; caps = parameter_caps(self.state,c,self.produce_id)
        finally:
            c.db.close()
        trial = deepcopy(self)
        if row['stamina'] > trial.state['hp']:
            raise Unsupported('Not enough stamina for public lesson')
        trial.state['hp'] -= row['stamina']
        for a, gain in [(primary, row['mainParameter']), (sub, row['subParameter'])]:
            gain = scale_floor(gain,trial.modifiers['parameter_bonus_permil'][a])
            trial.state['parameters'][a] = min(caps[a], trial.state['parameters'][a] + gain)
        trial._supports('EndLessonBeforePresent',dict(attribute=primary,sp=sp))
        trial.state['star'] += star_gain(trial.state,row['star']); trial.state['produce_points'] += stage['base_p_points']
        trial._supports('EndLesson',dict(attribute=primary,sp=sp))
        from .growth_items import end_lesson
        c=Catalog()
        try:effects,fired=end_lesson(trial.state,c)
        finally:c.db.close()
        trial.pending=effects;trial._drain();trial._validate(trial.state)
        trial.events.append(dict(kind='open_lesson', primary=primary, input=deepcopy(bindings),
                                 master_row=row, after=trial.state_digest(), card_battle=False))
        if fired:trial.events[-1]['custom_triggers']=fired
        self.__dict__.update(trial.__dict__)

    def rest(self):
        stage = self._stage()
        if not stage.get('rest_when_wounded') or self.state['hp'] == self.state['max_hp']:
            raise Unsupported('Rest is not available at full stamina/current stage')
        c = Catalog()
        try:
            setting = c.one('ProduceSetting', c.one('Produce', self.produce_id)['produceSettingId'])
            value = self.state['max_hp'] * setting['refreshStaminaRecoveryPermil'] // 1000
        finally:
            c.db.close()
        trial=deepcopy(self);trial._supports('StartRefresh')
        trial.state['hp'] = min(trial.state['max_hp'], trial.state['hp'] + value)
        trial.events.append(dict(kind='rest', position=self.position, recovery=value, after=trial.state_digest()))
        trial._advance_schedule()
        trial._validate(trial.state);self.__dict__.update(trial.__dict__)

    def to_finals(self):
        if self.phase != 'before_round1' or self.position != 7 or self.pending:
            raise Unsupported('Finish all actual day/reward events before R1')
        c = Catalog()
        try:
            setting = c.one('ProduceSetting', c.one('Produce', 'produce-008')['produceSettingId'])
            gain = pre_exam_recovery(self.state['max_hp'],setting['beforeAuditionRefreshStaminaRecoveryPermil'],
                                     self.modifiers['before_exam_recovery_permil'])
        finally:
            c.db.close()
        self.state['hp'] = min(self.state['max_hp'], self.state['hp'] + gain)
        document = dict(schema_version=1, provenance=deepcopy(self.initial['provenance']), state=deepcopy(self.state))
        FinalsSegment(document)
        self.phase = 'handed_to_finals'
        self.events.append(dict(kind='to_finals', recovery=gain, after=self.state_digest()))
        return document

    def record(self):
        from .evaluate import implementation_manifest
        result=dict(initial=deepcopy(self.initial), events=deepcopy(self.events), state=deepcopy(self.state),
                    phase=self.phase, position=self.position, implementation_manifest=implementation_manifest(), scope=SCOPE)
        if self.market is not None:
            result.update(market=deepcopy(self.market),continuation=self.continuation)
        if self.custom_market_start is not None:
            result.update(custom_market_start=deepcopy(self.custom_market_start),continuation=self.continuation)
        if 'supports' in self.state:result.update(support_trace=deepcopy(self.support_trace),end_support_phase=self.end_support_phase)
        return result


def apply_growth_event(env,event):
    kind = event['kind']
    if kind == 'event': env.choose_event(event['event_id'], event['option_id'], event['attribute'])
    elif kind == 'offer': env.receive_offer(event['input'])
    elif kind == 'reward_choice': env.choose_reward(event['option_id'])
    elif kind == 'change_source': env.choose_source(event['instance'])
    elif kind == 'open_lesson': env.open_lesson(event['primary'], event['input'])
    elif kind == 'rest': env.rest()
    elif kind == 'drink_grant': env.receive_drink_grant(event['input'])
    elif kind == 'drink_batch': env.receive_drink_batch(event['input'])
    elif kind == 'custom_shop_begin': env.begin_custom_shop(event['input'])
    elif kind == 'drink_discard': env.resolve_drink_overflow(event['action'])
    elif kind == 'market_open': env.open_market(event['market_kind'],event['input'],event.get('start_receipt'))
    elif kind == 'market_progress': env.install_market(event['input'])
    elif kind == 'present_choice': env.choose_present(event['progress_id'],event['position'],event['candidate_index'])
    elif kind == 'shop_action': env.transact_shop(event['action'])
    elif kind == 'market_end': env.end_market(event['input'])
    elif kind == 'to_finals': env.to_finals()
    else: raise Unsupported('Unknown growth replay event')


def replay_growth(record):
    from .evaluate import implementation_manifest
    if record['implementation_manifest'] != implementation_manifest():
        return dict(matches=False, error='Growth implementation changed')
    env = GrowthSession(record['initial'])
    for event in record['events']:
        apply_growth_event(env,event)
    actual = env.record()
    differences = [k for k in sorted(set(actual) | set(record)) if digest(actual.get(k)) != digest(record.get(k))]
    return dict(matches=not differences, differences=differences, events=len(env.events))
