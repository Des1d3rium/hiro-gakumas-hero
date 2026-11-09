"""用游戏自己的考试日志（ExamSaveData.userPlayLogList）逐事件对账引擎。

做法：从开考状态起步，回合属性取日志实际值，抽牌按日志强制成实际抽到的那几张（消除随机性），
出牌 / 喝饮料 / 结束回合按日志顺序执行。每执行一个动作，就把引擎的体力、元气、分数、干劲、好印象
和日志在下一个动作之前的累计值对比。对不上的检查点即是规则理解或建模的缺口。

日志编号（由夺冠局日志反推，见 simulator/tests/test_log_replay.py）：
  cellType   1 回合开始（含本回合抽牌）  3 出牌  4 喝饮料  6 状态触发  7 道具  8 回忆  14 定制道具开局
  detailLine 1 体力  2 元气  3 分数  4 状态（effectType 42 干劲、31 好印象、14 追加行动、39 得分加成 permil）
             6 移动/生成（_targetMovePosition 1 手牌，4 山札随机位置）  7 效果抽牌
"""
from collections import deque
from copy import deepcopy

from .campus import document,ATTR_BY_TYPE
from .core import Battle,Unsupported,ATTRIBUTE_LESSON

HP,GENKI,SCORE,STATUS=1,2,3,4
MOTIVATION,IMPRESSION=42,31
FIELDS=('hp','genki','score','motivation','impression')


def _card(card_id,upgrade,custom):
    return dict(card_id=card_id,upgrade=upgrade,customize=list(custom.get('_customizeList',[])) if isinstance(custom,dict) else list(custom or []))


def parse(log):
    """日志 → 有序事件：draw（强制抽牌队列项）、action（play/drink/end_turn）、values（日志记下的变化后值）。"""
    events=[];turn=0;last_action=None;turn_start=None
    def end_index(index):
        # 回合末触发（好印象得分、回合末干劲）排在本回合最后一个动作之后；没出过牌时排在回合开始的道具/回忆之后
        if last_action is not None:return last_action+0.5
        k=turn_start+1
        while k<index and log[k]['_cellType'] in (7,8):k+=1
        return k-0.5
    for index,cell in enumerate(log):
        kind=cell['_cellType']
        if kind==1:
            if turn:events.append(dict(kind='action',action='end_turn',index=end_index(index),turn=turn))
            turn=cell['_currentTurn'];last_action=None;turn_start=index
            events.append(dict(kind='turn',index=index,turn=turn,turn_types=list(cell['_turnTypeList']),bonus=cell['_bonusPermil']))
            # 回合开始的抽牌列表记的是本回合所有入手的卡（含之后卡牌效果抽到的），扣掉本回合效果抽牌才是开局抽牌
            later=[]
            for k in range(index+1,len(log)):
                if log[k]['_cellType']==1:break
                if log[k]['_cellType'] in (3,4):
                    # 7 = 效果抽牌；6 且目标位置 1 = 检索/移动入手（漢方等），由引擎自己执行，不进抽牌队列
                    later+=[ln['_targetId'] for d in log[k]['_detailList'] for ln in d['_detailLineList']
                            if ln['_detailLineType']==7 or ln['_detailLineType']==6 and ln['_targetMovePosition']==1]
            drawn=list(zip(cell['_drawCardList'],cell['_drawCardUpgradeList'],cell['_drawCardCustomizeList']))
            for card_id in later:
                for j in range(len(drawn)-1,-1,-1):
                    if drawn[j][0]==card_id:del drawn[j];break
            for c,u,m in drawn:
                events.append(dict(kind='draw',index=index,turn=turn,card=_card(c,u,m)))
        elif kind==3:
            last_action=index
            events.append(dict(kind='action',action='play',index=index,turn=turn,
                               card=_card(cell['_triggerId'],cell['_triggerSubscriptionNumber'],cell['_triggerSubscriptionNumberList'])))
        elif kind==4:
            last_action=index
            events.append(dict(kind='action',action='drink',index=index,turn=turn,drink=cell['_triggerId']))
        values={}
        for detail in cell['_detailList']:
            for line in detail['_detailLineList']:
                t=line['_detailLineType']
                if t==HP:values['hp']=line['_after']
                elif t==GENKI:values['genki']=line['_after']
                elif t==SCORE:values['score']=line['_after']
                elif t==STATUS and line['_effectType']==MOTIVATION:values['motivation']=line['_after']
                elif t==STATUS and line['_effectType']==IMPRESSION:values['impression']=line['_after']
                elif t==7 and kind in (3,4):
                    events.append(dict(kind='draw',index=index,turn=turn,card=dict(card_id=line['_targetId'],upgrade=None,customize=None)))
        if values:events.append(dict(kind='values',index=index,turn=turn,cell=kind,source=cell['_triggerId'],values=values))
    events.append(dict(kind='action',action='end_turn',index=end_index(len(log)),turn=turn))
    return sorted(events,key=lambda e:e['index'])


class ReplayBattle(Battle):
    """抽牌按日志队列强制；其余规则与 Battle 完全相同。"""

    def _draw(self,count,support=True):
        count=min(count,self.hand_limit-len(self.zones['hand']))
        received=[]
        for _ in range(count):
            if not self.forced:break
            want=self.forced.popleft()
            card=self._take(want)
            if card is None:raise Unsupported(f'日志抽到的 {want} 不在山札/弃牌里（区域 {self.zone_names()}）')
            received.append(card)
        self.zones['hand'].extend(received)
        self.draw_events.append(dict(turn=self.turn,drawn=received))
        return received

    def _match(self,instance,want):
        source=self.definition(instance).get('provenance') or {}
        if source.get('card_id')!=want['card_id']:return False
        if want['customize'] is not None:
            counts=[c['count'] for c in (source.get('customization') or {}).get('customizations',[])]
            if sum(want['customize'])!=sum(counts):return False
        return True

    def _take(self,want):
        for zone in ('deck','grave'):
            if zone=='grave' and self.zones['deck']:break  # 山札没空就不会洗回弃牌
            for card in self.zones[zone]:
                if self._match(card,want):
                    if zone=='grave':  # 洗牌：弃牌堆整体回到山札
                        self.zones['deck'].extend(self.zones['grave']);self.zones['grave'].clear()
                    self.zones['deck'].remove(card)
                    return card
        return None

    def _fix_ambiguous_draw(self,want):
        """效果抽牌的日志行只有卡 ID、没有定制。打出时手里同 ID 的卡定制不符，就和山札/弃牌里定制相符的那张对调。"""
        if any(self._match(c,want) for c in self.zones['hand']):return
        same=[c for c in self.zones['hand'] if self._match(c,dict(want,customize=None))]
        for zone in ('deck','grave'):
            for other in self.zones[zone]:
                if same and self._match(other,want):
                    i,j=self.zones['hand'].index(same[0]),self.zones[zone].index(other)
                    self.zones['hand'][i],self.zones[zone][j]=other,same[0]
                    return

    def zone_names(self):
        return {z:[self.definition(c)['name'] for c in cs] for z,cs in self.zones.items() if cs}


def _upgraded_key(env,card,upgrade):
    """日志记录的出牌强化等级高于持有等级时（支援的考试内临时强化），换成该等级、同定制的定义。"""
    source=env.definition(card).get('provenance') or {}
    if upgrade is None or upgrade==source.get('upgrade'):return env.cards[card]
    from .master_adapter import MasterAdapter
    custom=(source.get('customization') or {}).get('customizations') or None
    key=f"{source['card_id']}@{upgrade}:replay:{custom}"
    if key not in env.definitions:env.definitions[key]=MasterAdapter().card(source['card_id'],upgrade,custom)
    return key


def build(opening_observation,final_turn_types):
    doc=document(opening_observation)
    meta=doc.pop('campus')
    doc['opening_initial']=False  # 首回合手牌也按日志抽
    doc.pop('support_upgrades',None)  # 临时强化按日志记录的出牌等级强制，不让引擎自己掷骰
    doc['turns']=[dict(draw=meta['draw'],actions=1,bonus_permil=meta['bonus'][ATTR_BY_TYPE[t]],lesson=ATTRIBUTE_LESSON[ATTR_BY_TYPE[t]])
                  for t in final_turn_types[:opening_observation['scalars']['limitTurn']]]
    # 牌全部放回山札，从开考前开始
    doc['zones']=dict(deck=list(doc['cards']))
    return ReplayBattle(doc),meta


def snapshot(env):
    return dict(hp=env.hp,genki=env.shield,score=env.score,motivation=env.motivation,impression=env.impression)


def replay(opening_observation,save):
    """返回检查点列表：每个动作执行后引擎值与日志值的对比。"""
    log=save['userPlayLogList'];events=parse(log)
    env,meta=build(opening_observation,save['turnStatusParameterTypeList'])
    env.forced=deque(e['card'] for e in events if e['kind']=='draw')
    env.reset(0)
    s=opening_observation['scalars']
    expected=dict(hp=s['stamina'],genki=0,score=0,motivation=0,impression=0)
    checkpoints=[];pending=[];fresh=set()
    def check(label,turn,index):
        # 日志不记费用造成的扣减，所以只比较本步日志确实写过的字段（写的是变化后的值）
        got=snapshot(env)
        diff={k:(got[k],expected[k]) for k in FIELDS if k in fresh and got[k]!=expected[k]}
        fresh.clear()
        checkpoints.append(dict(label=label,turn=turn,log_index=index,diff=diff,engine=got,log=dict(expected),cells=list(pending)))
        pending.clear()
    # reset 已执行到第 1 回合开始（含开局道具与抽牌）；先吃掉第一个动作之前的日志值
    actions=[e for e in events if e['kind']=='action']
    cursor=0
    def absorb(until):
        nonlocal cursor
        while cursor<len(events) and events[cursor]['index']<until:
            e=events[cursor]
            if e['kind']=='values':
                expected.update(e['values']);fresh.update(e['values']);pending.append(dict(index=e['index'],cell=e['cell'],source=e['source'],values=e['values']))
            cursor+=1
    first=actions[0]['index'] if actions else len(log)
    fresh.update(FIELDS)  # 开局各值都已知
    absorb(first);check('开局（第 1 回合开始后）',1,first)
    for n,a in enumerate(actions):
        nxt=actions[n+1]['index'] if n+1<len(actions) else len(log)+1
        if env.ended:break
        try:
            if a['action']=='play':
                env._fix_ambiguous_draw(a['card'])
                legal=[x for x in env.legal_actions() if x['kind']=='play' and env._match(x['id'],a['card'])]
                if not legal:
                    checkpoints.append(dict(label=f"无法出牌 {a['card']['card_id']}",turn=a['turn'],log_index=a['index'],diff={'illegal':True},
                                            engine=snapshot(env),log=dict(expected),cells=[],hand=env.zone_names().get('hand')))
                    break
                card=legal[0]['id'];base=env.cards[card]
                upgraded=_upgraded_key(env,card,a['card']['upgrade'])
                env.cards[card]=upgraded
                name=env.definition(card)['name'];env._apply(legal[0])
                env.cards[card]=base
                label=f"打出 {name}"+("（临时强化）" if upgraded!=base else "")
            elif a['action']=='drink':
                slot=next(k for k,v in env.drinks.items() if v==a['drink'])
                label=f"喝 {env.drink_definitions[a['drink']]['name']}";env._apply(dict(kind='drink',id=slot))
            else:
                label='结束回合';env._apply(dict(kind='end_turn'))
        except Unsupported as error:
            checkpoints.append(dict(label='引擎拒绝：'+str(error),turn=a['turn'],log_index=a['index'],diff={'error':True},engine=snapshot(env),log=dict(expected),cells=[]))
            break
        absorb(nxt);check(label,a['turn'],a['index'])
    return checkpoints


def opening_from_result(save,stamina):
    """考试结束存档（含完整日志）→ 开考 observation：全部卡回山札、资源归零、饮料 = 剩余 + 日志里喝掉的。

    初始体力日志里看不到（费用扣减不记），由调用方给定候选值；开局的定制道具状态由日志第一条（cellType 14）补。"""
    from .exam_snapshot import project
    from .catalog import Catalog
    c=Catalog();commit=c.info['commit'];c.db.close()
    obs=project(save,client_version='result',master_commit=commit)
    s=obs['scalars']
    s.update(stamina=stamina,parameter=0,block=0,currentTurn=1,remainTurn=s['limitTurn'],extraTurn=0,examCardPlayCount=0,
             turnCardPlayCount=0,isTurnCardPlayEnd=False)
    # 考试中途生成的卡（日志移动行：来源位置 0 = 新建，如成長痛/冒険心 塞进山札的眠気）不属于开局卡组
    log=save['userPlayLogList']
    created=[ln['_targetId'] for x in log for d in x['_detailList'] for ln in d['_detailLineList']
             if ln['_detailLineType']==6 and ln['_cardFromPosition']==0]
    for card_id in created:
        victim=next((k for k,v in sorted(obs['cards'].items()) if v['card_id']==card_id),None)
        if victim is None:raise ValueError('生成记录对不上结束时的卡：'+card_id)
        del obs['cards'][victim]
    obs['zones']={k:[] for k in obs['zones']};obs['zones']['deck']=sorted(obs['cards'])
    for card in obs['cards'].values():
        card['effective_upgrade']=card['native']['_baseUpgradeCount']
    drunk=[{'_id':x['_triggerId']} for x in log if x['_cellType']==4]
    obs['drinks']=drunk+list(obs['drinks'])
    statuses=[]
    for x in log[:1]:
        if x['_cellType']!=14:break
        for d in x['_detailList']:
            for ln in d['_detailLineList']:
                if ln['_detailLineType']==STATUS and ln['_effectType']==MOTIVATION:
                    statuses.append(dict(type=dict(**{'class':'AggressiveStatusEffect'}),data=dict(_value=ln['_after'])))
                if ln['_detailLineType']==STATUS and ln['_effectType']==IMPRESSION:
                    statuses.append(dict(type=dict(**{'class':'ReviewStatusEffect'}),data=dict(_turn=ln['_after'])))
    # 回忆附魔在结束时可能已用完移除；保留结束时仍在的
    statuses+=[st for st in obs['active_statuses'] if (st['data'].get('_startEnchantOriginId') or '').startswith('p_memory_skill')]
    obs['active_statuses']=statuses
    obs['current_parameter_type']=save['turnStatusParameterTypeList'][0]
    obs['turn_used_supports']=[]
    return obs
