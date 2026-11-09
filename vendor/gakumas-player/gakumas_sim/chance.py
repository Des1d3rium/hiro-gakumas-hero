"""Finite public-pile enumeration under EXPLICIT independent chance priors.

Uniform physical pile permutations, independent support integer thresholds and
uniform single-candidate removals are a research model. The removal prior is
equivalent to independent continuous random keys, not discrete native keys.
They do not reproduce a shared native RNG posterior.
Continuation policies see only observe()/legal_actions(), never private order.
"""
from collections import deque,Counter
from copy import deepcopy
from fractions import Fraction
from itertools import permutations
from math import factorial
from .core import Battle,Unsupported
from .planning import fork_simulation

PRIOR = ('Exact finite independent uniform pile permutations + independent support threshold chances '
         '+ uniform candidate removals (continuous-key prior); not native shared RNG posterior')


class NeedChoice(Exception):
    def __init__(self,signature,options):self.signature,self.options=signature,options


class Budget:
    def __init__(self,maximum):self.maximum,self.nodes=maximum,0
    def spend(self):
        self.nodes+=1
        if self.nodes>self.maximum:raise Unsupported('Chance enumeration node budget exceeded; no complete expectation')


class ChanceRandom:
    """Action-local chance script; state=0 is a sentinel, never a native seed."""
    def __init__(self,choices,thresholds,max_options):
        self.choices=choices;self.thresholds=thresholds;self.max_options=max_options
        self.position=0;self.state=0;self.calls=0

    def take(self,signature,options):
        if self.position==len(self.choices):raise NeedChoice(signature,options)
        stored,value=self.choices[self.position];self.position+=1
        if stored!=signature:raise Unsupported('Chance script signature mismatch')
        return value

    def integer(self,low,high):
        self.calls+=1
        if low==high:return low
        if (low,high)!=(0,1000):raise Unsupported('Unported chance integer call')
        options=[(a,Fraction(b-a,1000)) for a,b in zip(self.thresholds,self.thresholds[1:])]
        if len(options)==1:return options[0][0]
        return self.take(('support-integer',tuple(self.thresholds)),options)

    def shuffle(self,values):
        n=len(values)
        if n<2:return
        count=factorial(n)
        if count>self.max_options:raise Unsupported('Chance shuffle permutation budget exceeded')
        order=self.take(('uniform-shuffle',n),[(p,Fraction(1,count)) for p in permutations(range(n))])
        old=list(values);values[:]=[old[i] for i in order];self.calls+=n-1

    def draw_index(self,count):
        if count<1:raise Unsupported('Cannot branch an empty chance pile')
        self.calls+=1
        if count==1:return 0
        return self.take(('uniform-next-card',count),[(i,Fraction(1,count)) for i in range(count)])

    def insert_index(self,count):
        self.calls+=1
        if count<=1:return 0
        return self.take(('uniform-before-bottom-insertion',count),[(i,Fraction(1,count)) for i in range(count)])

    def signed(self):raise Unsupported('Signed random ordering not ported in chance enumeration')
    def pick_one(self,values):
        if not values:return []
        # Native uses a count draw then one signed sorting key per candidate.
        # Here only the documented independent uniform removal PRIOR applies;
        # calls is a semantic count, not a simulated native RNG correlation.
        self.calls+=len(values)+1
        if len(values)==1:return [values[0]]
        index=self.take(('uniform-remove-one',len(values)),
                        [(i,Fraction(1,len(values))) for i in range(len(values))])
        return [values[index]]


class PrefixBattle(Battle):
    """Disposable chance world; only actual draws reveal an exchangeable pile.

    Batch hand insertion and support timing match Battle._draw. Drawn cards
    stay out of the refill. The real environment never receives this class.
    """
    def _draw_index(self):
        return self.rng.draw_index(len(self.zones['deck']))

    def _create_card(self,definition_id):
        if self.known_only:return super()._create_card(definition_id)
        # Before-bottom insertion is order sensitive. Expand the old tail
        # first; subsequent draws in this owned chance world use that order.
        if not getattr(self,'_chance_ordered',False):
            prefix=list(self.known_deck_prefix);tail=list(self.zones['deck'][len(prefix):])
            count=factorial(len(tail))
            if count>self.rng.max_options:raise Unsupported('Creation tail-order budget exceeded')
            order=tuple(range(len(tail))) if count==1 else self.rng.take(
                ('creation-tail-order',tuple(tail)),[(p,Fraction(1,count)) for p in permutations(range(len(tail)))])
            self.zones['deck']=prefix+[tail[i] for i in order]
            self._chance_ordered=True
        super()._create_card(definition_id)

    def _pool_pick_index(self,weights):
        total=sum(weights)
        if len(weights)==1:return 0
        return self.rng.take(('weighted-pool-creation',tuple(weights)),[(i,Fraction(w,total)) for i,w in enumerate(weights)])

    def _fetch_tag(self,tag):
        from .tag_fetch import matches
        candidates=[c for z,c in matches(self,tag) if z=='deck']
        prefix=self.known_deck_prefix
        fixed_match=any(self.definition(c)['search_tag']==tag for c in prefix)
        if not getattr(self,'_chance_ordered',False) and not fixed_match and len(candidates)>1:
            tail=list(self.zones['deck'][len(prefix):]);count=factorial(len(tail))
            if count>self.rng.max_options:raise Unsupported('Ordered tag search permutation budget exceeded')
            order=(tuple(range(len(tail))) if count==1 else self.rng.take(
                ('tag-search-tail-order',tag,tuple(tail)),[(p,Fraction(1,count)) for p in permutations(range(len(tail)))]))
            self.zones['deck']=list(prefix)+[tail[i] for i in order]
            self._chance_ordered=True
        return super()._fetch_tag(tag)

    def _draw(self,count,support=True):
        if self.known_only:return super()._draw(count,support)
        count=min(count,self.hand_limit-len(self.zones['hand']));received=[]
        while count>0:
            if not self.zones['deck']:
                if not self.zones['grave']:break
                self.zones['deck'].extend(self.zones['grave']);self.zones['grave'].clear()
                self.known_deck_prefix=[];self.deck_prior_issue=None;self._chance_ordered=False
            if any(self.definition(k).get('order',0)>0 for k in self.zones['deck']):
                raise Unsupported('Fixed-order draw is not admitted by prefix chance prior')
            self.known_deck_prefix=[c for c in self.known_deck_prefix if c in self.zones['deck']]
            index=0 if getattr(self,'_chance_ordered',False) or self.known_deck_prefix else self._draw_index()
            received.append(self.zones['deck'].pop(index));count-=1
        self.zones['hand'].extend(received)
        self.known_deck_prefix=[c for c in self.known_deck_prefix if c not in received]
        from .tag_fetch import normalize
        normalize(self)
        self.draw_events.append(dict(turn=self.turn,drawn=received))
        if support:
            from .support_upgrades import process_batch
            process_batch(self,received)
        return received


def fraction(value):return Fraction(value['numerator'],value['denominator'])
def encoded(value):return dict(numerator=value.numerator,denominator=value.denominator)


def weighted_objective(results,goal):
    weights=[fraction(r['weight']) for r in results]
    if sum(weights,Fraction(0))!=1:raise Unsupported('Complete chance weights must sum to one')
    def mean(fn):return sum((w*fn(r) for w,r in zip(weights,results)),Fraction(0))
    if goal.get('stop_at_target',False):
        target=goal['target_score']
        return (mean(lambda r:int(r['raw_score']>=target)),-mean(lambda r:max(0,target-r['raw_score'])),
            mean(lambda r:r['hp']),mean(lambda r:r['drinks_left']),mean(lambda r:r['raw_score']))
    if goal.get('opponent_totals') is not None:
        target=max(goal['opponent_totals'],default=0)
        return (mean(lambda r:int(r['final_total']>=target)),mean(lambda r:r['final_total']))
    return (mean(lambda r:r['raw_score']),)


def canonical_world(snapshot):
    from .rollout import sample_battle
    b=sample_battle(snapshot,0)
    # sample_battle supplies validated public restoration. Canonicalize all
    # hidden identities before any enumeration; no sampled order/RNG survives.
    for zone in ['deck','grave','hold','lost']:
        ids=sorted(snapshot['public_model'][zone+'_definition_ids'])
        if zone=='deck':
            prefix=snapshot['public_model'].get('known_deck_prefix_definition_ids',[])
            ids=list(prefix)+sorted((Counter(ids)-Counter(prefix)).elements())
        for key,definition in zip(b.zones[zone],ids):
            b.cards[key]=definition;b.support_bases[key]=definition
    b.rng=ChanceRandom([],[],1)
    return b


def transitions(env,action,budget,thresholds):
    from .rollout import advance
    pending=[([],Fraction(1))];results=[]
    while pending:
        choices,weight=pending.pop();budget.spend();trial=fork_simulation(env)
        trial.rng=ChanceRandom(choices,thresholds,budget.maximum)
        try:
            advance(trial,action)
            if trial.rng.position!=len(choices):raise Unsupported('Unused chance script branch')
            results.append((trial,weight))
        except NeedChoice as request:
            for value,p in request.options:pending.append((choices+[(request.signature,value)],weight*p))
    return results


def outcomes(snapshot,first_action,continuation,*,max_actions,max_live_cards,max_nodes,draw_mode='permutation'):
    if draw_mode not in ('permutation','prefix','grouped'):raise Unsupported('Unknown chance draw mode')
    if draw_mode=='grouped':
        from .grouping import GroupedPrefixBattle,admit
        admit(continuation)
    live=len(snapshot['hand'])+sum(len(snapshot['public_model'][z+'_definition_ids']) for z in ['deck','grave','hold'])
    if live>max_live_cards:raise Unsupported('Public live pile exceeds chance enumeration limit')
    base=canonical_world(snapshot);budget=Budget(max_nodes)
    thresholds=sorted({0,1000}|{min(1000,s['upgrade_permil']) for s in (base.support_config or {}).get('supports',[])})
    pending=deque()
    if draw_mode in ('prefix','grouped'):
        base.__class__=GroupedPrefixBattle if draw_mode=='grouped' else PrefixBattle
        pending.append((base,Fraction(1),first_action))
    else:
        fixed=list(base.known_deck_prefix);deck=list(base.zones['deck'][len(fixed):]);count=factorial(len(deck))
        if count>max_nodes:raise Unsupported('Initial public deck permutation budget exceeded')
        for order in permutations(deck):
            b=fork_simulation(base);b.zones['deck']=fixed+list(order);pending.append((b,Fraction(1,count),first_action))
    terminal=[];action_cache={}
    while pending:
        env,weight,action=pending.pop()
        if env.steps>=max_actions:raise Unsupported('Chance continuation action budget exceeded')
        for successor,p in transitions(env,action,budget,thresholds):
            live=sum(len(successor.zones[z]) for z in ('hand','deck','grave','hold'))
            if live>max_live_cards:raise Unsupported('Public live pile exceeds chance enumeration limit after creation')
            w=weight*p
            if successor.ended:
                r=successor.result();terminal.append(dict(raw_score=r['raw_score'],final_total=r['final_total'],hp=r['hp'],
                    drinks_left=r['drinks_left'],weight=encoded(w)))
            else:
                from .rollout import public_snapshot,fingerprint
                obs=successor.observe();key=fingerprint(public_snapshot(obs,allow_unresolved_prior=True))
                if key not in action_cache:action_cache[key]=continuation.choose(obs,successor.legal_actions())[0]
                pending.append((successor,w,deepcopy(action_cache[key])))
    if sum((fraction(r['weight']) for r in terminal),Fraction(0))!=1:raise Unsupported('Chance terminal mass is incomplete')
    return terminal,dict(nodes=budget.nodes,terminal_paths=len(terminal),complete=True,draw_mode=draw_mode)


def compare(snapshot,actions,continuation,*,max_actions,max_live_cards,max_nodes,draw_mode='permutation'):
    candidates=[];objectives=[]
    for action in actions:
        result,stats=outcomes(snapshot,action,continuation,max_actions=max_actions,max_live_cards=max_live_cards,max_nodes=max_nodes,draw_mode=draw_mode)
        target=weighted_objective(result,snapshot['goal']);objectives.append(target)
        mean=sum((fraction(r['weight'])*r['raw_score'] for r in result),Fraction(0))
        candidates.append(dict(action=deepcopy(action),objective=[float(v) for v in target],
            rational_objective=[encoded(v) for v in target],mean_raw_score=float(mean),mean_raw_score_fraction=encoded(mean),
            enumeration=stats,outcomes=result))
    best=max(range(len(actions)),key=lambda i:objectives[i])
    return deepcopy(actions[best]),candidates
