"""Public-only continuation of the restored HIF Logic Main state.

Future tail attributes come from public configuration weights, not the saved
future list. Hidden card order/future chances use the explicitly admitted
exchangeable research model, not a native seed conditional posterior.
"""
from copy import deepcopy
import hashlib

from .core import Unsupported
from .exam_snapshot import canonical
from .catalog import Catalog
from .exam_resume import MainPhaseSession,verified
from .exam_history import analyze,PRIOR
from .turn_order import allocate
from .rollout import public_snapshot,sample_battle


def fixed_tail(observation):
    verified(observation);s=observation['scalars']
    weights=dict(Vo=s['vocalConfigParameter'],Da=s['danceConfigParameter'],Vi=s['visualConfigParameter'])
    if s['produceId']!='produce-008' or s['examType']!=1 or len(set(weights.values()))!=3:
        raise Unsupported('Pinned Battle branch with distinct positive public weights required')
    allocation=allocate(s['limitTurn'],weights)
    prefix=s['limitTurn']-3;current=s['currentTurn']
    if current<prefix:raise Unsupported('Unobserved random-prefix attributes are not restored')
    if current<1 or s['extraTurn']<0 or current>s['limitTurn']+s['extraTurn'] or s['remainTurn']<1:
        raise Unsupported('Inactive native turn boundary')
    names={1:'Vo',2:'Da',3:'Vi'}
    if observation['current_parameter_type'] not in names:raise Unsupported('Unknown current public attribute')
    current_attr=names[observation['current_parameter_type']]
    tail=allocation['fixed_tail']
    def at(turn):return tail[min(turn-prefix-1,2)]
    if current>prefix and current_attr!=at(current):
        raise Unsupported('Current attribute disagrees with public fixed-tail rule')
    attributes=[current_attr]+[at(t) for t in range(current+1,s['limitTurn']+s['extraTurn']+1)]
    if len(attributes)!=s['remainTurn']:raise Unsupported('Remaining public tail count mismatch')
    bonus=dict(Vo=s['vocalBonusPermil'],Da=s['danceBonusPermil'],Vi=s['visualBonusPermil'])
    return dict(public_sha256=observation['public_sha256'],weights=weights,allocation=allocation,
                attributes=attributes,bonus_by_attribute=bonus,
                source='Saved public ConfigParameter -> tagged descending tuple list -> native remaining-base-turn index',
                native_future_order_read=False)


def restore_snapshot(observation,boundary,history):
    """A complete remaining research-model input, with no native true order."""
    admission=analyze(history,observation,boundary)
    tail=fixed_tail(observation)
    session=MainPhaseSession(observation,boundary)
    obs=deepcopy(session.initial_observation)
    c=Catalog()
    try:draw=c.one('ExamSetting',observation['scalars']['settingId'])['turnStartDistribute']
    finally:c.db.close()
    turns=[dict(draw=0 if i==0 else draw,actions=obs['actions'] if i==0 else 1,
                bonus_permil=tail['bonus_by_attribute'][attr]) for i,attr in enumerate(tail['attributes'])]
    obs['public_model']['turns']=turns
    obs['public_model']['support_upgrades']['attributes']=list(tail['attributes'])
    obs['remaining_bonus_permil']=[t['bonus_permil'] for t in turns]
    obs['extra_turn_template']=dict(draw=draw,actions=1,bonus_permil=tail['bonus_by_attribute'][tail['allocation']['fixed_tail'][-1]])
    # This entry evaluates remaining RAW score only; it does not infer a round
    # adjustment, prior-round total, rival score, or a victory target.
    obs['score_context']=dict(round_adjustment=dict(numerator=1,denominator=1),completed_adjusted_score=0)
    snapshot=public_snapshot(obs)
    result=dict(schema_version=1,scope='Restored actual T9 public state with an exchangeable continuation model',
        source_public_sha256=observation['public_sha256'],history_sha256=history['history_sha256'],
        snapshot=snapshot,tail=tail,prior=PRIOR,refill_proved=admission['last_refill']['proved'],
        model_continuation_admitted=True,native_exam_replay_admitted=False,native_posterior_admitted=False,
        actual_clear=False)
    result['future_randomness']='One fresh NativeRandom seed independent of past/permutation; future calls share that sampled state'
    result['packet_sha256']=hashlib.sha256(canonical(result).encode()).hexdigest()
    return result


def sample(packet,particle):
    if (not isinstance(packet,dict) or packet.get('schema_version')!=1 or packet.get('prior')!=PRIOR
            or packet.get('model_continuation_admitted') is not True):
        raise Unsupported('An admitted public continuation packet is required')
    if type(particle) is not int or particle<0:raise Unsupported('Nonnegative research particle required')
    expected={'schema_version','scope','source_public_sha256','history_sha256','snapshot','tail','prior',
        'refill_proved','model_continuation_admitted','native_exam_replay_admitted','native_posterior_admitted',
        'actual_clear','future_randomness','packet_sha256'}
    if set(packet)!=expected:raise Unsupported('Exact continuation packet fields required')
    if (packet['refill_proved'] is not True or packet['native_exam_replay_admitted'] is not False
            or packet['native_posterior_admitted'] is not False or packet['actual_clear'] is not False):
        raise Unsupported('Continuation admission is model-only and requires the refill witness')
    content=deepcopy(packet);digest=content.pop('packet_sha256')
    if hashlib.sha256(canonical(content).encode()).hexdigest()!=digest:
        raise Unsupported('Continuation packet fingerprint mismatch')
    s=packet['snapshot'];model=s['public_model']
    checked=public_snapshot(dict(s,deck_count=len(model['deck_definition_ids']),grave_count=len(model['grave_definition_ids'])))
    if checked!=s:raise Unsupported('Canonical public snapshot required')
    return sample_battle(packet['snapshot'],particle)
