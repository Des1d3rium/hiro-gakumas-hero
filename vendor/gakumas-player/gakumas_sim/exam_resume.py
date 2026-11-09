"""Resume the visible Main phase, without manufacturing a hidden-order prior.

The native queue boundary is acquired separately from the immutable v56 public
projection. This module does not resume the next turn or certify a full exam.
"""
from collections import Counter
from copy import deepcopy
import hashlib

from .core import Battle, Unsupported
from .exam_snapshot import PUBLIC_FIELDS, SCALARS, canonical, project
from .catalog import Catalog
from .master_adapter import MasterAdapter, sandbox
from .passives import PassiveCompiler
from .support_upgrades import expand_definitions, packet_supports

BOUNDARY_FIELDS = {'schema_version', 'scope', 'public_sha256', 'phase', 'commands', 'playing', 'gimmicks'}
BASE = {'_isTurnLimited', '_turn', '_isPassingTurnStart', '_uid'}
TRIGGER_FIELDS = BASE | {'_overrideType', '_isItemDirectEnchant', '_startEnchantOriginId',
    '_startEnchantOriginLevel', '_startEnchantOwnerId', '_startEnchantOriginType', '_trigger', '_effectList',
    '_turnCount', '_phaseCountDictionary', '_limitCount', '_limitCountInTurn', '_limitCountInTurnRemain',
    '_statusEnchantId', '_fromExamEffectIdList', '_descriptionReactiveDataTypeList',
    '_descriptionReactiveDataTextList', '_originType', '_isFromEnchantEffect', '_isEncoreEnchant',
    '_triggerItem', '_triggerDrink', '_triggerCard', '_triggerGimmickGroup'}
EFFECT_ENUM = {'ProduceExamEffectType_Unknown': 0, 'ProduceExamEffectType_ExamStaminaReduceFix': 7,
    'ProduceExamEffectType_ExamPlayableValueAdd': 14, 'ProduceExamEffectType_ExamLessonDependBlock': 19,
    'ProduceExamEffectType_ExamCardPlayAggressive': 42}
PHASE_ENUM = {'ProduceExamPhaseType_ExamCardPlay': 2, 'ProduceExamPhaseType_ExamStartTurn': 4}


def verified(observation):
    if not isinstance(observation, dict) or set(observation) != PUBLIC_FIELDS:
        raise Unsupported('Exact public acquisition required')
    packet = deepcopy(observation)
    fingerprint = packet.pop('public_sha256')
    if fingerprint != hashlib.sha256(canonical(packet).encode()).hexdigest():
        raise Unsupported('Public acquisition fingerprint mismatch')
    if (packet['schema_version'] != 1 or packet['scope'] != 'public-hif-exam-acquisition'
            or packet['source']['client_version'] != '3.4.1/build90'):
        raise Unsupported('Pinned public acquisition required')
    s=packet['scalars']
    strings={'settingId','characterId','produceId','idolCardId'}
    bools={'isTurnCardPlayEnd','isTurnCardGrave','isTurnCardLost'}
    if set(s)!=set(SCALARS) or any(type(s[k]) is not (str if k in strings else bool if k in bools else int) for k in s):
        raise Unsupported('Invalid public resume scalars')
    if s['stamina']<0 or s['stamina']>s['maxStamina'] or s['maxStamina']<1 or min(s['parameter'],s['block'])<0:
        raise Unsupported('Invalid public resume resources')
    zones=packet['zones'];cards=packet['cards']
    if set(zones)!={'hand','deck','grave','lost','hold','future','past','removed','playing'}:
        raise Unsupported('Unknown resume zones')
    if any(not isinstance(v,list) for v in zones.values()) or Counter(sum(zones.values(),[]))!=Counter(cards.keys()):
        raise Unsupported('Resume physical partitions disagree')


def acquire_boundary(document, *, client_version, master_commit):
    """Inspect queue emptiness only; never publish command contents or RNG."""
    observed = project(document, client_version=client_version, master_commit=master_commit)
    for field in ('commandList', 'gimmickList'):
        if field not in document or not isinstance(document[field], list) or document[field]:
            raise Unsupported('Resume requires an empty ' + field)
    if observed['scalars']['phase'] != 6 or observed['zones']['playing']:
        raise Unsupported('Resume requires an idle Main phase')
    if document.get('isExamEndComplete') is not False:
        raise Unsupported('Exam end boundary not admitted')
    return dict(schema_version=1, scope='hif-idle-main-boundary', public_sha256=observed['public_sha256'],
                phase=6, commands=0, playing=False, gimmicks=0)


def native_effect(master):
    """Exact persisted payload for the bounded active enchants, not a default stub."""
    if master['effectType'] not in EFFECT_ENUM:
        raise Unsupported('Active native effect enum not ported')
    mapping = {'id':'_id', 'effectValue1':'_effectValue1', 'effectValue2':'_effectValue2',
        'effectCount':'_effectCount', 'effectTurn':'_effectTurn', 'targetProduceCardId':'_targetProduceCardId',
        'targetUpgradeCount':'_targetUpgradeCount', 'produceCardSearchId':'_cardSearchId',
        'produceCardSearchId2':'_cardSearchId2', 'pickCountReferenceProduceCardSearchId':'_pickCountReferenceProduceCardSearchId',
        'pickCountReferenceProduceCardSearchId2':'_pickCountReferenceProduceCardSearchId2',
        'pickCountMin':'_pickCountMin', 'pickCountMax':'_pickCountMax',
        'pickCountMin2':'_pickCountMin2', 'pickCountMax2':'_pickCountMax2',
        'chainProduceExamEffectId':'_chainEffectId', 'chainProduceExamEffectIds':'_chainEffectIdList',
        'produceExamStatusEnchantId':'_statusEnchantId', 'effectGroupIds':'_effectGroupIdList',
        'produceCardGrowEffectIds':'_cardGrowEffectIdList'}
    enum_defaults = {'targetExamEffectType':'ProduceExamEffectType_Unknown',
        'movePositionType':'ProduceCardMovePositionType_Unknown', 'pickRangeType':'ProducePickRangeType_Unknown',
        'pickCountType':'ProducePickCountType_Unknown', 'pickRangeType2':'ProducePickRangeType_Unknown',
        'pickCountType2':'ProducePickCountType_Unknown'}
    if any(master[k] != v for k,v in enum_defaults.items()) or master['produceCardStatusEnchantId']:
        raise Unsupported('Active native effect selector/enchant not ported')
    result = {target:deepcopy(master[source]) for source,target in mapping.items()}
    result.update(_effectType=EFFECT_ENUM[master['effectType']], _targetExamEffectType=0, _movePositionType=0,
                  _pickRangeType=0, _pickCountType=0, _pickRangeType2=0, _pickCountType2=0,
                  _judgeTargetIndex=0, _pickCount=0)
    return result


def compile_runtime(observation, boundary, catalog=None):
    verified(observation)
    expected = dict(schema_version=1, scope='hif-idle-main-boundary', public_sha256=observation['public_sha256'],
                    phase=6, commands=0, playing=False, gimmicks=0)
    if not isinstance(boundary,dict) or set(boundary)!=BOUNDARY_FIELDS or canonical(boundary)!=canonical(expected):
        raise Unsupported('Matching idle Main boundary required')
    s=observation['scalars']
    if (s['phase']!=6 or s['produceId']!='produce-008' or s['planType']!=3
            or observation['idol_status']!={'_currentType':0,'_currentStep':0}):
        raise Unsupported('Only the pinned HIF Logic Main state is restored')
    if any(observation['zones'][k] for k in ('playing','future','past','hold','removed')):
        raise Unsupported('Unsupported resume card buffer')
    if s['remainTurn']!=s['limitTurn']+s['extraTurn']-s['currentTurn']+1 or s['remainTurn']<1:
        raise Unsupported('Native remaining-turn arithmetic disagrees')
    own=catalog is None;c=catalog or Catalog()
    try:
        if c.info['commit']!=observation['source']['master_commit']:
            raise Unsupported('Resume master mismatch')
        adapter=MasterAdapter(c);compiler=PassiveCompiler(c)
        values=dict(hp=s['stamina'],max_hp=s['maxStamina'],shield=s['block'],score=s['parameter'],
                    motivation=0,impression=0,focus=0,mental_motivation=0,stamina_modifiers=[],hooks=[])
        counters=[];uids=set();types=Counter();extra=0
        items={r['_id']:r for r in observation['items']}
        if len(items)!=len(observation['items']):raise Unsupported('Duplicate resume item')
        for entry in observation['active_statuses']:
            if entry['type']['ns']!='Campus.InGame.Exam' or entry['type']['asm']!='Assembly-CSharp':
                raise Unsupported('Unknown resume status provider')
            name=entry['type']['class'];d=entry['data'];types[name]+=1
            if not BASE<=set(d) or type(d['_uid']) is not int or d['_uid'] in uids:
                raise Unsupported('Duplicate/incomplete resume status')
            uids.add(d['_uid'])
            if any(type(d[k]) is not bool for k in ('_isTurnLimited','_isPassingTurnStart')) or type(d['_turn']) is not int:
                raise Unsupported('Invalid resume timer')
            if name in ('ReviewStatusEffect','AggressiveStatusEffect','StaminaConsumptionDownStatusEffect'):
                fields=BASE|({'_value'} if name=='AggressiveStatusEffect' else set())
                if set(d)!=fields or types[name]>1:raise Unsupported('Unported resume resource stack')
                if name=='ReviewStatusEffect':
                    if not d['_isTurnLimited'] or d['_turn']<1:raise Unsupported('Invalid impression timer')
                    values['impression']=d['_turn']
                elif name=='AggressiveStatusEffect':
                    if d['_isTurnLimited'] or d['_turn']!=-1 or type(d['_value']) is not int or d['_value']<0:
                        raise Unsupported('Invalid motivation status')
                    values['motivation']=d['_value']
                else:
                    if not d['_isTurnLimited'] or d['_turn']<1:raise Unsupported('Invalid stamina modifier')
                    values['stamina_modifiers'].append(dict(kind='stamina_down',permil=500,remaining=d['_turn'],fresh=not d['_isPassingTurnStart']))
            elif name=='PlayableValueAddStatusEffect':
                # Native collection selects one status; merging multiple is not assumed.
                if set(d)!=BASE|{'_value'} or types[name]>1 or type(d['_value']) is not int or d['_value']<0:
                    raise Unsupported('Unported extra-action status')
                extra=d['_value']
            elif name=='TriggerEffectStatusEffect':
                if set(d)!=TRIGGER_FIELDS or d['_isTurnLimited'] or d['_turn']!=-1:
                    raise Unsupported('Unported active trigger shape/timer')
                if any(type(d[k]) is not int or d[k]<-1 for k in ('_limitCount','_limitCountInTurn','_limitCountInTurnRemain')):
                    raise Unsupported('Invalid native remaining count')
                if (d['_overrideType']!=31 or d['_phaseCountDictionary']!={'_list':[]}
                    or d['_fromExamEffectIdList'] or d['_descriptionReactiveDataTypeList'] or d['_descriptionReactiveDataTextList']
                    or d['_startEnchantOriginId'] or d['_startEnchantOriginLevel'] or d['_startEnchantOwnerId']
                    or d['_startEnchantOriginType'] or d['_originType']!=1 or d['_isFromEnchantEffect'] or d['_isEncoreEnchant']
                    or d['_triggerDrink']!={'_id':''} or d['_triggerGimmickGroup']['_id']):
                    raise Unsupported('Unported active trigger context')
                raw=compiler.enchant(d['_statusEnchantId']);trigger=c.one('ProduceExamTrigger',raw['produceExamTriggerId'])
                try:phases=[PHASE_ENUM[p] for p in trigger['phaseTypes']]
                except KeyError:raise Unsupported('Unported resume trigger phase')
                if d['_trigger']!=dict(_id=trigger['id'],_phaseValueList=trigger['phaseValues'],_phaseTypeList=phases):
                    raise Unsupported('Native/master active trigger mismatch')
                if d['_effectList']!=[native_effect(c.one('ProduceExamEffect',key)) for key in raw['produceExamEffectIds']]:
                    raise Unsupported('Native/master active payload mismatch')
                source=d['_triggerItem']['_id']
                if d['_isItemDirectEnchant']:
                    if source not in items or d['_triggerItem']!=items[source] or d['_triggerCard'] is not None:
                        raise Unsupported('Native trigger item identity/count mismatch')
                    kind='item'
                else:
                    card=d['_triggerCard']
                    current=observation['cards'].get(card.get('instance')) if isinstance(card,dict) else None
                    # Trigger origins are saved copies. Their historical playCount
                    # is not the mutable owned card's later cumulative playCount.
                    old=deepcopy(card);now=deepcopy(current)
                    if old and now:
                        old_count=old['native'].pop('_playCount');now_count=now['native'].pop('_playCount')
                    else:old_count=now_count=-1
                    if source or not current or old!=now or type(old_count) is not int or type(now_count) is not int or not 0<=old_count<=now_count:
                        raise Unsupported('Native trigger card identity/version mismatch')
                    source=card['card_id'];kind='card'
                exhausted=d['_limitCount']==0
                counter=dict(uid=d['_uid'],source_kind=kind,source_id=source,remaining=d['_limitCount'],
                    per_turn_limit=d['_limitCountInTurn'],per_turn_remaining=d['_limitCountInTurnRemain'],
                    turn_count=d['_turnCount'],exhausted=exhausted)
                counters.append(counter)
                # Exhausted triggers are retained in the ledger, never reinstalled unlimited.
                if exhausted:continue
                if d['_limitCountInTurn']!=-1 or d['_limitCountInTurnRemain']!=-1:
                    raise Unsupported('Live per-turn trigger quota not ported')
                hook=compiler.hook(d['_statusEnchantId'],max(0,d['_limitCount']))
                if hook['phase']=='card_play' and kind=='card':
                    # Already installed mental enchant: future card bodies may add more.
                    if hook['category']!='mental' or hook['effects']!=[dict(op='motivation',value=1)] or hook['limit']:
                        raise Unsupported('Installed card enchant not ported')
                    values['mental_motivation']+=1
                else:
                    if kind!='item':raise Unsupported('Unknown active hook origin')
                    values['hooks'].append(dict(hook,source_id=source,source_kind='item',hook_index=d['_uid'],priority=0,uses=0))
            else:raise Unsupported('Resume status not ported: '+name)
        values['actions']=int(not s['isTurnCardPlayEnd'])+extra
        return dict(schema_version=1,scope='hif-current-main-runtime',public_sha256=observation['public_sha256'],
                    values=values,counters=counters,item_ledger=deepcopy(observation['items']),
                    native_statuses=deepcopy(observation['active_statuses']),
                    native_turn=s['currentTurn'],remaining_turns=s['remainTurn'],engine_admitted=False,
                    full_exam_admitted=False,posterior_admitted=False)
    finally:
        if own:c.db.close()


class MainPhaseSession:
    """Exact admitted visible actions, then stop at this turn's boundary.

    Never expose Battle.result(): the one-turn planning model is not an exam.
    Unknown draw identities remain unresolved; they cannot become played cards.
    """
    def __init__(self,observation,boundary):
        c=Catalog()
        try:
            runtime=compile_runtime(observation,boundary,c);adapter=MasterAdapter(c)
            specs=[];identities=list(observation['cards']);key_by_instance={}
            for identity in identities:
                card=observation['cards'][identity];native=card['native'];raw=c.card(card['card_id'],card['effective_upgrade'])
                if native['_tmpUpgradeCount'] or native['_growEffectExamStartAfterList'] or native['_affectGrowEffectIdList'] or native['_staminaConsumptionSpecifyEffectList']:
                    raise Unsupported('Card-local resume growth not ported')
                if native['_statusEffect']!={'_id':'','_produceExamTriggerId':'','_produceCardGrowEffectIdList':[],
                    '_effectGroupIdList':[],'_triggerCount':0,'_phaseCountDictionary':{'_list':[]},'_spendTurn':0,'_spendCount':0}:
                    raise Unsupported('Card-local resume enchant not ported')
                counts=card['customize_counts'];keys=raw['produceCardCustomizeIds']
                if len(counts) not in (0,len(keys)):raise Unsupported('Resume customization branch mismatch')
                specs.append(dict(card_id=card['card_id'],upgrade=card['effective_upgrade'],
                    customizations=[dict(id=k,count=n) for k,n in zip(keys,counts) if n]))
            doc=sandbox(specs,turns=1)
            key_by_instance={identity:doc['cards']['c'+str(i)] for i,identity in enumerate(identities)}
            # Add base grades and full support graph so a play resets to the actual base.
            for identity,spec in zip(identities,specs):
                base=observation['cards'][identity]['native']['_baseUpgradeCount']
                rule=adapter.card(spec['card_id'],base,spec['customizations'])
                from .support_upgrades import variant_key
                doc['definitions'][variant_key(rule['provenance'],base)]=rule
            definitions,graph=expand_definitions(doc,c)
            packet=dict(provenance=dict(kind='observed-progress',reference='public:'+observation['public_sha256']),
                cards=[dict(supportCardId=r['_supportCardId'],produceCardUpgradePermil=r['_produceCardUpgradePermil']) for r in observation['supports']])
            supports=packet_supports(packet,c)
            for raw,sup in zip(observation['supports'],supports):
                if raw['_filterParameterType']!={None:0,'Vo':1,'Da':2,'Vi':3}[sup['parameter']] or raw['_cardSearchId']!=sup['card_search_id']:
                    raise Unsupported('Resume support filter mismatch')
            s=observation['scalars'];bonus=s[{1:'vocalBonusPermil',2:'danceBonusPermil',3:'visualBonusPermil'}[observation['current_parameter_type']]]
            attr={1:'Vo',2:'Da',3:'Vi'}[observation['current_parameter_type']]
            hand=[dict(instance=i,definition_id=key_by_instance[i],definition=definitions[key_by_instance[i]]) for i in observation['zones']['hand']]
            rules=dict(supports=supports,next_definitions=graph,current_attribute=attr,attributes=[attr])
            upgraded={}
            for i in observation['zones']['hand']:
                card=observation['cards'][i];ids=card['native']['_supportUpgradeIdList']
                if ids:
                    spec=specs[identities.index(i)];base=card['native']['_baseUpgradeCount']
                    rule=adapter.card(card['card_id'],base,spec['customizations'])
                    upgraded[i]=dict(base_definition=variant_key(rule['provenance'],base),support_ids=list(ids))
                elif card['effective_upgrade']!=card['native']['_baseUpgradeCount']:
                    raise Unsupported('Unexplained effective hand grade')
            model=dict(definition_library=definitions,turns=[dict(draw=0,actions=runtime['values']['actions'],bonus_permil=bonus)],support_upgrades=rules)
            model['exam_upgrades']=dict(kind='public-exam-hand-upgrade',next_definitions=graph,
                grades={k:d['provenance']['upgrade'] for k,d in definitions.items()})
            for zone in ('deck','grave','hold','lost'):
                model[zone+'_definition_ids']=[key_by_instance[i] for i in observation['zones'][zone]]
            drink_rules={str(i):adapter.drink(row['_id']) for i,row in enumerate(observation['drinks'])}
            setting=c.one('ExamSetting',s['settingId'])
            v=runtime['values']
            obs=dict(v,hand=hand,hand_limit=setting['handLimit'],skip_recovery=setting['examTurnEndRecoveryStamina'],
                turn=s['currentTurn']-1,remaining_turns=s['remainTurn'],bonus_permil=bonus,remaining_bonus_permil=[bonus],
                public_model=model,support_state=dict(used=observation['turn_used_supports'],upgraded=upgraded),
                drinks=drink_rules,boosts=[],ticks=[],reservations=[],repeat_buffs=[],end_turn_motivation=0,permanent_boost=0,
                extra_turn_template=dict(draw=0,actions=1,bonus_permil=bonus),extra_turns_added=s['extraTurn'],
                deck_count=len(observation['zones']['deck']),grave_count=len(observation['zones']['grave']),
                hidden_card_counts=dict(Counter(observation['cards'][i]['card_id'] for zone in ('deck','grave') for i in observation['zones'][zone])),
                goal=dict(target_score=0))
            self.initial_observation=deepcopy(obs)
            self.model=Battle.visible_model(obs);self.runtime=runtime;self.source=deepcopy(observation);self.closed=False
        finally:c.db.close()

    def legal_actions(self):
        return [] if self.closed else self.model.legal_actions()

    def state(self):
        b=self.model
        return dict(native_turn=self.runtime['native_turn'],hp=b.hp,shield=b.shield,motivation=b.motivation,
            impression=b.impression,score=b.score,actions=b.actions,
            hand=[dict(instance=i,definition_id=b.cards[i]) for i in b.zones['hand']],
            drinks_left=len(b.drinks),support_used=list(b.support_used),unknown_hand_slots=b.unknown_draws,
            reservations=deepcopy(b.reservations),stamina_modifiers=deepcopy(b.stamina_modifiers),
            boosts=deepcopy(b.boosts),mental_motivation=b.mental_motivation,hooks=deepcopy(b.hooks),
            boundary='turn-end-before-next-start' if self.closed else 'current-main',
            full_exam_admitted=False,posterior_admitted=False)

    def step(self,action):
        if self.closed:raise Unsupported('Next turn needs a new observed state/posterior')
        if action not in self.legal_actions():
            from .core import IllegalAction
            raise IllegalAction('Action not legal in resumed Main')
        if action['kind']!='end_turn':
            definition=(self.model.definition(action['id']) if action['kind']=='play'
                        else self.model.drink_definitions[self.model.drinks[action['id']]])
            def uncertain(value):
                if isinstance(value,dict):
                    return value.get('op') in {'draw','discard_redraw','fetch_tag','extra_turn','create_deck_random','create_hand_pool',
                        'upgrade_hand','next_turn_upgrade_hand','repeat_card','remove_random'} or any(uncertain(v) for v in value.values())
                return isinstance(value,list) and any(uncertain(v) for v in value)
            if uncertain(definition['effects']):
                raise Unsupported('This Main resume needs an observed draw/version/order before that transition')
        self.model.step(action,reason='observed-main-resume')
        if action['kind']=='end_turn':self.closed=True
        return self.state()
