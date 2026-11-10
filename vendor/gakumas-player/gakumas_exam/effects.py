"""效果执行器：按 ProduceExamEffectType 分派（ExamEffectUtility.CreateExecutor）。

每个执行器按客户端逻辑实现（得分 / 元気的公式同客户端）。
未实现的类型记 'unimplemented' 事件（回放时可见），不静默跳过。
"""
from . import f32
from .cards import new_guid
from .master import E

ET = E('ProduceExamEffectType')
NAME = {v: k for k, v in ET.items()}
MOVE = E('ProduceCardMovePositionType')
POS = E('ProduceCardPositionType')
RANGE = E('ProducePickRangeType')


# ---------------------------------------------------------------- 数值

def CalculateAddingParameter(ex, value, ctx=None, add=None):
    sc = ex.status
    st = ex.setting
    if sc.IsSlump():
        return 0
    pb = 1.0
    if sc.IsParameterBuff():
        x = f32.add(f32.permil(st['examParameterBuffPermil']), -1.0)
        if sc.IsParameterBuffMultiplePerTurn():
            x = f32.add(x, f32.div(f32.i2f(st['examParameterBuffMultiplePerTurnPermil'] * sc.GetParameterBuffTurn()), 1000.0))
        if add:
            x = f32.mul(x, add.get('pb', 1.0))
        pb = f32.add(x, 1.0)
    dbf = f32.i2f(st['examGimmickParameterDebuffPermil'] if sc.IsParameterDebuff() else 1000)
    focus = sc.GetLessonBuff()
    focus_dn = sc.GetLessonDebuff()
    enth = sc.GetEnthusiasticBuff()
    lpm = sc.GetLessonParameterMultiple()
    down = sc.GetLessonParameterMultipleDown()
    down_f = max(0.0, f32.sub(1.0, down))
    dep = 0.0
    if sc.IsLessonParameterMultipleDependReviewOrAggressive():
        r = min(sc.GetReview(), sc.GetAggressive())
        dep = f32.mul(f32.permil(st['examLessonValueMultipleDependReviewOrAggressiveMultiplePermil']), f32.i2f(r))
        dep = min(f32.permil(st['examLessonValueMultipleDependReviewOrAggressiveMaxPermil']), dep)
    lbm = sc.GetLessonBuffMultiple()
    stance = 1.0
    t, step = sc.GetIdolStatusType(), sc.GetIdolStatusStep()
    if t == 1:
        p = st['examConcentrationLessonValueMultiplePermil1'] if step == 1 else st['examConcentrationLessonValueMultiplePermil2']
        rate = add.get('conc', 1.0) if add else 1.0
        stance = f32.add(f32.mul(f32.add(f32.add(f32.permil(p), -1.0), sc.GetConcentrationLessonMultipleAdditiveStatusEffectMultiple()),
                                 f32.add(f32.add(rate, -1.0), 1.0)), 1.0)
    elif t == 2:
        p = st['examPreservationLessonValueMultiplePermil1'] if step == 1 else st['examPreservationLessonValueMultiplePermil2']
        stance = f32.permil(p)
    elif t == 3:
        rate = add.get('full', 1.0) if add else 1.0
        stance = f32.add(f32.mul(f32.add(f32.add(f32.permil(st['examFullPowerLessonValueMultiplePermil']), -1.0),
                                         sc.GetFullPowerLessonMultipleAdditiveStatusEffectMultiple()),
                                 f32.add(f32.add(rate, -1.0), 1.0)), 1.0)
    elif t == 4:
        stance = f32.i2f(st['examOverPreservationLessonValueMultiplePermil'])
    if add and 'enth' in add:
        enth = f32.ceil_i(f32.mul(add['enth'], f32.i2f(enth)))
    dbf = f32.div(dbf, 1000.0)
    v = value
    c = sc.GetLessonChangeSpecifyMoreThanStatus(v)
    if c >= 0:
        v = c
    c = sc.GetLessonChangeSpecifyLessThanStatus(v)
    if c >= 0:
        v = c
    base = max(0, f32.ceil_i(f32.mul(lbm, f32.i2f(focus))) - focus_dn + enth + v)
    r = f32.mul(f32.f(float(base)), pb)
    r = f32.mul(r, dbf)
    r = f32.mul(f32.add(lpm, dep), r)
    r = f32.mul(down_f, r)
    r = f32.mul(stance, r)
    return min(f32.ceil_i(f32.add(r, f32.EPS32)), f32.INT_MAX)


def AddParameterFix(ex, v, ctx=None):
    """ExamEffectUtility.AddParameterFix：对战考试乘当前回合属性倍率（CalcCurrentTurnBattleBonus），截上限，加分。"""
    if ex.exam_type != 0:
        bonus = ex.bonus_permil.get(ex.current_parameter_type(), 1000)
        v = f32.ceil_i(f32.add(f32.mul(f32.permil(bonus), f32.f(float(v))), f32.EPS32))
    v = min(v, f32.INT_MAX)
    ex.score += v
    ex.counters['currentTurnTotalAddParameter'] = ex.counters.get('currentTurnTotalAddParameter', 0) + v
    ex.log('score', value=v, total=ex.score)


def AddParameter(ex, v, ctx=None):
    AddParameterFix(ex, 0 if ex.status.IsSlump() else v, ctx)


def CalculateAddBlock(ex, value, rate=1.0):
    sc = ex.status
    if value < 1:
        return 0
    if sc.IsBlockRestriction():
        return 0
    v = f32.ceil_i(f32.mul(f32.i2f(sc.GetAggressive()), rate)) + value
    if v < 1:
        return max(0, v)
    if sc.IsBlockAddDown() and not sc.IsBlockAddDownRestriction():
        v -= f32.ratio_int(v, f32.trunc_i(f32.sub(1000.0, f32.i2f(ex.setting['examBlockAddDownPermil']))))
        if v < 1:
            return max(0, v)
    if sc.GetBlockAddDownFix() >= 1 and not sc.IsBlockAddDownRestriction():
        v -= sc.GetBlockAddDownFix()
    return max(0, v)


def AddBlockFix(ex, value, ctx=None):
    """block + max(−block, value) → SetBlock(…, isConsume=false)。"""
    d = max(-ex.block, value)
    ex.SetBlock(ex.block + d)
    if d > 0:
        ex.log('block', value=d, total=ex.block)


def CalculateStaminaRecover(ex, value):
    """待核：回復禁止 → 0；回復量増加。"""
    sc = ex.status
    if sc.IsStaminaRecoverRestriction():
        return 0
    return value


def AddStaminaFix(ex, v, ctx=None):
    before = ex.stamina
    ex.stamina = max(0, min(ex.max_stamina, ex.stamina + v))
    ex.log('stamina', value=ex.stamina - before, total=ex.stamina)


# ---------------------------------------------------------------- 执行器

def _lesson(ex, e, ctx):
    for _ in range(e['effectCount']):
        AddParameter(ex, CalculateAddingParameter(ex, e['effectValue1'], ctx), ctx)


def _lesson_fix(ex, e, ctx):
    for _ in range(e['effectCount']):
        AddParameterFix(ex, e['effectValue1'], ctx)


def _block(ex, e, ctx):
    AddBlockFix(ex, CalculateAddBlock(ex, e['effectValue1']), ctx)


def _block_fix(ex, e, ctx):
    AddBlockFix(ex, e['effectValue1'], ctx)


def _review(ex, e, ctx):
    ex.status.TryAddReviewStatus(e['effectValue1'], ctx)


def _aggressive(ex, e, ctx):
    ex.status.TryAddAggressiveStatus(e['effectValue1'], ctx)


def _lesson_buff(ex, e, ctx):
    ex.status.TryAddLessonBuffStatus(e['effectValue1'], ctx)


def _parameter_buff(ex, e, ctx):
    ex.status.TryAddParameterBuffStatus(e['effectTurn'], ctx)


def _parameter_buff_mpt(ex, e, ctx):
    ex.status.TryAddParameterBuffMultiplePerTurnStatus(e['effectTurn'], ctx)


def _playable_value_add(ex, e, ctx):
    """AddPlayableValueAdd：出牌已结束时立刻重开并消耗一次（这里用剩余行动数直接表示）。"""
    ex.status.TryAddPlayableValueAddStatus(e['effectCount'], ctx)


def _draw(ex, e, ctx):
    got = ex.DrawCard(e['effectValue1'], ctx, is_effect=True)
    ex.counters['totalDrawCardCount'] = ex.counters.get('totalDrawCardCount', 0) + len(got)


def _stamina_recover_fix(ex, e, ctx):
    AddStaminaFix(ex, CalculateStaminaRecover(ex, e['effectValue1']), ctx)


def _stamina_recover_multiple(ex, e, ctx):
    v = f32.ceil_i(f32.div(f32.mul(f32.i2f(ex.max_stamina), f32.i2f(e['effectValue1'])), 1000.0))
    AddStaminaFix(ex, CalculateStaminaRecover(ex, v), ctx)


def _stamina_reduce_fix(ex, e, ctx):
    """StaminaReduceFixEffectExecutor：ReduceStaminaFix（直接扣体力，不经元気；没有低下状態無効判定）；
    真的减了且有 Uplifting 时消耗一层。（真机抓取 atuy #8：低下状態無効在场时 P 道具的体力 −1 照扣）"""
    v = e['effectValue1']
    if v < 1:
        return
    before = ex.stamina
    if not ex.ReduceStaminaFix(v, ctx):
        return
    ex.log('stamina', value=ex.stamina - before, total=ex.stamina)
    up = ex.status.first('UpliftingStatusEffect')
    if up is not None and up.value >= 1:
        up.value -= 1
        if up.value <= 0:
            ex.status.RemoveStatusEffect(up)


def _stamina_reduce(ex, e, ctx):
    v = e['effectValue1']
    if v < 1:
        return
    r = f32.add(f32.div(f32.f(float(v)), 1000.0), f32.NEG_EPS32)
    red = f32.trunc_i(f32.mul(r, f32.i2f(ex.max_stamina)))
    n = max(f32.i2f(ex.stamina - red), 0.0)
    n = min(n, f32.i2f(ex.max_stamina))
    n = f32.trunc_i(n)
    if n == ex.stamina:
        return
    before = ex.stamina
    ex.stamina = n
    ex.AddCurrentTurnConsumeStamina(before - n)
    ex.log('stamina', value=n - before, total=n)


def _lesson_value_multiple(ex, e, ctx):
    ex.status.TryAddLessonParameterMultipleStatus(e['effectValue1'], e['effectTurn'], ctx)


def _depend(ex, e, ctx, resource):
    """LessonDepend* A 族：base = ceil(v1/1000 × X − 0.0001) 只算一次，逐段打分；
    之后 X 覆盖式写回 ceil((1 − v2‰) × 打分前的 X)（没变化不写）。"""
    sc = ex.status
    if resource == 'review':
        x = sc.GetReview()
    elif resource == 'aggressive':
        x = sc.GetAggressive()
    elif resource == 'parameter_buff':
        x = sc.GetParameterBuffTurn()
    elif resource == 'stamina':
        x = ex.stamina
    else:
        x = ex.block
    rate = f32.div(f32.i2f(e['effectValue1']), 1000.0)
    base = f32.ceil_i(f32.add(f32.mul(rate, f32.i2f(x)), f32.EPS32))
    for _ in range(e['effectCount']):
        AddParameter(ex, CalculateAddingParameter(ex, base, ctx), ctx)
    n = f32.ceil_i(f32.mul(f32.sub(1.0, f32.permil(e['effectValue2'])), f32.i2f(x)))
    if n == x:
        return
    if resource == 'review':
        sc.SetReview(n)
    elif resource == 'aggressive':
        sc.SetAggressive(n)
    elif resource == 'parameter_buff':
        sc.SetParameterBuff(n)
    elif resource == 'stamina':
        before = ex.stamina
        ex.stamina = n
        ex.AddCurrentTurnConsumeStamina(before - n)
    else:
        ex.SetBlock(n, True)


def _block_add_multiple_aggressive(ex, e, ctx):
    """ExamBlockAddMultipleAggressive（D）：v = CalculateAddBlock(v1, rate = v2‰ + 1) 只算一次，重复 count 次 AddBlockFix。"""
    v = CalculateAddBlock(ex, e['effectValue1'], f32.add(f32.permil(e['effectValue2']), 1.0))
    for _ in range(e['effectCount']):
        AddBlockFix(ex, v, ctx)


def _search_play_count_buff(ex, e, ctx):
    """ExamCardSearchEffectPlayCountBuff：「次に使用する…スキルカードの効果をもう1回発動」。PlayCountBuffStatusEffect：
    _value = 追加发动次数（v1），_count = 可用次数，_turn = 持续，_searchId = 检索。按创建顺序第一个符合的生效（旧引擎 repeat_card）。"""
    from .status import Status
    st = Status('PlayCountBuffStatusEffect', e['effectTurn'], e['effectValue1'], e['effectTurn'] >= 0, False, 0,
                {'_count': e['effectCount'], '_searchId': e.get('produceCardSearchId', '')})
    ex.status.AddStatus(st)


def _hand_grave_count_card_draw(ex, e, ctx):
    """ExamHandGraveCountCardDraw（HandGraveCountCardDrawEffectExecutor）：手牌全部
    cc.MoveCard(→ Grave, effectType 98)，再抽同样张数（张数在移动前数好）。走 MoveCard：去掉支援强化、发移牌差分。
    （_value1 ≠ 0 的玩家选牌版本主数据未用。）"""
    cards = list(ex.zones['hand'])
    ex.MoveCard(cards, MOVE['Grave'], ctx)
    ex.log('discard_hand', n=len(cards))
    ex.DrawCard(len(cards), ctx, is_effect=True)


def _card_upgrade(ex, e, ctx):
    """ExamCardUpgrade：从可强化（IsUpgradableRaw）的卡里检索，SetTemporaryUpgrade（_tmpUpgradeCount = 1）。"""
    search = ex.master.search(e['produceCardSearchId']) if e.get('produceCardSearchId') else None
    # IsUpgradableRaw：base + tmp == 0 且 +1 等级存在（已强化过的卡不再强化）
    cards = [c for c in ex.PickSearchCards(search, e['pickRangeType'], e['pickCountMin'], e['pickCountMax'], ctx)
             if c.upgrade_raw == 0 and ex.master.get('ProduceCard', c.card_id, 1) is not None]
    for c in cards:
        c.tmp_upgrade = 1
        c.invalidate()
        ex.log('upgrade', card=c.card_id)


def _status_enchant(ex, e, ctx):
    en = ex.master.enchant(e['produceExamStatusEnchantId'])
    trig = ex.master.trigger(en['produceExamTriggerId'])
    effects = [ex.master.effect(i) for i in en['produceExamEffectIds']]
    limit = e['effectCount'] if e['effectCount'] > 0 else -1
    in_turn = e['effectValue1'] if e['effectValue1'] > 0 else -1
    ex.status.TryAddTriggerEffectStatus(trig, effects, 1, e['effectTurn'], limit, in_turn, en['id'], ctx=ctx)


def _effect_timer(ex, e, ctx, phase):
    chain = [ex.master.effect(i) for i in (e.get('chainProduceExamEffectIds') or [])]
    if not chain and e.get('chainProduceExamEffectId'):
        chain = [ex.master.effect(e['chainProduceExamEffectId'])]
    if not chain:
        return
    trig = dict(id='', phaseTypes=[phase], phaseValues=[e['effectValue1']], fieldStatusTypes=[], fieldStatusValues=[])
    ex.status.TryAddTriggerEffectStatus(trig, chain, 2, -1, 1, -1, '', ctx=ctx, override_type=30)


def _value_multiple(kind):
    """ReviewValueMultiple / AggressiveValueMultiple（C）：x = 当前值；v = ceil(v1‰ × x)；TryAdd(v, isFix=true)。"""
    def run(ex, e, ctx):
        sc = ex.status
        x = sc.GetReview() if kind == 'review' else sc.GetAggressive()
        if x == 0:
            return
        v = f32.ceil_i(f32.mul(f32.permil(e['effectValue1']), f32.i2f(x)))
        if kind == 'review':
            sc.TryAddReviewStatus(v, ctx, isFix=True)
        else:
            sc.TryAddAggressiveStatus(v, ctx, isFix=True)
    return run


def _turnkey(name):
    def run(ex, e, ctx):
        getattr(ex.status, name)(e['effectValue1'], e['effectTurn'], ctx)
    return run


def _turn_only(name):
    def run(ex, e, ctx):
        getattr(ex.status, name)(e['effectTurn'], ctx)
    return run


def _anti_debuff(ex, e, ctx):
    ex.status.TryAddAntiDebuffStatus(e['effectCount'], ctx)


def _extra_turn(ex, e, ctx):
    ex.extra_turn += 1
    ex.remain_turn += 1
    ex.log('extra_turn')


def _stamina_down_fix(ex, e, ctx):
    ex.status.TryAddStaminaConsumptionDownFixStatus(e['effectValue1'], e['effectTurn'], ctx)


def _card_create_id(ex, e, ctx):
    from .cards import ExamCard
    n = e.get('pickCountMin') or 1
    if e.get('pickCountMax') and e['pickCountMax'] != n:
        n = ex.rng.integer(n, e['pickCountMax'])
    cards = [ExamCard(ex.master, e['targetProduceCardId'], e['targetUpgradeCount'], guid=new_guid(ex)) for _ in range(n)]
    ex.AddCard(cards, e['movePositionType'], ctx)


def _card_move(ex, e, ctx):
    search = ex.master.search(e['produceCardSearchId']) if e.get('produceCardSearchId') else None
    cards = ex.PickSearchCards(search, e['pickRangeType'], e['pickCountMin'], e['pickCountMax'], ctx)
    ex.MoveCard(cards, e['movePositionType'], ctx)


EXEC = {
    ET['ExamCardCreateId']: _card_create_id,
    ET['ExamCardMove']: _card_move,
    ET['ExamLesson']: _lesson,
    ET['ExamLessonFix']: _lesson_fix,
    ET['ExamBlock']: _block,
    ET['ExamBlockFix']: _block_fix,
    ET['ExamReview']: _review,
    ET['ExamCardPlayAggressive']: _aggressive,
    ET['ExamLessonBuff']: _lesson_buff,
    ET['ExamParameterBuff']: _parameter_buff,
    ET['ExamParameterBuffMultiplePerTurn']: _parameter_buff_mpt,
    ET['ExamPlayableValueAdd']: _playable_value_add,
    ET['ExamCardDraw']: _draw,
    ET['ExamStaminaRecoverFix']: _stamina_recover_fix,
    ET['ExamStaminaRecoverMultiple']: _stamina_recover_multiple,
    ET['ExamStaminaReduceFix']: _stamina_reduce_fix,
    ET['ExamStaminaReduce']: _stamina_reduce,
    ET['ExamLessonValueMultiple']: _lesson_value_multiple,
    ET['ExamLessonDependExamReview']: lambda ex, e, ctx: _depend(ex, e, ctx, 'review'),
    ET['ExamLessonDependExamCardPlayAggressive']: lambda ex, e, ctx: _depend(ex, e, ctx, 'aggressive'),
    ET['ExamLessonDependBlock']: lambda ex, e, ctx: _depend(ex, e, ctx, 'block'),
    ET['ExamLessonDependParameterBuff']: lambda ex, e, ctx: _depend(ex, e, ctx, 'parameter_buff'),
    ET['ExamLessonDependStamina']: lambda ex, e, ctx: _depend(ex, e, ctx, 'stamina'),
    ET['ExamBlockAddMultipleAggressive']: _block_add_multiple_aggressive,
    ET['ExamCardSearchEffectPlayCountBuff']: _search_play_count_buff,
    ET['ExamHandGraveCountCardDraw']: _hand_grave_count_card_draw,
    ET['ExamCardUpgrade']: _card_upgrade,
    ET['ExamStatusEnchant']: _status_enchant,
    ET['ExamEffectTimer']: lambda ex, e, ctx: _effect_timer(ex, e, ctx, E('ProduceExamPhaseType')['ExamTurnTimer']),
    ET['ExamEffectTimerEndTurn']: lambda ex, e, ctx: _effect_timer(ex, e, ctx, E('ProduceExamPhaseType')['ExamEndTurnTimer']),
    ET['ExamReviewValueMultiple']: _value_multiple('review'),
    ET['ExamAggressiveValueMultiple']: _value_multiple('aggressive'),
    ET['ExamReviewMultiple']: _turnkey('TryAddReviewMultipleStatus'),
    ET['ExamReviewAdditive']: _turnkey('TryAddReviewAdditiveStatus'),
    ET['ExamStaminaConsumptionDown']: _turn_only('TryAddStaminaConsumptionDownStatus'),
    ET['ExamStaminaConsumptionAdd']: _turn_only('TryAddStaminaConsumptionAddStatus'),
    ET['ExamStaminaConsumptionDownFix']: _stamina_down_fix,
    ET['ExamBlockRestriction']: _turn_only('TryAddBlockRestrictionStatus'),
    ET['ExamAntiDebuff']: _anti_debuff,
    ET['ExamExtraTurn']: _extra_turn,
}


# ---------------------------------------------------------------- 批量：直接 TryAdd

def _tryadd_turn(method):
    def run(ex, e, ctx):
        getattr(ex.status, method)(e['effectTurn'], ctx)
    return run


def _tryadd_value(method):
    def run(ex, e, ctx):
        getattr(ex.status, method)(e['effectValue1'], ctx)
    return run


def _tryadd_value_turn(method, max0=False):
    def run(ex, e, ctx):
        v = max(0, e['effectValue1']) if max0 else e['effectValue1']
        getattr(ex.status, method)(v, e['effectTurn'], ctx)
    return run


TURN_ONLY = {
    'ExamGimmickSlump': 'TryAddSlumpStatus', 'ExamBlockAddDown': 'TryAddBlockAddDownStatus',
    'ExamGimmickParameterDebuff': 'TryAddParameterDebuffStatus', 'ExamStaminaRecoverRestriction': 'TryAddStaminaRecoverRestrictionStatus',
    'ExamLessonValueMultipleDependReviewOrAggressive': 'TryAddLessonParameterMultipleDependReviewOrAggressiveStatus',
    'ExamBuffConsumptionAdd': 'TryAddBuffConsumptionAddStatus', 'StanceLock': 'TryAddStanceLockStatus', 'ExamPanic': 'TryAddPanicStatus',
    'ExamBlockAddDownRestriction': 'TryAddBlockAddDownRestrictionStatus', 'ExamBuffConsumptionDown': 'TryAddBuffConsumptionDownStatus',
    'ExamParameterBuffTurnEndReduceLock': 'TryAddParameterBuffTurnEndReduceLockStatus', 'ExamReviewTurnEndReduceLock': 'TryAddReviewTurnEndReduceLockStatus',
    'ExamStaminaConsumptionAddDown': 'TryAddStaminaConsumptionAddDownStatus', 'ExamStaminaConsumptionDownAdd': 'TryAddStaminaConsumptionDownAddStatus',
    'ExamStanceLockConcentration': 'TryAddStanceLockConcentrationEffect', 'ExamStanceLockFullPower': 'TryAddStanceLockFullPowerEffect',
    'ExamStanceLockPreservation': 'TryAddStanceLockPreservationEffect', 'ExamGetCardUpgrade': 'TryAddGetCardUpgradeStatus',
}
VALUE_TURN = {
    'ExamLessonValueMultipleDown': 'TryAddLessonParameterMultipleDownStatus', 'ExamEnthusiasticAdditive': 'TryAddEnthusiasticAdditiveStatus',
    'ExamLessonBuffAdditive': 'TryAddLessonBuffAdditiveStatus', 'ExamGimmickStartTurnCardDrawDown': 'TryAddStartTurnCardDrawDownStatus',
    'ExamEnthusiasticMultiple': 'TryAddEnthusiasticMultipleStatus', 'ExamFullPowerPointAdditive': 'TryAddFullPowerPointAdditiveStatus',
    'ExamLessonBuffMultiple': 'TryAddLessonBuffMultipleStatus', 'ExamGimmickLessonDebuff': 'TryAddLessonDebuffStatus',
    'ExamAggressiveAdditive': 'TryAddAggressiveAdditiveStatus', 'ExamFullPowerLessonMultipleAdditive': 'TryAddFullPowerLessonMultipleAdditiveStatus',
    'ExamReviewCountAdd': 'TryAddReviewCountAddStatus', 'ExamParameterBuffAdditive': 'TryAddParameterBuffAdditiveStatus',
    'ExamLessonBuffAdditiveFix': 'TryAddLessonBuffAdditiveFixStatus', 'ExamConcentrationLessonMultipleAdditive': 'TryAddConcentrationLessonMultipleAdditiveStatus',
    'ExamAggressiveAdditiveFix': 'TryAddAggressiveAdditiveFixStatus', 'ExamParameterBuffAdditiveFix': 'TryAddParameterBuffAdditiveFixStatus',
    'ExamGrowEffectLessonAddAdditive': 'TryAddGrowEffectLessonAddAdditiveStatus', 'ExamFullPowerPointAdditiveFix': 'TryAddFullPowerPointAdditiveFixStatus',
    'ExamGimmickEnthusiastic': 'TryAddEnthusiasticStatus', 'ExamReviewAdditiveFix': 'TryAddReviewAdditiveFixStatus',
    'ExamStaminaReduceChange': 'TryAddStaminaReduceChangeStatus',
}
VALUE_TURN_MAX0 = {'ExamStaminaConsumptionAddFix': 'TryAddStaminaConsumptionAddFixStatus'}
VALUE_ONLY = {'ExamGimmickSleepy': 'TryAddBlockAddDownFixStatus', 'ExamEnthusiasticTurnAdd': 'TryAddEnthusiasticTurnAdditiveStatus',
              'ExamStaminaRecoverAdd': 'TryAddStaminaRecoverAddStatus', 'ExamUplifting': 'TryAddUpliftingStatus'}


# ---------------------------------------------------------------- 打分变体

def _multiple_lesson_buff_lesson(ex, e, ctx):
    sc = ex.status
    lb = sc.GetLessonBuff()
    lbm = sc.GetLessonBuffMultiple()
    add = f32.ceil_i(f32.div(f32.mul(lbm, f32.i2f(e['effectValue2'] * lb)), 1000.0))
    base = add + e['effectValue1']
    for _ in range(e['effectCount']):
        AddParameter(ex, CalculateAddingParameter(ex, base, ctx), ctx)


def _lesson_with_rate(key):
    def run(ex, e, ctx):
        add = {key: f32.add(f32.permil(e['effectValue2']), 1.0)}
        v = CalculateAddingParameter(ex, e['effectValue1'], ctx, add)
        for _ in range(e['effectCount']):
            AddParameter(ex, v, ctx)
    return run


def _lesson_add_multiple_lesson_buff(ex, e, ctx):
    lb = ex.status.GetLessonBuff()
    v = f32.ceil_i(f32.div(f32.i2f(e['effectValue1'] * lb), 1000.0))
    ex.status.TryAddLessonBuffStatus(v, ctx, isFix=True)


def _lesson_full_power_point(ex, e, ctx):
    total = ex.counters.get('fullPowerPointGetSumCount', 0)
    for _ in range(e['effectCount']):
        base = e['effectValue1'] + f32.floor_i(f32.mul(f32.permil(e['effectValue2']), f32.i2f(total)))
        AddParameter(ex, CalculateAddingParameter(ex, base, ctx), ctx)


def _lesson_add_block(ex, e, ctx):
    for _ in range(e['effectCount']):
        v = CalculateAddingParameter(ex, e['effectValue1'], ctx)
        AddParameter(ex, v, ctx)
        if v >= 1:
            AddBlockFix(ex, CalculateAddBlock(ex, v), ctx)


def _search_n(ex, e, key='produceCardSearchId'):
    sid = e.get(key)
    return len(ex.GetSearchCardList(ex.master.search(sid), None)) if sid else 0


def _lesson_per_search_count(ex, e, ctx):
    base = e['effectValue1'] + f32.ceil_i(f32.mul(f32.permil(e['effectValue2']), f32.i2f(_search_n(ex, e))))
    for _ in range(e['effectCount']):
        AddParameter(ex, CalculateAddingParameter(ex, base, ctx), ctx)


# ---------------------------------------------------------------- 依存打分 B / C

def _lesson_depend_sum(kind):
    def run(ex, e, ctx):
        c = ex.counters
        if kind == 'stamina':
            base = f32.ceil_i(f32.mul(f32.div(f32.i2f(e['effectValue1']), 1000.0), f32.i2f(c.get('totalConsumeStamina', 0))))
        elif kind == 'block':
            base = f32.ceil_i(f32.mul(f32.permil(e['effectValue1']), f32.i2f(c.get('blockConsumptionSumCount', 0))))
        elif kind == 'play':
            base = e['effectValue1'] + e['effectValue2'] * ex.exam_card_play_count
        else:  # enthusiastic
            base = f32.ceil_i(f32.mul(f32.permil(e['effectValue2']), f32.i2f(c.get('enthusiasticGetSumCount', 0)))) + e['effectValue1']
        for _ in range(e['effectCount'] if kind != 'enthusiastic' else max(1, e['effectCount'])):
            AddParameter(ex, CalculateAddingParameter(ex, base, ctx), ctx)
    return run


def _lesson_depend_and_search(kind):
    def run(ex, e, ctx):
        sc = ex.status
        x = ex.block if kind == 'block' else sc.GetReview() if kind == 'review' else sc.GetAggressive()
        n = _search_n(ex, e)
        rate = f32.add(f32.div(f32.i2f(e['effectValue1']), 1000.0), f32.mul(f32.permil(e['effectValue2']), f32.i2f(n)))
        base = f32.ceil_i(f32.add(f32.mul(rate, f32.i2f(x)), f32.EPS32))
        for _ in range(e['effectCount']):
            AddParameter(ex, CalculateAddingParameter(ex, base, ctx), ctx)
    return run


# ---------------------------------------------------------------- 状态互转 / 累计

def _convert(src, dst):
    """A 族：x 只读一次；v = ceil(v1/1000×x − 0.0001)；次数次获得；n = ceil((1−v2‰)×x)；x−n 消耗（Block 版 SetBlock(n)）。"""
    def run(ex, e, ctx):
        sc = ex.status
        x = {'parameter_buff': sc.GetParameterBuffTurn, 'lesson_buff': sc.GetLessonBuff, 'review': sc.GetReview,
             'aggressive': sc.GetAggressive}.get(src, lambda: ex.block)()
        v = f32.ceil_i(f32.add(f32.mul(f32.div(f32.i2f(e['effectValue1']), 1000.0), f32.i2f(x)), f32.EPS32))
        times = 1 if dst in ('lesson_buff', 'parameter_buff') or (src == 'review' and dst == 'aggressive') else e['effectCount']
        for _ in range(times):
            if dst == 'lesson_buff':
                sc.TryAddLessonBuffStatus(v, ctx)
            elif dst == 'parameter_buff':
                sc.TryAddParameterBuffStatus(v, ctx)
            elif dst == 'review':
                sc.TryAddReviewStatus(v, ctx)
            elif dst == 'aggressive':
                sc.TryAddAggressiveStatus(v, ctx)
            elif dst == 'block':
                AddBlockFix(ex, CalculateAddBlock(ex, v), ctx)
        n = f32.ceil_i(f32.mul(f32.sub(1.0, f32.permil(e['effectValue2'])), f32.i2f(x)))
        if src == 'block':
            if x == n:
                return
            ex.SetBlock(n, True)
            return
        if x - n == 0:
            return
        {'parameter_buff': sc.ConsumeParameterBuff, 'lesson_buff': sc.ConsumeLessonBuff, 'review': sc.ConsumeReview,
         'aggressive': sc.ConsumeAggressive}[src](x - n)
    return run


def _sum_to_status(kind):
    def run(ex, e, ctx):
        c = ex.counters
        if kind == 'block':
            v = f32.ceil_i(f32.mul(f32.permil(e['effectValue1']), f32.i2f(c.get('blockConsumptionSumCount', 0))))
            AddBlockFix(ex, CalculateAddBlock(ex, v), ctx)
        elif kind == 'review':
            v = f32.ceil_i(f32.mul(f32.permil(e['effectValue1']), f32.i2f(c.get('reviewConsumptionSumCount', 0))))
            ex.status.TryAddReviewStatus(v, ctx)
        elif kind == 'fullpower':
            v = f32.ceil_i(f32.mul(f32.permil(e['effectValue1']), f32.i2f(c.get('fullPowerPointGetSumCount', 0))))
            AddFullPowerPoint(ex, v, ctx)
    return run


def _block_per_use_card_count(ex, e, ctx):
    v = e['effectValue1'] + ex.exam_card_play_count * e['effectValue2']
    AddBlockFix(ex, CalculateAddBlock(ex, v), ctx)


def _per_search_count(kind):
    def run(ex, e, ctx):
        v = e['effectValue1'] + f32.ceil_i(f32.mul(f32.permil(e['effectValue2']), f32.i2f(_search_n(ex, e))))
        sc = ex.status
        if kind == 'lesson_buff':
            sc.TryAddLessonBuffStatus(v, ctx)
        elif kind == 'review':
            sc.TryAddReviewStatus(v, ctx)
        elif kind == 'parameter_buff':
            sc.TryAddParameterBuffStatus(v, ctx)
        elif kind == 'aggressive':
            sc.TryAddAggressiveStatus(v, ctx)
        elif kind == 'block':
            AddBlockFix(ex, CalculateAddBlock(ex, v), ctx)
        elif kind == 'fullpower':
            AddFullPowerPoint(ex, v, ctx)
    return run


def _effect_per_search_count(ex, e, ctx):
    n = _search_n(ex, e)
    times = f32.ceil_i(f32.mul(f32.permil(e['effectValue2']), f32.i2f(n)))
    chain = e.get('chainProduceExamEffectId')
    if times < 1 or not chain:
        return
    ex.insert([('effect', ex.master.effect(chain), ctx)] * times)


def _chain(kind):
    def run(ex, e, ctx):
        k = e['effectValue1'] if kind == 'value' else ex.current_turn if kind == 'passed' else ex.remain_turn
        chain = e.get('chainProduceExamEffectId')
        if k < 1 or not chain:
            return
        ex.insert([('effect', ex.master.effect(chain), ctx)] * k)
    return run


# ---------------------------------------------------------------- 减少

def _reduce(method, kind='value'):
    def run(ex, e, ctx):
        getattr(ex.status, method)(e['effectValue1'])
    return run


def _full_power_point_reduce(ex, e, ctx):
    ex.status.ConsumeFullPowerPoint(e['effectValue1'])   # 设置 ExamFullPowerPointReduceCancelDisable 分支（待核取值）


# ---------------------------------------------------------------- 卡

def _card_create_search(ex, e, ctx):
    from .cards import ExamCard
    copies = max(e['effectValue1'], 1)
    search = ex.master.search(e['produceCardSearchId']) if e.get('produceCardSearchId') else None
    found = ex.PickSearchCards(search, e['pickRangeType'], e['pickCountMin'], e['pickCountMax'], ctx)
    out = []
    for src in found:
        for _ in range(copies):
            out.append(ExamCard(ex.master, src.card_id, src.upgrade_raw, src.customize_counts, guid=new_guid(ex)))
    ex.AddCard(out, e['movePositionType'], ctx)


def _card_duplicate(ex, e, ctx):

    copies = max(e['effectValue1'], 1)
    search = ex.master.search(e['produceCardSearchId']) if e.get('produceCardSearchId') else None
    found = ex.PickSearchCards(search, e['pickRangeType'], e['pickCountMin'], e['pickCountMax'], ctx)
    for src in found:
        c = src.copy()

        c.guid = new_guid(ex)
        c.support_upgrades = []
        c.invalidate()
        ex.AddCard([c] * copies, e['movePositionType'], ctx)


def _force_play(with_cost):
    def run(ex, e, ctx):
        search = ex.master.search(e['produceCardSearchId']) if e.get('produceCardSearchId') else None
        found = [c for c in ex.PickSearchCards(search, e['pickRangeType'], e['pickCountMin'], e['pickCountMax'], ctx)
                 if all(x['effectType'] != ET['ExamForcePlayCardSearch'] for _, x, _ in c.play_effects())]
        if not found:
            return
        if not with_cost and e['effectValue1'] >= 1:
            ex.consume_stamina(e['effectValue1'])
        for c in found:
            ex.queue.append(('call', lambda c=c: ex.use_pool(c, consume_cost=with_cost)))
    return run


def _debuff_recover(ex, e, ctx):
    from .status import DEBUFF_STATUS_CLASSES
    targets = sorted([s for s in ex.status.effects if s.cls in DEBUFF_STATUS_CLASSES], key=lambda s: -s.uid)
    if e['effectValue1'] >= 1:
        targets = targets[:e['effectValue1']]
    for s in targets:
        ex.status.RemoveStatusEffect(s)


def _item_fire_limit_add(ex, e, ctx):
    from .status import TriggerEffect
    for s in ex.status.effects:
        if isinstance(s, TriggerEffect) and s.source.get('item') and s.source.get('item_idol_origin', True):
            if s.limit_count >= 0:
                s.limit_count += e['effectValue1']


def _status_enchant_encore(ex, e, ctx):
    _status_enchant(ex, e, ctx)
    from .status import TriggerEffect
    last = ex.status.effects[-1] if ex.status.effects else None
    if isinstance(last, TriggerEffect):
        last.encore = True


def _gimmick_play_card_limit(ex, e, ctx):
    ex.status.TryAddSearchPlayCardLimitStatus(e.get('produceCardSearchId', ''), e['effectTurn'], ctx)


def _search_play_card_stamina_change(ex, e, ctx):
    from .status import Status
    ex.status.AddStatus(Status('SearchPlayCardStaminaConsumptionChangeStatusEffect', e['effectTurn'], e['effectValue1'],
                               e['effectTurn'] >= 0, False, 0, {'_count': e['effectCount'], '_searchId': e.get('produceCardSearchId', ''),
                                                                 '_fromExamEffectId': e.get('id', '')}))


def _stamina_damage(ex, e, ctx):
    ex.consume_stamina(e['effectValue1'])


def _block_value_multiple(ex, e, ctx):
    """ExamBlockValueMultiple：元気 × v1‰（待以笔记细节核对取整）。"""
    v = f32.ceil_i(f32.mul(f32.permil(e['effectValue1']), f32.i2f(ex.block))) - ex.block
    AddBlockFix(ex, v, ctx)


# ---------------------------------------------------------------- 依赖核心线（指针 / 全力 / 成长）：先接口，待

def AddFullPowerPoint(ex, v, ctx=None):
    """ExamEffectUtility.AddFullPowerPoint：TryAdd 后按实际增量计 fullPowerPointGetSumCount；不在此进入全力
    （全力值 ≥10 在下一回合开始时消耗进入，见 Exam.start_turn）。"""
    before = ex.status.GetFullPowerPoint()
    if ex.status.TryAddFullPowerPoint(v, ctx):
        ex.counters['fullPowerPointGetSumCount'] = ex.counters.get('fullPowerPointGetSumCount', 0) + ex.status.GetFullPowerPoint() - before


def _full_power_point(ex, e, ctx):
    AddFullPowerPoint(ex, e['effectValue1'], ctx)


def _stance(stance, fixed_step=None):
    """Concentration / Preservation / OverPreservation / FullPower 执行器：全力中前三者忽略；
    全力执行器照常调用（全力中 SetStance 返回 False），成功后保留卡回手。"""
    def run(ex, e, ctx):
        if stance in (1, 2, 4) and ex.status.GetIdolStatusType() == 3:
            return
        ok = ex.SetStance(stance, fixed_step if fixed_step is not None else e['effectValue1'], ctx)
        if ok and stance == 3:
            ex.full_power_hold_to_hand(ctx)
    return run


def _stance_reset(ex, e, ctx):
    if ex.status.GetIdolStatusType() == 3:
        return
    ex.SetStance(0, 0, ctx)


def _add_grow_effect(ex, e, ctx):
    search = ex.master.search(e['produceCardSearchId']) if e.get('produceCardSearchId') else None
    cards = ex.PickSearchCards(search, e['pickRangeType'], e['pickCountMin'], e['pickCountMax'], ctx)
    ex.AddGrowEffects(cards, e.get('produceCardGrowEffectIds') or [], ctx)


def _register_more():
    def name(n):
        return ET[n]
    for n, m in TURN_ONLY.items():
        EXEC[name(n)] = _tryadd_turn(m)
    for n, m in VALUE_TURN.items():
        EXEC[name(n)] = _tryadd_value_turn(m)
    for n, m in VALUE_TURN_MAX0.items():
        EXEC[name(n)] = _tryadd_value_turn(m, max0=True)
    for n, m in VALUE_ONLY.items():
        EXEC[name(n)] = _tryadd_value(m)
    EXEC[name('ExamHandHold')] = lambda ex, e, ctx: ex.status.TryAddHandHoldStatus(1, ctx)
    EXEC.update({
        name('ExamMultipleLessonBuffLesson'): _multiple_lesson_buff_lesson,
        name('ExamLessonAddMultipleParameterBuff'): _lesson_with_rate('pb'),
        name('ExamMultipleEnthusiasticLesson'): _lesson_with_rate('enth'),
        name('ExamMultipleConcentrationLesson'): _lesson_with_rate('conc'),
        name('ExamMultipleFullPowerLesson'): _lesson_with_rate('full'),
        name('ExamLessonAddMultipleLessonBuff'): _lesson_add_multiple_lesson_buff,
        name('ExamLessonFullPowerPoint'): _lesson_full_power_point,
        name('ExamLessonAddBlock'): _lesson_add_block,
        name('ExamLessonPerSearchCount'): _lesson_per_search_count,
        name('ExamLessonDependStaminaConsumptionSum'): _lesson_depend_sum('stamina'),
        name('ExamLessonDependBlockConsumptionSum'): _lesson_depend_sum('block'),
        name('ExamLessonDependPlayCardCountSum'): _lesson_depend_sum('play'),
        name('ExamLessonDependEnthusiasticGetSum'): _lesson_depend_sum('enthusiastic'),
        name('ExamLessonDependBlockAndSearchCount'): _lesson_depend_and_search('block'),
        name('ExamLessonDependReviewAndSearchCount'): _lesson_depend_and_search('review'),
        name('ExamLessonDependAggressiveAndSearchCount'): _lesson_depend_and_search('aggressive'),
        name('ExamLessonBuffDependParameterBuff'): _convert('parameter_buff', 'lesson_buff'),
        name('ExamParameterBuffDependLessonBuff'): _convert('lesson_buff', 'parameter_buff'),
        name('ExamReviewDependExamBlock'): _convert('block', 'review'),
        name('ExamBlockDependExamReview'): _convert('review', 'block'),
        name('ExamReviewDependExamCardPlayAggressive'): _convert('aggressive', 'review'),
        name('ExamAggressiveDependReview'): _convert('review', 'aggressive'),
        name('ExamBlockDependBlockConsumptionSum'): _sum_to_status('block'),
        name('ExamReviewDependReviewConsumptionSum'): _sum_to_status('review'),
        name('ExamFullPowerPointDependFullPowerPointGetSum'): _sum_to_status('fullpower'),
        name('ExamBlockPerUseCardCount'): _block_per_use_card_count,
        name('ExamLessonBuffPerSearchCount'): _per_search_count('lesson_buff'),
        name('ExamReviewPerSearchCount'): _per_search_count('review'),
        name('ExamParameterBuffPerSearchCount'): _per_search_count('parameter_buff'),
        name('ExamAggressivePerSearchCount'): _per_search_count('aggressive'),
        name('ExamBlockPerSearchCount'): _per_search_count('block'),
        name('ExamFullPowerPointPerSearchCount'): _per_search_count('fullpower'),
        name('ExamEffectPerSearchCount'): _effect_per_search_count,
        name('ExamChainEffect'): _chain('value'),
        name('ExamChainEffectPerPassedTurn'): _chain('passed'),
        name('ExamChainEffectPerRemainTurn'): _chain('remain'),
        name('ExamParameterBuffReduce'): _reduce('ConsumeParameterBuff'),
        name('ExamAggressiveReduce'): _reduce('ConsumeAggressive'),
        name('ExamReviewReduce'): _reduce('ConsumeReview'),
        name('ExamLessonBuffReduce'): _reduce('ConsumeLessonBuff'),
        name('ExamParameterBuffMultiplePerTurnReduce'): _reduce('ConsumeParameterBuffMultiplePerTurn'),
        name('ExamFullPowerPointReduce'): _full_power_point_reduce,
        name('ExamCardCreateSearch'): _card_create_search,
        name('ExamCardDuplicate'): _card_duplicate,
        name('ExamForcePlayCardSearch'): _force_play(False),
        name('ExamForcePlayCardSearchWithCost'): _force_play(True),
        name('ExamDebuffRecover'): _debuff_recover,
        name('ExamItemFireLimitAdd'): _item_fire_limit_add,
        name('ExamStatusEnchantEncore'): _status_enchant_encore,
        name('ExamGimmickPlayCardLimit'): _gimmick_play_card_limit,
        name('ExamSearchPlayCardStaminaConsumptionChange'): _search_play_card_stamina_change,
        name('ExamStaminaDamage'): _stamina_damage,
        name('ExamBlockValueMultiple'): _block_value_multiple,
        name('ExamFullPowerPoint'): _full_power_point,
        name('ExamConcentration'): _stance(1),
        name('ExamPreservation'): _stance(2),
        name('ExamOverPreservation'): _stance(4, 1),
        name('ExamFullPower'): _stance(3, 1),
        name('ExamStanceReset'): _stance_reset,
        name('ExamAddGrowEffect'): _add_grow_effect,
    })


_register_more()


def execute_effect(ex, e, ctx):
    fn = EXEC.get(e['effectType'])
    ex.log('effect', id=e.get('id', ''), type=NAME.get(e['effectType'], e['effectType']), src=ctx.source if ctx else '')
    if fn is None:
        ex.log('unimplemented', type=NAME.get(e['effectType'], e['effectType']), id=e.get('id', ''))
        return
    fn(ex, e, ctx)
