"""Executable adapters for strategy/skills; NOT a Markdown interpreter or ML.

Public observation + legal action list are the only policy inputs. Battle
search is bounded and visible-hand-only, with explicitly heuristic leaf values.
"""
from copy import deepcopy
import json
from pathlib import Path
from .core import Battle, Unsupported,ratio,f32,meets
from .passives import matching_hooks
from .planning import fork_simulation
import math

ROOT = Path(__file__).resolve().parents[1]


def passive_forecast(model,horizon,hp_start=None):
    """Only already-scheduled effects, no future cards; public turn bonuses.

    Missing schedules use the current bonus as an explicit legacy fallback.
    This is a bounded public-state estimate, not an optimal continuation.
    """
    shield=model.shield;review=model.impression;score=0
    hp=model.hp if hp_start is None else hp_start;focus=model.focus;motivation=model.motivation;permanent=model.permanent_boost
    hooks=deepcopy(model.hooks)
    ticks=deepcopy(model.ticks);pending=deepcopy(model.reservations);boosts=deepcopy(model.boosts)
    current_bonus=model.bonus
    def add(amount):return ratio(ratio(amount,1000+permanent+sum(b['permil'] for b in boosts)),current_bonus)
    for offset in range(horizon+1):
        schedule=model.public_bonus_schedule
        current_bonus=schedule[min(offset,len(schedule)-1)] if schedule else model.bonus
        if offset:
            review=max(0,review-1)
            # Future lesson attributes are not assumed: lesson-gated passives stay off in this projection.
            values=dict(hp_min=hp,shield_min=shield,focus_min=focus,motivation_min=motivation,impression_min=review,hand_trouble_min=0,
                        max_hp=model.max_hp,lesson=None)
            selected=matching_hooks(hooks,'turn_start',values,model.remaining_turns-offset)
            for hook in selected:
                hook['uses']+=1
                for effect in hook['effects']:
                    op=effect['op'];value=effect.get('value',0)
                    if op=='shield_score':
                        score+=add(ratio(shield,effect['permil']))
                        shield=math.ceil(f32(f32(shield)*f32(1-f32(f32(effect.get('consume_permil',0))/f32(1000)))))
                    elif op=='score':score+=add(value+focus*effect.get('focus_scale',0))
                    elif op=='shield':shield+=value+motivation*effect.get('motivation_scale',0)
                    elif op=='focus':focus+=value
                    elif op=='motivation':motivation+=value
                    elif op=='impression':review+=value
                    elif op=='permanent_score_boost':permanent+=effect['permil']
                    elif op=='hp_loss':hp=max(0,hp-value)
                    elif op=='recover':hp=min(model.max_hp,hp+value)
                    elif op=='recover_permil':hp=min(model.max_hp,hp+ratio(model.max_hp,effect['permil']))
                    elif op=='hp_loss_permil':hp=max(0,hp-model.max_hp*effect['permil']//1000)
                    elif op in ('score_boost','timed_score_boost'):pass # one-turn boost on a turn with no projected plays
                    elif op=='extra_action':pass # projection deliberately has no future plays
                    else:raise Unsupported('Passive projection payload not ported')
            for timer in ticks:
                if timer.get('kind') in ('end_turn','status_change'):continue  # 回合末附魔在下面回合末处理；状态变化附魔不投影
                if timer['remaining']<1 and timer['remaining']!=-1 or timer['start']>model.turn+offset:continue
                kind=timer.get('kind')
                if kind=='start_play_draw':pass # no future hidden identity is assumed in this forecast
                elif kind=='shield_gain':shield+=timer['value']+motivation*timer.get('motivation_scale',0)
                elif kind=='impression':review=ratio(review,1000+timer['permil'])
                elif kind=='conditional':
                    values=dict(hp_min=hp,shield_min=shield,focus_min=focus,motivation_min=motivation,impression_min=review,
                                hand_trouble_min=0,max_hp=model.max_hp,lesson=None)
                    if meets(values,timer['requires']):
                        for effect in timer['effects']:
                            op=effect['op']
                            if op=='shield_score':score+=add(ratio(shield,effect['permil']))
                            elif op=='score':score+=add(effect['value']+focus*effect.get('focus_scale',0))
                            elif op=='recover_permil':hp=min(model.max_hp,hp+ratio(model.max_hp,effect['permil']))
                            elif op=='recover':hp=min(model.max_hp,hp+effect['value'])
                            elif op=='shield':shield+=effect['value']+motivation*effect.get('motivation_scale',0)
                            elif op=='motivation':motivation+=effect['value']
                            elif op=='impression':review+=effect['value']
                            elif op in ('hp_loss','hp_loss_permil'):
                                hp=max(0,hp-(effect['value'] if op=='hp_loss' else model.max_hp*effect['permil']//1000))
                            elif op!='draw':raise Unsupported('Conditional tick projection payload not ported')
                else:score+=add(ratio(shield,timer['permil']))
                if timer['remaining']>0:timer['remaining']-=1
            for timer in pending:
                if timer['start']!=model.turn+offset:continue
                for effect in timer['effects']:
                    if effect['op']=='shield_score':
                        score+=add(ratio(shield,effect['permil']))
                        shield=math.ceil(f32(f32(shield)*f32(1-f32(f32(effect.get('consume_permil',0))/f32(1000)))))
        if review:score+=add(review)
        motivation+=model.end_turn_motivation
        for timer in ticks:
            if timer.get('kind')!='end_turn' or timer['remaining']==0:continue
            for effect in timer['effects']:
                # 回合末附魔正文（与 Battle._apply end_turn 同序）；按需要的数值效果投影，不依赖手牌的效果不会出现在这里
                op=effect['op']
                if op=='score':score+=add(effect['value']+focus*effect.get('focus_scale',0))
                elif op=='shield':shield+=effect['value']+motivation*effect.get('motivation_scale',0)
                elif op=='shield_score':
                    score+=add(ratio(shield,effect['permil']))
                    shield=math.ceil(f32(f32(shield)*f32(1-f32(f32(effect.get('consume_permil',0))/f32(1000)))))
                elif op=='focus':focus+=effect['value']
                elif op=='motivation':motivation+=effect['value']
                elif op=='impression':review+=effect['value']
                elif op=='impression_growth':review=ratio(review,1000+effect['permil'])
                elif op=='resource_score':
                    from .resource_score import calculate
                    amount,left=calculate(review if effect['resource']=='impression' else motivation,effect['permil'],effect['consume_permil'])
                    score+=add(amount)
                    if effect['resource']=='impression':review=left
                    else:motivation=left
                elif op=='recover':hp=min(model.max_hp,hp+effect['value'])
                elif op=='recover_permil':hp=min(model.max_hp,hp+ratio(model.max_hp,effect['permil']))
                elif op=='hp_loss':hp=max(0,hp-effect['value'])
                elif op=='hp_loss_permil':hp=max(0,hp-model.max_hp*effect['permil']//1000)
            if timer.get('fresh'):timer['fresh']=False
            elif timer['remaining']>0:timer['remaining']-=1
        for boost in boosts:
            if boost.get('fresh'):boost['fresh']=False
            else:boost['remaining']-=1
        boosts=[b for b in boosts if b['remaining']>0]
    return score,shield


class Naive:
    def choose(self, observation, legal):
        return legal[0], 'baseline:first-legal'


def finisher_redraw_prior(observation,remaining,reservations,ticks=None):
    """Public base-draw SURROGATE, not a native conditional probability.

    A target in the current hand returns to the grave when passed. Exhaust
    the current deck, then approximate a uniform pool containing all current
    deck/grave/hand cards. Unknown future plays, draws/removals and changes
    within the hypothetical visible path are deliberately not reconstructed.
    """
    model=observation.get('public_model');templates=model.get('turns') if isinstance(model,dict) else None
    if not isinstance(templates,list) or len(templates)!=observation['remaining_turns']:return None
    if model.get('known_deck_prefix_definition_ids') or model.get('deck_prior_issue'):return None
    if any(d.get('order',0)>0 for d in model.get('definition_library',{}).values()):return None
    future=list(templates[1:])
    if remaining<len(future):return None
    future.extend([observation['extra_turn_template']]*(remaining-len(future)))
    if ticks is None:
        ticks=deepcopy(observation.get('ticks',[]))
        for timer in ticks:timer['start']-=observation['turn']
    budget=0
    for offset,template in enumerate(future,1):
        count=template.get('draw')
        if type(count) is not int or count<0:raise Unsupported('Invalid public future draw template')
        count+=sum(e['value'] for r in reservations if r['start']==offset for e in r['effects'] if e['op']=='draw')
        count+=sum(t['value'] for t in ticks if t.get('kind')=='start_play_draw'
            and max(1,t['start'])<=offset and (t['remaining']<0 or offset<max(1,t['start'])+t['remaining']))
        budget+=min(observation['hand_limit'],count)
    deck=observation['deck_count'];pool=deck+observation['grave_count']+len(observation['hand'])
    weight=min(1,max(0,budget-deck)/pool) if pool else 0
    return dict(weight=weight,future_base_and_scheduled_draws=budget,current_deck_count=deck,surrogate_pool_count=pool,
        basis='Uniform full-live-pool surrogate after current deck; unknown future plays, draws and removals excluded')


class SkillPolicy:
    def __init__(self, config=None, *, episode_start=None):
        self.config = config or json.loads((ROOT/'strategy/hif-policy.json').read_text())
        self.settings = self.config['simulator_adapter']
        self.hp_reserve_settings=self.settings.get('future_hp_reserve')
        if self.hp_reserve_settings is not None:
            from .hp_reserve import validate
            validate(self.hp_reserve_settings)
        gate=self.settings.get('future_hp_reserve_start_hp_fraction_max')
        if gate is not None and (type(gate) not in (int,float) or not math.isfinite(gate) or not 0<=gate<=1):
            raise Unsupported('Initial HP reserve gate must be a fraction in 0..1')
        if episode_start is not None:
            if (not isinstance(episode_start,dict) or set(episode_start)!={'hp','max_hp'}
                or any(type(n) is not int for n in episode_start.values()) or not 0<=episode_start['hp']<=episode_start['max_hp'] or episode_start['max_hp']<1):
                raise Unsupported('Explicit public episode-start HP/max HP required')
        active=gate is None or episode_start is not None and episode_start['hp']<=episode_start['max_hp']*gate
        self.hp_reserve_gate_state=dict(required=gate is not None,bound=episode_start is not None,active=bool(active))
        if not active:self.hp_reserve_settings=None
        self.redraw_mode=self.settings.get('finisher_reserve',{}).get('redraw_prior','legacy')
        if self.redraw_mode not in ('legacy','public-draw'):raise Unsupported('Unknown finisher redraw prior')
        if self.redraw_mode=='public-draw':
            self.statistics=dict(decisions=0,finisher_visible_decisions=0,zero_budget_prior_decisions=0,projection_fallbacks=0)
            self.diagnostics=[]

    def choose(self, observation, legal):
        if observation.get('mode') == 'decision':
            return self._decision(observation, legal)
        return self._battle(observation, legal)

    def _decision(self, obs, legal):
        state, context, definitions = obs['state'], obs['context'], obs['definitions']
        module = obs['module']
        scores = {'skip': (0, module+':skip-nonpositive-value')}
        owned = state.get('owned', {})
        draw_required = context.get('draw_pile_minimum', self.config['deck_gates']['before_battle_round_2']['minimum_draw_pile'])
        timing=self.config['deck_gates']['before_battle_round_2']['gate_timing']
        if timing=='after_opening_hand':opening=context.get('opening_hand_count',3) # explicit historical configuration
        elif timing=='exam_start_before_initial_draw':opening=0
        else:raise Unsupported('Unknown baton gate timing')
        reserve = context.get('reserved_p_points', 0)
        for option in obs['options']:
            key = option['id']
            if dict(kind='choose',id=key) not in legal:
                continue
            hp_after = min(state['max_hp'], state['hp']+option.get('hp_delta',0))
            cost = option.get('cost',0)
            score, reason = -cost*0.05, 'resource-cost'
            if module == 'training':
                deficits = context['stat_targets']
                score += sum(min(gain,max(0,deficits.get(stat,0)-state['stats'].get(stat,0)))
                             + 0.05*max(0,gain-max(0,deficits.get(stat,0)-state['stats'].get(stat,0)))
                             for stat,gain in option.get('stat_delta',{}).items())
                score += 0.15*option.get('hp_delta',0)
                # SP support gains must already be included in explicit deltas.
                score += 0.01*option.get('sp',False)
                score -= max(0,context.get('hp_reserve',0)-hp_after)*100
                gained = definitions.get(option.get('gain_card'), {})
                score -= 80 if gained.get('trouble') or gained.get('role') == 'pollution' else 0
                reason = 'deficit-and-hp-reserve'
            elif module == 'card_pick':
                card = definitions[option['gain_card']]
                role = card.get('role','unknown')
                if role == 'unknown':
                    raise Unsupported('Strategy needs an explicit card role')
                score += 160 if role in context.get('missing_roles',[]) else 0
                score += 25 if card.get('once') else -15
                score += 30*sum(e.get('value',0) for e in card['effects'] if e['op'] in ('draw','extra_action'))
                score += 15 if role in ('engine','compression') else -20
                if context.get('before_round2') and context.get('avoid_baton_generation',False) and len(owned)-opening < draw_required:
                    score += 80
                reason = 'engine-fit-and-draw-pile-gate'
            elif module == 'card_remove':
                card_id = owned[option['remove_card']]
                card = definitions[card_id]
                role = card.get('role','unknown')
                score += 70 if role in ('pollution','bridge','trouble') else -150
                trouble = sum(definitions[c].get('trouble',False) for c in owned.values())
                if card.get('trouble') and trouble <= context.get('required_trouble_copies',0):
                    score -= 1000
                if option['remove_card'] in context.get('scheduled_auto_remove',[]):
                    score -= 1000
                after_count = len(owned)-1+bool(option.get('gain_card'))
                if context.get('before_round2') and context.get('avoid_baton_generation',False) and after_count-opening < draw_required:
                    score -= 1000
                if option.get('gain_card'):
                    new = definitions[option['gain_card']]
                    score += 160 if new.get('role') in context.get('missing_roles',[]) else -20
                reason = 'remove-dead-draw-preserve-gate-and-finisher'
            elif module == 'drinks':
                drink = option.get('gain_drink')
                weights = self.settings['drink_buy_values']
                score += weights.get(drink,0)
                if drink == 'draw' and context.get('engine_ready'):
                    score *= 0.6
                if drink == 'burst' and not context.get('engine_ready'):
                    score *= 0.3
                if option['kind']=='recover':
                    score += min(option.get('hp_delta',0), max(0,context.get('hp_reserve',0)-state['hp']))*15
                score -= max(0,reserve-(state['p_points']-cost))*20
                reason = 'next-battle-drink-and-budget'
            scores[key] = (score, module+':'+reason)
        if not legal:
            raise Unsupported('No legal decision')
        selected = max(legal,key=lambda a:scores[a['id']][0])
        return selected,scores[selected['id']][1]

    def _battle(self, obs, legal):
        if obs['goal'].get('stop_at_target', False) and obs['score']>=obs['goal']['target_score']:
            return dict(kind='end_turn'), 'play-cards:selection-target-preserve-resources'
        model = Battle.visible_model(obs)
        if not self.settings.get('use_public_turn_bonuses',False):model.public_bonus_schedule=None
        weights = self.settings['leaf_weights']
        scale_priors=self.settings.get('scale_leaf_priors_by_bonus',False)
        if type(scale_priors) is not bool:raise Unsupported('scale_leaf_priors_by_bonus must be boolean')
        # Heuristic resource reserves share score units with actual card output.
        # Already-scored output and passive/finisher forecasts below already
        # contain their multipliers and must not be multiplied a second time.
        if scale_priors:
            scale=max(1.0,obs['bonus_permil']/1000.0)
            weights={name:value*scale for name,value in weights.items()}
        base_remaining = max(0,obs['remaining_turns']-1)
        passive_cache={}
        redraw_cache={}
        def redraw(m,remaining):
            if self.redraw_mode=='legacy':return 1
            key=(remaining,repr(m.reservations),repr(m.ticks))
            if key not in redraw_cache:redraw_cache[key]=finisher_redraw_prior(obs,remaining,m.reservations,m.ticks)
            proof=redraw_cache[key]
            return proof['weight'] if proof is not None else 1
        if self.redraw_mode=='public-draw':
            self.statistics['decisions']+=1
            proof=finisher_redraw_prior(obs,base_remaining,model.reservations,model.ticks)
            if proof is None:self.statistics['projection_fallbacks']+=1
            finishers=[c['instance'] for c in obs['hand'] if c['definition'].get('once') and any(e['op']=='shield_score' and e.get('consume_permil',0) for e in c['definition']['effects'])]
            if finishers:
                self.statistics['finisher_visible_decisions']+=1
                if proof and proof['weight']==0:self.statistics['zero_budget_prior_decisions']+=1
                self.diagnostics.append(dict(turn=obs['turn'],finisher_instances=finishers,redraw_prior=proof,
                    scope='Public heuristic reserve discount; not predicted native draw probability'))
        # Use only visible future bonuses and installed effects, no future cards.
        def value(m):
            remaining=base_remaining+m.extra_turns_added-obs.get('extra_turns_added',0)
            ending_hp=m.hp+m.skip_recovery_amount()
            key=(ending_hp,m.shield,m.focus,m.motivation,m.end_turn_motivation,m.impression,m.permanent_boost,m.bonus,remaining,repr(m.public_bonus_schedule),repr(m.ticks),repr(m.reservations),repr(m.boosts),repr(m.hooks))
            if key not in passive_cache:passive_cache[key]=passive_forecast(m,remaining,hp_start=ending_hp)
            passive,end_shield=passive_cache[key]
            score = m.score-obs['score']
            score += (ending_hp-obs['hp'])*weights['hp']
            score += (m.shield-obs['shield'])*weights['shield']*remaining
            score += passive+(end_shield-m.shield)*weights['shield']*remaining
            score += (m.focus-obs['focus'])*weights['focus']*remaining
            score += (m.motivation-obs['motivation'])*weights['motivation']*remaining
            score += (m.end_turn_motivation-obs.get('end_turn_motivation',0))*weights['motivation']*remaining*(remaining+1)/2
            score += m.unknown_draws*weights['draw']*min(1,m.actions)
            score += (len(m.drinks)-len(obs['drinks']))*weights['drink_reserve']*remaining
            if self.hp_reserve_settings is not None:
                from .hp_reserve import estimate
                scale=max(1.0,obs['bonus_permil']/1000.0) if scale_priors else 1
                score-=estimate(obs,m,remaining,self.hp_reserve_settings)['penalty']*scale
            # Like an unspent action drink, a finite after-play charge has
            # future option value. This prior does not assume a future draw.
            for hook in m.hooks:
                if hook['phase']=='card_play_after' and hook['limit']:
                    charges=max(0,hook['limit']-hook['uses'])
                    actions=sum(e.get('value',0) for e in hook['effects'] if e['op']=='extra_action')
                    score+=charges*actions*weights.get('limited_action_reserve',0)*remaining
            # A one-use shield-consuming finisher has option value while it is
            # unspent. Estimate from visible shield/motivation only; future
            # cards and draws are deliberately not peeked at. The supplied
            # future attribute schedule is public, so reserve can use its peak.
            reserve=self.settings.get('finisher_reserve',{})
            for card in obs['hand']:
                definition=card['definition']
                if definition.get('once') and card['instance'] not in m.zones['lost']:
                    for effect in definition['effects']:
                        if effect['op']=='shield_score' and effect.get('consume_permil',0):
                            growth=m.motivation*min(remaining,reserve.get('horizon_turns',0))*reserve.get('shield_growth_per_motivation_turn',0)
                            future=m.public_bonus_schedule[1:] if m.public_bonus_schedule else []
                            bonus=max(future) if future else m.bonus
                            score+=(m.shield+growth)*effect['permil']/1000*bonus/1000*(1+m.permanent_boost/1000)*remaining/(remaining+1)*redraw(m,remaining)
            return score
        root_value = value(model)
        beam = [(model, [])]
        best = (root_value, [], model)
        for _ in range(self.settings['search_depth']):
            candidates = []
            for state, path in beam:
                for action in state.legal_actions():
                    if action['kind']=='end_turn':
                        continue
                    trial = fork_simulation(state)
                    trial._apply(action)
                    trial._validate_zones()
                    sequence = path+[action]
                    utility = value(trial)
                    if utility > best[0]+1e-8:
                        best = (utility,sequence,trial)
                    candidates.append((utility,trial,sequence))
            if not candidates:
                break
            candidates.sort(key=lambda row:row[0], reverse=True)
            beam = [(state,path) for _,state,path in candidates[:self.settings['beam_width']]]
        if not best[1]:
            return dict(kind='end_turn'), 'play-cards:no-positive-visible-sequence'
        action = best[1][0]
        if action not in legal:
            raise Unsupported('Planner/legal action mismatch')
        return action, ('drinks' if action['kind']=='drink' else 'play-cards')+':visible-sequence-search'


def make_policy(name,config=None, *, episode_start=None):
    if name=='rollout':
        from .rollout import RolloutPolicy
        return RolloutPolicy(config,episode_start=episode_start)
    if name=='skill':
        return SkillPolicy(config,episode_start=episode_start)
    if name=='naive':
        if config is not None:raise Unsupported('Naive policy does not accept skill configuration')
        return Naive()
    raise ValueError('Unknown policy: '+name)
