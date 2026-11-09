"""Public-state determinization with an explicit uniform hidden-deck prior.

No main-environment handle, seed, private deck order or debug event is accepted.
Future policies see only each sampled world's then-current public observation.
"""
from collections import Counter
from copy import deepcopy
import hashlib,json,random,statistics
from .core import Battle,Unsupported,IllegalAction,public_definition,validate_definition
from .planning import fork_simulation

PUBLIC_FIELDS=('hp','max_hp','shield','score','hand_limit','skip_recovery','focus','motivation','impression',
    'permanent_boost','turn','remaining_turns','actions','bonus_permil','boosts','ticks',
    'mental_motivation','end_turn_motivation','reservations','extra_turn_template','extra_turns_added',
    'stamina_modifiers','repeat_buffs','hooks','goal','score_context')
OPTIONAL_PUBLIC_FIELDS=('card_play_scores','anti_debuff','stamina_down_fix','free_stamina','impression_boosts','block_restriction','impression_score_boosts')  # 后加的状态（輝くキミへ 附魔、低下状態無効）：旧观测没有时按空 / 0
MODEL_FIELDS={'definition_library','deck_definition_ids','grave_definition_ids','hold_definition_ids','lost_definition_ids','turns'}


def public_snapshot(observation,*,allow_unresolved_prior=False):
    if 'public_model' not in observation:raise Unsupported('No complete public rule/composition projection')
    if set(PUBLIC_FIELDS)-set(observation):raise Unsupported('Missing public state fields for rollout')
    model=observation['public_model']
    extra={k for k in ('support_upgrades','exam_upgrades','known_deck_prefix_definition_ids','deck_prior_issue') if isinstance(model,dict) and k in model}
    if 'deck_prior_issue' in extra:
        from .card_mutations import GENERATED_PRIOR,POOLED_UPGRADE_PRIOR
        if model['deck_prior_issue'] not in ('tag-first-match:remaining-order-posterior-required',GENERATED_PRIOR,POOLED_UPGRADE_PRIOR):raise Unsupported('Unknown deck prior issue')
        if not allow_unresolved_prior:raise Unsupported('Ordered-search posterior cannot be resampled as an independent uniform deck')
    if not isinstance(model,dict) or set(model)!=MODEL_FIELDS|extra:raise Unsupported('Unknown public model schema')
    if ('support_upgrades' in extra)!=('support_state' in observation):raise Unsupported('Incomplete public support state/rules')
    result={k:deepcopy(observation[k]) for k in PUBLIC_FIELDS}
    result.update({k:deepcopy(observation[k]) for k in OPTIONAL_PUBLIC_FIELDS if k in observation})
    if type(result['skip_recovery']) is not int or not 0<=result['skip_recovery']<=10000:
        raise Unsupported('Invalid public skip recovery')
    context=result['score_context']
    if (not isinstance(context,dict) or set(context)!={'round_adjustment','completed_adjusted_score'}
        or not isinstance(context['round_adjustment'],dict) or set(context['round_adjustment'])!={'numerator','denominator'}):
        raise Unsupported('Unknown public score context fields')
    result['hand']=[dict(instance=c['instance'],definition_id=c['definition_id']) for c in observation['hand']]
    library={k:public_definition(v) for k,v in model['definition_library'].items()}
    for rule in library.values():validate_definition(rule)
    result['public_model']={k:deepcopy(v) for k,v in model.items() if k!='definition_library'}
    result['public_model']['definition_library']=library
    result['public_model']['deck_definition_ids']=sorted(model['deck_definition_ids'])
    if 'support_upgrades' in extra:result['support_state']=deepcopy(observation['support_state'])
    result['drinks']={k:public_definition(v) for k,v in observation['drinks'].items()}
    if len(model['turns'])!=result['remaining_turns'] or not model['turns']:
        raise Unsupported('Need complete remaining public turn templates')
    for turn in model['turns']:
        if (set(turn)-{'lesson'}!={'draw','actions','bonus_permil'}
            or any(type(v) is not int or v<0 for k,v in turn.items() if k!='lesson')):
            raise Unsupported('Unknown public turn template fields')
    if len(model['deck_definition_ids'])!=observation['deck_count'] or len(model['grave_definition_ids'])!=observation['grave_count']:
        raise Unsupported('Public pile counts disagree')
    if model['turns'][0]['bonus_permil']!=result['bonus_permil']:raise Unsupported('Current public turn bonus disagrees')
    for key in ['deck_definition_ids','grave_definition_ids','hold_definition_ids','lost_definition_ids']:
        if any(d not in library for d in model[key]):raise Unsupported('Missing public definition')
    prefix=model.get('known_deck_prefix_definition_ids',[])
    if not isinstance(prefix,list) or any(not isinstance(d,str) for d in prefix) or Counter(prefix)-Counter(model['deck_definition_ids']):
        raise Unsupported('Invalid public known deck prefix')
    if any(c['definition_id'] not in library for c in result['hand']):raise Unsupported('Missing public hand definition')
    # Current-state fixed-order constraints/posteriors have not been restored.
    if any(library[d].get('order',0)>0 for key in ['deck_definition_ids','grave_definition_ids'] for d in model[key]):
        raise Unsupported('Fixed-order hidden-pile prior is not implemented')
    return result


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def sample_battle(snapshot,particle):
    """Uniform current hidden order; future RNG is a separate sampled prior."""
    key=fingerprint(snapshot)
    sample_seed=int.from_bytes(hashlib.sha256((key+'/'+str(particle)).encode()).digest()[:8],'little')
    sampler=random.Random(sample_seed);model=snapshot['public_model']
    if 'deck_prior_issue' in model:raise Unsupported('Cannot sample an unresolved ordered-search posterior')
    cards={c['instance']:c['definition_id'] for c in snapshot['hand']}
    if len(cards)!=len(snapshot['hand']):raise Unsupported('Duplicate public hand identity')
    zones=dict(hand=list(cards))
    for zone in ['deck','grave','hold','lost']:
        ids=list(model[zone+'_definition_ids'])
        if zone=='deck':
            prefix=list(model.get('known_deck_prefix_definition_ids',[]))
            for definition in prefix:ids.remove(definition)
            sampler.shuffle(ids);ids=prefix+ids
        zones[zone]=[]
        for i,definition in enumerate(ids):
            instance='belief-'+zone+'-'+str(i)
            while instance in cards:instance+='-copy'
            cards[instance]=definition;zones[zone].append(instance)
    turns=deepcopy(model['turns']);turns[0]['draw']=0;turns[0]['actions']=snapshot['actions']
    context=snapshot['score_context']
    doc=dict(schema_version=1,id='public-belief-sample',provenance=dict(kind='synthetic',purpose='uniform hidden-deck prior'),
             definitions=deepcopy(model['definition_library']),cards=cards,zones=zones,hand_limit=snapshot['hand_limit'],skip_recovery=snapshot['skip_recovery'],
             state=dict(hp=snapshot['hp'],max_hp=snapshot['max_hp']),turns=turns,shuffle=False,opening_initial=False,
             drink_definitions=deepcopy(snapshot['drinks']),drinks={k:k for k in snapshot['drinks']},goal=deepcopy(snapshot['goal']),
             round_adjustment=deepcopy(context['round_adjustment']),completed_adjusted_score=context['completed_adjusted_score'])
    if 'support_upgrades' in model:
        from .support_upgrades import sampled_config
        doc['support_upgrades']=sampled_config(model['support_upgrades'],turns)
    if 'exam_upgrades' in model:doc['exam_upgrades']=deepcopy(model['exam_upgrades'])
    env=Battle(doc);env.reset(sampler.getrandbits(32))
    env.known_deck_prefix=list(env.zones['deck'][:len(model.get('known_deck_prefix_definition_ids',[]))])
    for name in ['hp','shield','score','focus','motivation','impression','permanent_boost','actions','mental_motivation','end_turn_motivation','extra_turns_added']:
        setattr(env,name,deepcopy(snapshot[name]))
    for name in ['boosts','ticks','reservations','stamina_modifiers','repeat_buffs','hooks','extra_turn_template']:
        setattr(env,name,deepcopy(snapshot[name]))
    env.card_play_scores=deepcopy(snapshot.get('card_play_scores',[]))  # 出牌附魔（輝くキミへ）：推演里不能丢
    env.anti_debuff=snapshot.get('anti_debuff',0)
    env.stamina_down_fix=snapshot.get('stamina_down_fix',0)
    env.free_stamina=deepcopy(snapshot.get('free_stamina',[]))
    env.impression_boosts=deepcopy(snapshot.get('impression_boosts',[]))
    env.block_restriction=deepcopy(snapshot.get('block_restriction'))
    env.impression_score_boosts=deepcopy(snapshot.get('impression_score_boosts',[]))
    for timer in env.ticks+env.reservations:timer['start']-=snapshot['turn']
    if 'support_state' in snapshot:
        from .support_upgrades import restore_public
        restore_public(env,snapshot['support_state'])
    env._validate_zones()
    return env


def advance(env,action):
    if action not in env.legal_actions():raise IllegalAction('Rollout continuation proposed illegal action')
    # Sample worlds are disposable and isolated. Avoid copying growing audit
    # histories on each hypothetical step; the real environment still uses step.
    env.steps+=1;env._apply(action);env._validate_zones()


def trajectory(env,first_action,continuation,max_actions):
    advance(env,first_action)
    while not env.ended:
        if env.steps>=max_actions:raise Unsupported('Public rollout action budget exceeded')
        obs=env.observe();action,_=continuation.choose(obs,env.legal_actions());advance(env,action)
    result=env.result()
    return dict(raw_score=result['raw_score'],final_total=result['final_total'],hp=result['hp'],drinks_left=result['drinks_left'])


def objective(results,goal):
    mean=lambda key:statistics.mean(r[key] for r in results)
    if goal.get('stop_at_target',False):
        deficit=statistics.mean(max(0,goal['target_score']-r['raw_score']) for r in results)
        return (statistics.mean(r['raw_score']>=goal['target_score'] for r in results),-deficit,
                mean('hp'),mean('drinks_left'),mean('raw_score'))
    if goal.get('opponent_totals') is not None:
        target=max(goal['opponent_totals'],default=0)
        return (statistics.mean(r['final_total']>=target for r in results),mean('final_total'))
    return (mean('raw_score'),)


class RolloutPolicy:
    def __init__(self,config=None, *, episode_start=None):
        from .policies import SkillPolicy
        self.base=SkillPolicy(config,episode_start=episode_start);self.config=self.base.config
        self.hp_reserve_gate_state=deepcopy(self.base.hp_reserve_gate_state)
        self.settings=self.config['simulator_adapter'].get('rollout',{})
        self.particles=self.settings.get('particles',2)
        self.max_decisions=self.settings.get('max_decisions_per_turn',1)
        self.max_actions=self.settings.get('max_actions',256)
        self.max_remaining=self.settings.get('max_remaining_turns')
        self.chance=self.settings.get('chance_enumeration')
        if 'chance_enumeration' in self.settings:
            if (not isinstance(self.chance,dict) or set(self.chance)-{'max_live_cards','max_nodes','draw_mode'}
                or not {'max_live_cards','max_nodes'}<=set(self.chance)):
                raise Unsupported('Explicit chance enumeration live-card/node limits required')
            mode=self.chance.get('draw_mode','permutation')
            if mode not in ('permutation','prefix','grouped'):raise Unsupported('Unknown chance draw mode')
            for name,upper in [('max_live_cards',16 if mode in ('prefix','grouped') else 8),('max_nodes',20000)]:
                if type(self.chance[name]) is not int or not 1<=self.chance[name]<=upper:
                    raise Unsupported('Chance enumeration limit out of range: '+name)
        if 'max_remaining_turns' in self.settings and (type(self.max_remaining) is not int or not 1<=self.max_remaining<=64):
            raise Unsupported('max_remaining_turns must be an integer in 1..64')
        for value in [self.particles,self.max_decisions,self.max_actions]:
            if type(value) is not int or value<1:raise Unsupported('Positive integer rollout budgets required')
        if self.particles>32 or self.max_decisions>8 or self.max_actions>1000:raise Unsupported('Rollout budget exceeds supported bounds')
        inner=deepcopy(self.config);adapter=inner['simulator_adapter']
        adapter['search_depth']=self.settings.get('continuation_search_depth',1)
        adapter['beam_width']=self.settings.get('continuation_beam_width',4)
        adapter['use_public_turn_bonuses']=False
        if type(adapter['search_depth']) is not int or not 1<=adapter['search_depth']<=4 or type(adapter['beam_width']) is not int or not 1<=adapter['beam_width']<=16:
            raise Unsupported('Invalid continuation search budget')
        self.continuation=SkillPolicy(inner,episode_start=episode_start)
        self.cache={};self.turn_counts=Counter();self.diagnostics=[]
        self.statistics=dict(rollout_decisions=0,trajectories=0,changed_actions=0,fallbacks={})
        if self.max_remaining is not None:self.statistics['outside_window_decisions']=0
        if self.chance is not None:self.statistics.update(chance_attempts=0,complete_chance_nodes=0,complete_chance_decisions=0)

    def choose(self,observation,legal):
        baseline,reason=self.base.choose(observation,legal)
        if observation.get('mode')=='decision':return baseline,reason
        if observation['goal'].get('stop_at_target',False) and observation['score']>=observation['goal']['target_score']:return baseline,reason
        if self.max_remaining is not None and observation['remaining_turns']>self.max_remaining:
            self.statistics['outside_window_decisions']+=1
            return baseline,reason+':rollout-outside-final-window'
        try:snapshot=public_snapshot(observation)
        except Unsupported as error:
            cause=str(error);counts=self.statistics['fallbacks'];counts[cause]=counts.get(cause,0)+1
            return baseline,reason+':rollout-unavailable'
        key=fingerprint(snapshot)
        if key in self.cache:return deepcopy(self.cache[key])
        turn=observation['turn']
        if self.turn_counts[turn]>=self.max_decisions or len(legal)<2:return baseline,reason
        self.turn_counts[turn]+=1
        actions=[baseline]+[a for a in legal if a!=baseline]
        try:
            if self.chance is not None:
                from .chance import compare,PRIOR
                self.statistics['chance_attempts']+=1
                selected,records=compare(snapshot,actions,self.continuation,max_actions=self.max_actions,**self.chance)
                self.statistics['complete_chance_nodes']+=sum(r['enumeration']['nodes'] for r in records)
                self.statistics['complete_chance_decisions']+=1
                prior=PRIOR
                label='play-cards:public-weighted-chance'
            else:
                samples=[sample_battle(snapshot,i) for i in range(self.particles)]
                records=[]
                for action in actions:
                    outcomes=[]
                    for sample in samples:
                        self.statistics['trajectories']+=1
                        outcomes.append(trajectory(fork_simulation(sample),action,self.continuation,self.max_actions))
                    records.append(dict(action=deepcopy(action),objective=list(objective(outcomes,snapshot['goal'])),
                                        mean_raw_score=statistics.mean(r['raw_score'] for r in outcomes),
                                        sample_sd_raw_score=statistics.stdev(r['raw_score'] for r in outcomes) if len(outcomes)>1 else 0,
                                        outcomes=outcomes))
                selected=max(records,key=lambda r:r['objective'])['action'] # baseline wins exact ties
                prior='uniform hidden deck + independent future RNG; not native conditional posterior'
                label='play-cards:public-belief-rollout'
            choice=(selected,label)
            self.statistics['rollout_decisions']+=1
            self.statistics['changed_actions']+=selected!=baseline
            self.diagnostics.append(dict(public_fingerprint=key,turn=turn,baseline=baseline,chosen=selected,
                                         particles=self.particles if self.chance is None else None,candidates=records,
                                         prior=prior))
        except Unsupported as error:
            cause=str(error);counts=self.statistics['fallbacks'];counts[cause]=counts.get(cause,0)+1
            choice=(baseline,reason+':rollout-unsupported')
            self.diagnostics.append(dict(public_fingerprint=key,turn=turn,error=cause,chosen=baseline))
        self.cache[key]=deepcopy(choice)
        if len(self.cache)>128:self.cache.pop(next(iter(self.cache)))
        return choice
