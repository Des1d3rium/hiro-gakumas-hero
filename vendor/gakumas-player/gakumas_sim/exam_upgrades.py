"""Bounded stage-persistent plain-hand upgrade, independent of support lifetime.

Native raw eligibility is base + temporary == 0, not effective grade == 0.
support_bases stores the per-exam raw definition; it is never written back into
the persistent growth deck. A raw grade of one also represents a set temporary
flag: both are ineligible for a second ExamCardUpgrade and behave identically
for this bounded command and subsequent support upgrades.
"""
from copy import deepcopy
from .core import Unsupported

NATIVE='native-exam-hand-upgrade'
PUBLIC='public-exam-hand-upgrade'


def required(value):
    if isinstance(value,dict):
        return value.get('op') in ('upgrade_hand','next_turn_upgrade_hand') or any(required(v) for v in value.values())
    return isinstance(value,list) and any(required(v) for v in value)


def master_rules(document,c):
    from .support_upgrades import expand_definitions
    definitions,graph=expand_definitions(document,c)
    grades={k:d['provenance']['upgrade'] for k,d in definitions.items()}
    return definitions,dict(kind=NATIVE,master_commit=c.info['commit'],next_definitions=graph,grades=grades)


def bind(document,c):
    if not required([document['definitions'],document.get('drink_definitions',{}),document.get('passives',[])]):return document
    result=deepcopy(document)
    result['definitions'],result['exam_upgrades']=master_rules(result,c)
    return result


def validate(document,c=None):
    config=document.get('exam_upgrades')
    if config is None:
        if required([document['definitions'],document.get('drink_definitions',{}),document.get('passives',[])]):
            raise Unsupported('Hand upgrade requires a complete version binding')
        return
    if not isinstance(config,dict):raise Unsupported('Invalid exam upgrade binding')
    kind=config.get('kind');keys={'kind','next_definitions','grades'}
    if kind==NATIVE:
        keys.add('master_commit')
        if c is None or document.get('provenance',{}).get('kind')!='master-derived':raise Unsupported('Unbound native exam upgrade rules')
        definitions,expected=master_rules(document,c)
        if expected!=config or definitions!=document['definitions']:raise Unsupported('Exam upgrade versions diverge from master')
    elif kind==PUBLIC:
        if document.get('provenance',{}).get('kind')!='synthetic':raise Unsupported('Public exam upgrade needs a synthetic world')
    else:raise Unsupported('Unknown exam upgrade kind')
    if set(config)!=keys:raise Unsupported('Unknown exam upgrade fields')
    graph=config['next_definitions'];grades=config['grades'];definitions=document['definitions']
    if (not isinstance(graph,dict) or not isinstance(grades,dict) or set(grades)!=set(definitions)
        or any(type(g) is not int or not 0<=g<2**31 for g in grades.values())):
        raise Unsupported('Complete explicit exam upgrade grades required')
    for a,b in graph.items():
        if a not in definitions or b not in definitions or grades[b]!=grades[a]+1:
            raise Unsupported('Invalid exam upgrade edge')
    supports=document.get('support_upgrades')
    if supports is not None and supports['next_definitions']!=graph:
        raise Unsupported('Support and exam upgrade versions disagree')


def public_rules(config):
    return dict(kind=PUBLIC,next_definitions=deepcopy(config['next_definitions']),grades=deepcopy(config['grades']))


def apply(battle):
    config=battle.document.get('exam_upgrades')
    if config is None:raise Unsupported('Missing exam hand upgrade binding')
    graph=config['next_definitions'];grades=config['grades']
    for instance in list(battle.zones['hand']):
        base=battle.support_bases[instance]
        if grades[base]!=0 or base not in graph:continue
        # Stage upgrade persists when ResetSupportUpgrade later removes only
        # support IDs. Already-upgraded raw cards are not upgraded again.
        battle.support_bases[instance]=graph[base]
        effective=battle.cards[instance]
        if effective in graph:
            battle.cards[instance]=graph[effective]
        else:
            ids=battle.support_ids.get(instance)
            if not ids:raise Unsupported('Missing effective exam upgrade variant')
            ids.pop() # Native missing-version branch removes the last support.
            if not ids:battle.support_ids.pop(instance)
