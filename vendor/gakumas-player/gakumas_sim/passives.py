"""Source-checked HIF passive compilation and public trigger selection.

Only bounded payloads and explicit trigger shapes are admitted. After-use draw
and fixed-one random ID removal retain their proven zone and RNG behavior.
"""
from copy import deepcopy
import hashlib,json
from .core import Unsupported,validate_effects,validate_requirements,RARITIES
from .customize import nondefault

TEXT={'id','assetId','produceDescriptions','customizeProduceDescriptions',
      'playProduceDescriptions','playEffectProduceDescriptions'}
SCALAR_OPS={'score','shield','focus','motivation','impression','shield_score',
            'recover','hp_loss','extra_action','permanent_score_boost',
            'recover_permil','hp_loss_permil','score_boost','timed_score_boost',
            # 2026-10-31：状态类即时效果（与卡的同名 op 同一实现）
            'resource_score','stamina_down','stamina_up','block_restriction','shield_motivation_multiple','anti_debuff',
            'impression_boost','impression_score_boost','delayed_effects','stamina_down_fix'}
AFTER_OPS=SCALAR_OPS|{'draw','remove_random'}
START_PLAY_OPS=SCALAR_OPS|{'draw'}  # StartPlay（ターン開始後，通常发牌之后）可以抽牌
PHASE_OPS={'card_play_after':AFTER_OPS,'start_play':START_PLAY_OPS}
UNTARGETED={'turn_start','start_play','end_turn'}  # 不针对某张卡的阶段
HOOK_FIELDS={'phase','card_ids','category','rarities','requires','remaining_turns_max','effects','limit'}
LESSON_TYPES={'ProduceStepLessonType_LessonVocal':'vocal','ProduceStepLessonType_LessonDance':'dance',
              'ProduceStepLessonType_LessonVisual':'visual'}


def plain(row,allowed,label):
    extra=[k for k,v in row.items() if k not in allowed|TEXT and nondefault(v)]
    if extra:raise Unsupported(label+' semantic fields: '+','.join(extra))


def validate_hook(hook):
    if not isinstance(hook,dict) or set(hook)!=HOOK_FIELDS:raise Unsupported('Invalid passive hook fields')
    if hook['phase'] not in ('turn_start','card_play','card_play_after','start_play','end_turn'):raise Unsupported('Passive phase not ported')
    if hook['category'] not in ('any','mental','active'):raise Unsupported('Passive category not ported')
    for field in ('card_ids','rarities'):
        values=hook[field]
        if not isinstance(values,list) or any(not isinstance(v,str) or not v for v in values) or len(set(values))!=len(values):
            raise Unsupported('Invalid passive filter '+field)
    if any(r not in RARITIES for r in hook['rarities']):raise Unsupported('Invalid passive rarity')
    for field in ('limit','remaining_turns_max'):
        if type(hook[field]) is not int or hook[field]<0:raise Unsupported('Invalid passive limit')
    validate_requirements(hook['requires']);validate_effects(hook['effects'])
    allowed=PHASE_OPS.get(hook['phase'],SCALAR_OPS)
    if any(e['op'] not in allowed for e in hook['effects']):raise Unsupported('Passive payload not ported for this phase')
    if hook['phase'] in UNTARGETED and (hook['card_ids'] or hook['category']!='any' or hook['rarities'] or 'hand_trouble_min' in hook['requires']):
        raise Unsupported('Turn-start hook cannot use a card/unknown future hand filter')


def validate_passives(passives):
    if not isinstance(passives,list):raise Unsupported('Passives must be a list')
    seen=set()
    for bundle in passives:
        if (not isinstance(bundle,dict) or set(bundle)-{'kind','id','level','hooks','provenance','initializers'}
            or not {'kind','id','level','hooks','provenance'}<=set(bundle)):
            raise Unsupported('Invalid passive bundle')
        if bundle['kind'] not in ('item','memory') or not isinstance(bundle['id'],str) or not bundle['id']:
            raise Unsupported('Invalid passive identity')
        if type(bundle['level']) is not int or bundle['level']<1:raise Unsupported('Invalid passive level')
        key=(bundle['kind'],bundle['id'])
        if key in seen:raise Unsupported('Duplicate passive source')
        seen.add(key)
        if not isinstance(bundle['hooks'],list):raise Unsupported('Passive hooks must be a list')
        initializers=bundle.get('initializers',[])
        if not isinstance(initializers,list):raise Unsupported('Invalid passive initializers')
        if not bundle['hooks'] and not initializers:raise Unsupported('Passive needs hooks or initializers')
        for init in initializers:
            if (bundle['kind']!='item' or bundle['level']!=1 or not isinstance(init,dict)
                or set(init)!={'kind','item_id','minimum_draw_pile','pool_model'}
                or init['kind']!='hif_baton' or init['item_id']!=bundle['id']
                or type(init['minimum_draw_pile']) is not int or init['minimum_draw_pile']!=22
                or not isinstance(init['pool_model'],dict)):
                raise Unsupported('Unsupported source-bound initializer')
        for hook in bundle['hooks']:validate_hook(hook)
    if sum(len(b.get('initializers',[])) for b in passives)>1:
        raise Unsupported('Multiple creation initializers not ported')


def runtime_hooks(passives):
    validate_passives(passives);result=[]
    for bundle in passives:
        for index,hook in enumerate(bundle['hooks']):
            result.append(dict(deepcopy(hook),source_id=bundle['id'],source_kind=bundle['kind'],
                               hook_index=index,priority=0 if bundle['kind']=='item' else 1,uses=0))
    return result


def matching_hooks(hooks,phase,values,remaining,actor=None,hp_before=None):
    """Freeze eligibility BEFORE any selected payload changes state."""
    from .core import meets
    result=[]
    for hook in hooks:
        if hook['phase']!=phase or hook['limit'] and hook['uses']>=hook['limit']:continue
        if hook['remaining_turns_max'] and remaining>hook['remaining_turns_max']:continue
        check=values
        if hp_before is not None and any(k.startswith('hp_permil') for k in hook['requires']):
            check=dict(values,hp_min=hp_before)
        if not meets(check,hook['requires']):continue
        if actor is not None:
            if hook['card_ids'] and actor['card_id'] not in hook['card_ids']:continue
            if hook['category']!='any' and hook['category']!=actor['category']:continue
            if hook['rarities']:
                if actor['rarity'] is None:raise Unsupported('Rarity-filtered passive needs observed rarity')
                if actor['rarity'] not in hook['rarities']:continue
        elif phase not in UNTARGETED:raise Unsupported('Card passive requires actor')
        result.append(hook)
    return sorted(result,key=lambda h:h['priority']) # stable source/acquisition order


class PassiveCompiler:
    def __init__(self,catalog):
        self.catalog=catalog;self.refs=[]
        from .master_adapter import MasterAdapter
        self.adapter=MasterAdapter(catalog)

    def row(self,table,key,level=None):
        if level is None:row=self.catalog.one(table,key)
        else:
            rows=[r for r in self.catalog.by_id(table,key) if r['level']==level]
            if len(rows)!=1:raise Unsupported('Passive needs exact '+table+' level')
            row=rows[0]
        digest=hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        ref=dict(table=table,id=key,sha256=digest)
        if level is not None:ref['level']=level
        if ref not in self.refs:self.refs.append(ref)
        return row

    def enchant(self,key):
        row=self.row('ProduceExamStatusEnchant',key)
        plain(row,{'produceExamTriggerId','produceExamEffectIds'},'Passive enchant')
        if not row['produceExamEffectIds']:raise Unsupported('Empty passive enchant')
        return row

    def hook(self,enchant_id,limit):
        enchant=self.enchant(enchant_id);trigger=self.row('ProduceExamTrigger',enchant['produceExamTriggerId'])
        phases=trigger['phaseTypes'];phase={'ProduceExamPhaseType_ExamStartTurn':'turn_start',
                                         'ProduceExamPhaseType_ExamCardPlay':'card_play',
                                         'ProduceExamPhaseType_ExamCardPlayAfter':'card_play_after',
                                         # 2026-10-31：ターン開始後（StartPlay，发牌之后）与 ターン終了時（ExamEndTurn）
                                         'ProduceExamPhaseType_StartPlay':'start_play','ProduceExamPhaseType_ExamEndTurn':'end_turn'}
        if len(phases)!=1 or phases[0] not in phase:raise Unsupported('Passive trigger phase not ported')
        hook=dict(phase=phase[phases[0]],card_ids=[],category='any',rarities=[],requires={},remaining_turns_max=0,effects=[],limit=limit)
        if hook['phase'] in UNTARGETED:
            plain(trigger,{'phaseTypes','fieldStatusTypes','fieldStatusValues','fieldStatusCheckTypes','lessonType'},'Turn-start passive')
            fields,values=trigger['fieldStatusTypes'],trigger['fieldStatusValues']
            if nondefault(trigger['lessonType']):
                # 【ボーカルレッスン・ボーカルターンのみ】：按回合属性；可与体力条件组合（みんなの教科書：体力50%以下）
                if trigger['lessonType'] not in LESSON_TYPES:raise Unsupported('Turn-start lesson condition not ported')
                hook['requires']['lesson']=LESSON_TYPES[trigger['lessonType']]
                if fields:hook['requires'].update(self.adapter.field_requirements(trigger,allow_status=True))
            elif fields:
                if fields==['ProduceExamFieldStatusType_RemainingTurn'] and len(values)==1 and type(values[0]) is int and values[0]>=1:
                    hook['remaining_turns_max']=values[0]
                elif all(f in self.adapter.HP_FIELDS or f in self.adapter.STATUS_FIELDS for f in fields):hook['requires'].update(self.adapter.field_requirements(trigger,allow_status=True))
                else:raise Unsupported('Turn-start condition not ported')
            elif values:raise Unsupported('Orphan turn-start values')
        else:
            plain(trigger,{'phaseTypes','produceCardSearchId','lowerSearchCount','fieldStatusTypes','fieldStatusValues','fieldStatusCheckTypes','lessonType'},'Card passive')
            if trigger['fieldStatusTypes']:hook['requires'].update(self.adapter.field_requirements(trigger,allow_status=True))
            if nondefault(trigger['lessonType']):
                # 曇りをぬぐったタオル 等「【ボーカルレッスン・ボーカルターンのみ】アクティブスキルカード使用時」
                if trigger['lessonType'] not in LESSON_TYPES:raise Unsupported('Card passive lesson condition not ported')
                hook['requires']['lesson']=LESSON_TYPES[trigger['lessonType']]
            if not trigger['produceCardSearchId'] and not trigger['lowerSearchCount'] and hook['phase']=='card_play':
                # ぬくもりふたりぶん「スキルカード使用時、やる気が5以上の場合」：没有检索 = 任何出牌
                search=dict(cardPositionType='ProduceCardPositionType_Playing',produceCardIds=[],cardCategories=[],cardRarities=[])
            elif trigger['lowerSearchCount']!=1 or not trigger['produceCardSearchId']:raise Unsupported('Card passive needs single target search')
            else:search=self.row('ProduceCardSearch',trigger['produceCardSearchId'])
            plain(search,{'cardPositionType','produceCardIds','cardCategories','cardRarities','limitCount'},'Card passive search')
            expected='ProduceCardPositionType_Target' if hook['phase']=='card_play_after' else 'ProduceCardPositionType_Playing'
            if search['cardPositionType']!=expected:raise Unsupported('Card passive target position not ported')
            categories=search['cardCategories']
            if categories not in ([],['ProduceCardCategory_ActiveSkill'],['ProduceCardCategory_MentalSkill']):raise Unsupported('Card passive category not ported')
            hook['category']='any' if not categories else ('active' if categories[0].endswith('_ActiveSkill') else 'mental')
            hook['card_ids']=list(search['produceCardIds']);hook['rarities']=list(search['cardRarities'])
        for key in enchant['produceExamEffectIds']:
            raw=self.row('ProduceExamEffect',key)
            if raw['effectType']=='ProduceExamEffectType_ExamCardMove':self.row('ProduceCardSearch',raw['produceCardSearchId'])
            effects=self.adapter.effect(key)
            hook['effects'].extend(effects)
        validate_hook(hook);return hook

    def bundle(self,kind,key,level,hooks):
        return dict(kind=kind,id=key,level=level,hooks=hooks,
                    provenance=dict(commit=self.catalog.info['commit'],references=deepcopy(self.refs),
                                    scope='bounded exam passives; supplied ownership, not account verification'))

    def memory(self,key,level,group,mode,idol):
        self.refs=[];root=self.row('MemoryAbility',key,level)
        plain(root,{'level','skillId','evaluation','rarity','produceGroupIds','isUniqueActivation'},'Memory ability')
        if group['id'] not in root['produceGroupIds']:raise Unsupported('Memory not eligible for selected mode')
        skill=self.row('ProduceSkill',root['skillId'],level)
        plain(skill,{'level','rarity','tag','planType','produceType','produceSplitType'}|
              {f'{prefix}{i}' for i in (1,2,3) for prefix in ('produceEffectId','produceTriggerId','activationRatePermil')},'Memory skill')
        if skill['produceType']!=group['type'] or skill['planType'] not in ('ProducePlanType_Common',idol['planType']):raise Unsupported('Memory mode/plan mismatch')
        if nondefault(skill['produceSplitType']) and skill['produceSplitType']!=mode['produceSplitType']:raise Unsupported('Memory selection/final mismatch')
        hooks=[]
        for i in (1,2,3):
            effect_id=skill[f'produceEffectId{i}'];trigger_id=skill[f'produceTriggerId{i}'];rate=skill[f'activationRatePermil{i}']
            if not effect_id:
                if trigger_id or rate:raise Unsupported('Orphan memory trigger/rate')
                continue
            if rate not in (0,1000):raise Unsupported('Random memory activation not ported')
            trigger=self.row('ProduceTrigger',trigger_id)
            plain(trigger,{'phaseType'},'Memory outer trigger')
            if trigger['phaseType']!='ProducePhaseType_StartAudition':raise Unsupported('Memory outer phase not ported')
            effect=self.row('ProduceEffect',effect_id)
            plain(effect,{'produceEffectType','produceExamStatusEnchantId'},'Memory outer effect')
            if effect['produceEffectType']!='ProduceEffectType_ExamStatusEnchant':raise Unsupported('Memory outer payload not ported')
            outer=self.enchant(effect['produceExamStatusEnchantId']);timer=self.row('ProduceExamTrigger',outer['produceExamTriggerId'])
            plain(timer,{'phaseTypes','phaseValues'},'Memory install timer')
            if timer['phaseTypes']!=['ProduceExamPhaseType_ExamTurnTimer'] or timer['phaseValues']!=[1] or len(outer['produceExamEffectIds'])!=1:
                raise Unsupported('Memory install timing not ported')
            inner=self.row('ProduceExamEffect',outer['produceExamEffectIds'][0])
            plain(inner,{'effectType','effectTurn','effectCount','produceExamStatusEnchantId','effectGroupIds'},'Memory install effect')
            if inner['effectType']!='ProduceExamEffectType_ExamStatusEnchant' or inner['effectTurn']!=-1 or inner['effectCount']<1:
                raise Unsupported('Memory install duration/count not ported')
            hook=self.hook(inner['produceExamStatusEnchantId'],inner['effectCount'])
            if hook['phase']=='turn_start':raise Unsupported('First-turn deferred memory cannot be folded to turn start')
            hooks.append(hook)
        if not hooks:raise Unsupported('Memory has no executable hooks')
        return self.bundle('memory',key,level,hooks),root['skillId'] if root['isUniqueActivation'] else None

    def item(self,key,idol):
        self.refs=[];root=self.row('ProduceItem',key)
        plain(root,{'name','rarity','planType','libraryHidden','evaluation','isExamEffect','originIdolCardId','originSupportCardId',
                    'isUpgraded','skills','produceItemEffectIds','effectGroupIds','viewStartTime','order'},'Item')
        if not root['isExamEffect'] or root['planType'] not in ('ProducePlanType_Common',idol['planType']):raise Unsupported('Item exam/plan mismatch')
        if root['originIdolCardId'] and root['originIdolCardId']!=idol['id']:raise Unsupported('Item origin does not match selected idol card')
        if root['skills']!=[dict(produceTriggerId='',produceItemEffectId=k) for k in root['produceItemEffectIds']]:raise Unsupported('Item skill links/triggers not ported')
        from .baton import BATONS
        if key in BATONS:
            from .hif import HIFPlanner
            report=HIFPlanner(catalog=self.catalog).baton(key)
            for table,part in [('ProduceItemEffect','produce_effect'),('ProduceExamStatusEnchant','enchant'),
                               ('ProduceExamTrigger','trigger'),('ProduceExamEffect','effect'),
                               ('ProduceCardSearch','count_search'),('ProduceCardSearch','source_search')]:
                self.row(table,report['evidence'][part]['id'])
            self.row('ProduceCardPool',report['random_pool'])
            bundle=self.bundle('item',key,1,[])
            bundle['initializers']=[dict(kind='hif_baton',item_id=key,minimum_draw_pile=report['minimum_draw_pile'],
                                         pool_model=report['pool_model'])]
            return bundle,root['originIdolCardId']
        hooks=[]
        for key_effect in root['produceItemEffectIds']:
            effect=self.row('ProduceItemEffect',key_effect)
            plain(effect,{'effectType','effectTurn','effectCount','produceExamStatusEnchantId'},'Item effect')
            if effect['effectType']!='ProduceItemEffectType_ExamStatusEnchant' or effect['effectTurn']!=-1 or effect['effectCount']<0:
                raise Unsupported('Item effect type/duration/count not ported')
            # effectCount is the per-exam use limit (レッスン内N回); 0 means unlimited.
            hooks.append(self.hook(effect['produceExamStatusEnchantId'],effect['effectCount']))
        if not hooks:raise Unsupported('Item has no executable hooks')
        return self.bundle('item',root['id'],1,hooks),root['originIdolCardId']


def compile_loadout(catalog,loadout):
    if not isinstance(loadout,dict) or set(loadout)!={'mode','idol_card_id','items','memories'}:raise Unsupported('Loadout requires mode, idol_card_id, items, memories')
    if loadout['mode'] not in ('produce-007','produce-008'):raise Unsupported('Passive loadout currently requires HIF mode')
    mode=catalog.one('Produce',loadout['mode']);idol=catalog.one('IdolCard',loadout['idol_card_id'])
    groups=[g for g in catalog.rows('ProduceGroup') if loadout['mode'] in g['produceIds']]
    if len(groups)!=1:raise Unsupported('Ambiguous produce group')
    if not isinstance(loadout['items'],list) or not isinstance(loadout['memories'],list):raise Unsupported('Loadout lists required')
    compiler=PassiveCompiler(catalog);result=[];unique=set();origins=set()
    for key in loadout['items']:
        if not isinstance(key,str) or not key:raise Unsupported('Item ID required')
        bundle,origin=compiler.item(key,idol)
        if origin and origin in origins:raise Unsupported('Duplicate idol-origin item including upgrade variants')
        if origin:origins.add(origin)
        result.append(bundle)
    for entry in loadout['memories']:
        if (not isinstance(entry,dict) or set(entry)!={'id','level'} or not isinstance(entry['id'],str) or not entry['id']
            or type(entry['level']) is not int or entry['level']<1):raise Unsupported('Memory requires exact id/positive level')
        bundle,identity=compiler.memory(entry['id'],entry['level'],groups[0],mode,idol)
        if identity and identity in unique:raise Unsupported('Duplicate unique memory activation')
        if identity:unique.add(identity)
        result.append(bundle)
    validate_passives(result);return result
