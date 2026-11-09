"""Finite HandAdd posterior under independent permil trials, not native RNG.

The future-relevant state is the used-support set. A per-card allocation is only
one equivalent witness, not the recovered real support identity.
"""
from fractions import Fraction
from .core import Unsupported


def fraction(value):return dict(numerator=value.numerator,denominator=value.denominator)


def infer_batch(rules,bases,observed,attribute,used=()):
    if attribute not in ('Vo','Da','Vi') or not 1<=len(bases)<=6 or len(bases)!=len(observed):
        raise Unsupported('Bounded ordered HandAdd batch and observed attribute required')
    supports=rules['supports'];graph=rules['next_definitions']
    ids=[s['support_card_id'] for s in supports]
    if len(ids)>6 or len(set(ids))!=len(ids) or set(used)-set(ids) or len(set(used))!=len(used):
        raise Unsupported('Valid unique support identities required')
    for s in supports:
        if (s['parameter'] not in (None,'Vo','Da','Vi') or type(s['upgrade_permil']) is not int
            or not 0<=s['upgrade_permil']<=1000000 or s['card_search_id']!='p_card_search-hand'):
            raise Unsupported('Pinned plain-hand support trials required')
    # Acyclic version graph; a capped success still reserves the support.
    for node in graph:
        seen=set();cur=node
        while cur in graph:
            if cur in seen:raise Unsupported('Cyclic version graph')
            seen.add(cur);cur=graph[cur]
    # mask -> (joint mass, one reset-equivalent assignment witness)
    mask=sum(1<<ids.index(i) for i in used);states={mask:(Fraction(1),[])}
    for base,target in zip(bases,observed):
        after={}
        for mask,(mass,witness) in states.items():
            branches={mask:mass}
            if base in graph:
                for n,s in enumerate(supports):
                    following={};bit=1<<n;p=Fraction(min(s['upgrade_permil'],1000),1000)
                    for bits,weight in branches.items():
                        choices=[(bits,Fraction(1))] if bits&bit or s['parameter'] not in (None,attribute) else [(bits,1-p),(bits|bit,p)]
                        for bits2,prob in choices:
                            if prob:following[bits2]=following.get(bits2,Fraction())+weight*prob
                    branches=following
            for bits,weight in branches.items():
                fired=[ids[n] for n in range(len(ids)) if (bits&~mask)&(1<<n)];effective=base;applied=[]
                for support in fired:
                    if effective in graph:effective=graph[effective];applied.append(support)
                if effective!=target:continue
                previous=after.get(bits)
                after[bits]=(weight+(previous[0] if previous else 0),previous[1] if previous else witness+[applied])
        states=after
        if not states:raise Unsupported('Observed versions have zero probability in the admitted support model')
    total=sum((v[0] for v in states.values()),Fraction())
    return dict(scope='Independent HandAdd trial posterior; not native shared-RNG posterior',
        event_probability=fraction(total),states=[dict(used=[ids[n] for n in range(len(ids)) if mask&(1<<n)],
            probability=fraction(mass/total),witness_support_ids=witness) for mask,(mass,witness) in sorted(states.items())],
        witness_is_actual_identity=False,native_posterior_admitted=False)
