"""Resolve a bounded set of raw customization operations without mutating master.

The current level selects ONE master row per branch, as in the native resolver.
Historical nominal fees and consumed slots accumulate separately. EffectChange
still targets an original effect ID across replacements on that same slot.
"""
from copy import deepcopy
from .errors import Unsupported


def nondefault(value):
    return value not in ('',0,False,[],None) and not (isinstance(value,str) and value.endswith('_Unknown'))


# 定制的 overwrite 标签 → 其唯一 EffectAdd 指令必须加的效果类别（标签只是显示注解，原生按 grow ID 执行）
OVERWRITE_ANNOTATIONS={'LessonDependBlockAdd':'ExamLessonDependBlock','BlockAdd':'ExamBlock','LessonAdd':'ExamLesson',
                       'AggressiveAdd':'ExamCardPlayAggressive','LessonBuffAdd':'ExamLessonBuff','ReviewAdd':'ExamReview',
                       'LessonDependExamReviewAdd':'ExamLessonDependExamReview',
                       'LessonDependExamCardPlayAggressiveAdd':'ExamLessonDependExamCardPlayAggressive'}


def resolve(catalog,card_id,upgrade,customizations=None):
    if not isinstance(card_id,str) or not card_id or type(upgrade) is not int or upgrade<0:
        raise Unsupported('Card identity needs nonempty id and nonnegative integer upgrade')
    card=deepcopy(catalog.card(card_id,upgrade));choices=[] if customizations is None else customizations
    if not isinstance(choices,list):raise Unsupported('Customizations must be a list of {id,count}')
    slots=0;seen=set();cost=0;trace=[];effects={}
    origins=[entry['produceExamEffectId'] for entry in card['playEffects']]
    for choice in choices:
        if not isinstance(choice,dict) or set(choice)!={'id','count'}:
            raise Unsupported('Customization requires exact id/count')
        key,count=choice['id'],choice['count']
        if not isinstance(key,str) or not key:raise Unsupported('Customization id must be a nonempty string')
        if key in seen or key not in card['produceCardCustomizeIds']:
            raise Unsupported('Duplicate/unavailable customization branch')
        if type(count) is not int or count<1:raise Unsupported('Positive customization count required')
        seen.add(key);slots+=count
        if slots>card['maxCustomizeCount']:raise Unsupported('Customization slot limit exceeded')
        for level in range(1,count+1):
            matches=[r for r in catalog.by_id('ProduceCardCustomize',key) if r['customizeCount']==level]
            if len(matches)!=1:raise Unsupported('Missing exact customization level')
            row=matches[0]
            cost+=row['producePoint']
        # ProduceCardData.UpdateCustomize resolves (id, current count) once;
        # the ordinary data provider returns this exact master row. Its grow
        # IDs replace the lower-level branch definition, not add to it.
        level=count
        if nondefault(row['overwriteProduceCardGrowEffectType']):
            # Native ordinary IProduceCardCustomizeData exposes only id/count/
            # grow IDs; UpdateCustomize flattens the IDs, not this type label.
            # Admit the verified score-add annotation without reinterpreting
            # its actual EffectAdd instruction as a modification of old slots.
            # 2026-10-31：同一理由推广到其他「标签 = 所加效果的类别」的注解（元気 / 好印象 / やる気 / パラメータ 等，
            # 例如 夢色リップ、紅葉ランニング、星屑センセーション 的 070 定制）。所加效果作为独立条目执行
            # （元気 也会再吃一次やる気加成）——与原生 UpdateCustomize 只展开 grow ID 的行为一致，未用实机日志核对。
            ids=row['produceCardGrowEffectIds']
            label=row['overwriteProduceCardGrowEffectType'].split('_',1)[-1]
            if label not in OVERWRITE_ANNOTATIONS or len(ids)!=1:
                raise Unsupported('Customization overwrite type not ported')
            grow=catalog.one('ProduceCardGrowEffect',ids[0])
            if (grow['effectType']!='ProduceCardGrowEffectType_EffectAdd'
                or catalog.one('ProduceExamEffect',grow['playProduceExamEffectId'])['effectType']!='ProduceExamEffectType_'+OVERWRITE_ANNOTATIONS[label]):
                raise Unsupported('Customization score annotation disagrees with grow instruction')
        for grow_id in row['produceCardGrowEffectIds']:
            grow=catalog.one('ProduceCardGrowEffect',grow_id)
            kind=grow['effectType'].split('_',1)[-1];value=grow['value']
            permitted={'id','effectType','value','effectGroupIds'}
            if kind in {'CostReduce','CostPenetrateReduce','CostAggressiveReduce','CostReviewReduce','InitialAdd','AggressiveAdd','BlockAdd','LessonDependBlockAdd','LessonDependExamCardPlayAggressiveAdd','ReviewAdd',
                        'LessonAdd','LessonDependExamReviewAdd','StaminaConsumptionDownTurnAdd'}:
                pass
            elif kind=='PlayTriggerChange':permitted|={'playProduceExamTriggerId','targetPlayEffectProduceExamTriggerIds'}
            elif kind=='PlayEffectTriggerChange':permitted|={'playEffectProduceExamTriggerId','targetPlayEffectProduceExamTriggerIds'}
            elif kind=='EffectChange':permitted|={'playProduceExamEffectId','targetPlayProduceExamEffectIds'}
            elif kind=='EffectAdd':permitted|={'playProduceExamEffectId'}
            else:raise Unsupported('Customization grow type:'+kind)
            for field,v in grow.items():
                if field not in permitted and nondefault(v):raise Unsupported('Customization semantic field:'+field)
            if kind=='CostReduce':card['stamina']=max(0,card['stamina']-value)
            elif kind=='CostPenetrateReduce':card['forceStamina']=max(0,card['forceStamina']-value)
            elif kind=='CostAggressiveReduce':
                if card['costType']!='ExamCostType_ExamCardPlayAggressive':raise Unsupported('Customization cost channel mismatch')
                card['costValue']=max(0,card['costValue']-value)
            elif kind=='CostReviewReduce':
                if card['costType']!='ExamCostType_ExamReview':raise Unsupported('Customization cost channel mismatch')
                card['costValue']=max(0,card['costValue']-value)
            elif kind=='InitialAdd':card['isInitial']=True
            elif kind=='PlayTriggerChange':
                if card['playProduceExamTriggerId'] not in grow['targetPlayEffectProduceExamTriggerIds']:
                    raise Unsupported('Customization play trigger target mismatch')
                card['playProduceExamTriggerId']=grow['playProduceExamTriggerId']
            elif kind=='PlayEffectTriggerChange':
                hits=[e for e in card['playEffects'] if e['produceExamTriggerId'] in grow['targetPlayEffectProduceExamTriggerIds']]
                if not hits:raise Unsupported('Customization body condition target missing')
                for entry in hits:entry['produceExamTriggerId']=grow['playEffectProduceExamTriggerId']
            elif kind=='EffectAdd':
                card['playEffects'].append(dict(produceExamTriggerId='',produceExamEffectId=grow['playProduceExamEffectId'],isOncePlayEffect=False,hideIcon=False))
                origins.append(grow['playProduceExamEffectId'])
            elif kind=='EffectChange':
                hits=[i for i,origin in enumerate(origins) if origin in grow['targetPlayProduceExamEffectIds']]
                if not hits:raise Unsupported('Customization original effect target missing')
                for i in hits:card['playEffects'][i]['produceExamEffectId']=grow['playProduceExamEffectId']
            else:
                target={'AggressiveAdd':'ExamCardPlayAggressive','BlockAdd':'ExamBlock','LessonDependBlockAdd':'ExamLessonDependBlock',
                        'ReviewAdd':'ExamReview',  # 好印象+N（幸せのおまじない、星屑センセーション 等的定制）
                        'LessonAdd':'ExamLesson',  # パラメータ+N：加在每段打点上（effectValue1 是单段值）
                        'LessonDependExamReviewAdd':'ExamLessonDependExamReview',  # 好印象の N% 分パラメータ：‰ 相加
                        'StaminaConsumptionDownTurnAdd':'ExamStaminaConsumptionDown',  # 消費体力減少 回合数 +N
                        'LessonDependExamCardPlayAggressiveAdd':'ExamLessonDependExamCardPlayAggressive'}[kind]
                hits=0;modified=set()
                for entry in card['playEffects']:
                    identity=entry['produceExamEffectId']
                    if identity in modified:continue
                    effect=effects.get(identity) or deepcopy(catalog.one('ProduceExamEffect',identity))
                    if effect['effectType']=='ProduceExamEffectType_'+target:
                        # Local overrides only; never write to Catalog's cached raw rows.
                        if kind=='StaminaConsumptionDownTurnAdd':effect['effectTurn']+=value
                        else:effect['effectValue1']+=value
                        if kind=='LessonDependExamCardPlayAggressiveAdd':effect['effectValue1']=max(1,effect['effectValue1'])
                        effects[identity]=effect;hits+=1;modified.add(identity)
                if not hits:raise Unsupported('Customization direct effect target missing:'+target)
            trace.append(dict(customize_id=key,level=level,grow_id=grow_id))
    return card,effects,dict(customizations=deepcopy(choices),slots=slots,produce_points=cost,trace=trace)
