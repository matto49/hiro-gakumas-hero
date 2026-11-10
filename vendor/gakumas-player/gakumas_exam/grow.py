"""卡的成长（定制与考试中成长）：ExamEffectUtility.GrowEffectAffectProduceCardEffectList 及卡级成长。

效果列表（GrowEffectAffectProduceCardEffectList 的还原）：
1. 先按成长列表顺序处理结构类：EffectAdd（追加 playProduceExamEffectId）、EffectDelete（删除 targetPlayProduceExamEffectIds 命中的效果）、
   EffectChange（命中的效果原位替换为 playProduceExamEffectId 的新副本）。
2. 再按顺序处理值类：选中的效果字段 = Max(1, 字段 ± value)（Reduce 类为减）。作用范围：
   - LessonAdd/Reduce → IsLessonIconValueEffectType 的效果（1,23,52,56,118,132,147,183,184,185）的 effectValue1
   - LessonCountAdd/Reduce → IsLessonEffectType 的效果（22 种打分类）的 effectCount
   - BlockAdd/Reduce → IsBlockIconValueEffectType（3,133,143,161）的 effectValue1
   - FullPowerPoint(49)、LessonBuff(10)、Review(31)、Aggressive(42)、CardDraw(4)、LessonDependBlock(19)、
     LessonDependExamReview(124)、LessonDependExamCardPlayAggressive(125) 的 effectValue1
   - ParameterBuffTurn(2)、ParameterBuffMultiplePerTurn(129)、StaminaConsumptionDownTurn(5)、StaminaConsumptionAddTurn(69) 的 effectTurn
卡级（费用 / 触发 / 初始 / 去向 / 卡附魔 / 效果组）见 apply_card_grows。定制成长 = ProduceCardData.UpdateCustomize：
各分支取 ProduceCardCustomize(id, 当前次数) 一行的 produceCardGrowEffectIds 展开（SelectMany），不逐级累加。
"""
from .master import E

GT = E('ProduceCardGrowEffectType')
ET = E('ProduceExamEffectType')
COST = E('ExamCostType')

LESSON_ICON = {1, 23, 52, 56, 118, 132, 147, 183, 184, 185}
LESSON_TYPES = {1, 19, 23, 29, 52, 56, 118, 124, 125, 131, 132, 142, 144, 147, 163, 164, 165, 183, 184, 185, 186, 216}
BLOCK_ICON = {3, 133, 143, 161}

# 值类：成长类型 → (作用的效果类型集合, 字段, 是否减少)
VALUE = {}
for name, targets, field in [
    ('Lesson', LESSON_ICON, 'effectValue1'), ('LessonCount', LESSON_TYPES, 'effectCount'),
    ('Block', BLOCK_ICON, 'effectValue1'), ('FullPowerPoint', {49}, 'effectValue1'), ('LessonBuff', {10}, 'effectValue1'),
    ('Review', {31}, 'effectValue1'), ('Aggressive', {42}, 'effectValue1'), ('CardDraw', {4}, 'effectValue1'),
    ('ParameterBuffTurn', {2}, 'effectTurn'), ('ParameterBuffMultiplePerTurn', {129}, 'effectTurn'),
    ('StaminaConsumptionDownTurn', {5}, 'effectTurn'), ('StaminaConsumptionAddTurn', {69}, 'effectTurn')]:
    VALUE[GT[name + 'Add']] = (targets, field, False)
    VALUE[GT[name + 'Reduce']] = (targets, field, True)
VALUE[GT['LessonDependBlockAdd']] = ({19}, 'effectValue1', False)
VALUE[GT['LessonDependExamReviewAdd']] = ({124}, 'effectValue1', False)
VALUE[GT['LessonDependExamCardPlayAggressiveAdd']] = ({125}, 'effectValue1', False)

def _cost_type(t):
    """ExamExtensions.GetCostType（表，成长类型 40..54）。"""
    return {40: COST['ExamLessonBuff'], 41: COST['ExamLessonBuff'], 42: COST['ExamReview'], 43: COST['ExamReview'],
            44: COST['ExamCardPlayAggressive'], 45: COST['ExamCardPlayAggressive'], 46: COST['ExamParameterBuff'],
            47: COST['ExamParameterBuff'], 48: COST['ExamFullPowerPoint'], 49: COST['ExamFullPowerPoint'],
            53: COST['ExamParameterBuffMultiplePerTurn'], 54: COST['ExamParameterBuffMultiplePerTurn']}.get(t, 0)


# ExamExtensions.IsReduceType：位掩码 0x00415500aaaa5554
REDUCE_MASK = 0x00415500aaaa5554


def is_reduce(t):
    return t < 0x37 and (REDUCE_MASK >> t) & 1 == 1


def _last(grows, t):
    hit = None
    for g in grows:
        if g['effectType'] == t:
            hit = g
    return hit


def affect_effect_list(master, entries, grows):
    """entries: [[trigger_id, effect 行, once]]（原位修改，effect 行先复制）。"""
    for g in grows:
        t = g['effectType']
        if t == GT['EffectAdd']:
            entries.append(['', dict(master.effect(g['playProduceExamEffectId'])), False])
        elif t == GT['EffectDelete']:
            ids = set(g.get('targetPlayProduceExamEffectIds') or [])
            entries[:] = [x for x in entries if x[1]['id'] not in ids]
        elif t == GT['EffectChange']:
            ids = set(g.get('targetPlayProduceExamEffectIds') or [])
            for x in entries:
                if x[1]['id'] in ids:
                    x[1] = dict(master.effect(g['playProduceExamEffectId']))
    for g in grows:
        spec = VALUE.get(g['effectType'])
        if spec is None:
            continue
        targets, field, reduce = spec
        d = -g['value'] if reduce else g['value']
        for x in entries:
            if x[1]['effectType'] in targets:
                x[1] = dict(x[1])
                x[1][field] = max(1, x[1][field] + d)
    return entries


def apply_card_grows(master, row, grows):
    """ProduceCardData / ExamCardData 的成长 getter（ExamEffectUtility.GetAffectGrowEffect*）：
    - 体力 / 贯通体力（GetAffectGrowEffectStaminaCost）：原值逐条 ± value，最后一次 Max(0, …)；没有成长时原值。
    - 状态费用（GetAffectGrowEffectBuffCost）：只取 GetCostType(成长) == 卡的 costType 的条目，同样先加减后 Max(0, …)。
    - 出牌条件（…PlayTrigger）、卡附魔（…StatusEffect）、初始（…IsInitial）、
      去向（…CardMovePositionType）：同类成长取最后一条（LastOrDefault），不检查目标。
    - 效果触发（…PlayEffectTriggerList）：触发 id 命中 targetPlayEffectProduceExamTriggerIds 的原位替换。
    - 效果组（…EffectGroup）：成长后各效果的 effectGroupIds + 卡附魔的效果组，去重。
    """
    if '_effects' in row:   # 第二层（考试中成长）：在定制后的结果上再套
        entries = [list(x) for x in row['_effects']]
    else:
        entries = [[link.get('produceExamTriggerId', ''), dict(master.effect(link['produceExamEffectId'])), link.get('isOncePlayEffect', False)]
                   for link in row['playEffects']]
    row.setdefault('_penetrate', row['forceStamina'] > 0)
    if grows:
        # 体力费用只有一个值（ProduceCard.get_StaminaCostRaw：forceStamina>0 时取它，否则 stamina）；
        # GetAffectGrowEffectStaminaCost 只取与「贯通」标志同类的成长（贯通卡只吃 CostPenetrate*，普通卡只吃 Cost*）。
        pen = row.get('_penetrate')
        if pen is None:
            pen = row['_penetrate'] = row['forceStamina'] > 0
        field = 'forceStamina' if pen else 'stamina'
        kinds = (GT['CostPenetrateAdd'], GT['CostPenetrateReduce']) if pen else (GT['CostAdd'], GT['CostReduce'])
        hits = [g for g in grows if g['effectType'] in kinds]
        if hits:
            v = row[field]
            for g in hits:
                v = v - g['value'] if is_reduce(g['effectType']) else v + g['value']
            row[field] = max(0, v)
        hits = [g for g in grows if _cost_type(g['effectType'])]
        if hits:
            v = row['costValue']
            for g in hits:
                if _cost_type(g['effectType']) == row['costType']:
                    v = v - g['value'] if is_reduce(g['effectType']) else v + g['value']
            row['costValue'] = max(0, v)
        g = _last(grows, GT['PlayTriggerChange'])
        if g is not None:
            row['playProduceExamTriggerId'] = g.get('playProduceExamTriggerId') or ''
        g = _last(grows, GT['CardStatusEnchantChange'])
        if g is not None:
            row['produceCardStatusEnchantId'] = g.get('produceCardStatusEnchantId') or ''
        if _last(grows, GT['InitialAdd']) is not None:
            row['isInitial'] = True
        g = _last(grows, GT['PlayMovePositionTypeChange'])
        if g is not None:
            row['playMovePositionType'] = g['playMovePositionType']
        for g in grows:
            if g['effectType'] == GT['PlayEffectTriggerChange']:
                targets = set(g.get('targetPlayEffectProduceExamTriggerIds') or [])
                for x in entries:
                    if x[0] in targets:
                        x[0] = g['playEffectProduceExamTriggerId']
        affect_effect_list(master, entries, grows)
    row['_effects'] = [tuple(x) for x in entries]
    groups = []
    for _, e, _ in entries:
        for x in e.get('effectGroupIds') or []:
            if x not in groups:
                groups.append(x)
    se = master.get('ProduceCardStatusEnchant', row['produceCardStatusEnchantId']) if row.get('produceCardStatusEnchantId') else None
    for x in (se or {}).get('effectGroupIds') or []:
        if x not in groups:
            groups.append(x)
    row['effectGroupIds'] = groups
    return row


# ---- 考试中成长（ExamAddGrowEffect / 卡附魔）

STAMINA_COST = {GT['CostReduce'], GT['CostAdd'], GT['CostPenetrateReduce'], GT['CostPenetrateAdd']}
PENETRATE = {GT['CostPenetrateReduce'], GT['CostPenetrateAdd']}


def inverse(t):
    """ExamExtensions.GetInverseEffectType（表）：成对的加 / 减互换，其余不变。"""
    return INVERSE.get(t, t)


INVERSE = {}
for _n, _v in list(GT.items()):
    for _a, _b in (('Add', 'Reduce'), ('Reduce', 'Add')):
        if _n.endswith(_a) and _n[:-len(_a)] + _b in GT:
            INVERSE[_v] = GT[_n[:-len(_a)] + _b]
# 表里 LessonDepend*Add 三项没有配对（映射到自己）
for _n in ('LessonDependBlockAdd', 'LessonDependExamCardPlayAggressiveAdd', 'LessonDependExamReviewAdd'):
    INVERSE.pop(GT[_n], None)


def positive_key(t):
    """GetPositiveEffectType：负面类型取反向类型；用作合并键时等价于「同一对」。"""
    return min(t, inverse(t))


def is_valid(card, g):
    """ExamEffectUtility.IsValidCardGrowEffect(IProduceCardData, grow)：
    IsValidCostCardGrowEffect或 当前效果列表里有任一效果适用（effect 版，作用集合同 VALUE）。
    费用类：体力类要求卡无状态费用（costType == 0），且贯通与否与卡一致；状态费用类要求 GetCostType(成长) == 卡的 costType。"""
    t = g['effectType']
    if t in STAMINA_COST:
        if card.cost_type == 0 and (t in PENETRATE) == bool(card.force_stamina):
            return True
    elif _cost_type(t):
        if _cost_type(t) == card.cost_type:
            return True
    spec = VALUE.get(t)
    if spec is None:
        return False
    return any(e['effectType'] in spec[0] for _, e, _ in card.play_effects())


def merge(grows, g, lesson_add_multiple=1.0):
    """ExamEffectUtility.AddGrowEffect（作用于 _growEffectExamStartAfterList）：
    LessonAdd 先乘 GetGrowEffectLessonAddAdditiveMultiple 向上取整（fcvtps，无 epsilon）；
    已有同一对类型的条目：同向 value 相加；反向相减，差 0 删除，差为负则条目翻成反向类型、取绝对值。没有则追加副本。"""
    from . import f32
    g = dict(g)
    if g['effectType'] == GT['LessonAdd'] and lesson_add_multiple != 1.0:
        g['value'] = f32.ceil_i(f32.mul(lesson_add_multiple, f32.i2f(g['value'])))
    key = positive_key(g['effectType'])
    for x in grows:
        if positive_key(x['effectType']) == key:
            if is_reduce(g['effectType']) == is_reduce(x['effectType']):
                x['value'] = g['value'] + x['value']
            else:
                d = x['value'] - g['value']
                if d == 0:
                    grows.remove(x)
                elif d > 0:
                    x['value'] = d
                else:
                    x['effectType'] = inverse(x['effectType'])
                    x['value'] = -d
            return grows
    grows.append(g)
    return grows
