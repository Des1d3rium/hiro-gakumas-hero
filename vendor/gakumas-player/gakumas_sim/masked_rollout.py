"""Public conditional-prefix samples and bounded core decisions for HIF.

This is the existing independent exchangeable research prior with an unknown
attribute prefix. Ordered-pile posterior issues still reject admission.
"""
from collections import Counter
from copy import deepcopy
import hashlib,random,statistics
from .core import Battle,Unsupported
from .exam_snapshot import canonical
from .rollout import public_snapshot,sample_battle,advance
from .planning import fork_simulation
from .policies import SkillPolicy

ATTRS={'Vo','Da','Vi'}
PRIOR='exchangeable current piles + public-quota future attributes + fresh future RNG; not native conditional posterior'


def project(observation):
    """Accept an observation dict only; never an environment or RNG handle."""
    if not isinstance(observation,dict):raise Unsupported('Public observation dictionary required')
    obs=deepcopy(observation);model=obs.get('public_model',{})
    prior=model.pop('unobserved_attribute_order',None)
    if prior is not None:
        if set(prior)!={'remaining_prefix_counts','marginal_bonus_permil','fixed_tail','scope'}:
            raise Unsupported('Exact unknown-prefix public fields required')
        counts=prior['remaining_prefix_counts'];tail=prior['fixed_tail']
        if (not isinstance(counts,dict) or set(counts)-ATTRS or any(type(n) is not int or n<0 for n in counts.values())
                or not isinstance(tail,list) or len(tail)!=3 or set(tail)!=ATTRS):
            raise Unsupported('Bounded public attribute quota/tail required')
        size=sum(counts.values());turns=model['turns'];extra=obs['extra_turns_added']
        if (not 1<=size<=8 or type(extra) is not int or extra<0
                or len(turns)!=1+size+3+extra):raise Unsupported('Public attribute window length mismatch')
        bonus=dict(zip(tail,[t['bonus_permil'] for t in turns[1+size:1+size+3]]))
        average=sum(bonus[a]*n for a,n in counts.items())//size
        if (type(prior['marginal_bonus_permil']) is not int or prior['marginal_bonus_permil']!=average
                or any(t['bonus_permil']!=average for t in turns[1:1+size])
                or any(t['bonus_permil']!=bonus[tail[-1]] for t in turns[1+size+3:])):
            raise Unsupported('Unknown-prefix marginal or fixed-tail bonus mismatch')
        rules=model.get('support_upgrades')
        if (rules is None or rules.get('attributes')!=[rules.get('current_attribute')]
                or rules.get('current_attribute') not in ATTRS or obs['bonus_permil']!=bonus[rules['current_attribute']]):
            raise Unsupported('Only current support attribute may be revealed before tail')
        prior=dict(remaining_prefix_counts=counts,fixed_tail=tail,bonus=bonus)
    snapshot=public_snapshot(obs) # Keeps the existing unresolved-posterior gates.
    result=dict(schema_version=1,scope=PRIOR,snapshot=snapshot,attribute_prior=prior,
                native_posterior_admitted=False,actual_clear=False)
    result['packet_sha256']=hashlib.sha256(canonical(result).encode()).hexdigest();return result


class MaskedBattle(Battle):
    def observe(self):
        obs=super().observe();prior=self.prefix_prior
        size=sum(prior['remaining_prefix_counts'].values())
        if self.turn>=size:return obs
        attrs=self.support_config['attributes']
        left=Counter(prior['remaining_prefix_counts'])-Counter(attrs[1:self.turn+1])
        average=sum(prior['bonus'][a]*n for a,n in left.items())//sum(left.values())
        for i,t in enumerate(obs['public_model']['turns']):
            if i and self.turn+i<=size:t['bonus_permil']=average
        obs['remaining_bonus_permil']=[t['bonus_permil'] for t in obs['public_model']['turns']]
        obs['public_model']['support_upgrades']['attributes']=[attrs[self.turn]]
        obs['public_model']['unobserved_attribute_order']=dict(remaining_prefix_counts=dict(left),
            marginal_bonus_permil=average,fixed_tail=prior['fixed_tail'],scope='marginals only, not sampled future')
        return obs


def sample(packet,particle):
    if (not isinstance(packet,dict) or set(packet)!={'schema_version','scope','snapshot','attribute_prior','native_posterior_admitted','actual_clear','packet_sha256'}
            or packet['schema_version']!=1 or packet['scope']!=PRIOR or packet['native_posterior_admitted'] is not False or packet['actual_clear'] is not False):
        raise Unsupported('Exact model-only masked rollout packet required')
    if type(particle) is not int or particle<0:raise Unsupported('Nonnegative rollout particle required')
    p=deepcopy(packet);claimed=p.pop('packet_sha256')
    if hashlib.sha256(canonical(p).encode()).hexdigest()!=claimed:raise Unsupported('Masked rollout fingerprint mismatch')
    snapshot=p['snapshot'];prior=p['attribute_prior']
    if prior is None:return sample_battle(snapshot,particle)
    rng=random.Random(int.from_bytes(hashlib.sha256((claimed+'/attributes/'+str(particle)).encode()).digest(),'big'))
    pool=[a for a,n in prior['remaining_prefix_counts'].items() for _ in range(n)];rng.shuffle(pool)
    model=snapshot['public_model'];current=model['support_upgrades']['current_attribute']
    extra=snapshot['extra_turns_added'];attrs=[current]+pool+prior['fixed_tail']+[prior['fixed_tail'][-1]]*extra
    for i,(t,attr) in enumerate(zip(model['turns'],attrs)):
        if i:t['bonus_permil']=prior['bonus'][attr]
    model['support_upgrades']['attributes']=attrs
    base=sample_battle(snapshot,particle);result=MaskedBattle.__new__(MaskedBattle);result.__dict__=base.__dict__
    result.prefix_prior=prior;return result


class CoreRolloutPolicy:
    """Compare visible core use vs baseline/end, with a paired-noise guard."""
    def __init__(self,config,*,particles=4,max_decisions=4,noise_guard=2.0,core_ids=('p_card-02-act-1_037',),continuation_depth=1):
        if type(particles) is not int or not 2<=particles<=16 or type(max_decisions) is not int or not 1<=max_decisions<=16:
            raise Unsupported('Bounded core rollout budgets required')
        if type(noise_guard) not in (int,float) or not 0<=noise_guard<=10:raise Unsupported('Bounded empirical noise guard required')
        if type(continuation_depth) is not int or not 1<=continuation_depth<=3:raise Unsupported('Bounded continuation search required')
        inner=deepcopy(config);inner['simulator_adapter'].update(search_depth=continuation_depth,beam_width=4)
        self.base=SkillPolicy(config);self.continuation=SkillPolicy(inner);self.particles=particles
        self.max_decisions=max_decisions;self.noise_guard=noise_guard;self.core_ids=set(core_ids)
        self.turns=set();self.diagnostics=[];self.statistics=dict(visible_core_decisions=0,simulation_attempts=0,admitted=0,changed=0,budget_skips=0,fallbacks={})

    def choose(self,observation,legal):
        baseline,reason=self.base.choose(observation,legal)
        targets=[dict(kind='play',id=c['instance']) for c in observation['hand'] if c['definition'].get('provenance',{}).get('card_id') in self.core_ids]
        targets=[a for a in targets if a in legal]
        if not targets:return baseline,reason
        self.statistics['visible_core_decisions']+=1;turn=observation['turn']
        if turn in self.turns or self.statistics['simulation_attempts']>=self.max_decisions:
            self.statistics['budget_skips']+=1;return baseline,reason+':core-budget'
        self.turns.add(turn)
        try:
            packet=project(observation);self.statistics['simulation_attempts']+=1
            worlds=[sample(packet,i) for i in range(self.particles)]
            actions=[baseline]
            for a in targets+[dict(kind='end_turn')]:
                if a in legal and a not in actions:actions.append(a)
            records=[]
            for action in actions:
                scores=[]
                for world in worlds:
                    b=fork_simulation(world);advance(b,action)
                    while not b.ended:
                        if b.steps>=160:raise Unsupported('Core continuation action budget exceeded')
                        a,_=self.continuation.choose(b.observe(),b.legal_actions());advance(b,a)
                    scores.append(b.score)
                records.append(dict(action=deepcopy(action),raw_scores=scores,mean_raw_score=statistics.mean(scores)))
            origin=records[0];qualifying=[]
            for record in records[1:]:
                delta=[b-a for a,b in zip(origin['raw_scores'],record['raw_scores'])]
                mean=statistics.mean(delta);se=statistics.stdev(delta)/(len(delta)**.5)
                record.update(paired_mean_delta=mean,paired_standard_error=se,guarded_delta=mean-self.noise_guard*se)
                if record['guarded_delta']>0:qualifying.append(record)
            selected=max(qualifying,key=lambda r:r['guarded_delta']) if qualifying else origin
            self.statistics['admitted']+=1;self.statistics['changed']+=selected['action']!=baseline
            self.diagnostics.append(dict(turn=turn,public_packet_sha256=packet['packet_sha256'],baseline=baseline,
                selected=selected['action'],candidates=records,scope=PRIOR,guard='paired mean minus configured SE multiple, not confidence/optimality proof'))
            return deepcopy(selected['action']),'play-cards:masked-core-rollout'
        except Unsupported as error:
            cause=str(error);counts=self.statistics['fallbacks'];counts[cause]=counts.get(cause,0)+1
            self.diagnostics.append(dict(turn=turn,baseline=baseline,error=cause,selected=baseline))
            return baseline,reason+':core-rollout-fallback'
