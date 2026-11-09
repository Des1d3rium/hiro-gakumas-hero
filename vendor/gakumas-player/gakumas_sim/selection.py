"""HIF selection calendar host with explicit current events and exam receipts.

Completion means the supplied selection path settled, not account qualification
or memory/server generation. Unknown effects and missing current progress fail closed.
"""
from copy import deepcopy
from .catalog import Catalog
from .core import Unsupported
from .finals import FinalsSegment, exact, source, digest, text
from .growth import GrowthSession
from .schedule import HIFSchedule
from .growth_scaling import pre_exam_recovery
from .hif import HIFPlanner
from .master_adapter import MasterAdapter, _sandbox_from_adapter
from .audition import bind_native_audition_battle, bind_displayed_audition_battle
from .evaluate import run_battle, implementation_manifest
from .market import validate_rewards
from .supports import apply_phase
from .guidance import GuidanceMixin,apply_guidance_event
from .selection_results import SelectionResultMixin,exam_rewards

SCOPE = ('HIF selection event host with explicit current roots, offers, exam bonus/seed and result receipts; '
         'not server supply, full account modifiers, generated memory or actual qualification.')


class SelectionSession(SelectionResultMixin,GuidanceMixin,GrowthSession):
    produce_id = 'produce-007'

    def __init__(self, document):
        super().__init__(document)
        self.current_event = None
        self.battles = []
        self.exam_result_passed = None
        self.guidance = None
        self.custom_item_offer = None

    def _stage(self):
        if self.phase != 'schedule' or not 1 <= self.position <= 20:
            raise Unsupported('No selection schedule choice is currently offered')
        c = Catalog()
        try: return HIFSchedule(self.produce_id, c).stage(self.position)
        finally: c.db.close()

    def resource_digest(self):
        return self.state_digest()

    def _advance_schedule(self):
        self.current_event = None
        if self.exam_result_passed is False:
            self.phase = 'failed'; return
        if self.exam_result_passed is True:
            self.phase='await_custom_item_offer';return
        self.exam_result_passed = None
        self.position += 1
        self.phase = 'schedule' if self.position <= 20 else 'selection_complete_await_memory'

    def bind_event(self, binding):
        self._stage()
        if self.current_event is not None: raise Unsupported('Current event is already bound')
        exact(binding, {'provenance','state_digest','position','event_id','attribute'}, 'Current selection event')
        source(binding['provenance'])
        text(binding['event_id'],'Selection event root')
        if type(binding['position']) is not int or binding['position'] != self.position or binding['state_digest'] != self.state_digest():
            raise Unsupported('Selection event is bound to another date/state')
        # Explicit observed root; aliases with identical master suggestions are
        # accepted, but no reward family or phase is produced from ID spelling.
        GrowthSession.event_options(self,binding['event_id'],binding['attribute'])
        self.current_event = deepcopy(binding)
        self.events.append(dict(kind='selection_event_binding',input=deepcopy(binding)))

    def event_options(self,event_id,attribute=None):
        if (self.current_event is None or self.current_event['event_id'] != event_id
            or self.current_event['attribute'] != attribute):
            raise Unsupported('Need a bound current selection event, not a guessed future root')
        return super().event_options(event_id,attribute)

    def play_exam(self, protocol, seed, policy='skill', policy_config=None, objective='score'):
        stage = self._stage()
        if stage['kind'] != 'exam': raise Unsupported('No selection exam on this date')
        if objective not in ('score','qualification'):raise Unsupported('Unknown selection strategy objective')
        displayed = isinstance(protocol,dict) and 'display_bonus' in protocol
        exact(protocol, {'resource_digest','display_bonus','source'} if displayed else
              {'resource_digest','progress','audition','source'}, 'Selection exam input')
        if protocol['resource_digest'] != self.state_digest(): raise Unsupported('Stale selection exam input')
        trial = deepcopy(self); before = self.state_digest(); c = Catalog()
        try:
            setting = c.one('ProduceSetting',c.one('Produce',self.produce_id)['produceSettingId'])
            gain = pre_exam_recovery(trial.state['max_hp'],setting['beforeAuditionRefreshStaminaRecoveryPermil'],
                                     trial.modifiers['before_exam_recovery_permil'])
            trial.state['hp'] = min(trial.state['max_hp'],trial.state['hp']+gain)
            config = HIFPlanner(trial.state['idol_card_id'],c).configuration(self.produce_id,self.position)
            target = HIFPlanner(trial.state['idol_card_id'],c).selection(stage['exam_number'])
            doc = _sandbox_from_adapter(MasterAdapter(c),[trial.state['cards'][k] for k in trial.state['card_order']],
                config['battle_config']['turn'],[trial.state['drinks'][k] for k in trial.state['drink_order']],
                FinalsSegment._loadout(trial.state,self.produce_id))
            doc.update(id='hif-selection-position-'+str(self.position),
                cards=dict(zip(trial.state['card_order'],doc['cards'].values())),
                state=dict(hp=trial.state['hp'],max_hp=trial.state['max_hp']),
                drinks={k:trial.state['drinks'][k] for k in trial.state['drink_order']},
                goal=deepcopy(target['strategy_goals'][objective]),round_adjustment=dict(numerator=1,denominator=1))
            doc['zones'] = dict(deck=list(doc['cards']))
            from .growth_items import bind_battle_start
            doc = bind_battle_start(doc,trial.state,c)
            if displayed:
                doc = bind_displayed_audition_battle(doc,self.produce_id,self.position,protocol['display_bonus'],
                    protocol['source'],trial.state['idol_card_id'])
            else:
                doc = bind_native_audition_battle(doc,self.produce_id,self.position,protocol['progress'],protocol['audition'],
                    protocol['source'],trial.state['idol_card_id'])
            if trial.state.get('support_scope') == 'growth-and-exam':
                from .support_upgrades import bind
                doc = bind(doc,trial.state['support_exam'])
            battle = run_battle(doc,policy,seed,capture=True,policy_config=policy_config)
            if battle['status'] != 'ok': raise Unsupported('Selection exam did not complete: '+battle.get('error',battle['status']))
            trial.state['hp'] = battle['hp']
            used = {e['action']['id'] for e in battle['events'] if e['action']['kind']=='drink'}
            trial.state['drinks'] = {k:v for k,v in trial.state['drinks'].items() if k not in used}
            trial.state['drink_order'] = [k for k in trial.state['drink_order'] if k not in used]
            if len(trial.state['drinks']) != battle['drinks_left']: raise Unsupported('Selection drink reconciliation failed')
            if 'next_audition_enchants' in trial.state: trial.state['next_audition_enchants'] = []
            trial.state,trace = apply_phase(trial.state,'EndAudition',{},c)
            trial.support_trace.extend(trace)
        finally: c.db.close()
        trial._validate(trial.state);trial.phase='await_exam_progress';trial.battles.append(battle)
        trial.events.append(dict(kind='selection_exam',position=self.position,input=deepcopy(protocol),seed=seed,
            policy=policy,policy_config=deepcopy(policy_config),recovery=gain,before=before,after=trial.state_digest(),
            objective=objective,qualification_reference=dict(kind='pinned-NPC-upper-envelope',master_commit=c.info['commit'],
                idol_card_id=trial.state['idol_card_id'],mode=self.produce_id,position=self.position,
                envelope=deepcopy(target['pass_score_envelope']),goal=deepcopy(doc['goal']),scope=target['strategy_goal_scope']),
            result={k:battle[k] for k in ('raw_score','adjusted_score','hp','drinks_left','target_met')}))
        self.__dict__.update(trial.__dict__)
        return deepcopy(trial.events[-1])

    def settle_exam(self, receipt):
        if self.phase != 'await_exam_progress': raise Unsupported('No selection exam awaits result progress')
        exact(receipt,{'provenance','state_digest','position','passed','rewards','removed_instances','unmodelled_effects'},'Selection exam receipt')
        source(receipt['provenance'])
        if type(receipt['position']) is not int or receipt['state_digest']!=self.state_digest() or receipt['position']!=self.position:
            raise Unsupported('Stale selection result progress')
        if type(receipt['passed']) is not bool or receipt['unmodelled_effects']!=[]:
            raise Unsupported('Need explicit qualification result and fully modeled receipt')
        removed=receipt['removed_instances']
        if (not isinstance(removed,list) or any(not isinstance(k,str) for k in removed)
            or len(removed)!=len(set(removed)) or set(removed)-set(self.state['cards'])
            or removed and not receipt['passed']):
            raise Unsupported('Selection removals require unique current instances and a successful result')
        exam_rewards(receipt['rewards']);trial=deepcopy(self)
        for key in removed:
            del trial.state['cards'][key];trial.state['card_order'].remove(key)
        trial.exam_result_passed=receipt['passed'];trial.pending=[]
        for i,reward in enumerate(receipt['rewards']):
            effect={k:deepcopy(v) for k,v in reward.items() if k!='kind'}
            trial.pending.append(dict(effect,kind={'point':'point','card':'grant_card','drink':'grant_drink',
                'settled_parameter':'settled_parameter','settled_star':'settled_star'}[reward['kind']],
                source_id='selection-exam:'+str(self.position)+':'+str(i)))
        trial._drain();trial._validate(trial.state)
        trial.events.append(dict(kind='selection_exam_receipt',input=deepcopy(receipt),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def observe(self):
        result=super().observe();result.update(mode=self.produce_id,scope=SCOPE,current_event=deepcopy(self.current_event))
        if self.guidance is not None:result['guidance']=deepcopy(self.guidance)
        if self.custom_item_offer is not None:
            result['custom_item_offer']=deepcopy(self.custom_item_offer);result['custom_item_options']=self.custom_item_options()
        return result

    def record(self):
        result=super().record();result.update(mode=self.produce_id,scope=SCOPE,battles=deepcopy(self.battles),
            current_event=deepcopy(self.current_event),exam_result_passed=self.exam_result_passed)
        if self.guidance is not None:result['guidance']=deepcopy(self.guidance)
        if self.custom_item_offer is not None:result['custom_item_offer']=deepcopy(self.custom_item_offer)
        return result

    def strategy_evaluation(self):
        checks=[]
        for battle in self.battles:
            doc=battle['scenario_document'];goal=doc['goal']
            checks.append(dict(position=doc['exam_bonus']['position'],raw_score=battle['raw_score'],
                target_score=goal['target_score'],target_met=battle['target_met'],
                stop_at_target=goal.get('stop_at_target',False)))
        return dict(event_flow_complete=self.phase=='selection_complete_await_memory',exam_targets=checks,
            all_three_local_targets_met=[r['position'] for r in checks]==[7,13,20] and all(r['target_met'] for r in checks),
            scope='Conditional local score targets; event completion and supplied passed receipts do not certify account qualification.')


def replay_selection(record):
    if record['implementation_manifest'] != implementation_manifest(): return dict(matches=False,error='Selection implementation changed')
    env=SelectionSession(record['initial'])
    from .growth import apply_growth_event
    for event in record['events']:
        if event['kind']=='selection_event_binding':env.bind_event(event['input'])
        elif event['kind']=='selection_exam':env.play_exam(event['input'],event['seed'],event['policy'],event['policy_config'],event.get('objective','score'))
        elif event['kind']=='selection_exam_receipt':env.settle_exam(event['input'])
        elif event['kind']=='selection_custom_item_offer':env.receive_custom_item_offer(event['input'])
        elif event['kind']=='selection_custom_item_result':env.settle_custom_item(event['input'])
        elif event['kind'].startswith('guidance_'):apply_guidance_event(env,event)
        else:apply_growth_event(env,event)
    actual=env.record();expected=deepcopy(record)
    for obj in (actual,expected):
        for battle in obj['battles']:battle.pop('elapsed_seconds',None)
    differences=[k for k in sorted(set(actual)|set(expected)) if digest(actual.get(k))!=digest(expected.get(k))]
    return dict(matches=not differences,differences=differences,events=len(env.events),exams=len(env.battles),
                strategy_evaluation=env.strategy_evaluation())
