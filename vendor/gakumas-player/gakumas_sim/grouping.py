"""Audited physical-ID symmetry for one explicit uniform-draw research mode.

Only hidden cards sharing every per-instance rule/base/upgrade component are
exchangeable. This does not support an arbitrary ID-sensitive continuation.
"""
from fractions import Fraction
import hashlib
from pathlib import Path
from .core import Unsupported
from .chance import PrefixBattle


# 2026-10-30 feat_matto_campus_sim: core/passives/rollout/policies/support_upgrades 只新增 Campus 分支（未使用时行为不变），
# 全量回归通过后重签。
# 2026-10-31：低下状態無効 / 消費体力削減 / 随机池生成到手牌（create_hand_pool，未使用时行为不变），全量 822 通过后重签 core/chance/card_mutations/rollout；
# 同日 消費体力を0にする（free_stamina，勝利をつかめ！）再重签 core/rollout；
# 同日 好印象増加量増加（impression_boost，星屑センセーション）再重签 core/rollout；
# 同日 元気増加無効 / 好印象強化 / 条件 turn_progress_min・impression_below 再重签 core/rollout；
# 同日 道具钩子 start_play / end_turn、回合属性条件 再重签 core/passives；
# 2026-11-01 回合末 / 开始后附魔（end_turn_effects、永久 start_play_draw，厳選初星ティー）再重签 core/policies；
# 同日 ExamStatusChange 附魔（status_change_effects，びしっとキメ顔）再重签 core/policies。
AUDITED_SOURCES = {
    'motivation_shield.py': '01847b6f9a4f8d64d2f1d568d22130cf34043442ac5f4c1591eada6d80ef33a4',
    'hp_reserve.py': '95591703b9f09082e4c4416eb7d3e7925132f4ef3eb16d4e6dfcb5abf8850f61',
    'card_mutations.py': '5ae67185b99d1493e16030afb02c7c879b1335873366415d059e92ef0779ae08',
    'chance.py': '8cf2e68ced3f6e6a907d4d7280b21546f15515cfa2f1a0f1523c0d87a7f413dc',
    'core.py': '840e299cbe219e1dc32356d228998d270681db791865090e496343d5b4c95812',
    'policies.py': 'db1e1816b1fb6c6f60fc3c685368392c159b643b88fe6ae06fb07818e0891f6f',
    'passives.py': '3e402a4f556b5c5d6e2f06aeb9d2304177af79556cd33f810a4fc7193328e002',
    'support_upgrades.py': '099ee575082639123a4fdfaf212232b06d14e96d16d9b4e9e10e656cf33be3ef',
    'exam_upgrades.py': '4aa422f6db11ed061413334d4b838746011ba8dfbe55c329b282a8759b78f7d1',
    'rollout.py': 'd4d4fac11174a754e797e1baada599253ef855232a494915d3177e950b28743c',
    'tag_fetch.py': '80fab590b9d03f352670a7d8aa1298a777334a5d3f94bd7b6566099cf2489af6'
}


def admit(continuation):
    from .policies import SkillPolicy
    if type(continuation) is not SkillPolicy:
        raise Unsupported('Grouped draws require the audited exact SkillPolicy continuation')
    for name in ['choose','_battle']:
        bound=getattr(continuation,name);fn=getattr(bound,'__func__',None)
        if (fn is not getattr(SkillPolicy,name) or getattr(fn,'__module__',None)!='gakumas_sim.policies'
            or getattr(fn,'__qualname__',None)!='SkillPolicy.'+name):
            raise Unsupported('Grouped draws reject overridden continuation methods')
    expected={'motivation_shield.py','hp_reserve.py','core.py','policies.py','passives.py','support_upgrades.py','exam_upgrades.py','rollout.py','tag_fetch.py','card_mutations.py','chance.py'}
    if set(AUDITED_SOURCES)!=expected:raise Unsupported('Incomplete grouped-draw identity audit')
    for name,digest in AUDITED_SOURCES.items():
        if hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()!=digest:
            raise Unsupported('Grouped-draw identity audit changed: '+name)


class GroupedPrefixBattle(PrefixBattle):
    """Sample a rule/base/upgrade class, then retain one physical representative.

    A bijection swapping equal-class hidden IDs fixes the existing hand and all
    other instances. The audited engine and continuation preserve that swap.
    Batch order, distinct-class order and support application are retained.
    """
    def _draw_index(self):
        groups={};deck=self.zones['deck']
        for index,key in enumerate(deck):
            signature=(self.cards[key],self.support_bases[key],tuple(self.support_ids.get(key,[])))
            if signature not in groups:groups[signature]=[index,0]
            groups[signature][1]+=1
        self.rng.calls+=1
        if len(groups)==1:return next(iter(groups.values()))[0]
        signature=('uniform-next-card-group',tuple((s,row[1]) for s,row in groups.items()))
        return self.rng.take(signature,[(index,Fraction(count,len(deck))) for index,count in groups.values()])
