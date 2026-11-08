"""H.I.F 对手（NPC）分数与胜负判定。

确定的部分（master ProduceExamBattleNpcGroup，按角色难度 ProduceStepAuditionDifficulty 绑定）：
- 每个对手的最终分数区间 [scoreMin, scoreMax]；区间宽度为 0 的是固定分（如选拔的过线线 npc_hif_border）。
- 分数在考试前、中、后三段的占比 opScorePermil / midScorePermil / edScorePermil。

实机核对（2026-10-19 夺冠局本战、2026-10-30 3.5.1 选拔赛）：所有对手的实际分数都落在区间内；
选拔赛 10 回合的逐回合分数按前 4 / 中 3 / 后 3 回合分段，各段合计接近总分 × 占比，段内有波动。

未校准（标注为假设，待实机数据修正）：
- 区间内按均匀分布抽取总分；
- 段长：前段 = round(回合数 × 0.4)，其余两段平分（10 回合时与实机一致，9/12 回合未核对）；
- 段内逐回合波动按 ±20% 均匀噪声分摊（只用于画曲线，胜负只看总分）；
- 选拔过线：分数 ≥ 过线线（npc_hif_border）视为通过，排名规则未核对。
"""
from dataclasses import dataclass,field
import math
import random

from .catalog import Catalog
from .hif import HIFPlanner

BORDER_MOB='npc_hif_border'


@dataclass
class Opponent:
    identity:str
    score_min:int
    score_max:int
    op:int
    mid:int
    ed:int
    border:bool=False

    @property
    def fixed(self):return self.score_min==self.score_max


@dataclass
class OpponentField:
    mode:str
    position:int
    turns:int
    opponents:list=field(default_factory=list)

    def sample_totals(self,rng):
        """每个对手的最终原始分（均匀分布，假设）。"""
        return {o.identity:rng.randint(o.score_min,o.score_max) for o in self.opponents}

    def turn_curve(self,total,o,rng):
        """把总分分到每回合（段长与段内波动均为假设，见模块说明）。"""
        op_n=max(1,round(self.turns*0.4));rest=self.turns-op_n;mid_n=rest//2;ed_n=rest-mid_n
        out=[]
        for n,permil in ((op_n,o.op),(mid_n,o.mid),(ed_n,o.ed)):
            if n<=0:continue
            seg=total*permil/1000;weights=[1+rng.uniform(-0.2,0.2) for _ in range(n)];s=sum(weights)
            out+=[seg*w/s for w in weights]
        scaled=[math.floor(v) for v in out]
        scaled[-1]+=total-sum(scaled)
        return scaled


def field_for(idol_card_id,mode,position,catalog=None):
    own=catalog is None;c=catalog or Catalog()
    try:
        row=HIFPlanner(idol_card_id,c).configuration(mode,position)
        turns=row['battle_config']['turn']
        opponents=[Opponent(n['identity'],n['scoreMin'],n['scoreMax'],n['opScorePermil'],n['midScorePermil'],n['edScorePermil'],
                            border=n.get('produceExamBattleNpcMobId')==BORDER_MOB) for n in row['npcs']]  # fmt: skip
        return OpponentField(mode,position,turns,opponents)
    finally:
        if own:c.db.close()


def settled(raw,round_index):
    """本战 R1 原始分按 floor(raw×6/5) 计入总分；R2 原样。"""
    return raw*6//5 if round_index==1 else raw


def finals_result(r1_raw,r2_raw,r1_field,r2_field,rng):
    """本战两轮合计排名。对手 R1/R2 各自在区间内抽取（同一对手两轮独立，假设）。"""
    a,b=r1_field.sample_totals(rng),r2_field.sample_totals(rng)
    me=settled(r1_raw,1)+settled(r2_raw,2)
    totals={k:settled(a[k],1)+settled(b.get(k,0),2) for k in a}
    rank=1+sum(v>me for v in totals.values())  # 同分时玩家优先（已还原的客户端排序规则）
    return dict(total=me,rank=rank,win=rank==1,opponents=totals,margin=me-max(totals.values()))


def selection_result(raw,field,rng):
    """选拔：过线线是固定分；其余对手只影响名次显示（假设）。"""
    totals=field.sample_totals(rng)
    border=max((o.score_min for o in field.opponents if o.border),default=None)
    rank=1+sum(v>raw for v in totals.values())
    return dict(score=raw,rank=rank,border=border,passed=border is None or raw>=border,opponents=totals)


def win_probability(r1_scores,r2_scores,r1_field,r2_field,seed=0,draws=200):
    """离线评测用：给定若干 (R1, R2) 原始分样本，估计夺冠率（对手按区间抽样）。"""
    rng=random.Random(seed);wins=n=0
    for r1,r2 in zip(r1_scores,r2_scores):
        for _ in range(draws):
            n+=1;wins+=finals_result(r1,r2,r1_field,r2_field,rng)['win']
    return wins/n if n else 0.0
