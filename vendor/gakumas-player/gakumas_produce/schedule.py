"""Complete HIF calendar and exact base-reward training previews.

The calendar is an observed community table cross-checked against master step
counts. Dynamic SP/substat/offers/support triggers are never synthesized here.
"""
from copy import deepcopy
import hashlib
import json
from gakumas_exam.catalog import Catalog, ROOT
from gakumas_exam.errors import Unsupported
from .growth_scaling import checked,scale_floor,parameter_caps

STATS={'Vo':'Vocal','Da':'Dance','Vi':'Visual'}


class HIFSchedule:
    def __init__(self,produce_id='produce-007',catalog=None):
        self.catalog=catalog or Catalog()
        self.path=ROOT/'data/schedules/hif.json'
        self.document=json.loads(self.path.read_text())
        if self.document['master_commit']!=self.catalog.info['commit']:raise Unsupported('Schedule/master version mismatch')
        source=ROOT/'data/community/hif-wiki-tables.json'
        # Windows 上 Git（core.autocrlf=true）会把它检出成 CRLF：行尾统一成 LF 再算，和生成时一致
        if hashlib.sha256(source.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=self.document['provenance']['calendar_source_sha256']:
            raise Unsupported('Calendar source changed; rebuild explicitly')
        if produce_id not in self.document['modes']:raise Unsupported('Only HIF has a fully sourced per-day calendar')
        self.mode=self.document['modes'][produce_id];self.produce_id=produce_id
        if len(self.mode['stages'])!=self.mode['produce']['steps']:raise Unsupported('Incomplete calendar')

    def stage(self,position):
        if not 1<=position<=len(self.mode['stages']):raise Unsupported('Schedule position out of range')
        return deepcopy(self.mode['stages'][position-1])

    def export(self):
        return dict(produce_id=self.produce_id,provenance=self.document['provenance'],**deepcopy(self.mode))



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
