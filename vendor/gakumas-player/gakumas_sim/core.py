"""Deterministic single-deck environment with an explicit effect subset.

RNG/draw follow prior client research. Effect coefficients, turn schedules and
timer semantics are caller-supplied fixture data, not a complete native engine.
"""
from copy import deepcopy
import math
import struct


class Unsupported(ValueError):
    pass


class IllegalAction(ValueError):
    pass


class NativeRandom:
    def __init__(self, state):
        if type(state) is not int or not 0 <= state < 2**32:
            raise Unsupported('RNG state must be uint32')
        self.state, self.calls = state, 0

    def integer(self, low, high):
        if not low <= high:
            raise Unsupported('Invalid RNG interval')
        return low + ((self._advance() * (high - low)) >> 32)

    def _advance(self):
        old = self.state
        x = (old ^ (old << 13)) & 0xffffffff
        x = (x ^ (x >> 17)) & 0xffffffff
        self.state = (x ^ (x << 5)) & 0xffffffff
        self.calls += 1
        return old

    def signed(self):
        return self._advance()-2**31

    def insert_index(self,count):
        return self.integer(0,count)

    def pick_one(self,values):
        if not values:return []
        count=self.integer(1,2) # Native samples even when min == max.
        ranked=sorted(((self.signed(),i,value) for i,value in enumerate(values)),key=lambda x:x[0])
        return [row[2] for row in ranked[:count]]

    def shuffle(self, values):
        for n in range(len(values), 1, -1):
            j = self.integer(0, n)
            values[j], values[n-1] = values[n-1], values[j]


_F32 = struct.Struct('<f')


def f32(value):
    return _F32.unpack(_F32.pack(value))[0]


def _ratio(value, permil):
    # Ceil branch in the prior GetRatioEffectIntValue/battle-bonus research.
    return math.ceil(f32(f32(value) * f32(f32(permil) / f32(1000))) - 0.0001)


_RATIO_CACHE = {}


def ratio(value, permil):
    # 提速（2026-11-04）：纯函数，整数参数按值缓存（体力消耗 / 倍率的组合很少），结果与 _ratio 相同
    if type(value) is int and type(permil) is int:
        key = (value, permil)
        r = _RATIO_CACHE.get(key)
        if r is None:
            if len(_RATIO_CACHE) > 200_000:
                _RATIO_CACHE.clear()
            r = _RATIO_CACHE[key] = _ratio(value, permil)
        return r
    return _ratio(value, permil)


def totals(rounds):
    result = {}
    for round_ in rounds:
        for row in round_:
            if row['score'] < 0:
                raise Unsupported('Negative score')
            key = row['participant_id']
            result[key] = result.get(key, 0) + row['score']
    ids = sorted(result, key=lambda i: (-result[i], i != 0, i))
    return [dict(participant_id=i, score=result[i], rank=n+1) for n, i in enumerate(ids)]


EFFECT_FIELDS = {
    'score': {'op', 'value', 'focus_scale'},
    'shield': {'op', 'value', 'motivation_scale'},
    'shield_motivation_multiple': {'op','value','motivation_bonus_permil'},
    'motivation': {'op', 'value'}, 'focus': {'op', 'value'},
    'draw': {'op', 'value'}, 'extra_action': {'op', 'value'},
    'recover': {'op', 'value'},
    'shield_score': {'op', 'permil', 'consume_permil'},
    'resource_score': {'op','resource','permil','consume_permil'},
    'score_boost': {'op', 'permil', 'turns'},
    'timed_score_boost': {'op','permil','turns'},
    'end_turn_motivation': {'op','value'},
    'start_play_draw': {'op','value','turns'},  # 之后 turns 次（-1 永久）ターン開始後抽牌
    'status_change_effects': {'op','status','effects','turns'},  # 「N ターンの間、好印象 / 元気 / やる気 / 集中が増加した時、…」（ExamStatusChange 附魔）；turns=-1 永久
    'end_turn_effects': {'op','effects','turns'},  # 「以降 / N ターンの間、ターン終了時、…」附魔：每个回合末执行 effects，turns=-1 永久（厳選初星ティー）
    'shield_tick': {'op', 'permil', 'ticks'},
    'remove_trouble': {'op', 'value'},
    'next_turn_draw': {'op', 'value'},
    'mental_motivation': {'op', 'value'},
    'card_play_score': {'op', 'category', 'resource', 'permil'},
    'card_play_bonus': {'op', 'category', 'effects'},  # 出牌附魔多条正文（定制輝くキミへ）
    'impression': {'op','value'},
    'impression_growth': {'op','permil'},
    'impression_boost': {'op','permil','turns'},
    'block_restriction': {'op','turns'},
    'impression_score_boost': {'op','permil','turns'},
    'delayed_effects': {'op','delay','effects'},  # 「N ターン後、…」（ExamEffectTimer 通用形）：第 turn+delay 回合开始时执行  # 好印象強化 +permil‰（turns 回合，-1 永久）：回合末好印象得分加成  # 元気増加無効 turns 回合（-1 永久）：ハイテンション  # 好印象増加量増加 +permil‰（turns 回合，-1 为考试结束前）：星屑センセーション
    'extra_turn': {'op','value'},
    'permanent_score_boost': {'op','permil'},
    'impression_tick': {'op','permil','ticks'},
    'shield_gain_tick': {'op','value','motivation_scale','ticks'},
    'delayed_shield_score': {'op','permil','consume_permil','delay'},
    'conditional': {'op','requires','effects'},
    'stamina_down': {'op','permil','turns'},
    'stamina_up': {'op','permil','turns'},
    'anti_debuff': {'op','value'},
    'stamina_down_fix': {'op','value'},  # 消費体力削減 N（永久）：卡的体力费用在倍率之后再减 N  # 低下状態無効 N 层：每次施加低下状態（本引擎为消費体力増加）消耗 1 层并抵消
    'hp_loss': {'op','value'},
    'repeat_card': {'op','category','rarities','extra','uses','turns'},
    'remove_random': {'op','card_id'},
    'upgrade_hand': {'op'},
    'next_turn_upgrade_hand': {'op'},
    'fetch_tag': {'op','tag'},
    'discard_redraw': {'op'},
    'create_deck_random': {'op','definition_id'},
    'free_stamina': {'op','search','uses'},  # 之后 uses 次打出符合 search 的卡体力消耗为 0（勝利をつかめ！）
    'create_hand_pool': {'op','candidates'},  # 随机池生成到手牌（初星スペシャル青汁）：candidates=[{definition_id, plan, weight}]
    # Campus 広 extensions (feat_matto_campus_sim)
    'recover_permil': {'op','permil'},          # ExamStaminaRecoverMultiple: ceil(max_hp*permil)
    'hp_loss_permil': {'op','permil'},          # ExamStaminaReduce: floor(max_hp*permil), live-calibrated
    'conditional_tick': {'op','requires','effects','ticks'},  # turn-start enchant; ticks -1 = whole exam
    'play_interval': {'op','interval','effects'},  # ExamPlayCountInterval
}
# 回合末附魔正文允许的即时数值效果（不含抽牌 / 追加回合 / 生成牌：回合末手牌随后弃掉，追加回合时点未核对）
END_TURN_OPS = {'score','shield','motivation','impression','focus','recover','resource_score','shield_score','impression_growth',
                'hp_loss','recover_permil','hp_loss_permil','anti_debuff'}
# ExamStatusChange 附魔监听的状态：好印象（ExamReview）/ 元気（ExamBlock，带やる気加成的那类）/ やる気 / 集中
STATUS_CHANGE_KINDS = ('impression','shield','motivation','focus')
RARITIES = {'ProduceCardRarity_'+r for r in ('N','R','Sr','Ssr','Legend')}
CONDITIONS = {'hp_min', 'shield_min', 'focus_min', 'motivation_min', 'hand_trouble_min','impression_min',
              'hp_permil_max','hp_permil_min','trouble_not_lost_min',
              'turn_progress_min','impression_below','no_shield','shield_below','motivation_below','focus_below'}  # N ターン目以降（已过回合数 ≥ N）；好印象が N-1 以下（Not ReviewUp N）  # trouble_not_lost_min：除外以外的麻烦卡张数（がむしゃら）
LESSONS = ('vocal','dance','visual')
ATTRIBUTE_LESSON = {'Vo':'vocal','Da':'dance','Vi':'visual'}


class _LazyConds(dict):
    """Battle._condition_values 的结果：hand_trouble_min / trouble_not_lost_min 第一次被读时才扫牌区（结果和原来一样）。"""
    __slots__=('_battle',)
    _LAZY=('hand_trouble_min','trouble_not_lost_min')

    def __init__(self,battle,**kw):
        super().__init__(**kw)
        self._battle=battle

    def _fill(self,key):
        b=self._battle
        if key=='hand_trouble_min':
            v=sum(b.definition(c).get('trouble',False) for c in b.zones['hand'])
        else:
            v=sum(b.definition(c).get('trouble',False) for z,cards in b.zones.items() if z!='lost' for c in cards)
        dict.__setitem__(self,key,v)
        return v

    def __missing__(self,key):
        if key in self._LAZY:
            return self._fill(key)
        raise KeyError(key)

    def get(self,key,default=None):
        if key in self._LAZY and not dict.__contains__(self,key):
            return self._fill(key)
        return dict.get(self,key,default)

    def __contains__(self,key):
        return key in self._LAZY or dict.__contains__(self,key)

    def materialize(self):
        for k in self._LAZY:
            self.get(k)
        return dict(self)


def meets(values, requires):
    """Requirement check. hp_permil_* compare HP to max HP exactly (StaminaLess/UpMultiple, 以下/以上)."""
    for k, v in requires.items():
        if k == 'hp_permil_max':
            if values['hp_min']*1000 > v*values['max_hp']:return False
        elif k == 'hp_permil_min':
            if values['hp_min']*1000 < v*values['max_hp']:return False
        elif k == 'lesson':
            if values.get('lesson') != v:return False
        elif k == 'no_shield':
            if values.get('shield_min') is None or values['shield_min'] != 0:return False
        elif k.endswith('_below'):
            # X が v 未満（Not XxxUp v）：好印象が8以下 = impression_below 9
            current=values.get(k[:-len('_below')]+'_min')
            if current is None or current >= v:return False
        elif values.get(k) is None or values[k] < v:return False  # 取值方没提供的条件按不满足（预测里保守）
    return True
DEFINITION_FIELDS = {'name', 'role', 'once', 'initial', 'order', 'trouble',
                     'cost', 'requires', 'effects', 'provenance', 'end_turn_lost','category','rarity','search_tag'}


def validate_requirements(requirements):
    if not isinstance(requirements,dict) or set(requirements)-CONDITIONS-{'lesson'}:
        raise Unsupported('Unknown condition')
    if 'lesson' in requirements and requirements['lesson'] not in LESSONS:
        raise Unsupported('Unknown lesson condition')
    if any(type(v) is not int or v<0 for k,v in requirements.items() if k!='lesson'):
        raise Unsupported('Conditions require nonnegative integers')
    if any(requirements.get(k,0)>1000 for k in ('hp_permil_max','hp_permil_min')):
        raise Unsupported('HP permil condition above 1000')


def validate_effects(effects,depth=0):
    if not isinstance(effects,list) or depth>8:raise Unsupported('Invalid/deep effect list')
    for effect in effects:
        if not isinstance(effect,dict) or effect.get('op') not in EFFECT_FIELDS or set(effect)-EFFECT_FIELDS[effect['op']]:
            raise Unsupported('Unknown effect: '+repr(effect))
        if effect['op']=='conditional':
            if set(effect)!={'op','requires','effects'}:raise Unsupported('Conditional effect needs requirements and body')
            validate_requirements(effect['requires']);validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='conditional_tick':
            if set(effect)!=EFFECT_FIELDS['conditional_tick'] or type(effect['ticks']) is not int or not (effect['ticks']==-1 or effect['ticks']>=1):
                raise Unsupported('Conditional turn enchant needs requirements, body and ticks (-1 or positive)')
            validate_requirements(effect['requires']);validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='start_play_draw':
            if (set(effect)!=EFFECT_FIELDS['start_play_draw'] or type(effect['value']) is not int or effect['value']<0
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)):
                raise Unsupported('Start-play draw needs a nonnegative count and turns (-1 or positive)')
            continue
        if effect['op']=='status_change_effects':
            if (set(effect)!=EFFECT_FIELDS['status_change_effects'] or effect['status'] not in STATUS_CHANGE_KINDS
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)
                or not isinstance(effect['effects'],list) or not effect['effects']
                or any(not isinstance(e,dict) or e.get('op') not in END_TURN_OPS for e in effect['effects'])):
                raise Unsupported('Status-change enchant needs a status, turns (-1 or positive) and an immediate-effect body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='end_turn_effects':
            if (set(effect)!=EFFECT_FIELDS['end_turn_effects'] or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)
                or not isinstance(effect['effects'],list) or not effect['effects']
                or any(not isinstance(e,dict) or e.get('op') not in END_TURN_OPS for e in effect['effects'])):
                raise Unsupported('End-turn enchant needs turns (-1 or positive) and an immediate-effect body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='play_interval':
            if set(effect)!=EFFECT_FIELDS['play_interval'] or type(effect['interval']) is not int or effect['interval']<1:
                raise Unsupported('Play-count interval enchant needs positive interval and body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op'] in ('upgrade_hand','next_turn_upgrade_hand','discard_redraw'):continue
        if effect['op']=='free_stamina':
            search=effect.get('search')
            if (set(effect)!=EFFECT_FIELDS['free_stamina'] or type(effect['uses']) is not int or effect['uses']<1
                or not isinstance(search,dict) or set(search)!={'category','card_ids'} or search['category'] not in ('any','active','mental')
                or not isinstance(search['card_ids'],list) or not all(isinstance(c,str) and c for c in search['card_ids'])):
                raise Unsupported('Free-stamina play needs a search and positive uses')
            continue
        if effect['op']=='create_hand_pool':
            candidates=effect['candidates'] if set(effect)==EFFECT_FIELDS['create_hand_pool'] else None
            if (not isinstance(candidates,list) or not candidates
                or any(not isinstance(c,dict) or set(c)!={'definition_id','plan','weight'} or not isinstance(c['definition_id'],str)
                       or not c['definition_id'] or not isinstance(c['plan'],str) or type(c['weight']) is not int or c['weight']<1 for c in candidates)):
                raise Unsupported('Random-pool creation needs weighted candidate definitions')
            continue
        if effect['op']=='create_deck_random':
            if set(effect)!={'op','definition_id'} or not isinstance(effect['definition_id'],str) or not effect['definition_id']:
                raise Unsupported('Specified creation needs one definition ID')
            continue
        if effect['op']=='fetch_tag':
            if set(effect)!={'op','tag'} or not isinstance(effect['tag'],str) or not effect['tag'].strip():
                raise Unsupported('Tag retrieval needs one explicit nonblank tag')
            continue
        if effect['op']=='shield_motivation_multiple':
            if set(effect)!=EFFECT_FIELDS['shield_motivation_multiple']:
                raise Unsupported('Scaled motivation shield needs base and bonus')
            from .motivation_shield import calculate
            calculate(effect['value'],0,effect['motivation_bonus_permil'])
            continue
        if effect['op']=='card_play_bonus':
            if set(effect)!=EFFECT_FIELDS['card_play_bonus'] or effect['category'] not in ('mental','any') or not isinstance(effect['effects'],list) or not effect['effects']:
                raise Unsupported('Invalid card-play bonus enchant')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='card_play_score':
            if (set(effect)!=EFFECT_FIELDS['card_play_score'] or effect['category'] not in ('mental','any')
                or effect['resource'] not in ('impression','motivation') or type(effect['permil']) is not int or not 0<=effect['permil']<2**31):
                raise Unsupported('Invalid card-play score enchant')
            continue
        if effect['op']=='resource_score':
            if (set(effect)!=EFFECT_FIELDS['resource_score'] or effect['resource'] not in ('impression','motivation')
                or type(effect['permil']) is not int or not 0<=effect['permil']<2**31
                or type(effect['consume_permil']) is not int or not 0<=effect['consume_permil']<=1000):
                raise Unsupported('Invalid resource-dependent score')
            continue
        if effect['op']=='repeat_card':
            if set(effect)!=EFFECT_FIELDS['repeat_card'] or effect['category'] not in ('active','mental','any'):
                raise Unsupported('Repeat needs explicit category, rarities, extra, uses and turns')
            rarities=effect['rarities']
            if (not isinstance(rarities,list) or any(not isinstance(r,str) or r not in RARITIES for r in rarities)
                or len(set(rarities))!=len(rarities)):
                raise Unsupported('Invalid repeat rarity filter')
            if any(type(effect[k]) is not int or effect[k]<1 for k in ('extra','uses')):
                raise Unsupported('Repeat counts must be positive integers')
            if type(effect['turns']) is not int or effect['turns'] not in (-1,) and effect['turns']<1:
                raise Unsupported('Repeat duration must be positive or -1')
            continue
        if effect['op']=='block_restriction':
            if set(effect)!=EFFECT_FIELDS['block_restriction'] or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1):
                raise Unsupported('Block restriction needs turns (-1 or positive)')
            continue
        if effect['op']=='delayed_effects':
            if (set(effect)!=EFFECT_FIELDS['delayed_effects'] or type(effect['delay']) is not int or effect['delay']<1
                or not isinstance(effect['effects'],list) or not effect['effects']):
                raise Unsupported('Delayed effects need a positive delay and a body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='impression_score_boost':
            if (set(effect)!=EFFECT_FIELDS['impression_score_boost'] or type(effect['permil']) is not int or not 0<effect['permil']<2**31
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)):
                raise Unsupported('Impression score boost needs positive permil and turns (-1 or positive)')
            continue
        if effect['op']=='impression_boost':
            if (set(effect)!=EFFECT_FIELDS['impression_boost'] or type(effect['permil']) is not int or not 0<effect['permil']<2**31
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)):
                raise Unsupported('Impression gain boost needs positive permil and turns (-1 or positive)')
            continue
        if effect['op']=='remove_random':
            if set(effect)!={'op','card_id'} or not isinstance(effect['card_id'],str) or not effect['card_id']:
                raise Unsupported('Random removal needs one explicit original card ID')
            continue
        for key,value in effect.items():
            if key!='op' and (type(value) is not int or value<0):raise Unsupported('Effects require nonnegative integer resolved coefficients')
        required={'shield_score':'permil','score_boost':'permil','timed_score_boost':'permil','shield_tick':'permil',
                  'impression_growth':'permil','impression_tick':'permil',
                  'permanent_score_boost':'permil','delayed_shield_score':'permil',
                  'stamina_down':'permil','stamina_up':'permil',
                  'recover_permil':'permil','hp_loss_permil':'permil'}.get(effect['op'],'value')
        if required not in effect:raise Unsupported('Missing effect coefficient: '+required)
        if effect.get('consume_permil',0)>1000:raise Unsupported('Invalid shield consumption')
        timer={'score_boost':'turns','timed_score_boost':'turns','start_play_draw':'turns','shield_tick':'ticks','impression_tick':'ticks','shield_gain_tick':'ticks','delayed_shield_score':'delay','stamina_down':'turns','stamina_up':'turns'}.get(effect['op'])
        if timer and effect.get(timer,0)<1:raise Unsupported('Positive explicit timer required')


def validate_definition(definition):
    if set(definition) - DEFINITION_FIELDS:
        raise Unsupported('Unknown card fields: ' + repr(set(definition) - DEFINITION_FIELDS))
    if 'effects' not in definition or 'provenance' not in definition:
        raise Unsupported('Every definition needs effects and provenance')
    if 'search_tag' in definition and not isinstance(definition['search_tag'],str):
        raise Unsupported('search_tag must be an explicitly known string')
    if definition.get('category','other') not in ('active','mental','trouble','other'):
        raise Unsupported('Unknown card category')
    if 'rarity' in definition and definition['rarity'] not in RARITIES:
        raise Unsupported('Unknown card rarity')
    if set(definition.get('cost', {})) - {'hp', 'shieldable','focus','motivation','impression'}:
        raise Unsupported('Unsupported cost channel')
    validate_requirements(definition.get('requires',{}))
    if type(definition.get('order', 0)) is not int or not -2**31 <= definition.get('order', 0) < 2**31:
        raise Unsupported('Card order must be int32')
    validate_effects(definition['effects'])
    for value in definition.get('cost', {}).values():
        if type(value) is not int or value < 0:
            raise Unsupported('Invalid cost')


def public_definition(definition):
    """Resolved gameplay rules, without raw master/debug provenance payloads."""
    rule={k:deepcopy(v) for k,v in definition.items() if k in DEFINITION_FIELDS and k!='provenance'}
    source=definition.get('provenance')
    rule['provenance']=dict(kind='public-rule-projection')
    if isinstance(source,dict) and 'card_id' in source:rule['provenance']['card_id']=source['card_id']
    return rule


class Battle:
    """reset/observe/legal_actions/step; commits are atomic on illegal input."""
    def __init__(self, document):
        self.document = deepcopy(document)
        kind=document.get('provenance', {}).get('kind')
        if document.get('schema_version') != 1 or kind not in ('synthetic','master-derived'):
            raise Unsupported('Battle needs synthetic or verified master-derived sandbox provenance')
        if kind=='master-derived':
            from .master_adapter import validate_master_document
            validate_master_document(document)
        allowed = {'schema_version', 'id', 'provenance', 'definitions', 'cards', 'zones', 'hand_limit',
                   'state', 'turns', 'drink_definitions', 'drinks', 'goal', 'shuffle', 'opening_initial',
                   'round2_gate', 'round_adjustment', 'completed_adjusted_score', 'max_steps','loadout','passives','exam_bonus','growth_start','support_upgrades','exam_upgrades','skip_recovery'}
        if set(document)-allowed or set(document['state'])-{'hp','max_hp','shield','score','focus','motivation','impression','permanent_boost'}:
            raise Unsupported('Unknown battle/state fields')
        if kind=='synthetic' and 'loadout' in document:raise Unsupported('Source loadout requires master-derived scene')
        if kind=='synthetic' and 'growth_start' in document:raise Unsupported('Growth start requires master-derived sources')
        if kind=='synthetic':
            from .support_upgrades import validate
            validate(document)
            from .exam_upgrades import validate as validate_exam_upgrades
            validate_exam_upgrades(document)
        from .attributes import validate_binding
        validate_binding(document)
        from .passives import validate_passives
        validate_passives(document.get('passives',[]))
        initializers=[init for bundle in document.get('passives',[]) for init in bundle.get('initializers',[])]
        if initializers and (kind!='master-derived' or document.get('round2_gate')):
            raise Unsupported('Source-bound initializers need master loadout and cannot use legacy round2_gate')
        if initializers and any(document['zones'].get(zone) for zone in ('hand','grave','lost','hold','playing')):
            raise Unsupported('ExamStart initialization requires pre-opening deck-only zones; in-turn restoration must not run initializers')
        self.definitions = deepcopy(document['definitions'])
        for definition in self.definitions.values():
            validate_definition(definition)
        self.cards = dict(document['cards'])  # instance -> exact definition variant
        if any(key not in self.definitions for key in self.cards.values()):
            raise Unsupported('Missing owned card definition')
        self.public_definitions={k:public_definition(self.definitions[k]) for k in sorted(set(self.cards.values()))}
        self.drink_definitions = deepcopy(document.get('drink_definitions', {}))
        for definition in self.drink_definitions.values():
            validate_definition(definition)
        from .card_mutations import closure
        closure(self.definitions,self.definitions,[self.drink_definitions,document.get('passives',[])])
        self.turns = deepcopy(document['turns'])
        for turn in self.turns:
            if (set(turn)-{'lesson'} != {'draw', 'actions', 'bonus_permil'}
                or any(type(v) is not int or v < 0 for k,v in turn.items() if k!='lesson')
                or turn.get('lesson',LESSONS[0]) not in LESSONS):
                raise Unsupported('Turn requires explicit nonnegative draw/actions/bonus_permil (optional lesson)')
        if not self.turns or document['hand_limit'] < 1:
            raise Unsupported('Invalid turn count/hand limit')
        if any(type(v) is not int or v < 0 for v in document['state'].values()):
            raise Unsupported('Resources require nonnegative integers')
        goal = document['goal']
        if set(goal)-{'target_score','stop_at_target','opponent_totals'} or type(goal.get('target_score')) is not int or goal['target_score'] < 0:
            raise Unsupported('Invalid explicit goal')
        self.hand_limit = document['hand_limit']
        self.skip_recovery = document.get('skip_recovery',2)
        if type(self.skip_recovery) is not int or not 0<=self.skip_recovery<=10000:
            raise Unsupported('skip_recovery must be an integer in 0..10000')
        if set(document['zones'])-{'deck','hand','grave','lost','hold','playing'} or document['zones'].get('playing'):
            raise Unsupported('Unknown zone / active card in initial fixture')
        rule = document.get('round_adjustment', dict(numerator=1, denominator=1))
        if set(rule) != {'numerator','denominator'} or rule['numerator'] < 0 or rule['denominator'] <= 0:
            raise Unsupported('Invalid explicit round adjustment')
        self.seed = None
        from .tag_fetch import has_fetch
        if has_fetch(list(self.definitions.values())+list(self.drink_definitions.values())):
            if any('search_tag' not in self.definitions[d] for d in self.cards.values()):
                raise Unsupported('Tag retrieval needs explicit search_tag on every owned card')

    def definition(self, instance):
        return self.definitions[self.cards[instance]]

    def reset(self, seed):
        self.seed, self.rng = seed, NativeRandom(seed)
        self.cards=dict(self.document['cards'])
        self.card_mutation_events=[]
        self.public_definitions={k:public_definition(self.definitions[k]) for k in sorted(set(self.cards.values()))}
        self.initialization_trace=[]
        initial = self.document['state']
        self.turns=deepcopy(self.document['turns'])
        self.turn_order_trace=None
        binding=self.document.get('exam_bonus',{})
        if binding.get('kind') in ('native-audition','displayed-audition'):
            from .turn_order import generate,config_parameters
            config=binding['order_config']
            self.turn_order_trace=generate(config['turn'],config_parameters(config),self.rng)
            for turn,attr in zip(self.turns,self.turn_order_trace['turn_attributes']):
                turn['bonus_permil']=binding['preview']['parameter_bonus_permil'][attr]
                turn['lesson']=ATTRIBUTE_LESSON[attr]
        self.extra_turn_template=deepcopy(self.turns[-1]);self.extra_turns_added=0
        self.public_bonus_schedule=None
        self.hp, self.max_hp = initial['hp'], initial['max_hp']
        self.shield, self.motivation, self.focus, self.score = (initial.get(k, 0) for k in ('shield', 'motivation', 'focus', 'score'))
        self.impression=initial.get('impression',0);self.permanent_boost=initial.get('permanent_boost',0)
        if self.hp > self.max_hp or min(self.hp, self.shield, self.focus, self.motivation, self.score) < 0:
            raise Unsupported('Invalid initial resources')
        self.zones = {k: list(self.document['zones'].get(k, [])) for k in ('deck', 'hand', 'grave', 'lost', 'hold', 'playing')}
        self.drinks = dict(self.document.get('drinks', {}))
        if any(d not in self.drink_definitions for d in self.drinks.values()):
            raise Unsupported('Missing drink definition')
        self.turn, self.actions, self.steps = 0, 0, 0
        self.boosts, self.ticks, self.draw_events, self.events = [], [], [], []
        self.reservations, self.mental_motivation = [], 0
        self.card_play_scores = []  # 出牌时按资源得分的附魔（輝くキミへ）：[{category, resource, permil}]
        self.end_turn_motivation=0
        self.play_count=0;self.play_intervals=[];self.in_turn_start=False
        self.stamina_modifiers=[]
        self.anti_debuff=0  # 低下状態無効剩余层数（AntiDebuffStatusEffect._count）
        self.stamina_down_fix=0  # 消費体力削減合计（StaminaConsumptionDownFixStatusEffect._value）
        self.block_restriction=None  # 元気増加無効：{remaining(-1 永久), fresh} 或 None（BlockRestrictionStatusEffect）
        self.impression_score_boosts=[]  # 好印象強化：[{permil, remaining(-1 永久), fresh}]（ReviewMultipleStatusEffect）
        self.impression_boosts=[]  # 好印象増加量増加：[{permil, remaining(-1 永久), fresh}]（ReviewAdditiveStatusEffect）
        self.free_stamina=[]  # 消費体力を0にする：[{category, card_ids, remaining}]，按安装顺序
        self.repeat_buffs=[]
        from .passives import runtime_hooks
        self.hooks=runtime_hooks(self.document.get('passives',[]));self.hook_events=[]
        self.remaining_offset=0 # only nonzero in an in-turn public planning model
        self.hidden_card_counts={};self.hidden_removed=0;self.possible_hidden_removals=0
        self.ended, self.known_only, self.unknown_draws = False, False, 0
        self.known_deck_prefix=[];self.deck_prior_issue=None;self.fetch_events=[]
        from .support_upgrades import reset
        reset(self)
        self._validate_zones()
        if self.document.get('shuffle', True):
            self._shuffle(self.zones['deck'])
        self._initialize_exam()
        self.support_bases.update(self.cards)
        self._start_turn(initial=True)
        return self.observe()

    def _initialize_exam(self):
        """Bounded ExamStartExam branch: before turn-start hooks/initial deal."""
        from .baton import generate
        for bundle in self.document.get('passives',[]):
            for init in bundle.get('initializers',[]):
                result=generate(init['item_id'],self.zones['deck'],self.rng,occupied_ids=list(self.cards))
                for card in result['generated']:
                    definition=card['card_id']+'@'+str(card['upgrade'])
                    if definition not in self.definitions:raise Unsupported('Missing admitted generated card')
                    self.cards[card['instance']]=definition
                self.zones['deck']=result['draw_pile_after']
                self.initialization_trace.append(result)
        from .card_mutations import closure
        library=set(self.definitions) if self.support_config or self.document.get('exam_upgrades') else closure(
            self.definitions,set(self.cards.values()),[self.drink_definitions,self.document.get('passives',[])])
        self.public_definitions={k:public_definition(self.definitions[k]) for k in sorted(library)}
        self._validate_zones()

    def _validate_zones(self):
        values = sum(self.zones.values(), [])
        if sorted(values) != sorted(self.cards) or len(set(values)) != len(values):
            raise Unsupported('Every physical card must occupy exactly one zone')
        if len(self.zones['hand']) > self.hand_limit:
            raise Unsupported('Hand overflow')

    def _shuffle(self, pool):
        self.known_deck_prefix=[];self.deck_prior_issue=None
        orders = [self.definition(c).get('order', 0) for c in pool]
        if any(o > 0 for o in orders):
            if len(set(orders)) != len(orders) or max(orders)-min(orders) > 2147483647:
                raise Unsupported('Native fixed-sort tie/overflow behavior is not ported')
            pool.sort(key=lambda c: self.definition(c).get('order', 0))
        else:
            self.rng.shuffle(pool)

    def _random_insert_index(self, count):
        return self.rng.insert_index(count)

    def _create_card(self, definition_id):
        from .card_mutations import create
        create(self,definition_id)

    def _pool_pick_index(self, weights):
        """按权重取一个下标（随机池生成）。"""
        point=self.rng.integer(0,sum(weights))
        for index,weight in enumerate(weights):
            if point<weight:return index
            point-=weight
        raise Unsupported('Weighted pick out of range')

    def _draw(self, count, support=True):
        count = min(count, self.hand_limit-len(self.zones['hand']))
        if self.known_only:
            # Hidden cards occupy virtual hand slots and cannot be spent in
            # visible-only search. Do not count the same hidden draw twice.
            received = min(count, max(0,self.hidden_count-self.possible_hidden_removals), self.hand_limit-len(self.zones['hand'])-self.unknown_draws-getattr(self,'fetch_uncertain_hand_slots',0))
            self.unknown_draws += max(0, received)
            self.hidden_count -= max(0, received)
            return []  # Never reveal sampled order in the policy's search tree.
        received = []
        while count > 0:
            if not self.zones['deck']:
                if not self.zones['grave']:
                    break
                self._shuffle(self.zones['grave'])
                self.zones['deck'].extend(self.zones['grave'])
                self.zones['grave'].clear()
            n = min(count, len(self.zones['deck']))
            received.extend(self.zones['deck'][:n])
            del self.zones['deck'][:n]
            count -= n
        self.zones['hand'].extend(received)
        self.known_deck_prefix=[c for c in self.known_deck_prefix if c not in received]
        from .tag_fetch import normalize
        normalize(self)
        self.draw_events.append(dict(turn=self.turn, drawn=received))
        if support:
            from .support_upgrades import process_batch
            process_batch(self,received)
        return received

    def _start_turn(self, initial=False):
        # 回合开始阶段获得的状态当回合就计入（原生 _isPassingTurnStart=True）；回合中获得的才「当回合不减」。
        # 依据：夺冠局 R2 日志，第 1 回合开始的とろけた時間 +50%（1 回合）在第 2 回合末好印象得分时已失效。
        self.in_turn_start=True
        try:self._start_turn_body(initial)
        finally:self.in_turn_start=False

    def _start_turn_body(self, initial=False):
        self.support_used=[]
        self.actions = self.turns[self.turn]['actions']
        self.bonus = self.turns[self.turn]['bonus_permil']
        # 好印象是回合计数状态：回合中途从 0 新建的（impression_fresh），下一个回合开始不减（3.5.1 选拔赛日志：
        # T1 打出本番前夜 得好印象 4，T2 开始仍为 4）；回合开始阶段或更早就存在的照常减 1（夺冠局 R1/R2、SSR 局续算）。
        if not initial:
            if getattr(self,'impression_fresh',False):self.impression_fresh=False
            else:self.impression=max(0,self.impression-1)
        self._emit('turn_start')
        for timer in list(self.ticks):
            if timer.get('kind') in ('start_play_draw','end_turn','status_change'):continue
            if timer['start'] <= self.turn:
                if timer.get('kind')=='impression':self.impression=ratio(self.impression,1000+timer['permil'])
                elif timer.get('kind')=='conditional':
                    values=self._condition_values()
                    if meets(values,timer['requires']):self._effects(timer['effects'],values)
                elif timer.get('kind')=='shield_gain' and self.block_restriction and timer.get('motivation_scale',0):pass
                elif timer.get('kind')=='shield_gain':self.shield+=timer['value']+self.motivation*timer.get('motivation_scale',0)
                else:self._score(ratio(self.shield, timer['permil']))
                timer['remaining'] -= 1
        self.ticks = [t for t in self.ticks if t['remaining'] != 0]  # -1 = 永久（回合末 / 开始后附魔）
        draw = self.turns[self.turn]['draw']
        due=[t for t in self.reservations if t['start']<=self.turn]
        self.reservations=[t for t in self.reservations if t['start']>self.turn]
        initial_added=[]
        if initial and self.document.get('opening_initial', True):
            overflow = []
            for card in list(reversed(self.zones['deck'])):
                if self.definition(card).get('initial', False):
                    self.zones['deck'].remove(card)
                    if len(self.zones['hand']) < self.hand_limit:
                        self.zones['hand'].append(card)
                        initial_added.append(card)
                    else:
                        overflow.append(card)
            self.zones['deck'][:0] = overflow
            draw = max(0, draw-len(self.zones['hand']))
        received=self._draw(draw,support=False)
        from .support_upgrades import process_batch
        # Native SetInitialCard queues guaranteed HandAdd then ordinary draw
        # before processing inserted support commands. Both use shared RNG.
        process_batch(self,initial_added+received)
        # Reserved effects occur after normal distribution (community sequence
        # DDDA -> 37); they still obey the actual hand cap.
        for timer in due:self._effects(timer['effects'])
        # Native StartPlay occurs after the normal distribution/queued draws.
        for timer in list(self.ticks):
            if timer.get('kind')=='start_play_draw' and timer['start']<=self.turn:
                self._draw(timer['value'])
                if timer['remaining']>0:timer['remaining']-=1
        self.ticks=[t for t in self.ticks if t['remaining']!=0]
        # 道具 / 回忆的 StartPlay 钩子（スカウト遮り帽「ターン開始後、スキルカードを引く」）：在附魔的 StartPlay 抽牌之后（假设）
        if any(h['phase']=='start_play' for h in self.hooks):self._emit('start_play')
        if initial and self.document.get('round2_gate', False) and len(self.zones['deck']) < 22:
            raise Unsupported('Legacy R2 after-opening gate: automatic native ExamStartExam binding remains unverified; use the separate baton component only at an explicit phase boundary')
        self._validate_zones()

    def _score(self, amount):
        # Explicit fixture score boosts aggregate additively; not all native statuses.
        boosted = ratio(amount, 1000+self.permanent_boost+sum(b['permil'] for b in self.boosts))
        from .attributes import battle_bonus_score
        self.score += battle_bonus_score(boosted, self.bonus)

    @property
    def next_draws(self):
        return [dict(start=t['start'],value=t['effects'][0]['value']) for t in self.reservations
                if len(t['effects'])==1 and t['effects'][0]['op']=='draw']

    def _condition_values(self):
        # 提速（2026-11-04）：两个要扫牌区的计数（手牌トラブル数、未除外的トラブル数）按需才算（_LazyConds），其余照旧直接给
        lesson=self.turns[self.turn].get('lesson') if self.turn<len(self.turns) else None
        return _LazyConds(self,hp_min=self.hp,shield_min=self.shield,focus_min=self.focus,
                          motivation_min=self.motivation,impression_min=self.impression,
                          turn_progress_min=self.turn,max_hp=self.max_hp,lesson=lesson)

    def _usable(self, definition, card=True):
        if card and self.actions < 1:
            return False
        req = definition.get('requires', {})
        if req and not meets(self._condition_values(), req):  # 没有使用条件时不算条件值（热路径）
            return False
        cost = definition.get('cost', {})
        if card and self._free_stamina_slot(definition) is not None:cost=dict(cost,hp=0,shieldable=0)
        return (self.hp >= self._stamina_amount(cost.get('hp',0))+max(0,self._stamina_amount(cost.get('shieldable',0))-self.shield)
                and self.focus>=cost.get('focus',0) and self.motivation>=cost.get('motivation',0)
                and self.impression>=cost.get('impression',0))

    def legal_actions(self):
        if self.ended:
            return []
        result = [dict(kind='play', id=c) for c in self.zones['hand'] if self._usable(self.definition(c))]
        result += [dict(kind='drink', id=d) for d in self.drinks if self._usable(self.drink_definitions[self.drinks[d]], card=False)]
        return result + [dict(kind='end_turn')]

    def _stamina_amount(self,value):
        # 社区「レッスン・試験詳細」：最终体力消耗 = 卡面消耗 × 倍率 + 追加 − 削減，算完向上取整；费用为 0 的卡不受影响
        multiplier=1000
        for modifier in self.stamina_modifiers:multiplier=multiplier*modifier['permil']//1000
        # 倍率 1000、整数消耗时 ratio(value, 1000) == value（float32 精确表示 |value| < 2^24、1000/1000 精确为 1.0）
        base=value if (multiplier==1000 and type(value) is int and -16777216<value<16777216) else ratio(value,multiplier)
        if value<=0:return base
        return max(0,base-self.stamina_down_fix)

    def _impression_turn_score(self):
        """回合末好印象得分：原生 ExamSequence 回合末（3.4.1 0x32df6f8..0x32df834）
        = ceil(f32(好印象 × (1 + Σ ReviewMultiple._value/1000)))，倍率与 GetReviewAdditiveMultiple 同为 float32 逐项累加。"""
        if not self.impression_score_boosts:return self.impression
        multiplier=f32(1.0)
        for boost in self.impression_score_boosts:multiplier=f32(multiplier+f32(f32(boost['permil'])/f32(1000.0)))
        return math.ceil(f32(multiplier*f32(self.impression)))

    def _impression_gain(self, value):
        """好印象增加量：原生 ExamStatusEffectCollection.TryAddReviewStatus（3.4.1 0x3284bf4..0x3284c48）
        = ceil(f32((value + AdditiveFix) × (1 + Σ ReviewAdditive._value / 1000)))，倍率为 float32 逐项累加
        （GetReviewAdditiveMultiple 0x3284ec0）。AdditiveFix（ExamReviewAdditiveFix）未移植，按 0。
        只作用于直接加好印象（ExamReview，卡 / 饮料 / 道具同一入口）；好印象強化（impression_growth 按现有值倍增）不经过这里。"""
        if not self.impression_boosts:return value
        multiplier=f32(1.0)
        for boost in self.impression_boosts:multiplier=f32(multiplier+f32(f32(boost['permil'])/f32(1000.0)))
        return math.ceil(f32(multiplier*f32(value)))

    def _free_stamina_slot(self, definition):
        """第一个对这张卡生效的「消費体力を0にする」状态的下标。"""
        if not self.free_stamina:return None
        source=definition.get('provenance');card_id=source.get('card_id') if isinstance(source,dict) else None
        for index,entry in enumerate(self.free_stamina):
            if entry['category'] not in ('any',definition.get('category')):continue
            if entry['card_ids'] and card_id not in entry['card_ids']:continue
            return index
        return None

    def _pay(self, definition, card=False):
        cost = definition.get('cost', {})
        slot=self._free_stamina_slot(definition) if card else None
        if slot is not None:
            # 消費体力（含直接扣体力的 forceStamina）都为 0，倍率 / 削減不再起作用；状态按「下一次使用」计次。
            # 假设：符合检索的卡即使本来费用就是 0 也消耗一次（原生 UseSearchPlayCardStaminaConsumptionChange 的
            # 调用条件未核对）；安装它的那张卡自己先付费，不受影响。
            cost=dict(cost,hp=0,shieldable=0)
            self.free_stamina[slot]['remaining']-=1
            if self.free_stamina[slot]['remaining']<1:del self.free_stamina[slot]
        shield_cost = self._stamina_amount(cost.get('shieldable',0))
        self.hp -= self._stamina_amount(cost.get('hp',0))+max(0, shield_cost-self.shield)
        self.shield = max(0, self.shield-shield_cost)
        self.focus -= cost.get('focus',0)
        self.motivation -= cost.get('motivation',0)
        self.impression -= cost.get('impression',0)

    def _effects(self, effects,conditions=None):
        conditions=self._condition_values() if conditions is None else conditions
        for effect in effects:
            op, value = effect['op'], effect.get('value', 0)
            if op == 'score':
                self._score(value+self.focus*effect.get('focus_scale', 0))
            elif op == 'shield':
                # 元気増加無効只挡经 CalculateAddBlock 的元気（ExamBlock，带 motivation_scale）；ExamBlockFix 不经过它
                if 'motivation_scale' in effect and self.block_restriction:continue
                gain=value+self.motivation*effect.get('motivation_scale', 0)
                self.shield += gain
                if 'motivation_scale' in effect and gain>0:self._status_changed('shield')
            elif op=='shield_motivation_multiple':
                if self.block_restriction:continue
                from .motivation_shield import calculate
                self.shield+=calculate(value,self.motivation,effect['motivation_bonus_permil'])
            elif op in ('focus', 'motivation','impression'):
                if op=='impression' and value>0:value=self._impression_gain(value)
                if op=='impression' and self.impression==0 and value>0 and not self.in_turn_start:self.impression_fresh=True
                setattr(self, op, getattr(self, op)+value)
                if value>0:self._status_changed(op)
            elif op == 'recover':
                self.hp = min(self.max_hp, self.hp+value)
            elif op=='hp_loss':
                self.hp=max(0,self.hp-value)
            elif op=='repeat_card':
                self.repeat_buffs.append(dict(category=effect['category'],rarities=list(effect['rarities']),
                                              extra=effect['extra'],uses=effect['uses'],remaining=effect['turns'],fresh=not self.in_turn_start))
            elif op=='remove_random':
                self._remove_random(effect['card_id'])
            elif op=='upgrade_hand':
                from .exam_upgrades import apply
                apply(self)
            elif op == 'draw':
                self._draw(value)
            elif op=='discard_redraw':
                from .card_mutations import redraw
                redraw(self)
            elif op=='create_deck_random':
                self._create_card(effect['definition_id'])
            elif op=='create_hand_pool':
                from .card_mutations import create_hand
                create_hand(self,effect['candidates'])
            elif op=='fetch_tag':
                self._fetch_tag(effect['tag'])
            elif op == 'extra_action':
                self.actions += value
            elif op=='extra_turn':
                self.turns.extend(deepcopy(self.extra_turn_template) for _ in range(value))
                self.extra_turns_added+=value
                if self.known_only and self.public_bonus_schedule is not None:
                    self.public_bonus_schedule.extend([self.extra_turn_template['bonus_permil']]*value)
            elif op=='permanent_score_boost':self.permanent_boost+=effect['permil']
            elif op=='anti_debuff':self.anti_debuff+=value
            elif op=='stamina_down_fix':self.stamina_down_fix+=value
            elif op=='free_stamina':self.free_stamina.append(dict(effect['search'],remaining=effect['uses']))
            elif op=='block_restriction':
                # 原生 TryAddBlockRestrictionStatus 先调 IsBlockAddStatus(18)：低下状態（目标类型 2）有低下状態無効时消耗 1 层并抵消
                if self.anti_debuff>0:self.anti_debuff-=1
                elif self.block_restriction is None:self.block_restriction=dict(remaining=effect['turns'],fresh=not self.in_turn_start)
                elif self.block_restriction['remaining']!=-1:
                    # 同一状态叠回合（执行器前后各读一次 GetBlockRestrictionTurn），与消費体力増加 同口径
                    self.block_restriction['remaining']=-1 if effect['turns']==-1 else self.block_restriction['remaining']+effect['turns']
            elif op=='stamina_up' and self.anti_debuff>0:
                self.anti_debuff-=1  # 低下状態無効：抵消这次消費体力増加
            elif op in ('stamina_down','stamina_up'):
                old=next((m for m in self.stamina_modifiers if m['kind']==op),None)
                if old:
                    if old['permil']!=effect['permil']:raise Unsupported('Different stamina multipliers in one status family')
                    old['remaining']+=effect['turns']
                else:self.stamina_modifiers.append(dict(kind=op,permil=effect['permil'],remaining=effect['turns'],fresh=not self.in_turn_start))
            elif op=='impression_score_boost':
                self.impression_score_boosts.append(dict(permil=effect['permil'],remaining=effect['turns'],fresh=not self.in_turn_start))
            elif op=='impression_boost':
                self.impression_boosts.append(dict(permil=effect['permil'],remaining=effect['turns'],fresh=not self.in_turn_start))
            elif op=='impression_growth':self.impression=ratio(self.impression,1000+effect['permil'])
            elif op=='conditional':
                if meets(conditions,effect['requires']):self._effects(effect['effects'],conditions)
            elif op=='recover_permil':
                self.hp=min(self.max_hp,self.hp+ratio(self.max_hp,effect['permil']))
            elif op=='hp_loss_permil':
                # Live R1 T10 (2026-10-19): 15% of max 33 removed 4 HP -> truncation, not ratio()'s ceil.
                self.hp=max(0,self.hp-self.max_hp*effect['permil']//1000)
            elif op=='conditional_tick':
                self.ticks.append(dict(kind='conditional',requires=deepcopy(effect['requires']),effects=deepcopy(effect['effects']),
                                       remaining=effect['ticks'] if effect['ticks']>0 else 10**9,start=self.turn+1))
            elif op=='play_interval':
                # 「以降、N回使用するごとに」：从安装这张牌之后开始数。夺冠局日志：R1 安装后第 5、10 张触发，R2 第 5 张触发。
                self.play_intervals.append(dict(interval=effect['interval'],effects=deepcopy(effect['effects']),base=self.play_count+1))
            elif op == 'next_turn_draw':
                self.reservations.append(dict(start=self.turn+1,effects=[dict(op='draw',value=value)]))
            elif op=='next_turn_upgrade_hand':
                self.reservations.append(dict(start=self.turn+1,effects=[dict(op='upgrade_hand')]))
            elif op=='delayed_effects':
                self.reservations.append(dict(start=self.turn+effect['delay'],effects=deepcopy(effect['effects'])))
            elif op=='delayed_shield_score':
                self.reservations.append(dict(start=self.turn+effect['delay'],effects=[dict(op='shield_score',permil=effect['permil'],consume_permil=effect.get('consume_permil',0))]))
            elif op=='impression_tick':
                self.ticks.append(dict(kind='impression',permil=effect['permil'],remaining=effect['ticks'],start=self.turn+1))
            elif op=='shield_gain_tick':
                self.ticks.append(dict(kind='shield_gain',value=value,motivation_scale=effect.get('motivation_scale',0),remaining=effect['ticks'],start=self.turn+1))
            elif op == 'mental_motivation':
                self.mental_motivation += value
            elif op == 'card_play_score':
                self.card_play_scores.append(dict(category=effect['category'],resource=effect['resource'],permil=effect['permil']))
            elif op == 'card_play_bonus':
                self.card_play_scores.append(dict(category=effect['category'],effects=deepcopy(effect['effects'])))
            elif op=='end_turn_motivation':self.end_turn_motivation+=value
            elif op=='start_play_draw':
                self.ticks.append(dict(kind='start_play_draw',value=value,remaining=effect['turns'],start=self.turn+1))
            elif op=='status_change_effects':
                self.ticks.append(dict(kind='status_change',status=effect['status'],effects=deepcopy(effect['effects']),remaining=effect['turns'],
                                       start=self.turn,fresh=not self.in_turn_start))
            elif op=='end_turn_effects':
                # 本回合的回合末也触发（与厳選初星ティー / コーヒー 同口径）；回合中途装上的当回合末不减回合数（同 LessonParameterMultiple）
                self.ticks.append(dict(kind='end_turn',effects=deepcopy(effect['effects']),remaining=effect['turns'],start=self.turn,
                                       fresh=not self.in_turn_start))
            elif op == 'shield_score':
                self._score(ratio(self.shield, effect['permil']))
                retain = 1-f32(f32(effect.get('consume_permil', 0))/f32(1000))
                self.shield = math.ceil(f32(f32(self.shield)*f32(retain)))
            elif op=='resource_score':
                from .resource_score import calculate
                amount,remaining=calculate(getattr(self,effect['resource']),effect['permil'],effect['consume_permil'])
                self._score(amount)
                setattr(self,effect['resource'],remaining)
            elif op == 'score_boost':
                self.boosts.append(dict(permil=effect['permil'], remaining=effect['turns']))
            elif op=='timed_score_boost':
                # Native lookup merges by remaining Turn, not by magnitude or
                # birth turn, and leaves the existing passed-start flag intact.
                existing=next((b for b in self.boosts if b.get('native') and b['remaining']==effect['turns']),None)
                if existing is not None:existing['permil']+=effect['permil']
                else:self.boosts.append(dict(permil=effect['permil'],remaining=effect['turns'],fresh=not self.in_turn_start,native=True))
            elif op == 'shield_tick':
                self.ticks.append(dict(permil=effect['permil'], remaining=effect['ticks'], start=self.turn+1))
            elif op == 'remove_trouble':
                for card in [c for c in self.zones['hand'] if self.definition(c).get('trouble', False)][:value]:
                    self.zones['hand'].remove(card)
                    self.zones['lost'].append(card)
            else:
                raise Unsupported('Unknown effect')

    def _status_changed(self,status):
        """ExamStatusChange 附魔：对应状态被效果增加之后立即执行正文（正文自己加的同类状态不再触发，避免递归）。"""
        if getattr(self,'_in_status_change',False):return
        for timer in [t for t in self.ticks if t.get('kind')=='status_change' and t['status']==status]:
            self._in_status_change=True
            try:self._effects(deepcopy(timer['effects']))
            finally:self._in_status_change=False

    def _consume_repeat(self,definition):
        for buff in self.repeat_buffs:
            if buff['category']!='any' and buff['category']!=definition.get('category'):
                continue
            if buff['rarities']:
                if 'rarity' not in definition:raise Unsupported('Rarity-filtered repeat needs explicit card rarity')
                if definition['rarity'] not in buff['rarities']:continue
            buff['uses']-=1
            extra=buff['extra']
            self.repeat_buffs=[b for b in self.repeat_buffs if b['uses']>0]
            return extra
        return 0

    @property
    def remaining_turns(self):
        return len(self.turns)-self.turn+self.remaining_offset

    def original_card_id(self,card):
        source=self.definition(card).get('provenance')
        return source.get('card_id',self.cards[card]) if isinstance(source,dict) else self.cards[card]

    def _fetch_tag(self,tag):
        from .tag_fetch import apply
        apply(self,tag)

    def _remove_random(self,card_id):
        if self.known_only:
            # Virtual draws have unknown identity. Keep a conservative count
            # interval instead of sampling a hidden location in the planner.
            upper=min(self.hidden_card_counts.get(card_id,0),self.hidden_count)
            lower=max(0,self.hidden_card_counts.get(card_id,0)-self.unknown_draws)
            if lower:
                self.hidden_count-=1;self.hidden_removed+=1
            elif upper:self.possible_hidden_removals+=1
            if upper:
                self.hidden_card_counts[card_id]-=1
                if hasattr(self,'fetch_projection_uncertain'):self.fetch_projection_uncertain=True
            return
        candidates=[card for zone in ('deck','grave') for card in self.zones[zone] if self.original_card_id(card)==card_id]
        for card in self.rng.pick_one(candidates):
            zone='deck' if card in self.zones['deck'] else 'grave'
            self.zones[zone].remove(card);self.zones['lost'].append(card)
        from .tag_fetch import normalize
        normalize(self)

    def _emit(self,phase,definition=None,definition_id=None,hp_before=None):
        selected,values=self._prepare_hooks(phase,definition,definition_id,hp_before)
        self._fire_hooks(selected,values)

    def _prepare_hooks(self,phase,definition=None,definition_id=None,hp_before=None):
        from .passives import matching_hooks
        actor=None
        if definition is not None:
            source=definition.get('provenance')
            actor=dict(card_id=source.get('card_id',definition_id) if isinstance(source,dict) else definition_id,
                       category=definition.get('category','other'),rarity=definition.get('rarity'))
        values=self._condition_values()
        selected=matching_hooks(self.hooks,phase,values,self.remaining_turns,actor,hp_before=hp_before)
        return selected,values

    def _fire_hooks(self,selected,values):
        for hook in selected:
            hook['uses']+=1
            self._effects(hook['effects'],values)
            self.hook_events.append(dict(phase=hook['phase'],source_id=hook['source_id'],hook_index=hook['hook_index'],
                                         use=hook['uses'],turn=self.turn,remaining_turns=self.remaining_turns))

    def skip_recovery_amount(self):
        """One active skip, not a heal per unspent action or an exhausted turn."""
        return min(self.skip_recovery,self.max_hp-self.hp) if self.actions>0 else 0

    def _apply(self, action):
        if action['kind'] == 'end_turn':
            self.hp += self.skip_recovery_amount()
            if self.impression:self._score(self._impression_turn_score())
            self.motivation+=self.end_turn_motivation
            # 回合末附魔（end_turn_effects）：与回合末やる気同一时点，在好印象得分之后（假设）
            for timer in [t for t in self.ticks if t.get('kind')=='end_turn']:self._effects(deepcopy(timer['effects']))
            # 道具的 ターン終了時 钩子：与回合末附魔（end_turn_motivation）同一时点，在好印象得分之后、弃手牌之前（假设）
            if any(h['phase']=='end_turn' for h in self.hooks):self._emit('end_turn')
            for card in self.zones['hand']:
                from .support_upgrades import reset_card
                reset_card(self,card)
                self.zones['lost' if self.definition(card).get('end_turn_lost') else 'grave'].append(card)
                if self.known_only and not self.definition(card).get('end_turn_lost'):
                    from .tag_fetch import returned_visible
                    returned_visible(self,card)
            self.zones['hand'].clear()
            for timer in self.ticks:
                if timer.get('kind') in ('end_turn','status_change'):
                    if timer['fresh']:timer['fresh']=False
                    elif timer['remaining']>0:timer['remaining']-=1
            self.ticks=[t for t in self.ticks if t['remaining']!=0]
            for boost in self.boosts:
                if boost.get('fresh'):boost['fresh']=False
                else:boost['remaining'] -= 1
            self.boosts = [b for b in self.boosts if b['remaining'] > 0]
            if self.block_restriction:
                if self.block_restriction['fresh']:self.block_restriction['fresh']=False
                elif self.block_restriction['remaining']>0:self.block_restriction['remaining']-=1
                if self.block_restriction['remaining']==0:self.block_restriction=None
            for boost in self.impression_score_boosts:
                if boost['fresh']:boost['fresh']=False
                elif boost['remaining']>0:boost['remaining']-=1
            self.impression_score_boosts=[b for b in self.impression_score_boosts if b['remaining']!=0]
            for boost in self.impression_boosts:
                # 与 LessonParameterMultiple 同口径：回合中途获得的当回合末不减；-1 为永久
                if boost['fresh']:boost['fresh']=False
                elif boost['remaining']>0:boost['remaining']-=1
            self.impression_boosts=[b for b in self.impression_boosts if b['remaining']!=0]
            for modifier in self.stamina_modifiers:
                if modifier['fresh']:modifier['fresh']=False
                else:modifier['remaining']-=1
            self.stamina_modifiers=[m for m in self.stamina_modifiers if m['remaining']>0]
            for buff in self.repeat_buffs:
                if buff['fresh']:buff['fresh']=False
                elif buff['remaining']>0:buff['remaining']-=1
            self.repeat_buffs=[b for b in self.repeat_buffs if b['remaining']!=0]
            self.turn += 1
            if self.turn == len(self.turns):
                self.ended = True
                self.actions = 0
            else:
                self._start_turn()
        elif action['kind'] == 'play':
            card = action['id']
            definition = self.definition(card)
            conditions=self._condition_values()
            self.zones['hand'].remove(card)
            self.zones['playing'].append(card)  # It cannot be re-drawn mid-effect.
            self.actions -= 1
            self._pay(definition,card=True)
            # Live R1 T10: the item fired at HP17 although paying the card left HP15, so HP-ratio
            # trigger fields read the HP before this card's cost. Other fields keep post-payment values.
            self._emit('card_play',definition,self.cards[card],hp_before=conditions['hp_min'])
            # ExamCardPlay observes the pre-existing enchant collection; the
            # enchant installed by this card's body cannot trigger itself.
            if definition.get('category')=='mental':
                self.motivation+=self.mental_motivation
            for enchant in list(self.card_play_scores):
                # 与 mental_motivation 同一时点（正文之前、只看已安装的附魔）；触发时点尚无实机日志核对
                if enchant['category']=='any' or enchant['category']==definition.get('category'):
                    if 'effects' in enchant:self._effects(deepcopy(enchant['effects']))
                    else:self._effects([dict(op='resource_score',resource=enchant['resource'],permil=enchant['permil'],consume_permil=0)])
            ordinal=self.play_count+1
            for enchant in list(self.play_intervals):
                # 日志里这次触发记在该张出牌事件内、正文之前
                if ordinal>enchant['base'] and (ordinal-enchant['base'])%enchant['interval']==0:self._effects(enchant['effects'])
            activations=1+self._consume_repeat(definition)
            for _ in range(activations):self._effects(definition['effects'],conditions)
            self.play_count=ordinal
            selected,after_conditions=self._prepare_hooks('card_play_after',definition,self.cards[card])
            from .support_upgrades import reset_card
            reset_card(self,card)
            self.zones['playing'].remove(card)
            self.zones['lost' if definition.get('once') else 'grave'].append(card)
            if self.known_only and not definition.get('once'):
                self.hidden_count+=1
                key=self.original_card_id(card);self.hidden_card_counts[key]=self.hidden_card_counts.get(key,0)+1
                from .tag_fetch import returned_visible
                returned_visible(self,card)
            self._fire_hooks(selected,after_conditions)
            return dict(body_activations=activations)
        else:
            conditions=self._condition_values()
            definition = self.drink_definitions[self.drinks.pop(action['id'])]
            self._pay(definition)
            self._effects(definition['effects'],conditions)

    def observe(self):
        hand = [dict(instance=c, definition_id=self.cards[c], definition=deepcopy(self.definition(c))) for c in self.zones['hand']]
        pool={}
        if self.known_only:pool=deepcopy(self.hidden_card_counts)
        else:
            for card in self.zones['deck']+self.zones['grave']:
                key=self.original_card_id(card);pool[key]=pool.get(key,0)+1
        schedule=(list(self.public_bonus_schedule) if self.known_only and self.public_bonus_schedule is not None
                  else [t['bonus_permil'] for t in self.turns[self.turn:]])
        public_model=dict(definition_library=deepcopy(self.public_definitions),
                          deck_definition_ids=sorted(self.cards[c] for c in self.zones['deck']),
                          grave_definition_ids=[self.cards[c] for c in self.zones['grave']],
                          hold_definition_ids=[self.cards[c] for c in self.zones['hold']],
                          lost_definition_ids=[self.cards[c] for c in self.zones['lost']],
                          turns=deepcopy(self.turns[self.turn:]))
        if self.known_deck_prefix:
            public_model['known_deck_prefix_definition_ids']=[self.cards[c] for c in self.known_deck_prefix]
        if self.deck_prior_issue:public_model['deck_prior_issue']=self.deck_prior_issue
        if any(k not in self.zones['hand'] and k not in self.zones['playing'] for k in self.support_ids):
            from .card_mutations import POOLED_UPGRADE_PRIOR
            public_model['deck_prior_issue']=POOLED_UPGRADE_PRIOR
        if self.support_config:
            from .support_upgrades import public_rules
            public_model['support_upgrades']=public_rules(self)
        if self.document.get('exam_upgrades'):
            from .exam_upgrades import public_rules as exam_public_rules
            public_model['exam_upgrades']=exam_public_rules(self.document['exam_upgrades'])
        result=dict(hp=self.hp, max_hp=self.max_hp, shield=self.shield, score=self.score,
                    hand_limit=self.hand_limit,skip_recovery=self.skip_recovery,
                    focus=self.focus, motivation=self.motivation,impression=self.impression,permanent_boost=self.permanent_boost, turn=self.turn,
                    remaining_turns=self.remaining_turns, actions=self.actions,
                    bonus_permil=self.bonus, remaining_bonus_permil=schedule, hand=hand,
                    lesson=self.turns[self.turn].get('lesson') if self.turn<len(self.turns) else None,
                    play_count=self.play_count,
                    public_model=public_model,
                    score_context=dict(round_adjustment=deepcopy(self.document.get('round_adjustment',dict(numerator=1,denominator=1))),
                                       completed_adjusted_score=self.document.get('completed_adjusted_score',0)),
                    deck_count=len(self.zones['deck']), grave_count=len(self.zones['grave']),
                    remaining_cards=sorted(self.cards[c] for c in self.zones['deck']),
                    hidden_card_counts=pool,
                    drinks={d:deepcopy(self.drink_definitions[k]) for d,k in self.drinks.items()},
                    boosts=deepcopy(self.boosts), ticks=deepcopy(self.ticks),
                    next_draws=deepcopy(self.next_draws),mental_motivation=self.mental_motivation,
                    card_play_scores=deepcopy(self.card_play_scores),
                    end_turn_motivation=self.end_turn_motivation,
                    reservations=deepcopy(self.reservations),extra_turn_template=deepcopy(self.extra_turn_template),extra_turns_added=self.extra_turns_added,
                    stamina_modifiers=deepcopy(self.stamina_modifiers),anti_debuff=self.anti_debuff,stamina_down_fix=self.stamina_down_fix,
                    free_stamina=deepcopy(self.free_stamina),impression_boosts=deepcopy(self.impression_boosts),
                    block_restriction=deepcopy(self.block_restriction),impression_score_boosts=deepcopy(self.impression_score_boosts),
                    repeat_buffs=deepcopy(self.repeat_buffs),
                    hooks=deepcopy(self.hooks),
                    goal=deepcopy(self.document['goal']), ended=self.ended)
        if self.support_config:
            from .support_upgrades import public_state
            result['support_state']=public_state(self)
        return result

    def step(self, action, reason='external'):
        if action not in self.legal_actions():
            raise IllegalAction('Action not legal: ' + repr(action))
        trial = deepcopy(self)
        before = self.observe()
        trial.steps += 1
        if trial.steps > trial.document.get('max_steps', 1000):
            raise Unsupported('Action loop limit exceeded')
        details=trial._apply(action)
        trial._validate_zones()
        after = trial.observe()
        trial.events.append(dict(index=trial.steps, before=before, legal=self.legal_actions(),
                                 action=deepcopy(action), reason=reason, after=after))
        if details:trial.events[-1]['details']=details
        triggered=trial.hook_events[len(self.hook_events):]
        if triggered:trial.events[-1]['triggered']=deepcopy(triggered)
        # Audit-only internals stay outside observe()/policy input.
        if not self.known_only:
            trial.events[-1]['engine']=dict(rng_before=self.rng.state,rng_after=trial.rng.state,
                                            rng_calls_before=self.rng.calls,rng_calls_after=trial.rng.calls,
                                            zones_before=deepcopy(self.zones),zones_after=deepcopy(trial.zones))
            fetched=trial.fetch_events[len(self.fetch_events):]
            if fetched:trial.events[-1]['engine']['tag_fetch']=deepcopy(fetched)
        self.__dict__.update(trial.__dict__)
        return after

    @classmethod
    def visible_model(cls, observation):
        """Planning model created ONLY from public input; hidden cards absent."""
        hand = observation['hand']
        doc = dict(schema_version=1, provenance=dict(kind='synthetic'),
                   definitions={c['definition_id']:c['definition'] for c in hand},
                   cards={c['instance']:c['definition_id'] for c in hand},
                   zones=dict(hand=[c['instance'] for c in hand]), hand_limit=64,skip_recovery=observation['skip_recovery'],
                   turns=[dict(draw=0, actions=observation['actions'], bonus_permil=observation['bonus_permil'])],
                   state=dict(hp=observation['hp'], max_hp=observation['max_hp']),
                   goal=observation['goal'], shuffle=False)
        from .tag_fetch import has_fetch,prepare_projection
        fetch_projection=has_fetch(list(observation['drinks'].values())+[c['definition'] for c in hand])
        if fetch_projection:doc['definitions']=deepcopy(observation['public_model']['definition_library'])
        from .card_mutations import targets
        if list(targets([observation['drinks'],[c['definition'] for c in hand]])):
            doc['definitions']=deepcopy(observation['public_model']['definition_library'])
        if 'exam_upgrades' in observation.get('public_model',{}):
            doc['definitions']=deepcopy(observation['public_model']['definition_library'])
            doc['exam_upgrades']=deepcopy(observation['public_model']['exam_upgrades'])
        if 'support_state' in observation:
            from .support_upgrades import sampled_config
            rules=observation['public_model']['support_upgrades']
            doc['definitions']=deepcopy(observation['public_model']['definition_library'])
            # This visible-only model never reveals or rolls upgrades on hidden draws.
            current=dict(rules,attributes=[rules['current_attribute']]);current.pop('bonus_by_attribute',None)
            doc['support_upgrades']=sampled_config(current,doc['turns'])
        model = cls(doc)
        model.reset(1)
        for key in ('hp', 'shield', 'score', 'focus', 'motivation', 'actions', 'bonus_permil'):
            setattr(model, 'bonus' if key=='bonus_permil' else key, observation[key])
        model.boosts, model.ticks = deepcopy(observation['boosts']), deepcopy(observation['ticks'])
        model.reservations=deepcopy(observation.get('reservations',[]))
        model.mental_motivation=observation.get('mental_motivation',0)
        model.card_play_scores=deepcopy(observation.get('card_play_scores',[]))
        model.end_turn_motivation=observation.get('end_turn_motivation',0)
        model.impression=observation.get('impression',0);model.permanent_boost=observation.get('permanent_boost',0)
        model.extra_turn_template=deepcopy(observation.get('extra_turn_template',model.extra_turn_template))
        model.extra_turns_added=observation.get('extra_turns_added',0)
        model.stamina_modifiers=deepcopy(observation.get('stamina_modifiers',[]))
        model.anti_debuff=observation.get('anti_debuff',0)
        model.stamina_down_fix=observation.get('stamina_down_fix',0)
        model.free_stamina=deepcopy(observation.get('free_stamina',[]))
        model.impression_boosts=deepcopy(observation.get('impression_boosts',[]))
        model.block_restriction=deepcopy(observation.get('block_restriction'))
        model.impression_score_boosts=deepcopy(observation.get('impression_score_boosts',[]))
        model.repeat_buffs=deepcopy(observation.get('repeat_buffs',[]))
        model.hooks=deepcopy(observation.get('hooks',[]))
        model.hidden_card_counts=deepcopy(observation.get('hidden_card_counts',{}))
        model.remaining_offset=observation['remaining_turns']-1
        model.public_bonus_schedule=deepcopy(observation.get('remaining_bonus_permil',[observation['bonus_permil']]*observation['remaining_turns']))
        for timer in model.ticks+model.reservations:timer['start']-=observation['turn']
        # Preserve actual hand cap; observe explicitly includes it below.
        model.hand_limit = observation['hand_limit']
        model.drink_definitions = deepcopy(observation['drinks'])
        model.drinks = {k:k for k in model.drink_definitions}
        model.known_only, model.hidden_count = True, observation['deck_count']+observation['grave_count']
        if fetch_projection:prepare_projection(model,observation)
        if 'support_state' in observation:
            from .support_upgrades import restore_public
            restore_public(model,observation['support_state'])
        return model

    def result(self):
        rule = self.document.get('round_adjustment', dict(numerator=1, denominator=1))
        adjusted = self.score*rule['numerator']//rule['denominator']
        total = adjusted+self.document.get('completed_adjusted_score', 0)
        goal = self.document['goal']
        rivals = goal.get('opponent_totals')
        win = None if rivals is None else total >= max(rivals, default=0)
        result=dict(raw_score=self.score, adjusted_score=adjusted, final_total=total,
                    target_met=self.score >= goal['target_score'], conditional_win=win,
                    hp=self.hp, drinks_left=len(self.drinks), actions=self.steps,
                    rng_calls=self.rng.calls, final_rng_state=self.rng.state,turn_order_trace=deepcopy(self.turn_order_trace))
        if self.initialization_trace:result['initialization_trace']=deepcopy(self.initialization_trace)
        if self.support_config:result['support_upgrade_events']=deepcopy(self.support_events)
        return result
