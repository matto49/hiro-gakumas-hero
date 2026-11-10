"""Pinned support skill resolution and a bounded deterministic event evaluator.

Master ProduceTrigger has no predicate AST. Exact accepted IDs below are
cross-checked against the referenced skill descriptions, never parsed as code.
"""
from copy import deepcopy
from functools import lru_cache

from gakumas_exam.catalog import Catalog
from gakumas_exam.errors import Unsupported
from gakumas_exam.customize import nondefault
from .growth_scaling import checked,parameter_caps

TABLES={t:'SupportCardProduceSkillLevel'+t for t in ('Dance','Vocal','Visual','Assist')}
EXECUTION_SCOPE='growth-only-counterfactual'
EXAM_EXECUTION_SCOPE='growth-and-exam'
ATTR={'Vocal':'Vo','Dance':'Da','Visual':'Vi'}
SCALARS={'ProduceEffectType_'+a+'Addition':v for a,v in ATTR.items()}
SIMPLE={
    'p_trigger-start_customize','p_trigger-customize_produce_card',
    'p_trigger-start_present','p_trigger-start_shop','p_trigger-start_shop-no_description',
    'p_trigger-end_step_event_activity','p_trigger-end_step_event_school','p_trigger-start_refresh',
    'p_trigger-buy_shop_item_produce_card','p_trigger-buy_shop_item_produce_drink',
    'p_trigger-get_produce_drink','p_trigger-end_audition','p_trigger-change_produce_card',
    'p_trigger-get_produce_card-0000_0000-p_card_search-deck_all',
    'p_trigger-upgrade_produce_card-0000_0000-p_card_search-deck_all',
    'p_trigger-delete_produce_card-0000_0000-p_card_search-deck_all',
    'p_trigger-end_lesson_before_present-lesson','p_trigger-end_lesson-lesson_sp'}
LESSONS={}
for label,attribute in [('vocal','Vo'),('dance','Da'),('visual','Vi')]:
    for suffix,sp in [('',None),('_sp',True),('_normal',False)]:
        LESSONS['p_trigger-end_lesson-lesson_'+label+suffix]=(attribute,sp)
    LESSONS['p_trigger-end_lesson_before_present-lesson_'+label+'_sp']=(attribute,True)
CATEGORIES={}
for action in ('get','delete','upgrade'):
    for label,category in [('mental','MentalSkill'),('active','ActiveSkill')]:
        CATEGORIES['p_trigger-'+action+'_produce_card-0000_0000-p_card_search-'+label+'_skill-deck_all']='ProduceCardCategory_'+category


def exact(value,fields,label):
    if not isinstance(value,dict) or set(value)!=fields:raise Unsupported('Exact '+label+' fields required')


@lru_cache(maxsize=4)
def level_rows(commit):
    c=Catalog()
    try:
        if c.info['commit']!=commit:raise Unsupported('Support master changed')
        index={}
        for table in TABLES.values():
            for r in c.rows(table):index.setdefault((table,r['supportCardId']),[]).append(r)
        return index
    finally:c.db.close()


def compile_card(c,card_id,level,limit_rank,plan):
    checked(level,'Support level');card=c.one('SupportCard',card_id)
    if card['planType'] not in ('ProducePlanType_Common',plan):raise Unsupported('Support belongs to another plan')
    limits=[r for r in c.by_id('SupportCardLevelLimit',card['supportCardLevelLimitId']) if r['rank']==limit_rank]
    if len(limits)!=1 or not 1<=level<=limits[0]['levelLimit']:raise Unsupported('Support level exceeds actual limit rank')
    suffix=card['type'].replace('SupportCardType_','')
    if suffix not in TABLES:raise Unsupported('Support type not ported')
    active={}
    for row in level_rows(c.info['commit']).get((TABLES[suffix],card_id),[]):
        if row['supportCardLevel']>level:continue
        prior=active.get(row['order'])
        if prior and prior['supportCardLevel']==row['supportCardLevel']:raise Unsupported('Ambiguous support skill tier')
        if not prior or row['supportCardLevel']>prior['supportCardLevel']:active[row['order']]=row
    skills=[]
    for order,row in sorted(active.items()):
        raw=[r for r in c.by_id('ProduceSkill',row['produceSkillId']) if r['level']==row['produceSkillLevel']]
        if len(raw)!=1:raise Unsupported('Missing exact support skill level')
        skill=raw[0];entries=[]
        for i in (1,2,3):
            effect_id=skill['produceEffectId'+str(i)];trigger_id=skill['produceTriggerId'+str(i)]
            if bool(effect_id)!=bool(trigger_id):raise Unsupported('Partial support trigger/effect')
            if effect_id:entries.append(dict(effect=c.one('ProduceEffect',effect_id),trigger=c.one('ProduceTrigger',trigger_id),rate=skill['activationRatePermil'+str(i)]))
        if len(entries)!=1 or entries[0]['rate'] or skill['planType'] not in ('ProducePlanType_Common',plan) or nondefault(skill['produceType']) or nondefault(skill['produceSplitType']):
            raise Unsupported('Support skill shape/plan/random activation not ported')
        skills.append(dict(order=order,id=skill['id'],level=skill['level'],limit=checked(skill['activationCount'],'Support activation limit'),
            tier=deepcopy(row),description=deepcopy(skill['produceDescriptions']),**entries[0]))
    return dict(card=deepcopy(card),skills=skills,master_commit=c.info['commit'])




def trigger_matches(trigger,context,c):
    key=trigger['id']
    if key=='p_trigger-end_lesson-lesson_sp':return context.get('sp') is True
    if key in SIMPLE:return True
    if key in LESSONS:
        attribute,sp=LESSONS[key]
        return context.get('attribute')==attribute and (sp is None or context.get('sp') is sp)
    if key in CATEGORIES:
        spec=context.get('card')
        if not isinstance(spec,dict):raise Unsupported('Support card trigger needs exact affected card')
        return c.card(spec['card_id'],spec['upgrade'])['category']==CATEGORIES[key]
    raise Unsupported('Support trigger condition not ported: '+key)








