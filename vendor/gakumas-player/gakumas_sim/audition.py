"""Exact client application of supplied audition-progress bonus fields.

Does not derive protocol base bonuses or StarBonusPermil from growth stats or
star quantity, and does not authenticate an input as a captured server message.
"""
from copy import deepcopy
import math
from .core import Unsupported,f32
from .attributes import ATTRIBUTES,checked

BASE_FIELDS=('vocalPermil','dancePermil','visualPermil')
AUDITION_FIELDS=set(BASE_FIELDS)|{'voteBonusPermil','starBonusPermil'}


def combine_bonus(base,effect,vote,star):
    for name,value in [('base',base),('effect',effect),('vote',vote),('star',star)]:
        checked(value,name,1000000)
    # Actual path uses int32 addition before FloatFromPermil for vote+star.
    effect_ratio=f32(f32(effect)/f32(1000))
    audience_ratio=f32(f32(vote+star)/f32(1000))
    first=f32(f32(f32(1)+effect_ratio)*f32(base))
    product=f32(first*f32(f32(1)+audience_ratio))
    rounded=math.ceil(f32(product/f32(10)))
    if rounded > (2**31-1)//10:
        raise Unsupported('Native Int32 audition multiplier overflow is unsupported')
    return rounded*10


def audition_preview(progress,audition):
    if not isinstance(progress,dict) or set(progress)!={'auditionEffectParameterBonusPermil'}:
        raise Unsupported('Need explicit auditionEffectParameterBonusPermil (including zero)')
    if not isinstance(audition,dict) or set(audition)!=AUDITION_FIELDS:
        raise Unsupported('Need exactly vocal/dance/visual/vote/star Permil fields (including zeros)')
    effect=progress['auditionEffectParameterBonusPermil']
    vote=audition['voteBonusPermil'];star=audition['starBonusPermil']
    bonuses={a:combine_bonus(audition[field],effect,vote,star) for a,field in zip(ATTRIBUTES,BASE_FIELDS)}
    return dict(schema_version=1,progress=deepcopy(progress),audition=deepcopy(audition),
                parameter_bonus_permil=bonuses,
                scope='client application of supplied protocol values; producer formulas and snapshot authenticity not inferred')


def bind_audition_battle(document,mode,position,progress,audition,turn_attributes,source,idol_card_id='i_card-shro-3-000'):
    from .catalog import Catalog
    from .hif import HIFPlanner
    if document.get('provenance',{}).get('kind')!='master-derived':
        raise Unsupported('Audition binding requires source-verified cards')
    if 'exam_bonus' in document:raise Unsupported('Battle already has an exam bonus binding')
    if (not isinstance(source,dict) or set(source)!={'kind','reference'}
        or source['kind'] not in {'provided-protocol-values','synthetic-protocol-fixture'}
        or not isinstance(source['reference'],str) or not source['reference'].strip()):
        raise Unsupported('Need explicit provenance for supplied protocol values')
    proof=audition_preview(progress,audition)
    c=Catalog()
    try:
        config=HIFPlanner(idol_card_id,c).configuration(mode,position)
        count=config['battle_config']['turn']
        if not isinstance(turn_attributes,list) or len(turn_attributes)!=count or len(document['turns'])!=count:
            raise Unsupported('Need supplied complete base exam turn order')
        if any(a not in ATTRIBUTES for a in turn_attributes):raise Unsupported('Unknown turn attribute')
        result=deepcopy(document)
        result['provenance']['warning']='Source cards and native client application of supplied protocol bonuses; input authenticity, turn generation and full HIF events not verified'
        result['exam_bonus']=dict(kind='audition-progress',mode=mode,position=position,idol_card_id=idol_card_id,
                                  progress=deepcopy(progress),audition=deepcopy(audition),
                                  turn_attributes=deepcopy(turn_attributes),source=deepcopy(source),preview=proof,
                                  master_commit=c.info['commit'],battle_config_id=config['battle_config']['id'])
        for turn,attr in zip(result['turns'],turn_attributes):turn['bonus_permil']=proof['parameter_bonus_permil'][attr]
        return result
    finally:c.db.close()


def validate_audition_binding(document):
    b=document['exam_bonus']
    if b['kind']=='displayed-audition':
        return validate_displayed_binding(document)
    if b['kind']=='native-audition':
        return validate_native_binding(document)
    if set(b)!={'kind','mode','position','idol_card_id','progress','audition','turn_attributes','source','preview','master_commit','battle_config_id'}:
        raise Unsupported('Unknown audition bonus binding fields')
    without=deepcopy(document);del without['exam_bonus']
    rebuilt=bind_audition_battle(without,b['mode'],b['position'],b['progress'],b['audition'],b['turn_attributes'],b['source'],b['idol_card_id'])
    if rebuilt!=document:raise Unsupported('Audition bonus source or turn multiplier diverges from client application')
    loadout=document.get('loadout',{})
    if loadout.get('mode',b['mode'])!=b['mode'] or loadout.get('idol_card_id',b['idol_card_id'])!=b['idol_card_id']:
        raise Unsupported('Loadout and audition phase/idol differ')


def bind_native_audition_battle(document,mode,position,progress,audition,source,idol_card_id='i_card-shro-3-000'):
    from .catalog import Catalog
    from .hif import HIFPlanner
    from .turn_order import allocate,config_parameters
    c=Catalog()
    try:config=HIFPlanner(idol_card_id,c).configuration(mode,position)['battle_config']
    finally:c.db.close()
    allocation=allocate(config['turn'],config_parameters(config))
    result=bind_audition_battle(document,mode,position,progress,audition,['Vo']*config['turn'],source,idol_card_id)
    binding=result['exam_bonus'];binding['kind']='native-audition';del binding['turn_attributes']
    binding['order_config']=config;binding['allocation']=allocation
    for turn in result['turns']:turn['bonus_permil']=0 # Explicit runtime-derived slot, not zero game multiplier.
    result['provenance']['warning']='Source cards + supplied protocol values + restored config turn order. Other native setup RNG consumers, full events and input authenticity unverified.'
    return result


def validate_native_binding(document):
    b=document['exam_bonus']
    if set(b)!={'kind','mode','position','idol_card_id','progress','audition','source','preview','master_commit','battle_config_id','order_config','allocation'}:
        raise Unsupported('Unknown native audition binding fields')
    without=deepcopy(document);del without['exam_bonus']
    expected=bind_native_audition_battle(without,b['mode'],b['position'],b['progress'],b['audition'],b['source'],b['idol_card_id'])
    if expected!=document:raise Unsupported('Native order config or multiplier bundle diverges from source')
    loadout=document.get('loadout',{})
    if loadout.get('mode',b['mode'])!=b['mode'] or loadout.get('idol_card_id',b['idol_card_id'])!=b['idol_card_id']:
        raise Unsupported('Loadout and native exam phase/idol differ')


def displayed_bonus(display_bonus):
    """Normalize explicit FINAL multipliers without inventing raw fields."""
    if not isinstance(display_bonus,dict) or set(display_bonus)!={'unit','values'}:
        raise Unsupported('Final displayed bonus needs explicit unit and values')
    unit=display_bonus['unit'];values=display_bonus['values']
    if unit not in ('percent','permil'):raise Unsupported('Displayed bonus unit must be percent or permil')
    if not isinstance(values,dict) or set(values)!=set(ATTRIBUTES):
        raise Unsupported('Need all three displayed Vo/Da/Vi values; missing values cannot be inferred')
    normalized={}
    for attr,value in values.items():
        checked(value,attr+' displayed bonus',214748364 if unit=='percent' else 2147483647)
        value=value*10 if unit=='percent' else value
        if value%10:raise Unsupported('Final displayed bonus must align to 10 permil')
        normalized[attr]=value
    return dict(parameter_bonus_permil=normalized,
        scope='Explicit final multipliers; upstream protocol/stat/star producer and input authenticity unverified')


def bind_displayed_audition_battle(document,mode,position,display_bonus,source,idol_card_id='i_card-shro-3-000'):
    from .catalog import Catalog
    from .hif import HIFPlanner
    from .turn_order import allocate,config_parameters
    if document.get('provenance',{}).get('kind')!='master-derived':
        raise Unsupported('Displayed audition binding requires source-verified cards')
    if 'exam_bonus' in document:raise Unsupported('Battle already has an exam bonus binding')
    if any(document.get('zones',{}).get(k) for k in ('hand','grave','lost','hold','playing')):
        raise Unsupported('Displayed bonus binding requires a pre-deal deck')
    if (not isinstance(source,dict) or set(source)!={'kind','reference'}
        or source['kind'] not in {'provided-display-values','synthetic-display-fixture'}
        or not isinstance(source['reference'],str) or not source['reference'].strip()):
        raise Unsupported('Need explicit source for final displayed values')
    proof=displayed_bonus(display_bonus);c=Catalog()
    try:
        config=HIFPlanner(idol_card_id,c).configuration(mode,position)['battle_config']
        if len(document['turns'])!=config['turn']:raise Unsupported('Displayed bonus base turn count differs from exam')
        loadout=document.get('loadout',{})
        if loadout.get('mode',mode)!=mode or loadout.get('idol_card_id',idol_card_id)!=idol_card_id:
            raise Unsupported('Loadout and displayed exam mode/idol differ')
        result=deepcopy(document)
        result['exam_bonus']=dict(kind='displayed-audition',mode=mode,position=position,idol_card_id=idol_card_id,
            display_bonus=deepcopy(display_bonus),source=deepcopy(source),preview=proof,
            master_commit=c.info['commit'],battle_config_id=config['id'],order_config=config,
            allocation=allocate(config['turn'],config_parameters(config)))
        for turn in result['turns']:turn['bonus_permil']=0
        result['provenance']['warning']='Source cards + supplied final displayed multipliers + restored native order. Input authenticity, upstream producers, other setup consumers and full HIF not verified.'
        return result
    finally:c.db.close()


def validate_displayed_binding(document):
    b=document['exam_bonus']
    if set(b)!={'kind','mode','position','idol_card_id','display_bonus','source','preview','master_commit','battle_config_id','order_config','allocation'}:
        raise Unsupported('Unknown displayed audition binding fields')
    without=deepcopy(document);del without['exam_bonus']
    rebuilt=bind_displayed_audition_battle(without,b['mode'],b['position'],b['display_bonus'],b['source'],b['idol_card_id'])
    if rebuilt!=document:raise Unsupported('Displayed bonus binding diverges from declared final values or pinned order')
