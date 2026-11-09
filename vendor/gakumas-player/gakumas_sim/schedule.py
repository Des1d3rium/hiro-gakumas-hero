"""Complete HIF calendar and exact base-reward training previews.

The calendar is an observed community table cross-checked against master step
counts. Dynamic SP/substat/offers/support triggers are never synthesized here.
"""
from copy import deepcopy
import hashlib
import json
from .catalog import Catalog,ROOT
from .core import Unsupported
from .decisions import Decision
from .policies import SkillPolicy
from .growth_scaling import checked,scale_floor,parameter_caps

STATS={'Vo':'Vocal','Da':'Dance','Vi':'Visual'}


class HIFSchedule:
    def __init__(self,produce_id='produce-007',catalog=None):
        self.catalog=catalog or Catalog()
        self.path=ROOT/'data/schedules/hif.json'
        self.document=json.loads(self.path.read_text())
        if self.document['master_commit']!=self.catalog.info['commit']:raise Unsupported('Schedule/master version mismatch')
        source=ROOT/'data/community/hif-wiki-tables.json'
        if hashlib.sha256(source.read_bytes()).hexdigest()!=self.document['provenance']['calendar_source_sha256']:
            raise Unsupported('Calendar source changed; rebuild explicitly')
        if produce_id not in self.document['modes']:raise Unsupported('Only HIF has a fully sourced per-day calendar')
        self.mode=self.document['modes'][produce_id];self.produce_id=produce_id
        if len(self.mode['stages'])!=self.mode['produce']['steps']:raise Unsupported('Incomplete calendar')

    def stage(self,position):
        if not 1<=position<=len(self.mode['stages']):raise Unsupported('Schedule position out of range')
        return deepcopy(self.mode['stages'][position-1])

    def export(self):
        return dict(produce_id=self.produce_id,provenance=self.document['provenance'],**deepcopy(self.mode))

    def training_scene(self,position,state,bindings):
        """Input includes observed SP flags/substats. No guessed probabilities."""
        stage=self.stage(position)
        if 'training' not in stage['options']:raise Unsupported('This calendar slot has no public training')
        if set(bindings.get('sp_flags',{}))!=set(STATS) or set(bindings.get('sub_stats',{}))!=set(STATS):
            raise Unsupported('Need observed SP flags and substat for all three training options')
        if any(type(v) is not bool for v in bindings['sp_flags'].values()):raise Unsupported('SP flags must be observed booleans')
        if set(bindings.get('parameter_bonus_permil',{}))!=set(STATS):
            raise Unsupported('Need all already-applied parameter coefficients')
        for value in bindings['parameter_bonus_permil'].values():checked(value,'parameter coefficient',1000000)
        checked(bindings.get('star_bonus_permil'),'star coefficient',1000000)
        caps=parameter_caps({'parameter_caps':bindings['parameter_caps']} if 'parameter_caps' in bindings else {},self.catalog,self.produce_id)
        options=[];origins={};star_values={};base_star_values={}
        for primary,enum in STATS.items():
            secondary=bindings['sub_stats'][primary]
            if secondary not in STATS or secondary==primary:raise Unsupported('Substat must differ from selected primary stat')
            sp=bindings['sp_flags'][primary]
            candidates=[self.catalog.one('ProduceStepOpenLesson',key) for key in stage['open_lesson_ids']]
            choices=[r for r in candidates if r['subParameterType']=='ProduceParameterType_'+STATS[secondary]
                     and ('-sp-' in r['id'])==sp]
            if len(choices)!=1:raise Unsupported('Ambiguous training master row')
            row=choices[0]
            options.append(dict(id=primary,kind='train',hp_delta=-row['stamina'],
                                stat_delta={primary:scale_floor(row['mainParameter'],bindings['parameter_bonus_permil'][primary]),
                                            secondary:scale_floor(row['subParameter'],bindings['parameter_bonus_permil'][secondary])},sp=sp))
            origins[primary]=row['id'];star_values[primary]=scale_floor(row['star'],bindings['star_bonus_permil']);base_star_values[primary]=row['star']
        # Decision is a composed test context whose numeric base rewards come
        # directly from master. This label does not assert a full native step.
        scene=dict(schema_version=1,id=self.produce_id+f'-step-{position}',module='training',
                   provenance=dict(kind='synthetic',source_values='pinned-master',commit=self.catalog.info['commit'],
                                   master_rows=origins,scope='master base with supplied growth coefficients; no support/item triggers'),
                   definitions={},state=deepcopy(state),options=options,context=dict(
                       stat_targets=bindings['stat_targets'],hp_reserve=bindings.get('hp_reserve',0),
                       caps=caps))
        return scene,dict(master_rows=origins,star_values=star_values,base_star_values=base_star_values,base_p_points=stage['base_p_points'])

    def choose_training(self,position,state,bindings):
        scene,extra=self.training_scene(position,state,bindings)
        env=Decision(scene);obs=env.reset();action,reason=SkillPolicy().choose(obs,env.legal_actions())
        env.step(action,reason)
        star_gain=0
        if action['id']!='skip':
            env.state['p_points']+=extra['base_p_points'];star_gain=extra['star_values'][action['id']]
            env.state['star']=env.state.get('star',0)+star_gain
        return dict(position=position,action=action,reason=reason,after=env.state,star_gain=star_gain,
                    base_star_gain=0 if action['id']=='skip' else extra['base_star_values'][action['id']],
                    master_rows=extra['master_rows'],scope=scene['provenance']['scope'])

    def exam_options(self,position,idol_card_id):
        stage=self.stage(position)
        if stage['kind'] not in ('exam','round1','round2'):raise Unsupported('This calendar slot is not an exam')
        idol=self.catalog.one('IdolCard',idol_card_id)
        reference=idol['produceStepAuditionDifficultyId']
        kind={'round1':'AuditionMid1','round2':'AuditionFinal'}.get(stage['kind'])
        if stage['kind']=='exam':kind={1:'AuditionMid1',2:'AuditionMid2',3:'AuditionFinal'}[stage['exam_number']]
        candidates=[r for r in self.catalog.by_id('ProduceStepAuditionDifficulty',reference)
                    if r['id']==reference and r['produceId']==self.produce_id and r['stepType']=='ProduceStepType_'+kind]
        if not candidates:raise Unsupported('No matching exam configuration for idol and phase')
        result=[]
        for r in candidates:
            battle=self.catalog.one('ProduceExamBattleConfig',r['produceExamBattleConfigId'])
            result.append(dict(difficulty=r,battle_config=battle,
                               score_config_rows=self.catalog.by_id('ProduceExamBattleScoreConfig',battle['produceExamBattleScoreConfigId']),
                               npc_reference=r['produceExamBattleNpcGroupId'],gimmick_reference=r['produceExamGimmickEffectGroupId']))
        return dict(position=position,idol_card=idol_card_id,configurations=result,
                    unresolved=['native score bonus and attribute order','all gimmick/item/status effects','NPC outcomes'],
                    scope='exact master configurations; not a fully executable exam')
