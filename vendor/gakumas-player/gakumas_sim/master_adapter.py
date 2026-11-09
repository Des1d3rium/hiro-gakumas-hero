"""Strict master -> resolved-effect adapter; no names/descriptions parsed as rules.

This supports bounded direct, turn-start and mental-play effects in a neutral sandbox, not the full
native status/trigger engine. Every unsupported semantic field blocks the card.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from .catalog import Catalog
from .core import Unsupported, validate_definition, END_TURN_OPS
from .customize import resolve, nondefault


def suffix(value):return value.split('_',1)[-1]


DELAYABLE={'score','shield','motivation','impression','focus','recover','resource_score','shield_score','impression_growth','anti_debuff'}


class MasterAdapter:
    def __init__(self,catalog=None):self.catalog=catalog or Catalog()

    def effect(self,key,overrides=None,seen=()):
        if key in seen:raise Unsupported('Cyclic effect reference')
        row=(overrides or {}).get(key) or self.catalog.one('ProduceExamEffect',key)
        kind=suffix(row['effectType'])
        metadata={'id','effectType','effectValue1','effectValue2','effectCount','effectTurn',
                  'produceDescriptions','customizeProduceDescriptions','effectGroupIds'}
        if kind=='ExamStatusEnchant':metadata.add('produceExamStatusEnchantId')
        if kind=='ExamEffectTimer':metadata.add('chainProduceExamEffectId')
        if kind=='ExamCardSearchEffectPlayCountBuff':metadata|={'produceCardSearchId','pickRangeType'}
        if kind=='ExamCardMove':metadata|={'produceCardSearchId','movePositionType','pickRangeType','pickCountMin','pickCountMax'}
        if kind=='ExamCardUpgrade':metadata|={'produceCardSearchId','pickRangeType'}
        if kind=='ExamCardCreateId':metadata|={'targetProduceCardId','targetUpgradeCount','movePositionType','pickCountMin','pickCountMax'}
        if kind=='ExamCardCreateSearch':metadata|={'produceCardSearchId','movePositionType','pickRangeType','pickCountMin','pickCountMax'}
        if kind=='ExamSearchPlayCardStaminaConsumptionChange':metadata|={'produceCardSearchId','pickRangeType'}
        for name,value in row.items():
            if name not in metadata and nondefault(value):
                raise Unsupported('effect semantic field:'+name)
        value=row['effectValue1'];count=row['effectCount']
        if kind=='ExamHandGraveCountCardDraw':
            if value or count or row['effectValue2'] or row['effectTurn']:
                raise Unsupported('Only whole-hand zero-filter discard/redraw is ported')
            return [dict(op='discard_redraw')]
        if kind=='ExamCardCreateId':
            if (value or count or row['effectValue2'] or row['effectTurn'] or row['pickCountMin']!=1 or row['pickCountMax']!=1
                or row['movePositionType']!='ProduceCardMovePositionType_DeckRandom'):
                raise Unsupported('Only fixed-one specified DeckRandom creation is ported')
            target=self.catalog.card(row['targetProduceCardId'],row['targetUpgradeCount'])
            return [dict(op='create_deck_random',definition_id=target['id']+'@'+str(target['upgradeCount']))]
        if kind=='ExamCardCreateSearch':return self.create_hand_pool(row)
        if kind=='ExamSearchPlayCardStaminaConsumptionChange':
            # 勝利をつかめ！（p_card-00-sup-3_158）「次に使用したスキルカードの消費体力を0にする（1回）」：
            # 之后 N 次打出符合检索的卡，体力消耗为 0。存档状态类 SearchPlayCardStaminaConsumptionChangeStatusEffect
            # （3.4.1 元数据字段 _count / _searchId / _fromExamEffectId）。
            if value or row['effectValue2'] or count<1 or row['effectTurn']!=-1 or row['pickRangeType']!='ProducePickRangeType_All':
                raise Unsupported('Free-stamina play contract')
            return [dict(op='free_stamina',search=self.free_stamina_search(row['produceCardSearchId']),uses=count)]
        if kind=='ExamCardUpgrade':
            if value or count or row['effectValue2'] or row['effectTurn'] or row['pickRangeType']!='ProducePickRangeType_All':
                raise Unsupported('Only unparameterized all-hand exam upgrade is ported')
            search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
            if (search['cardPositionType']!='ProduceCardPositionType_Hand'
                or any(nondefault(v) for k,v in search.items() if k not in {'id','cardPositionType','produceDescriptions'})):
                raise Unsupported('Exam upgrade needs a plain Hand search')
            return [dict(op='upgrade_hand')]
        if kind=='ExamCardMove':
            if (not value and not count and not row['effectValue2'] and not row['effectTurn']
                and row['pickCountMin']==0 and row['pickCountMax']==0
                and row['movePositionType']=='ProduceCardMovePositionType_Hand'
                and row['pickRangeType']=='ProducePickRangeType_All'):
                search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
                allowed={'id','produceDescriptions','cardPositionType','cardSearchTag','limitCount'}
                if (search['cardPositionType']!='ProduceCardPositionType_DeckGrave' or search['cardSearchTag']!='idol-unique'
                    or search['limitCount']!=1 or any(nondefault(v) for k,v in search.items() if k not in allowed)):
                    raise Unsupported('Hand retrieval needs exact order-zero one-card DeckGrave idol-unique search')
                setting=self.catalog.one('ExamSetting','p_exam_setting-1')
                if setting['fixMoveCardShuffleDeckEnable'] is not True:
                    raise Unsupported('Legacy MoveCard end reshuffle not ported')
                return [dict(op='fetch_tag',tag=search['cardSearchTag'])]
            if (value or count or row['effectValue2'] or row['effectTurn'] or row['pickCountMin']!=1 or row['pickCountMax']!=1
                or row['movePositionType']!='ProduceCardMovePositionType_Lost' or row['pickRangeType']!='ProducePickRangeType_Random'):
                raise Unsupported('Only fixed-one random removal to Lost is ported')
            search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
            allowed={'id','produceCardIds','cardPositionType','produceDescriptions'}
            if (search['cardPositionType']!='ProduceCardPositionType_DeckGrave' or len(search['produceCardIds'])!=1
                or any(nondefault(v) for k,v in search.items() if k not in allowed)):
                raise Unsupported('Random removal needs DeckGrave and one card ID filter')
            return [dict(op='remove_random',card_id=search['produceCardIds'][0])]
        if kind=='ExamCardSearchEffectPlayCountBuff':
            if (value<1 or count<1 or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1
                or row['pickRangeType']!='ProducePickRangeType_All'):
                raise Unsupported('Repeat effect count/duration/range')
            category,rarities=self.repeat_search(row['produceCardSearchId'])
            return [dict(op='repeat_card',category=category,rarities=rarities,extra=value,uses=count,turns=row['effectTurn'])]
        if kind=='ExamStatusEnchant':
            if value or row['effectValue2'] or count:raise Unsupported('Enchant coefficient/count')
            enchant=self.catalog.one('ProduceExamStatusEnchant',row['produceExamStatusEnchantId'])
            if any(nondefault(v) for k,v in enchant.items() if k not in {'id','assetId','produceExamTriggerId','produceExamEffectIds','produceDescriptions'}):
                raise Unsupported('Enchant fields')
            trigger=self.catalog.one('ProduceExamTrigger',enchant['produceExamTriggerId'])
            conditional=self.turn_enchant(row,enchant,trigger,overrides,seen+(key,))
            if conditional is not None:return conditional
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamPlayCountInterval']:
                plain={'id','phaseTypes','phaseValues','produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions'}
                if any(nondefault(v) for k,v in trigger.items() if k not in plain) or len(trigger['phaseValues'])!=1 or trigger['phaseValues'][0]<1:
                    raise Unsupported('Play-count interval trigger fields')
                if row['effectTurn']!=-1:raise Unsupported('Play-count interval duration not ported')
                body=[e for k in enchant['produceExamEffectIds'] for e in self.effect(k,overrides,seen+(key,))]
                return [dict(op='play_interval',interval=trigger['phaseValues'][0],effects=body)]
            multi_play=len(enchant['produceExamEffectIds'])>1 and trigger['phaseTypes']==['ProduceExamPhaseType_ExamCardPlay']
            if len(enchant['produceExamEffectIds'])!=1 and not multi_play:raise Unsupported('Multiple enchant effects not ported')
            leaf=[e for k in enchant['produceExamEffectIds'] for e in self.effect(k,overrides,seen+(key,))]
            plain={'id','phaseTypes','produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions'}
            if trigger['phaseTypes'] in (['ProduceExamPhaseType_ExamEndTurn'],['ProduceExamPhaseType_StartPlay']):
                if any(nondefault(v) for k,v in trigger.items() if k not in plain):raise Unsupported('Conditional drink phase enchant')
                duration=row['effectTurn']
                if duration!=-1 and duration<1:raise Unsupported('End/play-start enchant duration not ported')
                if trigger['phaseTypes']==['ProduceExamPhaseType_ExamEndTurn']:
                    # 回合末附魔：永久 / N 回合，正文为即时数值效果（可多条，按 master 顺序）。厳選初星ティー = 永久 好印象+1
                    if duration==-1 and len(leaf)==1 and leaf[0]['op']=='motivation':
                        return [dict(op='end_turn_motivation',value=leaf[0]['value'])]
                    if leaf and all(e['op'] in END_TURN_OPS for e in leaf):
                        return [dict(op='end_turn_effects',effects=leaf,turns=duration)]
                    raise Unsupported('End-turn enchant payload not ported')
                if len(leaf)==1 and leaf[0]['op']=='draw':
                    return [dict(op='start_play_draw',value=leaf[0]['value'],turns=duration)]
                raise Unsupported('Play-start enchant payload not ported')
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamStatusChange']:
                # 「N ターンの間、好印象が増加した時、…」（びしっとキメ顔）：无条件的状态增加触发器
                kinds={'ProduceExamEffectType_ExamReview':'impression','ProduceExamEffectType_ExamBlock':'shield',
                       'ProduceExamEffectType_ExamCardPlayAggressive':'motivation','ProduceExamEffectType_ExamLessonBuff':'focus'}
                if (any(nondefault(v) for k,v in trigger.items() if k not in plain|{'effectTypes'}) or len(trigger['effectTypes'])!=1
                    or trigger['effectTypes'][0] not in kinds):
                    raise Unsupported('Conditional status-change enchant')
                duration=row['effectTurn']
                if (duration!=-1 and duration<1) or not leaf or any(e['op'] not in END_TURN_OPS for e in leaf):
                    raise Unsupported('Status-change enchant payload/duration not ported')
                return [dict(op='status_change_effects',status=kinds[trigger['effectTypes'][0]],effects=leaf,turns=duration)]
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamStartTurn']:
                if any(nondefault(v) for k,v in trigger.items() if k not in plain):raise Unsupported('Conditional turn enchant')
                if row['effectTurn']<1 or len(leaf)!=1:
                    raise Unsupported('Turn enchant type/duration not ported')
                if leaf[0]['op']=='shield_score' and not leaf[0].get('consume_permil',0):
                    return [dict(op='shield_tick',permil=leaf[0]['permil'],ticks=row['effectTurn'])]
                if leaf[0]['op']=='impression_growth':return [dict(op='impression_tick',permil=leaf[0]['permil'],ticks=row['effectTurn'])]
                if leaf[0]['op']=='shield':return [dict(op='shield_gain_tick',value=leaf[0]['value'],motivation_scale=leaf[0].get('motivation_scale',0),ticks=row['effectTurn'])]
                raise Unsupported('Turn enchant payload not ported')
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamCardPlay']:
                permitted=plain|{'produceCardSearchId','lowerSearchCount'}
                if any(nondefault(v) for k,v in trigger.items() if k not in permitted) or trigger['lowerSearchCount']!=1:
                    raise Unsupported('Card-play enchant filter')
                search=self.catalog.one('ProduceCardSearch',trigger['produceCardSearchId'])
                allowed={'id','cardCategories','cardPositionType','produceDescriptions'}
                # 检索条件：打出中的卡（Playing），类别为メンタル或不限（輝くキミへ：p_card_search-playing）
                if (search['cardCategories'] not in (['ProduceCardCategory_MentalSkill'],[]) or search['cardPositionType']!='ProduceCardPositionType_Playing'
                    or any(nondefault(v) for k,v in search.items() if k not in allowed)):
                    raise Unsupported('Card-play enchant search')
                mental=search['cardCategories']==['ProduceCardCategory_MentalSkill']
                if row['effectTurn']!=-1:raise Unsupported('Card-play enchant type/duration')
                if len(leaf)>1:
                    # 定制后的輝くキミへ（p_card_custom-100 → enchant enc03）：「スキルカード使用時、好印象の50%分パラメータ上昇・好印象+1」
                    # 多条正文按 master 顺序在同一时点（正文之前）执行；只收不消耗的按资源得分与直接加好印象
                    if any(not (e['op']=='resource_score' and not e['consume_permil'] or e['op']=='impression') for e in leaf):
                        raise Unsupported('Card-play enchant multi-effect payload not ported')
                    return [dict(op='card_play_bonus',category='mental' if mental else 'any',effects=leaf)]
                if len(leaf)!=1:raise Unsupported('Card-play enchant type/duration')
                if mental and leaf[0]['op']=='motivation':return [dict(op='mental_motivation',value=leaf[0]['value'])]
                if leaf[0]['op']=='resource_score' and not leaf[0]['consume_permil']:
                    # 輝くキミへ：「以降、スキルカード使用時、好印象の30%分パラメータ上昇」
                    return [dict(op='card_play_score',category='mental' if mental else 'any',resource=leaf[0]['resource'],permil=leaf[0]['permil'])]
                raise Unsupported('Card-play enchant type/duration')
            raise Unsupported('Enchant phase not ported')
        if kind=='ExamEffectTimer':
            if value<1 or count!=1 or row['effectValue2'] or row['effectTurn']:raise Unsupported('Effect timer delay not ported')
            leaf=self.effect(row['chainProduceExamEffectId'],overrides,seen+(key,))
            if len(leaf)==1 and leaf[0]['op']=='draw' and value==1:return [dict(op='next_turn_draw',value=leaf[0]['value'])]
            if len(leaf)==1 and leaf[0]['op']=='upgrade_hand' and value==1:return [dict(op='next_turn_upgrade_hand')]
            if len(leaf)==1 and leaf[0]['op']=='shield_score':
                return [dict(op='delayed_shield_score',delay=value,permil=leaf[0]['permil'],consume_permil=leaf[0].get('consume_permil',0))]
            # 通用形：N 回合后回合开始时执行（与 next_turn_draw / delayed_shield_score 同一 reservations 时点）。
            # 只收不依赖手牌 / 行动数的即时数值效果；存档里尚未到期的计时器如何还原未核对。
            if leaf and all(e['op'] in DELAYABLE for e in leaf):return [dict(op='delayed_effects',delay=value,effects=leaf)]
            raise Unsupported('Effect timer payload not ported')
        if kind=='ExamExtraTurn':
            if value or count or row['effectTurn'] or row['effectValue2']:raise Unsupported('Extra turn contract')
            return [dict(op='extra_turn',value=1)]
        if kind=='ExamLessonValueMultiple' and row['effectTurn']==-1:
            if value<0 or count or row['effectValue2']:raise Unsupported('Permanent score boost contract')
            return [dict(op='permanent_score_boost',permil=value)]
        if kind=='ExamLessonValueMultiple' and row['effectTurn']>0:
            if value<0 or count or row['effectValue2']:raise Unsupported('Timed score boost contract')
            return [dict(op='timed_score_boost',permil=value,turns=row['effectTurn'])]
        if kind=='ExamReviewMultiple':
            # 好印象強化 +N‰（effectTurn 回合；-1 永久）：回合末好印象得分 × (1 + Σ/1000)。びしっとキメ顔、究極スマイル 等
            if value<1 or count or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Impression score boost contract')
            return [dict(op='impression_score_boost',permil=value,turns=row['effectTurn'])]
        if kind=='ExamBlockRestriction':
            # 元気増加無効 N ターン（ハイテンション p_card-00-men-2_014 等）：期间 ExamBlock 类元気增加为 0
            # （3.4.1 ExamEffectUtility.CalculateAddBlock 0x32464f8 IsBlockRestriction → 0）
            if value or count or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Block restriction contract')
            return [dict(op='block_restriction',turns=row['effectTurn'])]
        if kind=='ExamReviewAdditive':
            # 好印象増加量増加 +N‰（effectTurn 回合；-1 为考试结束前）。星屑センセーション（p_card-02-men-3_004）等
            if value<1 or count or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Impression gain boost contract')
            return [dict(op='impression_boost',permil=value,turns=row['effectTurn'])]
        if kind=='ExamReviewValueMultiple':
            if value<0 or count or row['effectTurn'] or row['effectValue2']:raise Unsupported('Impression multiplier contract')
            return [dict(op='impression_growth',permil=value)]
        if kind in ('ExamStaminaConsumptionDown','ExamStaminaConsumptionAdd'):
            if value or count or row['effectValue2'] or row['effectTurn']<1:raise Unsupported('Timed stamina modifier contract')
            setting=self.catalog.one('ExamSetting','p_exam_setting-1')
            if kind=='ExamStaminaConsumptionDown':
                if setting['examStaminaConsumptionDownPermil']!=500:raise Unsupported('Unverified stamina reduction setting')
                return [dict(op='stamina_down',permil=500,turns=row['effectTurn'])]
            if setting['examStaminaConsumptionAddPermil']!=1000:raise Unsupported('Unverified stamina increase setting')
            return [dict(op='stamina_up',permil=2000,turns=row['effectTurn'])]
        if kind=='ExamBlockAddMultipleAggressive':
            if value<1 or row['effectTurn'] or count!=1 or not 0<=row['effectValue2']<=1000000:
                raise Unsupported('Scaled motivation shield coefficient/count')
            return [dict(op='shield_motivation_multiple',value=value,motivation_bonus_permil=row['effectValue2'])]
        if kind=='ExamLessonDependBlock':
            if value<0 or row['effectTurn'] or count!=1 or not 0<=row['effectValue2']<=1000:
                raise Unsupported('shield score coefficient/count')
            return [dict(op='shield_score',permil=value,consume_permil=row['effectValue2'])]
        if kind in ('ExamLessonDependExamReview','ExamLessonDependExamCardPlayAggressive'):
            if value<0 or row['effectTurn'] or count!=1 or not 0<=row['effectValue2']<=1000:
                raise Unsupported('Resource-dependent score coefficient/count')
            return [dict(op='resource_score',resource='impression' if kind=='ExamLessonDependExamReview' else 'motivation',
                         permil=value,consume_permil=row['effectValue2'])]
        if kind in ('ExamStaminaRecoverMultiple','ExamStaminaReduce'):
            if not 0<value<=1000 or row['effectTurn'] or row['effectValue2'] or count:raise Unsupported('HP-percent effect fields')
            return [dict(op='recover_permil' if kind=='ExamStaminaRecoverMultiple' else 'hp_loss_permil',permil=value)]
        if kind=='ExamStaminaConsumptionDownFix':
            if value<1 or row['effectTurn']!=-1 or row['effectValue2'] or count:raise Unsupported('Only permanent stamina fix reduction is ported')
            return [dict(op='stamina_down_fix',value=value)]
        if kind=='ExamAntiDebuff':
            if value or row['effectTurn'] or row['effectValue2'] or count<1:raise Unsupported('Anti-debuff contract')
            return [dict(op='anti_debuff',value=count)]
        if kind=='ExamPlayableValueAdd':
            if value or row['effectTurn'] or row['effectValue2'] or count<1:raise Unsupported('Action-add field contract')
            return [dict(op='extra_action',value=count)]
        if value<0 or row['effectTurn'] or row['effectValue2']:
            raise Unsupported('effect timer/secondary coefficient:'+kind)
        ops={'ExamLesson':'score','ExamLessonFix':'score','ExamBlock':'shield','ExamBlockFix':'shield',
             'ExamLessonBuff':'focus','ExamCardPlayAggressive':'motivation','ExamCardDraw':'draw',
             'ExamPlayableValueAdd':'extra_action','ExamStaminaRecoverFix':'recover','ExamReview':'impression',
             'ExamStaminaReduceFix':'hp_loss'}
        if kind not in ops:raise Unsupported('effect type:'+kind)
        if kind in ('ExamLesson','ExamLessonFix'):
            if not 1<=count<=20:raise Unsupported('score hit count:'+str(count))
        elif count not in (0,1):raise Unsupported('effect count:'+kind)
        effect=dict(op=ops[kind],value=value)
        if kind=='ExamLesson':effect['focus_scale']=1
        if kind=='ExamBlock':effect['motivation_scale']=1
        return [deepcopy(effect) for _ in range(count if kind in ('ExamLesson','ExamLessonFix') else 1)]

    def repeat_search(self,search_id):
        """「次に使用した…スキルカードの効果をもう1回発動」的检索条件 → (category, rarities)。存档还原 PlayCountBuffStatusEffect 的 _searchId 也用它。"""
        search=self.catalog.one('ProduceCardSearch',search_id)
        allowed={'id','cardCategories','cardRarities','cardPositionType','produceDescriptions'}
        if (search['cardPositionType']!='ProduceCardPositionType_Playing'
            or any(nondefault(v) for k,v in search.items() if k not in allowed)):
            raise Unsupported('Repeat search needs Playing with only category/rarity filters')
        categories=search['cardCategories']
        if categories not in ([],['ProduceCardCategory_ActiveSkill'],['ProduceCardCategory_MentalSkill']):
            raise Unsupported('Repeat category combination not ported')
        category='any' if not categories else ('active' if categories[0].endswith('_ActiveSkill') else 'mental')
        return category,list(search['cardRarities'])

    def free_stamina_search(self,search_id):
        """消費体力を0にする 的检索条件：DeckAll（任何位置）+ 可选类别 / 指定卡 ID。存档还原也用它把 _searchId 编译成条件。"""
        search=self.catalog.one('ProduceCardSearch',search_id)
        allowed={'id','cardCategories','produceCardIds','cardPositionType','produceDescriptions'}
        if search['cardPositionType']!='ProduceCardPositionType_DeckAll' or any(nondefault(v) for k,v in search.items() if k not in allowed):
            raise Unsupported('Free-stamina search shape not ported')
        categories={(): 'any',('ProduceCardCategory_ActiveSkill',):'active',('ProduceCardCategory_MentalSkill',):'mental'}
        if tuple(search['cardCategories']) not in categories:raise Unsupported('Free-stamina category combination')
        return dict(category=categories[tuple(search['cardCategories'])],card_ids=list(search['produceCardIds']))

    def create_hand_pool(self,row):
        """「ランダムな強化済みスキルカード（SSR）を、手札に生成」（初星スペシャル青汁 pdrink_00-3-005 等）。

        只移植固定生成 1 张、从 RandomPool 随机取 1 张进手牌的形状。候选来自 ProduceCardPool（按 ratio 份展开，
        见 mechanics/HIF应援棒生成.md：名字里的 RandomPool 实际读 ProduceCardPool）。
        原生 ExamEffectUtility.GetSearchCardList 在取出 RandomPool 候选后逐张检查计划：卡的 PlanType 为 Common，
        或等于考试 ExamParameterModel.PlanType 才保留（3.4.1 反汇编 0x323f684..0x323f72c）。计划在这里不知道，
        编译结果保留全部候选与各自计划，由组装对局文档时按偶像计划筛选（card_mutations.admit_pool_candidates）。
        假设：计划筛选在 limitCount=1 截断之前（否则可能生成 0 张，与实机「一定得到一张」不符）。"""
        value,count=row['effectValue1'],row['effectCount']
        if (value!=1 or count or row['effectValue2'] or row['effectTurn'] or row['pickCountMin']!=1 or row['pickCountMax']!=1
            or row['movePositionType']!='ProduceCardMovePositionType_Hand' or row['pickRangeType']!='ProducePickRangeType_Random'):
            raise Unsupported('Only fixed-one random-pool creation to Hand is ported')
        search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
        allowed={'id','orderType','cardPositionType','produceCardRandomPoolId','produceCardPoolId','limitCount','produceDescriptions'}
        if (search['cardPositionType']!='ProduceCardPositionType_RandomPool' or search['orderType']!='ProduceCardOrderType_Random'
            or search['limitCount']!=1 or not search['produceCardPoolId'] or search['produceCardRandomPoolId']!=search['produceCardPoolId']
            or any(nondefault(v) for k,v in search.items() if k not in allowed)):
            raise Unsupported('Random-pool creation search shape not ported')
        pool=self.catalog.one('ProduceCardPool',search['produceCardPoolId'])
        if set(pool)!={'id','produceCardRatios'} or not pool['produceCardRatios']:raise Unsupported('Unsupported ProduceCardPool shape')
        candidates=[]
        for entry in pool['produceCardRatios']:
            if set(entry)!={'id','upgradeCount','ratio'} or type(entry['ratio']) is not int or entry['ratio']<1:
                raise Unsupported('Invalid random-pool row')
            card=self.catalog.card(entry['id'],entry['upgradeCount'])
            candidates.append(dict(definition_id=entry['id']+'@'+str(entry['upgradeCount']),plan=card['planType'],weight=entry['ratio']))
        return [dict(op='create_hand_pool',candidates=candidates)]

    HP_FIELDS={'ProduceExamFieldStatusType_StaminaLessMultiple':'hp_permil_max',
               'ProduceExamFieldStatusType_StaminaUpMultiple':'hp_permil_min'}

    STATUS_FIELDS={'ProduceExamFieldStatusType_ReviewUp':'impression','ProduceExamFieldStatusType_BlockUp':'shield',
                   'ProduceExamFieldStatusType_CardPlayAggressiveUp':'motivation','ProduceExamFieldStatusType_LessonBuffUp':'focus'}

    def field_requirements(self,trigger,allow_status=False):
        """HP-ratio trigger fields → requires.

        allow_status（道具钩子用）：好印象 / 元気 / やる気 / 集中 が N 以上（XxxUp），以及 Not 检查（「元気が30以下」= Not BlockUp 31）。"""
        types,values=trigger['fieldStatusTypes'],trigger['fieldStatusValues']
        checks=trigger.get('fieldStatusCheckTypes') or []
        if len(types)!=len(values):raise Unsupported('Trigger field status not ported')
        if checks and (not allow_status or len(checks)!=len(types) or any(c!='ProduceExamTriggerCheckType_Not' for c in checks)):
            raise Unsupported('Trigger field check not ported')
        result={}
        for t,v in zip(types,values):
            if t in self.HP_FIELDS and not checks:
                if type(v) is not int or not 0<v<=1000:raise Unsupported('HP-ratio trigger value')
                result[self.HP_FIELDS[t]]=v
            elif allow_status and t in self.STATUS_FIELDS:
                if type(v) is not int or v<0:raise Unsupported('Status trigger value')
                result[self.STATUS_FIELDS[t]+('_below' if checks else '_min')]=v
            else:raise Unsupported('Trigger field status not ported')
        return result

    def turn_enchant(self,row,enchant,trigger,overrides,seen):
        """Turn-start enchant with HP-ratio condition and/or several payloads (エウレカ！ etc.).

        Plain single-payload ExamStartTurn enchants keep their earlier specialised ops; this only takes
        the shapes that were previously rejected."""
        if trigger['phaseTypes']!=['ProduceExamPhaseType_ExamStartTurn']:return None
        if not trigger['fieldStatusTypes'] and len(enchant['produceExamEffectIds'])==1 and row['effectTurn']!=-1:return None
        plain={'id','phaseTypes','fieldStatusTypes','fieldStatusValues','produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions'}
        if any(nondefault(v) for k,v in trigger.items() if k not in plain):raise Unsupported('Conditional turn enchant fields')
        if row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Conditional turn enchant duration')
        body=[e for k in enchant['produceExamEffectIds'] for e in self.effect(k,overrides,seen)]
        allowed={'score','shield','shield_score','recover','recover_permil','hp_loss','hp_loss_permil','motivation','impression','draw'}
        if any(e['op'] not in allowed or e['op']=='shield_score' and e.get('consume_permil') for e in body):
            raise Unsupported('Conditional turn enchant payload not ported')
        return [dict(op='conditional_tick',requires=self.field_requirements(trigger),effects=body,ticks=row['effectTurn'])]

    def requirement(self,key,play_effect=False):
        if not key:return {}
        row=self.catalog.one('ProduceExamTrigger',key)
        permitted={'id','phaseTypes','fieldStatusTypes','fieldStatusValues','lessonType',
                   'produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions','fieldStatusProduceCardSearchIds',
                   'fieldStatusCheckTypes','phaseValues'}
        if row.get('phaseValues') not in (None,[],[0]):raise Unsupported('card use trigger compound:'+key)
        negated=row.get('fieldStatusCheckTypes') or []
        if negated and negated!=['ProduceExamTriggerCheckType_Not']:raise Unsupported('card use trigger compound:'+key)
        if row.get('fieldStatusProduceCardSearchIds') and row['fieldStatusProduceCardSearchIds']!=['p_card_search-trouble-not_lost']:
            raise Unsupported('card use trigger compound:'+key)
        phases=[['ProduceExamPhaseType_None']]
        if play_effect:phases.append(['ProduceExamPhaseType_ExamCardPlay'])
        elif row['phaseTypes']==['ProduceExamPhaseType_ExamStartTurn'] and row['fieldStatusTypes'] in (
                ['ProduceExamFieldStatusType_StaminaUpMultiple'],['ProduceExamFieldStatusType_StaminaLessMultiple'],
                ['ProduceExamFieldStatusType_TurnProgressUp'],['ProduceExamFieldStatusType_NoBlock']):
            # 「…の場合、使用可」（ご指導ご鞭撻、ストレッチ談議、おアツイ視线）的使用条件 trigger 带 ExamStartTurn 阶段；
            # 假设：使用条件按出牌时的当前状态判定，阶段只是 trigger 复用的元数据（未用实机核对）
            phases.append(['ProduceExamPhaseType_ExamStartTurn'])
        if row['phaseTypes'] not in phases or suffix(row['lessonType'])!='Unknown':
            raise Unsupported('card use trigger phase:'+key)
        if any(nondefault(value) for name,value in row.items() if name not in permitted):
            raise Unsupported('card use trigger compound:'+key)
        mapping={'ProduceExamFieldStatusType_LessonBuffUp':'focus_min',
                 'ProduceExamFieldStatusType_CardPlayAggressiveUp':'motivation_min',
                 'ProduceExamFieldStatusType_BlockUp':'shield_min','ProduceExamFieldStatusType_ReviewUp':'impression_min'}
        if row['fieldStatusTypes']==['ProduceExamFieldStatusType_CardSearchCountUp'] and len(row['fieldStatusValues'])==1:
            # 除外以外にあるトラブルカードが N 枚以上（がむしゃら）；检索条件已在上面限定为 p_card_search-trouble-not_lost
            if row.get('fieldStatusProduceCardSearchIds')!=['p_card_search-trouble-not_lost']:raise Unsupported('card use trigger status:'+key)
            return {'trouble_not_lost_min':row['fieldStatusValues'][0]}
        if row['fieldStatusTypes']==['ProduceExamFieldStatusType_NoBlock'] and not row['fieldStatusValues'] and not negated:
            return {'no_shield':0}  # おアツイ視線「元気が0の場合」
        if len(row['fieldStatusTypes'])!=1 or len(row['fieldStatusValues'])!=1:raise Unsupported('card use trigger status:'+key)
        kind,value=row['fieldStatusTypes'][0],row['fieldStatusValues'][0]
        if negated:
            # ロマンチック「好印象が8以下の場合」= Not(ReviewUp 9)
            if kind!='ProduceExamFieldStatusType_ReviewUp':raise Unsupported('card use trigger status:'+key)
            return {'impression_below':value}
        if kind in self.HP_FIELDS:
            # 「体力が50%以上の場合」（ファーストステップ、執念キャッチャー）：按出牌前（付费前）的体力判定，与道具 HP 条件同口径
            if type(value) is not int or not 0<value<=1000:raise Unsupported('HP-ratio trigger value')
            return {self.HP_FIELDS[kind]:value}
        if kind=='ProduceExamFieldStatusType_TurnProgressUp':return {'turn_progress_min':value}
        if kind=='ProduceExamFieldStatusType_NoBlock':return {'no_shield':0}  # おアツイ視線「元気が0の場合」  # 頭上に注意！「3ターン目以降」= 已过 2 回合
        if kind not in mapping:
            raise Unsupported('card use trigger status:'+key)
        return {mapping[row['fieldStatusTypes'][0]]:row['fieldStatusValues'][0]}

    @staticmethod
    def cost(row):
        cost=dict(hp=row['forceStamina'],shieldable=row['stamina'])
        status_cost={'ExamLessonBuff':'focus','ExamCardPlayAggressive':'motivation','ExamReview':'impression'}
        kind=suffix(row['costType'])
        if kind in status_cost:cost[status_cost[kind]]=row['costValue']
        elif kind!='Unknown' or row['costValue']:raise Unsupported('status resource cost:'+kind)
        return cost

    def card(self,card_id,upgrade=0,customizations=None):
        row,overrides,custom=resolve(self.catalog,card_id,upgrade,customizations)
        # Card.order is a 64-bit library display key, NOT ExamCardPool.order.
        # It must not enter deterministic deck sorting.
        unsupported_flags=['isRestrict','produceCardStatusEnchantId',
                           'moveProduceExamEffectIds','moveProduceExamTriggerIds']
        for key in unsupported_flags:
            if row.get(key):raise Unsupported('card semantic field:'+key)
        if suffix(row['moveEffectTriggerType'])!='Unknown':raise Unsupported('card move trigger')
        requirements=self.requirement(row['playProduceExamTriggerId'])
        cost=self.cost(row)
        destination=suffix(row['playMovePositionType'])
        if destination not in ('Lost','Grave'):raise Unsupported('card move destination:'+destination)
        effects=[]
        for entry in row['playEffects']:
            if entry['isOncePlayEffect']:raise Unsupported('once-only sub-effect')
            if set(entry)-{'produceExamTriggerId','produceExamEffectId','isOncePlayEffect','hideIcon'}:
                raise Unsupported('unknown play effect fields')
            body=self.effect(entry['produceExamEffectId'],overrides)
            condition=self.requirement(entry['produceExamTriggerId'],play_effect=True)
            if condition:effects.append(dict(op='conditional',requires=condition,effects=body))
            else:effects.extend(body)
        role='bridge'
        if any(e['op'] in ('draw','extra_action','extra_turn') for e in effects):role='engine'
        elif any(e['op'] in ('focus','motivation','shield','impression','permanent_score_boost') for e in effects):role='builder'
        elif any(e['op'] in ('score','shield_score','resource_score') for e in effects):role='finisher'
        if suffix(row['category'])=='Trouble':role='trouble'
        definition=dict(name=row['name'],role=role,once=destination=='Lost',initial=row['isInitial'],order=0,
                        category={'ActiveSkill':'active','MentalSkill':'mental','Trouble':'trouble'}.get(suffix(row['category']),'other'),
                        rarity=row['rarity'],search_tag=row['searchTag'],
                        trouble=role=='trouble',end_turn_lost=row['isEndTurnLost'],
                        cost=cost,requires=requirements,effects=effects,
                        provenance=dict(kind='master',commit=self.catalog.info['commit'],table='ProduceCard',
                                        card_id=card_id,upgrade=upgrade,customize_ids=[r['id'] for r in custom['customizations']],
                                        customization=custom))
        validate_definition(definition)
        return definition

    def drink(self,drink_id):
        row=self.catalog.one('ProduceDrink',drink_id);effects=[]
        for key in row['produceDrinkEffectIds']:
            entry=self.catalog.one('ProduceDrinkEffect',key)
            if entry['produceEffectId']:raise Unsupported('out-of-battle drink effect')
            effects.extend(self.effect(entry['produceExamEffectId']))
        definition=dict(name=row['name'],role=('fetch' if any(e['op']=='fetch_tag' for e in effects) else 'draw' if any(e['op']=='draw' for e in effects) else 'ordinary'),effects=effects,
                        provenance=dict(kind='master',commit=self.catalog.info['commit'],table='ProduceDrink',drink_id=drink_id))
        validate_definition(definition)
        return definition

    def coverage(self):
        cards=[];reasons=Counter();base=set();compatible=set();plans={}
        for row in self.catalog.rows('ProduceCard'):
            record=dict(card_id=row['id'],upgrade=row['upgradeCount'],name=row['name'],plan=row['planType'],hidden=row['libraryHidden'])
            base.add(row['id']);group=plans.setdefault(row['planType'],dict(total=0,executable=0));group['total']+=1
            try:
                self.card(row['id'],row['upgradeCount']);record['status']='executable';compatible.add(row['id']);group['executable']+=1
            except Unsupported as error:
                record.update(status='blocked',reason=str(error));reasons[str(error)]+=1
            cards.append(record)
        drinks=[]
        for row in self.catalog.rows('ProduceDrink'):
            try:self.drink(row['id']);drinks.append(dict(id=row['id'],status='executable'))
            except Unsupported as error:drinks.append(dict(id=row['id'],status='blocked',reason=str(error)))
        return dict(commit=self.catalog.info['commit'],scope='bounded neutral status/effect sandbox; all raw versions collected, subset executable',
                    card_base_ids=len(base),card_variants=len(cards),executable_variants=sum(c['status']=='executable' for c in cards),
                    base_ids_with_executable_variant=len(compatible),blocked_by_reason=dict(reasons.most_common()),by_plan=plans,
                    cards=cards,drinks=drinks,customization='bounded current-level raw grow operations implemented; unsupported types/overwrites block')


def validate_master_document(document):
    provenance=document['provenance'];adapter=MasterAdapter()
    try:
        if provenance.get('commit')!=adapter.catalog.info['commit'] or provenance.get('scope')!='neutral-effect-sandbox':
            raise Unsupported('Master-derived sandbox needs a matching pinned snapshot and scope')
        setting=adapter.catalog.one('ExamSetting','p_exam_setting-1')
        if document.get('skip_recovery',setting['examTurnEndRecoveryStamina'])!=setting['examTurnEndRecoveryStamina']:
            raise Unsupported('Skip recovery diverges from pinned ExamSetting')
        if 'loadout' in document:
            from .passives import compile_loadout
            if document.get('passives')!=compile_loadout(adapter.catalog,document['loadout']):raise Unsupported('Passive bundle diverges from source loadout')
            from .baton import candidate_definitions
            for key,expected in candidate_definitions(adapter,document['passives']).items():
                if document['definitions'].get(key)!=expected:raise Unsupported('Missing or altered initializer candidate definition')
        elif document.get('passives') or 'passives' in document:
            raise Unsupported('Master passives require explicit source loadout')
        for table,definitions in [('ProduceCard',document['definitions']),('ProduceDrink',document.get('drink_definitions',{}))]:
            for definition in definitions.values():
                source=definition.get('provenance',{})
                if not isinstance(source,dict) or source.get('table')!=table:raise Unsupported('Missing raw master origin')
                expected=adapter.card(source['card_id'],source['upgrade'],source.get('customization',{}).get('customizations')) if table=='ProduceCard' else adapter.drink(source['drink_id'])
                if definition!=expected:raise Unsupported('Resolved definition diverges from pinned master adapter')
        if 'loadout' in document:validate_loadout_plans(adapter,document)
        from .growth_items import validate_battle_start
        validate_battle_start(document,adapter.catalog)
        from .support_upgrades import validate
        validate(document,adapter.catalog)
        from .exam_upgrades import validate as validate_exam_upgrades
        validate_exam_upgrades(document,adapter.catalog)
    finally:adapter.catalog.db.close()


def validate_loadout_plans(adapter,document):
    plan=adapter.catalog.one('IdolCard',document['loadout']['idol_card_id'])['planType']
    for definition in document['definitions'].values():
        origin=definition['provenance'];row=adapter.catalog.card(origin['card_id'],origin['upgrade'])
        if row['planType'] not in ('ProducePlanType_Common',plan):raise Unsupported('Card plan incompatible with passive loadout')
    for definition in document.get('drink_definitions',{}).values():
        row=adapter.catalog.one('ProduceDrink',definition['provenance']['drink_id'])
        if row['planType'] not in ('ProducePlanType_Common',plan):raise Unsupported('Drink plan incompatible with passive loadout')


def _sandbox_from_adapter(adapter,card_specs,turns=6,drinks=None,loadout=None):
    definitions={};cards={}
    if type(turns) is not int or turns<1:raise Unsupported('Positive turn count required')
    if not isinstance(card_specs,list) or not card_specs:raise Unsupported('Need nonempty card spec list')
    for index,spec in enumerate(card_specs):
        if not isinstance(spec,dict) or set(spec)-{'card_id','upgrade','customizations','name','plan','hidden','status','reason'}:
            raise Unsupported('Unknown card spec field')
        card_id,upgrade=spec['card_id'],spec.get('upgrade',0);key=card_id+'@'+str(upgrade)
        choices=spec.get('customizations',[])
        if choices:key+=':'+hashlib.sha256(json.dumps(choices,sort_keys=True).encode()).hexdigest()
        definitions[key]=deepcopy(adapter.card(card_id,upgrade,choices));cards['c'+str(index)]=key
    setting=adapter.catalog.one('ExamSetting','p_exam_setting-1')
    drink_definitions={d:adapter.drink(d) for d in (drinks or [])}
    result=dict(schema_version=1,id='master-neutral-sandbox',
                provenance=dict(kind='master-derived',scope='neutral-effect-sandbox',commit=adapter.catalog.info['commit'],
                                warning='Real resolved cards; neutral explicit turn bonuses; not an HIF win-rate model'),
                definitions=definitions,cards=cards,zones=dict(deck=list(cards)),hand_limit=setting['handLimit'],skip_recovery=setting['examTurnEndRecoveryStamina'],
                state=dict(hp=30,max_hp=36),turns=[dict(draw=setting['turnStartDistribute'],actions=1,bonus_permil=1000) for _ in range(turns)],
                drink_definitions=drink_definitions,drinks={str(i):d for i,d in enumerate(drinks or [])},
                goal=dict(target_score=100))
    if loadout is not None:
        from .passives import compile_loadout
        result['loadout']=deepcopy(loadout);result['passives']=compile_loadout(adapter.catalog,loadout)
        from .baton import candidate_definitions
        result['definitions'].update(candidate_definitions(adapter,result['passives']))
        validate_loadout_plans(adapter,result)
    # Creating a card requires its complete rule definition before execution;
    # no runtime catalog lookup or silent neutral placeholder is allowed.
    from .card_mutations import targets
    pending=list(targets([result['definitions'],result['drink_definitions'],result.get('passives',[])]))
    while pending:
        key=pending.pop()
        if key in result['definitions']:continue
        identity,grade=key.rsplit('@',1)
        definition=adapter.card(identity,int(grade));result['definitions'][key]=definition
        pending.extend(targets(definition))
    from .card_mutations import admit_pool_candidates
    plan=adapter.catalog.one('IdolCard',loadout['idol_card_id'])['planType'] if loadout else None
    admit_pool_candidates(adapter,result['definitions'],[result['definitions'],result['drink_definitions'],result.get('passives',[])],plan)
    from .exam_upgrades import bind as bind_exam_upgrades
    return bind_exam_upgrades(result,adapter.catalog)


def sandbox(card_specs,turns=6,drinks=None,loadout=None):
    adapter=MasterAdapter()
    try:return _sandbox_from_adapter(adapter,card_specs,turns,drinks,loadout)
    finally:adapter.catalog.db.close()
