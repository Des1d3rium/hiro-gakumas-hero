"""实机开考快照 → 可按种子重复开局的研究对局（Campus mode!! 広 起步，结构上不限角色）。

输入是 native/exam_capture 解码出的开考 observation.json（live-runs/*/r1-opening-decoded 等）。
保留：全部持有卡的强化与定制、考试类 P 道具、回忆附魔、饮料、开考体力、定制道具给的初始干劲/好印象、
三项属性倍率与原生属性配额。每局重新洗牌并按配额随机排列前段回合属性（末三回合固定）。

支援卡的考试内临时强化按快照里的实际概率执行（每张支援每回合至多一次、只在其属性回合，共享 RNG）。

不还原：原生共享随机数与真实牌序、对手分数。所以它是研究用的同分布对局，
不是原局的逐帧重放；用于比较策略，不用于宣称实机得分。
"""
from copy import deepcopy
import random
from .core import Battle,Unsupported,ATTRIBUTE_LESSON
from .catalog import Catalog
from .master_adapter import MasterAdapter,sandbox
from .passives import PassiveCompiler
from .turn_order import allocate

ATTR_BY_TYPE={1:'Vo',2:'Da',3:'Vi'}


def _custom_start(observation):
    """定制道具在 ExamStart 装好的初始干劲 / 好印象（以开考状态为准）。"""
    motivation=impression=0
    for status in observation['active_statuses']:
        name,data=status['type']['class'],status['data']
        if name=='AggressiveStatusEffect':motivation=data['_value']
        elif name=='ReviewStatusEffect':impression=data['_turn']
    return motivation,impression


def _memories(observation,catalog):
    result=[]
    for status in observation['active_statuses']:
        data=status['data']
        origin=data.get('_startEnchantOriginId','')
        if status['type']['class']!='TriggerEffectStatusEffect' or not origin.startswith('p_memory_skill'):continue
        level=data['_startEnchantOriginLevel'];key='memory_ability-'+origin+'-%03d'%level
        if not [r for r in catalog.by_id('MemoryAbility',key) if r['level']==level]:raise Unsupported('Memory ability not found: '+key)
        result.append(dict(id=key,level=level))
    return result


def _exam_items(observation,catalog,idol):
    items=[]
    for row in observation['items']:
        key=row['_id']
        if key.startswith('customize_'):continue  # 开考时已结算为初始状态
        if not catalog.one('ProduceItem',key)['isExamEffect']:continue  # 培育中生效的道具
        PassiveCompiler(catalog).item(key,idol)
        items.append(key)
    return items


def card_specs(observation,catalog):
    specs=[]
    for card in observation['cards'].values():
        upgrade=card['native']['_baseUpgradeCount']
        ids=catalog.card(card['card_id'],upgrade)['produceCardCustomizeIds']
        custom=[dict(id=k,count=n) for k,n in zip(ids,card['customize_counts']) if n]
        spec=dict(card_id=card['card_id'],upgrade=upgrade)
        if custom:spec['customizations']=custom
        specs.append(spec)
    return sorted(specs,key=lambda s:(s['card_id'],s['upgrade'],str(s.get('customizations'))))


def document(observation,*,hp=None,drinks=None):
    """开考 observation → master 派生对局文档（回合属性先按配额顺序占位，开局时再抽样）。"""
    s=observation['scalars'];c=Catalog()
    try:
        idol=c.one('IdolCard',s['idolCardId'])
        loadout=dict(mode=s['produceId'],idol_card_id=s['idolCardId'],items=_exam_items(observation,c,idol),memories=_memories(observation,c))
        specs=card_specs(observation,c)
        setting=c.one('ExamSetting',s['settingId'])
    finally:c.db.close()
    drinks=[d['_id'] for d in observation['drinks']] if drinks is None else list(drinks)
    doc=sandbox(specs,turns=s['limitTurn'],drinks=drinks,loadout=loadout)
    motivation,impression=_custom_start(observation)
    doc['state']=dict(hp=s['stamina'] if hp is None else hp,max_hp=s['maxStamina'],motivation=motivation,impression=impression)
    if not 0<=doc['state']['hp']<=doc['state']['max_hp']:raise Unsupported('HP outside 0..max')
    doc['shuffle']=True
    bonus=dict(Vo=s['vocalBonusPermil'],Da=s['danceBonusPermil'],Vi=s['visualBonusPermil'])
    weights=dict(Vo=s['vocalConfigParameter'],Da=s['danceConfigParameter'],Vi=s['visualConfigParameter'])
    supports=observation.get('supports') or []
    if supports:
        from .support_upgrades import packet_supports,expand_definitions,PUBLIC
        c=Catalog()
        try:
            packet=dict(provenance=dict(kind='observed-progress',reference=observation.get('public_sha256') or 'exam-opening'),
                        cards=[dict(supportCardId=x['_supportCardId'],produceCardUpgradePermil=x['_produceCardUpgradePermil']) for x in supports])
            rules=packet_supports(packet,c)
            doc['definitions'],graph=expand_definitions(doc,c)
            # 支援卡临时强化会把卡展开到下一版本（例如 アイドル魂 → アイドル魂+，后者带 upgrade_hand）：
            # sandbox 在展开前绑定的 exam_upgrades 只覆盖原卡组，展开后必须按完整版本集重新绑定
            doc.pop('exam_upgrades',None)
            from .exam_upgrades import bind as bind_exam_upgrades
            doc=bind_exam_upgrades(doc,c)
        finally:c.db.close()
        # attributes 逐局由 CampusExam.episode 按实际抽到的回合属性改写
        doc['support_upgrades']=dict(kind=PUBLIC,supports=rules,next_definitions=graph,attributes=['Vo'])
    doc['campus']=dict(bonus=bonus,allocation=allocate(s['limitTurn'],weights),draw=setting['turnStartDistribute'],
                       first=ATTR_BY_TYPE.get(observation.get('current_parameter_type')),source=observation.get('public_sha256'))
    return doc


def turn_attributes(meta,rng,*,keep_first=True):
    """前段按配额随机排列，末三回合固定；keep_first 时第一回合沿用实机观测。"""
    pool=[a for a,n in meta['allocation']['random_prefix_counts'].items() for _ in range(n)]
    first=meta['first'] if keep_first and meta['first'] in pool else None
    if first:pool.remove(first)
    rng.shuffle(pool)
    return ([first] if first else [])+pool+list(meta['allocation']['fixed_tail'])


class CampusExam:
    """同一份开考配置上反复开局。Battle 只构造一次（master 校验较慢），每局换回合属性与种子。"""

    def __init__(self,observation,*,hp=None,drinks=None):
        doc=document(observation,hp=hp,drinks=drinks)
        self.meta=doc.pop('campus')
        self.battle=Battle(doc)

    def episode(self,seed,*,keep_first=True):
        attrs=turn_attributes(self.meta,random.Random(seed),keep_first=keep_first)
        turns=[dict(draw=self.meta['draw'],actions=1,bonus_permil=self.meta['bonus'][a],lesson=ATTRIBUTE_LESSON[a]) for a in attrs]
        env=deepcopy(self.battle)
        env.document['turns']=turns
        if env.document.get('support_upgrades'):env.document['support_upgrades']['attributes']=list(attrs)
        env.reset(seed)
        env.turn_attributes=attrs;env.turn_offset=0
        return env


STATUS_TICK_KINDS={'shield_tick','impression_tick','shield_gain_tick','conditional_tick','start_play_draw','end_turn','status_change'}


def _enchant_effects(catalog,adapter,enchant_id,cache={}):
    """附魔 ID → 安装它的那条 ProduceExamEffect 编译出的效果（取第一条匹配；回合数由状态覆盖）。"""
    if enchant_id not in cache:
        rows=[r for r in catalog.rows('ProduceExamEffect') if r.get('produceExamStatusEnchantId')==enchant_id]
        if not rows:raise Unsupported('No installing effect for enchant '+enchant_id)
        cache[enchant_id]=adapter.effect(rows[0]['id'])
    return deepcopy(cache[enchant_id])


TIMER_PHASE=21  # 3.4.1 ProduceExamPhaseType：ExamEffectTimer 的计时触发（存档 _trigger._phaseTypeList [21]，_phaseValueList [N 回合]）


def _timer_effects(catalog,adapter,data):
    """未到期的「N ターン後、…」（ExamEffectTimer）：存档里是 _statusEnchantId 为空的 TriggerEffectStatusEffect，
    _trigger 阶段 21 / 值 N，_turnCount 是已过回合数，_fromExamEffectIdList 是被延后的那条效果（timer 行的 chain）。
    返回 (编译出的 timer 效果, 还需几个回合)。"""
    trigger=data['_trigger']
    if (trigger.get('_phaseTypeList')!=[TIMER_PHASE] or len(trigger.get('_phaseValueList',[]))!=1
        or len(data['_fromExamEffectIdList'])!=1 or data['_isFromEnchantEffect'] or data['_isItemDirectEnchant']):
        raise Unsupported('Enchant-less trigger status is not an effect timer')
    delay=trigger['_phaseValueList'][0];chain=data['_fromExamEffectIdList'][0]
    left=delay-data['_turnCount']
    if left<1:raise Unsupported('Effect timer already due in a main-phase save')
    compiled=[]
    for row in catalog.rows('ProduceExamEffect'):
        if row['effectType']!='ProduceExamEffectType_ExamEffectTimer' or row['chainProduceExamEffectId']!=chain or row['effectValue1']!=delay:continue
        try:effects=adapter.effect(row['id'])
        except Unsupported:continue
        if effects not in compiled:compiled.append(effects)
    if len(compiled)!=1:raise Unsupported('Effect timer %s/%d has %d compiled forms'%(chain,delay,len(compiled)))
    return compiled[0],left


def _hook_for(hooks,data):
    origin=data.get('_startEnchantOriginId')
    for hook in hooks:
        if origin and origin in hook['source_id']:return hook
        # enchant-p_item_effect_02-3-076-0-enc01 ↔ pitem_02-3-076-0
        if not origin and hook['source_id'].startswith('pitem_') and '_'+hook['source_id'][len('pitem_'):]+'-enc' in data['_statusEnchantId']:
            return hook
    return None


def _extend_upgrade_graph(env,catalog):
    """存档里有开考快照没有的版本（定制 / 强化与开考时不同）：补进临时强化的版本图与等级表，否则 exam_upgrades.apply 查不到。"""
    from .support_upgrades import expand_definitions
    definitions,graph=expand_definitions(dict(definitions=env.definitions),catalog)
    env.definitions.update({k:v for k,v in definitions.items() if k not in env.definitions})
    config=env.document.get('exam_upgrades')
    if config is not None:
        config['next_definitions']=dict(graph);config['grades']={k:d['provenance']['upgrade'] for k,d in env.definitions.items()}
    if env.support_config:env.support_config['next_definitions']=dict(graph)


def restore(exam,observation,seed,*,turn_attributes=None):
    """把实机快照（出牌阶段，回合开始或回合中途）恢复成可继续对局的 Battle。

    exam 是同一局开考快照构造的 CampusExam（提供卡牌定义与道具）。山札顺序未知，按 seed 重新洗；
    剩余回合属性默认按配额与已过回合推断不出来时随机抽样，可用 turn_attributes 显式给定（含本回合）。"""
    s=observation['scalars']
    if observation['zones'].get('playing'):raise Unsupported('A card is still resolving; capture again after it settles')
    env=exam.episode(seed)
    c=Catalog();adapter=MasterAdapter(c)
    try:
        # 卡牌：实例 → 与开考同口径的定义键（实际强化/定制）
        keys={};added=False
        for inst,card in observation['cards'].items():
            upgrade=card['native']['_baseUpgradeCount']
            ids=c.card(card['card_id'],upgrade)['produceCardCustomizeIds']
            custom=[dict(id=k,count=n) for k,n in zip(ids,card['customize_counts']) if n]
            definition=adapter.card(card['card_id'],upgrade,custom or None)
            key=next((k for k,d in env.definitions.items() if d==definition),None)
            if key is None:
                key=card['card_id']+'@'+str(upgrade)+(':restored' if custom else '')
                env.definitions[key]=definition;added=True
            keys[inst]=key
        if added:_extend_upgrade_graph(env,c)
        env.cards=dict(keys)
        env.support_bases.update(keys);env.support_ids={};env.support_used=list(observation.get('turn_used_supports') or [])
        graph=(env.support_config or {}).get('next_definitions',{})
        for inst,card in observation['cards'].items():
            extra=card['effective_upgrade']-card['native']['_baseUpgradeCount']
            for _ in range(max(0,extra)):
                if env.cards[inst] in graph:env.cards[inst]=graph[env.cards[inst]]
            if extra>0:env.support_ids[inst]=list(card['native'].get('_supportUpgradeIdList') or ['restored'])
        rng=random.Random(seed)
        zones={z:list(observation['zones'].get(z,[])) for z in ('deck','hand','grave','lost','hold')}
        rng.shuffle(zones['deck'])
        zones['playing']=[]
        env.zones=zones
        env.hp,env.max_hp,env.shield,env.score=s['stamina'],s['maxStamina'],s['block'],s['parameter']
        env.motivation=env.impression=env.permanent_boost=env.focus=0;env.boosts=[];env.ticks=[];env.reservations=[]
        env.mental_motivation=env.end_turn_motivation=0;env.card_play_scores=[];env.play_intervals=[];env.repeat_buffs=[];env.stamina_modifiers=[];env.anti_debuff=0;env.stamina_down_fix=0;env.free_stamina=[];env.impression_boosts=[];env.block_restriction=None;env.impression_score_boosts=[]
        env.play_count=s['examCardPlayCount']
        # 剩余行动（3.4.1 ExecuteCardCommandImpl 0x32c61f0..0x32c6224，mechanics/HIF中途恢复与次数.md）：
        #   remainCanPlay = 追加次数（PlayableValueAddStatusEffect._value）+ (isTurnCardPlayEnd ? 0 : 1)
        # isTurnCardPlayEnd 是“基础出牌”是否已用掉的标志，不是出牌数：CardPlayCountAdd 优先消耗追加次数，没有追加才置位。
        # 所以 turnCardPlayCount 不参与（有使用数追加 / 不耗行动的卡时，出过牌的回合仍可能剩 1 次；实机 11-01 决赛 R1）
        env.actions=0 if s['isTurnCardPlayEnd'] else 1
        for hook in env.hooks:hook['uses']=0
        for status in observation['active_statuses']:
            name,data=status['type']['class'],status['data']
            if name=='AggressiveStatusEffect':env.motivation=data['_value']
            elif name=='ReviewStatusEffect':
                env.impression=data['_turn']
                env.impression_fresh=not data.get('_isPassingTurnStart',True)  # 本回合中途新建：下回合开始不减
            elif name=='LessonParameterMultipleStatusEffect':
                # 本回合中途获得的（未经过回合开始）回合末不递减
                fresh=not data['_isPassingTurnStart']
                if data['_isTurnLimited']:env.boosts.append(dict(permil=data['_value'],remaining=data['_turn'],fresh=fresh,native=True))
                else:env.permanent_boost+=data['_value']
            elif name=='PlayableValueAddStatusEffect':
                env.actions+=data.get('_value',0)  # 追加次数与基础标志无关：基础用完后追加次数仍可出牌
            elif name in ('StaminaConsumptionAddStatusEffect','StaminaConsumptionDownStatusEffect'):
                kind,permil=('stamina_up',2000) if name.startswith('StaminaConsumptionAdd') else ('stamina_down',500)
                env.stamina_modifiers.append(dict(kind=kind,permil=permil,remaining=data['_turn'],fresh=not data['_isPassingTurnStart']))
            elif name=='AntiDebuffStatusEffect':env.anti_debuff=data['_count']  # 3.4.1 元数据：剩余层数字段 _count
            elif name=='ReviewMultipleStatusEffect':
                env.impression_score_boosts.append(dict(permil=data['_value'],remaining=data['_turn'] if data['_isTurnLimited'] else -1,
                                                        fresh=not data['_isPassingTurnStart']))
            elif name=='BlockRestrictionStatusEffect':
                # 元気増加無効：无自有字段（3.4.1 元数据），回合数在 _turn，与 LessonParameterMultiple 同口径
                env.block_restriction=dict(remaining=data['_turn'] if data['_isTurnLimited'] else -1,fresh=not data['_isPassingTurnStart'])
            elif name=='ReviewAdditiveStatusEffect':
                # 好印象増加量増加：_value 为 ‰（GetReviewAdditiveMultiple 读的就是它），_turn 剩余回合，与 LessonParameterMultiple 同口径
                env.impression_boosts.append(dict(permil=data['_value'],remaining=data['_turn'] if data['_isTurnLimited'] else -1,
                                                  fresh=not data['_isPassingTurnStart']))
            elif name=='SearchPlayCardStaminaConsumptionChangeStatusEffect':
                # 勝利をつかめ！：_count 剩余次数，_searchId 检索条件（3.4.1 元数据）
                if data['_count']>0:env.free_stamina.append(dict(adapter.free_stamina_search(data['_searchId']),remaining=data['_count']))
            elif name=='PlayCountBuffStatusEffect':
                # 次に使用した…スキルカードの効果をもう1回発動（特製ハツボシエキス 等）：_value 额外发动次数，_count 剩余使用次数，
                # _searchId 检索条件（实机 2026-11-01 决赛 R2：_value 1 / _count 1 / _turn 1 / active_skill-n-r-sr-ssr-playing）；
                # 回合数与 LessonParameterMultiple 同口径（_turn 含本回合，回合中途装上的当回合末不减）
                if data['_count']>0 and data['_value']>0:
                    category,rarities=adapter.repeat_search(data['_searchId'])
                    env.repeat_buffs.append(dict(category=category,rarities=rarities,extra=data['_value'],uses=data['_count'],
                        remaining=data['_turn'] if data['_isTurnLimited'] else -1,fresh=not data['_isPassingTurnStart']))
            elif name=='LessonBuffStatusEffect':
                # 集中：层数在 _value（与 AggressiveStatusEffect 同族；字段名按 3.4.1 元数据同族推断，缺字段则拒绝还原）
                if type(data.get('_value')) is not int:raise Unsupported('Focus status without _value')
                env.focus=data['_value']
            elif name=='StaminaConsumptionDownFixStatusEffect':
                if data['_isTurnLimited']:raise Unsupported('Timed stamina fix reduction not ported')
                env.stamina_down_fix+=data['_value']
            elif name=='TriggerEffectStatusEffect':
                enchant=data['_statusEnchantId']
                if data['_isItemDirectEnchant'] or data.get('_startEnchantOriginId'):
                    # 道具 / 回忆钩子：_limitCount 是剩余次数（-1 不限）
                    hook=_hook_for(env.hooks,data)
                    if hook is None:raise Unsupported('No passive hook for restored enchant '+enchant)
                    if hook['limit'] and data['_limitCount']>=0:hook['uses']=hook['limit']-data['_limitCount']
                    continue
                if not enchant:
                    effects,left=_timer_effects(c,adapter,data)
                    before=len(env.reservations);env._effects(effects)
                    if len(env.reservations)==before:raise Unsupported('Effect timer did not schedule a reservation')
                    for timer in env.reservations[before:]:timer['start']=env.turn+left  # 存档在出牌阶段：env.turn 0 即本回合
                    continue
                effects=_enchant_effects(c,adapter,enchant)
                before=len(env.ticks)
                env._effects(effects)
                for tick in env.ticks[before:]:
                    if tick.get('kind') in ('end_turn','status_change'):
                        # 回合末附魔：本回合的回合末还没触发（存档在出牌阶段）；_turn 是剩余回合数，与 LessonParameterMultiple 同口径：
                        # 已经过回合开始的本回合末减 1，回合中途装上的（fresh）本回合末不减；永久为 -1
                        tick['start']=env.turn;tick['fresh']=not data['_isPassingTurnStart']
                        tick['remaining']=data['_turn'] if data['_isTurnLimited'] else -1
                        continue
                    # 原生回合限定状态的 _turn 含本回合：已经过回合开始的，本回合已触发过，之后还会触发 _turn-1 次；
                    # 本回合中途装上的还没触发过，之后触发 _turn 次
                    tick['start']=env.turn+1
                    if data['_isTurnLimited']:tick['remaining']=data['_turn']-(1 if data['_isPassingTurnStart'] else 0)
                env.ticks=[t for t in env.ticks if t['remaining']!=0]
            else:raise Unsupported('Status not restored: '+name)
        env.drinks={str(i):d['_id'] for i,d in enumerate(observation['drinks'])}
        for key in env.drinks.values():
            if key not in env.drink_definitions:
                env.drink_definitions[key]=adapter.drink(key)
                # 随机池生成（初星スペシャル青汁）的候选按偶像计划补进定义库
                from .card_mutations import admit_pool_candidates
                admit_pool_candidates(adapter,env.definitions,[env.drink_definitions[key]],c.one('IdolCard',s['idolCardId'])['planType'])
    finally:c.db.close()
    # 剩余回合：本回合 + 之后的基础回合 + 已获得的追加回合；属性末三回合固定，前段未知部分随机
    remaining=s['remainTurn']
    if turn_attributes is None:
        meta=exam.meta;base_left=max(0,s['limitTurn']-s['currentTurn']+1)
        current_attr=ATTR_BY_TYPE.get(observation.get('current_parameter_type'))
        attrs=turn_attributes_for(meta,s['currentTurn'],s['limitTurn'],rng,current_attr)
        # 已经在追加回合里（currentTurn > limitTurn，base_left 为 0）时基础回合属性列表为空：追加回合沿用本回合观测属性，
        # 没有观测时沿用最后一个固定回合的属性（与 Battle 的追加回合模板一致）
        last=attrs[-1] if attrs else (current_attr or meta['allocation']['fixed_tail'][-1])
        attrs=attrs[:base_left]+[last]*(remaining-base_left) if remaining>base_left else attrs[:remaining]
    else:attrs=list(turn_attributes)
    if len(attrs)!=remaining:raise Unsupported('Remaining turn attributes length mismatch')
    env.turns=[dict(draw=exam.meta['draw'],actions=1,bonus_permil=exam.meta['bonus'][a],lesson=ATTRIBUTE_LESSON[a]) for a in attrs]
    env.turn=0;env.bonus=env.turns[0]['bonus_permil'];env.extra_turn_template=deepcopy(env.turns[-1])
    env.turn_attributes=attrs;env.ended=False
    env.turn_offset=s['currentTurn']-1  # env.turn 0 对应存档第 currentTurn 回合；determinize 用它把配额前段换算成相对下标
    env._validate_zones()
    return env


def turn_attributes_for(meta,current,limit,rng,current_attr):
    """第 current 回合起到第 limit 回合的属性：末三回合固定，之前的按配额随机（本回合用观测值）。"""
    tail=list(meta['allocation']['fixed_tail']);prefix_len=limit-len(tail)
    if current>prefix_len:return tail[current-prefix_len-1:]
    left=prefix_len-current
    pool=[a for a,n in meta['allocation']['random_prefix_counts'].items() for _ in range(n)]
    if current_attr in pool:pool.remove(current_attr)
    rng.shuffle(pool)
    return [current_attr]+pool[:left]+tail
