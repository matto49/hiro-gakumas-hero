"""触发条件判定：ExamExtensions.IsEffectTriggerFieldValid / IsEffectTriggerFieldStatusValid / IsFieldStatusTriggerStatusEffect。

trigger 为主数据 ProduceExamTrigger 行（整数枚举）：phaseTypes / phaseValues / fieldStatusTypes / fieldStatusValues /
fieldStatusCheckTypes / fieldStatusProduceCardSearchIds / produceCardSearchId / upperSearchCount / lowerSearchCount / lessonType …
"""
from . import f32
from .cards import is_search_target
from .master import E

FS = E('ProduceExamFieldStatusType')
LT = E('ProduceStepLessonType')
CAT = E('ProduceCardCategory')
POS = E('ProduceCardPositionType')
NOT = E('ProduceExamTriggerCheckType')['Not']
PH = E('ProduceExamPhaseType')


def _ge(v, threshold):
    return v >= threshold


def field_status(ex, kind, value, search, ctx=None):
    """IsFieldStatusTriggerStatusEffect（跳转表）。"""
    sc = ex.status
    if kind in (FS['PlayCardSkill'], FS['PlayCardLesson'], FS['PlayCardSupport'], FS['PlayCardSearch']):
        # 出牌日志（只算 UseHand / UsePool，b__125_0）；当前正在打的牌存在时先 SkipLast(1)，即看「上一张」
        plays = list(ex.play_log)
        if ex.playing is not None and plays:
            plays = plays[:-1]
        last = plays[-1] if plays else None
        if kind == FS['PlayCardSearch']:
            return last is not None and is_search_target(last, search)
        if kind == FS['PlayCardSkill']:
            return last is not None and last.category == CAT['MentalSkill']
        if kind == FS['PlayCardLesson']:
            return last is not None and last.category == CAT['ActiveSkill']
        return False
    if kind == FS['CardSearchCountUp']:
        return len(ex.GetSearchCardList(search, ctx)) >= value
    if kind == FS['ParameterBuff']:
        return sc.GetParameterBuffTurn(False) > 0
    if kind == FS['StaminaUpMultiple']:
        return f32.div(f32.i2f(ex.stamina), f32.i2f(ex.max_stamina)) >= f32.div(f32.i2f(value), 1000.0)
    if kind == FS['StaminaLessMultiple']:
        return f32.div(f32.i2f(ex.stamina), f32.i2f(ex.max_stamina)) <= f32.div(f32.i2f(value), 1000.0)
    if kind == FS['StaminaConsumptionDown']:
        return sc.IsStaminaConsumptionDown(False)
    if kind == FS['ConcentrationUp']:
        return sc.GetIdolStatusType() == 1 and (value == 0 or sc.GetIdolStatusStep() >= value)
    if kind == FS['PreservationUp']:
        t = sc.GetIdolStatusType()
        if t == 2 and (value == 0 or sc.GetIdolStatusStep() >= value):
            return True
        return t == 4
    if kind == FS['FullPowerUp']:
        return sc.GetIdolStatusType() == 3
    if kind == FS['NoBlock']:
        return ex.block == 0
    if kind == FS['TurnProgressUp']:
        return ex.current_turn > value
    if kind in (FS['ConditionThresholdMultiple'], FS['ConditionThresholdMultipleDown']):
        border = ex.clear_border or 1
        r = f32.div(f32.i2f(ex.judge_parameter), f32.i2f(border))
        v = f32.div(f32.i2f(value), 1000.0)
        return v <= r if kind == FS['ConditionThresholdMultiple'] else v >= r
    if kind == FS['LessonBuffUp']:
        return sc.GetLessonBuff(False) >= value
    if kind == FS['BlockUp']:
        return ex.block >= value
    if kind == FS['ReviewUp']:
        return sc.GetReview(False) >= value
    if kind == FS['GimmickLessonDebuffUp']:
        return sc.GetLessonDebuff(False) == value
    if kind == FS['GimmickSleepyUp']:
        return sc.GetBlockAddDownFix(False) >= value
    if kind == FS['ParameterBuffUp']:
        return sc.GetParameterBuffTurn(False) >= value
    if kind == FS['BlockAddDown']:
        return sc.IsBlockAddDown(False)
    if kind == FS['GimmickParameterDebuff']:
        return sc.GetParameterDebuffTurn() == value
    if kind == FS['RemainingTurn']:
        return ex.remain_turn <= value
    if kind == FS['CardPlayAggressiveUp']:
        return sc.GetAggressive(False) >= value
    if kind == FS['ParameterLessThan']:
        return ex.judge_parameter <= value
    if kind == FS['FullPowerPointUp']:
        return sc.GetFullPowerPoint(False) >= value
    if kind == FS['FullPowerPointGetSumUp']:
        return ex.counters.get('fullPowerPointGetSumCount', 0) >= value
    if kind == FS['NoStance']:
        return sc.GetIdolStatusType() == 0
    if kind == FS['StanceChangeCountUp']:
        return ex.counters.get('stanceChangeCount', 0) >= value
    if kind == FS['ConcentrationChangeCountUp']:
        return ex.counters.get('stanceConcentrationChangeCount', 0) >= value
    if kind == FS['PreservationChangeCountUp']:
        return ex.counters.get('stancePreservationChangeCount', 0) >= value
    if kind == FS['FullPowerChangeCountUp']:
        return ex.counters.get('stanceFullPowerChangeCount', 0) >= value
    if kind == FS['ParameterBuffMultiplePerTurnUp']:
        return sc.GetParameterBuffMultiplePerTurnTurn() >= value
    if kind == FS['EnthusiasticUp']:
        return sc.GetEnthusiasticBuff(False) >= value
    if kind == FS['EnchantCountUp']:
        # b__125_1：排除 TriggerMissionStatusEffect；有非计时器阶段（b__125_5：phase ∉ {TurnTimer, EndTurnTimer}）。前两项过滤待核
        n = 0
        for s in sc.effects:
            if s.cls == 'TriggerEffectStatusEffect' and any(p not in (PH['ExamTurnTimer'], PH['ExamEndTurnTimer'])
                                                           for p in (s.trigger.get('phaseTypes') or [])):
                n += 1
        return n >= value
    if kind == FS['TurnPlayCardCountUp']:
        return ex.turn_card_play_count >= value
    if kind == FS['DeckCardAllNoDuplicate']:
        cards = ex.cards_at(POS['DeckAll'])
        if not cards:
            return False
        ids = [c.card_id for c in cards]
        return len(ids) == len(set(ids))
    if kind == FS['DebuffCountUp']:
        from .status import DEBUFF_STATUS_CLASSES
        return sum(1 for s in sc.effects if s.cls in DEBUFF_STATUS_CLASSES) >= value
    return False


def field_status_valid(ex, trigger, ctx=None):
    """IsEffectTriggerFieldStatusValid：逐项判定，取反项（CheckType Not）结果需为 false。"""
    types = trigger.get('fieldStatusTypes') or []
    values = trigger.get('fieldStatusValues') or []
    checks = trigger.get('fieldStatusCheckTypes') or []
    searches = trigger.get('fieldStatusProduceCardSearchIds') or []
    for i, kind in enumerate(types):
        value = values[i] if i < len(values) else 0
        neg = (checks[i] if i < len(checks) else 0) == NOT
        sid = searches[i] if i < len(searches) else ''
        search = ex.master.search(sid) if sid else None
        if field_status(ex, kind, value, search, ctx) == neg:
            return False
    return True


def lesson_type_valid(ex, lesson_type):
    """IsEffectTriggerFieldValid 前段：LessonType 5/6/7 = 当前回合属性 Vo/Da/Vi（GetCurrentParameterType 1/2/3）。
    其他课程类型（Normal/Sp/Hard）只在课程（Lesson 考试类型）里判定，考试中按不满足处理（待核）。"""
    if not lesson_type:
        return True
    cur = ex.current_parameter_type()
    m = {LT['LessonVocal']: 1, LT['LessonDance']: 2, LT['LessonVisual']: 3}
    if lesson_type in m:
        return cur == m[lesson_type]
    return False


def trigger_field_valid(ex, trigger, ctx=None, target_cards=None):
    """IsEffectTriggerFieldValid：课程类型 → 场面状态 → 卡检索数量（lower ≤ n ≤ upper，0 表示不限）。"""
    if trigger is None:
        return True
    if not lesson_type_valid(ex, trigger.get('lessonType', 0)):
        return False
    if not field_status_valid(ex, trigger, ctx):
        return False
    sid = trigger.get('produceCardSearchId')
    if sid:
        search = ex.master.search(sid)
        if search.get('cardPositionType') == POS['Target'] and target_cards is None:
            return True
        if target_cards is not None:
            cards = [c for c in target_cards if is_search_target(c, search)]
        else:
            cards = ex.GetSearchCardList(search, ctx)
        n = len(cards)
        lo, hi = trigger.get('lowerSearchCount', 0), trigger.get('upperSearchCount', 0)
        if lo and n < lo:
            return False
        if hi and n > hi:
            return False
    return True


# ---------------------------------------------------------------- 触发判定（ExamEffectUtility.Is*Trigger）

def _phases(t):
    return t.get('phaseTypes') or []


def _v0(t):
    return (t.get('phaseValues') or [0])[0]


def phase_trigger(ex, t, phase, ctx=None):
    """IsPhaseTrigger = 场面条件 && 阶段包含。"""
    return t is not None and phase in _phases(t) and trigger_field_valid(ex, t, ctx)


def card_phase_trigger(ex, t, phase, cards, movable, ctx=None):
    """IsCardPhaseTrigger：场面条件、阶段包含；cardMovePositionType 非 0 时须等于 movable；
    检索的 cardPositionType 为 Target(11) 时要求事件卡里有检索命中的。"""
    if t is None or phase not in _phases(t):
        return False
    if not trigger_field_valid(ex, t, ctx, target_cards=cards):
        return False
    mp = t.get('cardMovePositionType', 0)
    if mp and mp != movable:
        return False
    sid = t.get('produceCardSearchId')
    if not sid:
        return True
    s = ex.master.search(sid)
    if s.get('cardPositionType') != POS['Target']:
        return True
    return any(is_search_target(c, s) for c in cards)


def change_effect_trigger(ex, t, key, d, ctx=None):
    """IsChangeEffectTrigger：phaseValue[0] ≤ 增量、阶段含 ExamStatusChange、effectTypes 含 key。"""
    if t is None or PH['ExamStatusChange'] not in _phases(t):
        return False
    pv = t.get('phaseValues') or []
    if pv and pv[0] > d:
        return False
    if key not in (t.get('effectTypes') or []):
        return False
    return trigger_field_valid(ex, t, ctx)


def consume_trigger(ex, t, key, ctx=None):
    """IsConsumeEffectTrigger：场面条件、阶段含 ExamBuffConsume；effectTypes 为空 = 任意状态，否则须含 key。"""
    if t is None or PH['ExamBuffConsume'] not in _phases(t):
        return False
    if not trigger_field_valid(ex, t, ctx):
        return False
    ets = t.get('effectTypes') or []
    return not ets or key in ets


is_consume_trigger = consume_trigger


def interval_trigger(ex, t, phase, n, ctx=None, at_least_one=True):
    """×× Interval 类（ParameterBuffUp 等）：阶段包含 && (n ≥ 1) && n % phaseValue[0] == 0。"""
    if t is None or phase not in _phases(t):
        return False
    v = _v0(t)
    if at_least_one and n < 1:
        return False
    if v == 0:
        if n != 0:
            return False
    elif n % v != 0:
        return False
    return trigger_field_valid(ex, t, ctx)


def timer_trigger(ex, t, phase, n, ctx=None):
    """计数等于型（TurnTimer / StanceChangeCount）：phaseValue[0] == n。"""
    return t is not None and phase in _phases(t) and _v0(t) == n and trigger_field_valid(ex, t, ctx)


def is_preservation(x):
    return x in (2, 4)


def same_idol_status_genre(a, b):
    """ExamExtensions.IsSameIdolStatusTypeGenre（待核：推测同类型或同为温存系）。"""
    return a == b or (is_preservation(a) and is_preservation(b))


def stance_change_trigger(ex, t, before, after, step, ctx=None):
    """IsStanceChangeTrigger：进入強気(37) / 温存系(38)；のんびり按 3 段；phaseValue 0 = 从别的类型切换来。"""
    if is_preservation(after):
        phase = PH['ExamStanceChangePreservation']
    elif after == 1:
        phase = PH['ExamStanceChangeConcentration']
    else:
        return False
    if after == 4:
        step = 3
    if not phase_trigger(ex, t, phase, ctx):
        return False
    v = _v0(t)
    if v == 0:
        return not same_idol_status_genre(before, after)
    return step == v


def stance_change_from_trigger(ex, t, before, after, step, ctx=None):
    """IsStanceChangeFromTrigger：离开温存系(46) / 強気(45)。"""
    if is_preservation(before):
        phase = PH['ExamStanceChangeFromPreservation']
    elif before == 1:
        phase = PH['ExamStanceChangeFromConcentration']
    else:
        return False
    if not phase_trigger(ex, t, phase, ctx):
        return False
    v = _v0(t)
    if v == 0:
        return not same_idol_status_genre(before, after)
    return step == v


def target_search_ok(ex, t, card):
    """IsCardPlayValidTrigger / IsCardPlayAfterValidTrigger：触发检索的 cardPositionType 为 Target(11) 时，
    打出的卡须满足检索（真机抓取 atuy #4：「每打出 2 张元気卡」在非元気卡上不触发）。"""
    sid = t.get('produceCardSearchId')
    if not sid:
        return True
    s = ex.master.search(sid)
    if s.get('cardPositionType') != POS['Target']:
        return True
    return is_search_target(card, s)
