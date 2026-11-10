"""客户端存档 ExamSaveData（Unity JsonUtility + SerializeReference）↔ 引擎局面。

- load(save_dict, master) → Exam：从任意实机存档起步；
- snapshot(exam) / snapshot_save(save_dict)：规范化成可比较的 dict（体力、元気、分数、回合、行动数、牌区、状态、附魔次数、随机数状态 …），
  两边用同一份规范化，逐字段 diff。
"""
from .cards import ExamCard
from .status import Status, TriggerEffect, StatusCollection, INT_CLASSES

# 状态类特有字段（存档字段名 → extra 键）
_EXTRA = ('_count', '_searchId', '_fromExamEffectId', '_value2', '_limitCountInTurn', '_limitCountInTurnRemain', '_isDisable')


def _refs(save):
    return {r['rid']: r for r in save.get('references', {}).get('RefIds', [])}


def _effect_row(e, master):
    """存档里序列化的 ProduceExamEffect（_effectType 等）→ 主数据行（effectType 等）。优先按 _id 取主数据，保留存档数值。"""
    row = master.get('ProduceExamEffect', e['_id']) if e.get('_id') else None
    row = dict(row) if row else {'id': e.get('_id', '')}
    row.update(effectType=e['_effectType'], effectValue1=e['_effectValue1'], effectValue2=e['_effectValue2'],
               effectCount=e['_effectCount'], effectTurn=e['_effectTurn'])
    return row


def status_from_ref(ref, master):
    cls, d = ref['type']['class'], ref['data']
    if cls in ('TriggerEffectStatusEffect', 'TriggerMissionStatusEffect'):
        trig = master.trigger(d['_trigger']['_id']) or {'id': d['_trigger']['_id'], 'phaseTypes': d['_trigger']['_phaseTypeList'],
                                                         'phaseValues': d['_trigger']['_phaseValueList']}
        source = {}
        if d.get('_triggerItem', {}).get('_id'):
            source['item'] = d['_triggerItem']['_id']
        if d.get('_triggerDrink', {}).get('_id'):
            source['drink'] = d['_triggerDrink']['_id']
        if d.get('_triggerCard', {}).get('_cardData', {}).get('_id'):
            source['card'] = d['_triggerCard']['_cardData']['_id']
            source['card_guid'] = d['_triggerCard'].get('_guid', '')
        if d.get('_startEnchantOriginId'):
            source['origin'] = d['_startEnchantOriginId']
        t = TriggerEffect(trig, [_effect_row(e, master) for e in d['_effectList']], d['_originType'], d['_turn'],
                          d['_limitCount'], d['_limitCountInTurn'], d['_statusEnchantId'], source, d['_overrideType'],
                          d['_isPassingTurnStart'], d['_uid'])
        t.limited = d['_isTurnLimited']
        t.turn_count = d['_turnCount']
        t.phase_count = {p['key']: p['value']['current'] for p in d['_phaseCountDictionary'].get('_list', [])}
        t.limit_in_turn_remain = d['_limitCountInTurnRemain']
        t.from_effect_ids = list(d.get('_fromExamEffectIdList', []))
        t.from_enchant = d.get('_isFromEnchantEffect', False)
        t.encore = d.get('_isEncoreEnchant', False)
        return t
    value = d.get('_value', d.get('_count', 0))
    s = Status(cls, d.get('_turn', -1), value, d.get('_isTurnLimited', False), d.get('_isPassingTurnStart', False), d.get('_uid', 0),
               {k: d[k] for k in _EXTRA if k in d})
    return s


def load(save, master):
    from .exam import Exam
    refs = _refs(save)
    ex = Exam(master, setting_id=save['settingId'])
    ex.plan_type = save['planType']
    ex.exam_type = save['examType']
    ex.produce_id = save['produceId']
    ex.idol_card_id = save['idolCardId']
    ex.step_type = save['stepType']
    ex.stamina, ex.max_stamina = save['stamina'], save['maxStamina']
    ex.block = save['block']
    ex.score = save['parameter']
    ex.phase = save['phase']
    ex.current_turn, ex.limit_turn, ex.remain_turn, ex.extra_turn = save['currentTurn'], save['limitTurn'], save['remainTurn'], save['extraTurn']
    ex.turn_types = list(save['turnStatusParameterTypeList'])
    ex.bonus_permil = {1: save['vocalBonusPermil'], 2: save['danceBonusPermil'], 3: save['visualBonusPermil']}
    ex.rng.state = save['random'] & 0xffffffff
    ex.drink_limit = save['produceDrinkPossessLimit']
    ex.exam_card_play_count = save['examCardPlayCount']
    ex.turn_card_play_count = save['turnCardPlayCount']
    ex.is_turn_card_play_end = save['isTurnCardPlayEnd']
    ex.is_turn_card_grave = save['isTurnCardGrave']
    ex.is_turn_card_lost = save['isTurnCardLost']
    ex.counters = {k: save[k] for k in ('fullPowerPointGetSumCount', 'blockConsumptionSumCount', 'reviewConsumptionSumCount',
                                        'stanceChangeCount', 'stanceFullPowerChangeCount', 'stanceConcentrationChangeCount',
                                        'stancePreservationChangeCount', 'totalConsumeStamina', 'totalDrawCardCount',
                                        'currentTurnTotalAddParameter', 'currentTurnTotalConsumeStamina', 'currentTurnTotalBlock')
                   if k in save}
    ex.parameters = {1: save.get('parameterVocal', 0), 2: save.get('parameterDance', 0), 3: save.get('parameterVisual', 0)}
    ex.drinks = [d['_id'] for d in save['drinkList']]
    ex.items = [dict(i) for i in save['itemList']]
    ex.supports = [dict(s) for s in save['supportCardList']]
    ex.turn_used_supports = list(save['turnUseSupportCardIdList'])
    ex.npcs = save.get('npcDataList', [])
    gl = save.get('gimmickList') or []
    if gl:   # 存档里的机关组（id 或 {_id}）→ 主数据行，按原生 Setup 排程
        ids = [g if isinstance(g, str) else (g.get('_id') or g.get('id')) for g in gl]
        rows = master.table('ProduceExamGimmickEffectGroup')
        ex.setup_gimmicks([r for i in dict.fromkeys(ids) for r in rows if r['id'] == i])   # 一组多行（待核：存档条目是组还是行）
    for zone, key in (('hand', 'handList'), ('deck', 'deckList'), ('grave', 'graveList'), ('lost', 'lostList'), ('hold', 'holdList')):
        ex.zones[zone] = [ExamCard.from_save(c, master) for c in save[key]]
    pc = save.get('playingCard') or {}
    ex.playing = ExamCard.from_save(pc, master) if pc.get('_cardData', {}).get('_id') else None
    sc = StatusCollection(ex)
    st = save['status']
    sc.effects = [status_from_ref(refs[x['rid']], master) for x in st['_effectList']]
    sc.removed = [status_from_ref(refs[x['rid']], master) for x in st.get('_removedEffectList', [])]
    sc.create_count = st['_effectCreateCount']
    sc.idol_type = st['_idolStatusEffect']['_currentType']
    sc.idol_step = st['_idolStatusEffect']['_currentStep']
    ex.status = sc
    ex.log_len = len(save.get('userPlayLogList', []))
    # 剩余行动（主阶段存档）：客户端可出牌次数 = PlayableValueAdd._value +（isTurnCardPlayEnd ? 0 : 1）（3.4.1 ExecuteCardCommandImpl，
    # 出牌时优先消耗追加次数，没有追加才置位）。引擎把「基础 1 次」放在 remaining_actions、追加次数在出牌结束时逐次转入，
    # 所以基础次数已用掉而还有追加时，先转入 1 次（计数相同）。
    pv = sc.first('PlayableValueAddStatusEffect')
    extra = pv.value if pv is not None else 0
    if not save.get('isTurnCardPlayEnd'):
        ex.remaining_actions = 1
    elif extra > 0:
        ex.remaining_actions = 1
        pv.value -= 1
        if pv.value <= 0:
            sc.RemoveStatusEffect(pv)
    else:
        ex.remaining_actions = 0
    return ex


# ---------------------------------------------------------------- 规范化快照

def _status_key(s):
    if isinstance(s, TriggerEffect):
        return dict(kind='enchant', id=s.enchant_id or s.trigger.get('id'), turn=s.turn, limited=s.limited, passing=s.passing,
                    left=s.limit_count, in_turn=s.limit_in_turn_remain, turn_count=s.turn_count,
                    phase_count=dict(sorted(s.phase_count.items())),
                    effects=[e['id'] for e in s.effects])
    d = dict(kind=s.cls, turn=s.turn, limited=s.limited, passing=s.passing)
    if s.cls in INT_CLASSES or s.cls == 'AntiDebuffStatusEffect':
        d['value'] = s.value
    return d


# 没有规则读取、清零时点待核的计数（存档时点不一，无法据存档判定），对账不比较
UNCHECKED_COUNTERS = {'currentTurnTotalConsumeStamina', 'currentTurnTotalBlock', 'currentTurnTotalAddParameter', 'totalDrawCardCount'}


def snapshot(ex):
    return dict(
        stamina=ex.stamina, max_stamina=ex.max_stamina, block=ex.block, score=ex.score,
        turn=ex.current_turn, remain=ex.remain_turn, limit=ex.limit_turn, extra=ex.extra_turn,
        rng=ex.rng.state, card_plays=ex.exam_card_play_count, turn_plays=ex.turn_card_play_count,
        idol=(ex.status.idol_type, ex.status.idol_step), effect_create_count=ex.status.create_count,
        zones={z: [c.key() for c in cs] for z, cs in ex.zones.items()},
        statuses=[_status_key(s) for s in ex.status.effects],
        drinks=list(ex.drinks),
        counters={k: v for k, v in sorted(ex.counters.items()) if k not in UNCHECKED_COUNTERS},
    )


def snapshot_save(save, master):
    return snapshot(load(save, master))


def diff(a, b, path=''):
    """两份快照的差异列表 [(路径, 左, 右)]。"""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            out += diff(a.get(k), b.get(k), f'{path}.{k}' if path else str(k))
    elif isinstance(a, list) and isinstance(b, list) and path.endswith('statuses'):
        # 状态列表：按种类 + id 对齐，长度不同时列出多 / 少的
        def key(x):
            return (x.get('kind'), x.get('id'))
        ka, kb = [key(x) for x in a], [key(x) for x in b]
        for i, x in enumerate(a):
            if key(x) in kb:
                y = b[kb.index(key(x))]
                kb[kb.index(key(x))] = None
                out += diff(x, y, f'{path}[{key(x)[1] or key(x)[0]}]')
            else:
                out.append((f'{path}+', x, None))
        for j, k in enumerate(kb):
            if k is not None:
                out.append((f'{path}-', None, b[j]))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f'{path}[{i}]')
    elif a != b:
        out.append((path, a, b))
    return out
