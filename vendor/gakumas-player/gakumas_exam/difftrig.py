"""差分驱动的结果触发（ExamSequence.GetInsertEffectResultTriggerCommand）
与卡自身附魔（ProduceCardStatusEffect，GetTriggeredCardStatusEffectCommand）。

一条命令（效果 / 付费）执行前后各取一次「观测值」，加上执行中记下的事件（Exam.note_diff），合成差分：
- 体力减少 → ExamStaminaReduce(20)，出牌中再 ExamStaminaReduceCard(24)
- 卡移动到 手牌 / 弃牌 / 除外 → ExamCardMoveHand/Grave/Lost（每张卡一组）
- 指针变化 → ExamStanceChange 系列（见 stance_triggers）
- 状态 key 增加（只看增加；key 升序）→ ExamStatusChange（增量 ≥ phaseValue[0]）与 ××UpInterval
每个事件先发状态附魔（L），再发卡附魔（C）。整体插到命令队首。
入口条件：命令的 ctx.IsEnchantTriggerActive()（道具效果、非计时器附魔的效果不产生结果触发）。
"""
from .master import E
from .status import TriggerEffect
from . import triggers

PH = E('ProduceExamPhaseType')
MOVE = E('ProduceCardMovePositionType')
POS = E('ProduceCardPositionType')

# 状态 key（ProduceExamEffectType）→ 观测取值
STATUS_KEYS = {
    2: lambda ex: ex.status.GetParameterBuffTurn(),
    3: lambda ex: ex.block,
    10: lambda ex: ex.status.GetLessonBuff(),
    31: lambda ex: ex.status.GetReview(),
    42: lambda ex: ex.status.GetAggressive(),
    49: lambda ex: ex.status.GetFullPowerPoint(),
}
UP_INTERVAL = {2: PH['ExamParameterBuffUpInterval'], 10: PH['ExamLessonBuffUpInterval'], 31: PH['ExamReviewUpInterval'],
               42: PH['ExamAggressiveUpInterval'], 49: PH['ExamFullPowerPointUpInterval']}


# observe 的单趟取值：类 → (key, 字段)。与 STATUS_KEYS 同义（各类取第一个状态的 value / turn，没有为 0），每条命令前后都要观测，所以只扫一遍状态表
_FIRST = {'ParameterBuffStatusEffect': (2, 'turn'), 'LessonBuffStatusEffect': (10, 'value'), 'ReviewStatusEffect': (31, 'turn'),
          'AggressiveStatusEffect': (42, 'value'), 'FullPowerPointStatusEffect': (49, 'value')}


def observe(ex):
    sc = ex.status
    keys = {2: 0, 3: ex.block, 10: 0, 31: 0, 42: 0, 49: 0}
    seen = 0
    for st in sc.effects:
        m = _FIRST.get(st.cls)
        if m is not None and not (seen >> m[0]) & 1:
            seen |= 1 << m[0]
            keys[m[0]] = getattr(st, m[1])
    return dict(stamina=ex.stamina, score=ex.score, keys=keys, stance=(sc.idol_type, sc.idol_step),
                stance_count=ex.counters.get('stanceChangeCount', 0))


def rebase(pre, windows):
    """把触发屏蔽窗口里的变化计入「前值」，使其不产生结果触发。"""
    if not windows:
        return pre
    pre = dict(pre, keys=dict(pre['keys']))
    for a, b in windows:
        pre['stamina'] += b['stamina'] - a['stamina']
        pre['score'] += b['score'] - a['score']
        for k in pre['keys']:
            pre['keys'][k] += b['keys'][k] - a['keys'][k]
    return pre


# ---------------------------------------------------------------- 卡附魔（ProduceCardStatusEffect）

def card_phase_count(se, phase):
    for x in (se.get('_phaseCountDictionary') or {}).get('_list', []):
        if x.get('key') == phase:
            return x.get('value', {}).get('current', 0)
    return 0


def inc_card_phase_count(se, phase):
    d = se.setdefault('_phaseCountDictionary', {'_list': []})
    for x in d['_list']:
        if x.get('key') == phase:
            x['value']['current'] = x['value'].get('current', 0) + 1
            return
    d['_list'].append({'key': phase, 'value': {'current': 1}})


def deck_all(ex):
    """ExamCardMoveController.CreateCardPositionDataList(DeckAll 6)：手牌、牌堆、弃牌、保留、出牌中（不含除外）。"""
    return ex.cards_at(POS['DeckAll'])


def card_enchant_trigger(ex, card):
    se = card.ensure_status_effect()
    if not se or not se.get('_id'):
        return None, None
    tc = se.get('_triggerCount', 0)
    if tc and se.get('_spendCount', 0) >= tc:
        return se, None
    return se, ex.master.trigger(se.get('_produceExamTriggerId'))


def inc_card_enchant_phase(ex, phases, card_filter=None):
    """IncrementEnchantPhaseCount 的卡附魔部分：DeckAll 中附魔触发含这些阶段的卡各自计数 +1。"""
    for c in deck_all(ex):
        se = c.ensure_status_effect()
        if not se or not se.get('_id'):
            continue
        tr = ex.master.trigger(se.get('_produceExamTriggerId'))
        ph = (tr or {}).get('phaseTypes') or []
        for p in phases:
            if p in ph and (card_filter is None or card_filter(c, tr)):
                inc_card_phase_count(se, p)


def card_enchant_cmds(ex, pred, event_cards=None):
    """GetTriggeredCardStatusEffectCommand：DeckAll 中附魔未用满 triggerCount、pred(卡, 触发, 附魔) 为真的卡，
    触发检索 isSelf 时要求这张卡在事件卡里；命中则 spendCount+1，命令 = 把附魔的成长加给这张卡（CardPhaseStatusAffect）。"""
    cmds = []
    for c in deck_all(ex):
        se, tr = card_enchant_trigger(ex, c)
        if tr is None:
            continue
        sid = tr.get('produceCardSearchId')
        if sid and event_cards is not None:
            s = ex.master.search(sid)
            if s.get('isSelf') and not any(x is c for x in event_cards):
                continue
        if not pred(c, tr, se):
            continue
        se['_spendCount'] = se.get('_spendCount', 0) + 1
        grows = list(se.get('_produceCardGrowEffectIdList') or [])
        ex.log('card_enchant', card=c.card_id, enchant=se['_id'])
        cmds.append(('call', lambda c=c, grows=grows: ex.AddGrowEffects([c], grows, valid_check=False)))
    return cmds


# ---------------------------------------------------------------- 状态附魔选择

def enchants(ex, pred):
    return [t for t in list(ex.status.effects) if isinstance(t, TriggerEffect) and ex._available(t) and pred(t)]


def phase_L_C(ex, phase, ctx, cards=None):
    L = ex.fire(enchants(ex, lambda t: triggers.phase_trigger(ex, t.trigger, phase, ctx)))
    C = card_enchant_cmds(ex, lambda c, tr, se: triggers.phase_trigger(ex, tr, phase, ctx), cards)
    return L + C


# ---------------------------------------------------------------- 结果触发

MOVE_TRIGGER = E('ProduceCardMoveEffectTriggerType')
POOL_TO_MOVE_TRIGGER = {MOVE['Grave']: MOVE_TRIGGER['Grave'], MOVE['Lost']: MOVE_TRIGGER['Lost'], MOVE['Hold']: MOVE_TRIGGER['Hold']}


def move_effect_cmds(ex, card):
    """ExamSequence.InsertMoveEffect：卡的移动时效果（moveProduceExamEffectIds，第 i 个效果的条件 =
    moveProduceExamTriggerIds[i]），以这张卡为来源执行。待核：_isMoveProduceExamEffectUseInTurn 是否限制每回合一次。"""
    from .exam import Ctx
    row = card.row()
    effs = row.get('moveProduceExamEffectIds') or []
    if not effs:
        return []
    trs = row.get('moveProduceExamTriggerIds') or []
    c = Ctx(ex, card=card)
    out = []
    for i, eid in enumerate(effs):
        tr = ex.master.trigger(trs[i]) if i < len(trs) and trs[i] else None
        if tr is None or triggers.trigger_field_valid(ex, tr, c, target_cards=[card]):
            out.append(('effect', ex.master.effect(eid), c))
    card.move_used_in_turn = True
    if out:
        ex.log('move_effect', card=card.card_id)
    return out


def move_effects_for(ex, kind, kw):
    """InsertMoveEffectOnHandAdd / OnPoolAdd。"""
    out = []
    for c in kw['cards']:
        mt = c.row().get('moveEffectTriggerType', 0)
        if kind == 'hand_add':
            if mt == MOVE_TRIGGER['Hand'] or (mt == MOVE_TRIGGER['Draw'] and kw['frm'] == POS['Deck'] and kw['etype'] == 4):
                out += move_effect_cmds(ex, c)
        elif mt and POOL_TO_MOVE_TRIGGER.get(kw['pos']) == mt:
            out += move_effect_cmds(ex, c)
    return out


def result_triggers(ex, pre, post, events, ctx):
    if ctx is None or not ctx.IsEnchantTriggerActive():
        # 只处理移动附带效果，不发任何触发
        out = []
        for k, kw in events:
            if k in ('hand_add', 'move'):
                out += move_effects_for(ex, k, kw)
        return out
    out = []
    # 0 JudgeParameter（打分）：ExamLesson 阶段
    if post['score'] > pre['score']:
        out += phase_L_C(ex, PH['ExamLesson'], ctx)
    # 1 / 3 体力减少（含元気混合伤害）
    if post['stamina'] < pre['stamina'] or any(k == 'stamina' and kw.get('mix') for k, kw in events):
        out += phase_L_C(ex, PH['ExamStaminaReduce'], ctx)
        if ex.playing is not None:
            out += phase_L_C(ex, PH['ExamStaminaReduceCard'], ctx)
    # 5 HandAdd：移动附带效果；7 PoolAdd：卡移动触发（每张一组）→ 移动附带效果
    for k, kw in events:
        if k == 'hand_add':
            out += move_effects_for(ex, k, kw)
        elif k == 'move':
            for c in kw['cards']:
                out += move_card_triggers(ex, c, kw['pos'], ctx)
            out += move_effects_for(ex, k, kw)
    # 12 指针变化
    if post['stance'] != pre['stance'] or post['stance_count'] != pre['stance_count']:
        out += stance_triggers(ex, pre, post, ctx)
    # B 段：状态 key 增加（升序）
    for key in sorted(STATUS_KEYS):
        b, a = pre['keys'][key], post['keys'][key]
        if a <= b:
            continue
        d = a - b
        out += ex.fire(enchants(ex, lambda t: triggers.change_effect_trigger(ex, t.trigger, key, d, ctx)))
        out += card_enchant_cmds(ex, lambda c, tr, se: triggers.change_effect_trigger(ex, tr, key, d, ctx))
        ph = UP_INTERVAL.get(key)
        if ph is not None:
            out += ex.fire(enchants(ex, lambda t: triggers.interval_trigger(ex, t.trigger, ph, t.phase_count.get(ph, 0), ctx)))
            out += card_enchant_cmds(ex, lambda c, tr, se: triggers.interval_trigger(ex, tr, ph, card_phase_count(se, ph), ctx))
    return out


def move_card_triggers(ex, card, pos, ctx):
    """IsMoveCardTrigger：只认 Hand / Grave / Lost。"""
    phase = {MOVE['Hand']: PH['ExamCardMoveHand'], MOVE['Grave']: PH['ExamCardMoveGrave'], MOVE['Lost']: PH['ExamCardMoveLost']}.get(pos)
    if phase is None:
        return []
    L = ex.fire(enchants(ex, lambda t: triggers.card_phase_trigger(ex, t.trigger, phase, [card], pos, ctx)))
    C = card_enchant_cmds(ex, lambda c, tr, se: triggers.card_phase_trigger(ex, tr, phase, [card], pos, ctx), [card])
    return L + C


def stance_triggers(ex, pre, post, ctx):
    """t=12 StanceChange。b/a = 前后指针类型，step = 段数。"""
    (b, bs), (a, as_) = pre['stance'], post['stance']
    st_diff = b != a
    step_diff = bs != as_
    cnt_diff = pre['stance_count'] != post['stance_count']
    out = []
    if st_diff and a != 0:
        out += phase_L_C(ex, PH['ExamStanceChange'], ctx)
    if cnt_diff:
        ph = PH['ExamStanceChangeCount']
        out += ex.fire(enchants(ex, lambda t: triggers.timer_trigger(ex, t.trigger, ph, t.phase_count.get(ph, 0), ctx)))
        out += card_enchant_cmds(ex, lambda c, tr, se: triggers.timer_trigger(ex, tr, ph, card_phase_count(se, ph), ctx))
        ph2 = PH['ExamStanceChangeCountInterval']
        out += ex.fire(enchants(ex, lambda t: triggers.interval_trigger(ex, t.trigger, ph2, t.phase_count.get(ph2, 0), ctx, at_least_one=False)))
        out += card_enchant_cmds(ex, lambda c, tr, se: triggers.interval_trigger(ex, tr, ph2, card_phase_count(se, ph2), ctx, at_least_one=False))
    # 离开原指针
    if b == 1 or b in (2, 4):
        out += ex.fire(enchants(ex, lambda t: triggers.stance_change_from_trigger(ex, t.trigger, b, a, bs, ctx)))
        out += card_enchant_cmds(ex, lambda c, tr, se: triggers.stance_change_from_trigger(ex, tr, b, a, bs, ctx))
    elif b == 3:
        out += phase_L_C(ex, PH['ExamStanceChangeFromFullPower'], ctx)
    # 进入新指针
    if a == 0:
        out += phase_L_C(ex, PH['ExamStanceReset'], ctx)
    elif a == 1 or a in (2, 4):
        if st_diff or step_diff:
            out += ex.fire(enchants(ex, lambda t: triggers.stance_change_trigger(ex, t.trigger, b, a, as_, ctx)))
            out += card_enchant_cmds(ex, lambda c, tr, se: triggers.stance_change_trigger(ex, tr, b, a, as_, ctx))
        if st_diff:
            ph = PH['ExamStanceChangeConcentrationInterval'] if a == 1 else PH['ExamStanceChangePreservationInterval']
            out += ex.fire(enchants(ex, lambda t: triggers.interval_trigger(ex, t.trigger, ph, t.phase_count.get(ph, 0), ctx, at_least_one=False)))
            out += card_enchant_cmds(ex, lambda c, tr, se: triggers.interval_trigger(ex, tr, ph, card_phase_count(se, ph), ctx, at_least_one=False))
    elif a == 3:
        out += phase_L_C(ex, PH['ExamStanceChangeFullPower'], ctx)
        ph = PH['ExamStanceChangeFullPowerInterval']
        out += ex.fire(enchants(ex, lambda t: triggers.interval_trigger(ex, t.trigger, ph, t.phase_count.get(ph, 0), ctx, at_least_one=False)))
        out += card_enchant_cmds(ex, lambda c, tr, se: triggers.interval_trigger(ex, tr, ph, card_phase_count(se, ph), ctx, at_least_one=False))
    return out
