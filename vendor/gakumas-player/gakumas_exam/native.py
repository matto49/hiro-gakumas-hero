"""Rust 后端（gakumas_exam_rs）的桥：主数据导出、局面导出、转成原生对局、两边局面比对。

    from gakumas_exam import native
    if native.available():
        rx = native.to_native(ex)        # Python Exam → 原生 Exam（之后 clone / step / legal_actions 都在 Rust 里）
        rx.step(('play', 0))
    native.diff(native.export_state(ex), json.loads(rx.export_json()))   # 逐字段比对

规则的参照实现仍是 Python 版；原生版逐函数移植 对拍。
主数据按提交号导出成 JSON（缓存在 ~/.cache/gakumas_exam_rs/），只在第一次用时生成。
"""
import json
import os
from pathlib import Path

TABLES = ('ProduceCard', 'ProduceExamEffect', 'ProduceExamTrigger', 'ProduceCardSearch', 'ProduceExamStatusEnchant',
          'ProduceCardStatusEnchant', 'ProduceCardGrowEffect', 'ProduceCardCustomize', 'ProduceDrink', 'ProduceDrinkEffect',
          'ExamSetting')
DROP = ('produceDescriptions', 'customizeProduceDescriptions', 'playProduceDescriptions', 'playEffectProduceDescriptions')
CACHE = Path(os.environ.get('GAKUMAS_EXAM_RS_CACHE', Path.home() / '.cache/gakumas_exam_rs'))

try:
    import gakumas_exam_rs as _rs
    if not hasattr(_rs, 'Exam'):  # 没装扩展时，源码目录 gakumas_exam_rs/ 会被当成空的命名空间包导入进来
        _rs = None
except ImportError:  # 没装原生包：调用方退回 Python 版
    _rs = None


def available():
    return _rs is not None


def master_path(m):
    """主数据导出成 JSON（按提交号缓存），返回路径。"""
    from .status import DEBUFF_EFFECT_TYPES, DEBUFF_STATUS_CLASSES
    p = CACHE / f'master-{m.commit}.json'
    if p.exists():
        return str(p)
    CACHE.mkdir(parents=True, exist_ok=True)
    tables = {t: [{k: v for k, v in r.items() if k not in DROP} for r in m.table(t)] for t in TABLES}
    doc = dict(commit=m.commit, tables=tables,
               rules=dict(debuff_effect_types=sorted(DEBUFF_EFFECT_TYPES), debuff_status_classes=sorted(DEBUFF_STATUS_CLASSES)))
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(doc, ensure_ascii=False))
    tmp.replace(p)
    return str(p)


# ---------------------------------------------------------------- 局面导出
def _grow(g):
    return {'id': g.get('id', ''), 'effectType': g.get('effectType', 0), 'value': g.get('value', 0),
            'playProduceExamTriggerId': g.get('playProduceExamTriggerId') or '', 'playEffectProduceExamTriggerId': g.get('playEffectProduceExamTriggerId') or '',
            'targetPlayEffectProduceExamTriggerIds': list(g.get('targetPlayEffectProduceExamTriggerIds') or []),
            'playProduceExamEffectId': g.get('playProduceExamEffectId') or '',
            'targetPlayProduceExamEffectIds': list(g.get('targetPlayProduceExamEffectIds') or []),
            'produceCardStatusEnchantId': g.get('produceCardStatusEnchantId') or '', 'playMovePositionType': g.get('playMovePositionType') or 0}


def _effect(e):
    return {'id': e.get('id') or '', 'effectType': e.get('effectType', 0), 'effectValue1': e.get('effectValue1', 0),
            'effectValue2': e.get('effectValue2', 0), 'effectCount': e.get('effectCount', 0), 'effectTurn': e.get('effectTurn', 0),
            'produceCardSearchId': e.get('produceCardSearchId') or '', 'pickRangeType': e.get('pickRangeType', 0),
            'pickCountMin': e.get('pickCountMin', 0), 'pickCountMax': e.get('pickCountMax', 0), 'movePositionType': e.get('movePositionType', 0),
            'targetProduceCardId': e.get('targetProduceCardId') or '', 'targetUpgradeCount': e.get('targetUpgradeCount', 0),
            'produceExamStatusEnchantId': e.get('produceExamStatusEnchantId') or '',
            'chainProduceExamEffectIds': list(e.get('chainProduceExamEffectIds') or []), 'chainProduceExamEffectId': e.get('chainProduceExamEffectId') or '',
            'produceCardGrowEffectIds': list(e.get('produceCardGrowEffectIds') or []), 'effectGroupIds': list(e.get('effectGroupIds') or [])}


def _trigger(t):
    t = t or {}
    return {'id': t.get('id') or '', 'phaseTypes': list(t.get('phaseTypes') or []), 'phaseValues': list(t.get('phaseValues') or []),
            'fieldStatusTypes': list(t.get('fieldStatusTypes') or []), 'fieldStatusValues': list(t.get('fieldStatusValues') or []),
            'fieldStatusCheckTypes': list(t.get('fieldStatusCheckTypes') or []),
            'fieldStatusProduceCardSearchIds': [x or '' for x in (t.get('fieldStatusProduceCardSearchIds') or [])],
            'produceCardSearchId': t.get('produceCardSearchId') or '', 'upperSearchCount': t.get('upperSearchCount', 0),
            'lowerSearchCount': t.get('lowerSearchCount', 0), 'lessonType': t.get('lessonType', 0), 'effectTypes': list(t.get('effectTypes') or []),
            'cardMovePositionType': t.get('cardMovePositionType', 0)}


def _se(se):
    if se is None:
        return None
    if not se.get('_id'):
        return {}
    return {'_id': se['_id'], '_produceExamTriggerId': se.get('_produceExamTriggerId') or '',
            '_produceCardGrowEffectIdList': list(se.get('_produceCardGrowEffectIdList') or []),
            '_effectGroupIdList': list(se.get('_effectGroupIdList') or []), '_triggerCount': se.get('_triggerCount', 0),
            'phase_count': [[x['key'], x.get('value', {}).get('current', 0)] for x in (se.get('_phaseCountDictionary') or {}).get('_list', [])],
            '_spendTurn': se.get('_spendTurn', 0), '_spendCount': se.get('_spendCount', 0)}


def _card(c):
    return {'card_id': c.card_id, 'base_upgrade': c.base_upgrade, 'tmp_upgrade': c.tmp_upgrade, 'support_upgrades': list(c.support_upgrades),
            'customize_counts': list(c.customize_counts),
            'custom_override': [[x['id'], x['count']] for x in c.custom_override] if c.custom_override else None,
            'guid': c.guid, 'fixed_order': c.fixed_order, 'play_count': c.play_count, 'grow_after': [_grow(g) for g in c.grow_after],
            'affect_grow': list(c.affect_grow), 'se': _se(c.status_effect), 'move_used_in_turn': bool(c.move_used_in_turn),
            'stamina_specify': [{'_turn': x.get('_turn', 0), '_intValue': x.get('_intValue', 0), '_boolValue': bool(x.get('_boolValue', False)),
                                 '_count': x.get('_count', 0), '_isTurnLimited': bool(x.get('_isTurnLimited', False)),
                                 '_isCountLimited': bool(x.get('_isCountLimited', False)),
                                 '_isPassingTurnStart': bool(x.get('_isPassingTurnStart', False))} for x in c.stamina_specify]}


def _status(s):
    from .status import TriggerEffect
    d = {'cls': s.cls, 'uid': s.uid, 'turn': s.turn, 'limited': bool(s.limited), 'passing': bool(s.passing), 'value': s.value,
         'extra': dict(s.extra), 'trig': None}
    if isinstance(s, TriggerEffect):
        d['value'] = 0
        d['trig'] = {'trigger': _trigger(s.trigger), 'effects': [_effect(e) for e in s.effects], 'turn_count': s.turn_count,
                     'phase_count': [[k, v] for k, v in s.phase_count.items()], 'limit_count': s.limit_count,
                     'limit_in_turn': s.limit_in_turn, 'limit_in_turn_remain': s.limit_in_turn_remain, 'enchant_id': s.enchant_id or '',
                     'origin_type': s.origin_type, 'source': {k: v for k, v in s.source.items() if v not in (None, '')},
                     'override_type': s.override_type, 'from_effect_ids': list(s.from_effect_ids), 'from_enchant': bool(s.from_enchant),
                     'encore': bool(s.encore)}
    return d


def export_state(ex):
    """Python Exam → 原生后端的局面格式（卡按对象去重成一张表，牌区与出牌日志用下标）。命令队列必须为空。"""
    if ex.queue:
        raise ValueError('命令队列非空：只能在两个动作之间导出')
    if getattr(ex, 'draw_queue', None) is not None:
        raise ValueError('强制抽牌（回放）的局面不支持原生后端')
    cards, index = [], {}

    def ref(c):
        k = id(c)
        if k not in index:
            index[k] = len(cards)
            cards.append(c)
        return index[k]

    zones = {z: [ref(c) for c in ex.zones[z]] for z in ('hand', 'deck', 'grave', 'lost', 'hold')}
    playing = ref(ex.playing) if ex.playing is not None else None
    play_log = [ref(c) for c in ex.play_log]
    stg = getattr(ex, 'contest_stage', None)
    return {
        'setting_id': ex.setting['id'], 'rng': ex.rng.state, 'cards': [_card(c) for c in cards], 'zones': zones, 'playing': playing,
        'status': {'effects': [_status(s) for s in ex.status.effects], 'create_count': ex.status.create_count,
                   'idol_type': ex.status.idol_type, 'idol_step': ex.status.idol_step},
        'plan_type': ex.plan_type, 'exam_type': ex.exam_type, 'stamina': ex.stamina, 'max_stamina': ex.max_stamina, 'block': ex.block,
        'score': ex.score, 'current_turn': ex.current_turn, 'limit_turn': ex.limit_turn, 'remain_turn': ex.remain_turn,
        'extra_turn': ex.extra_turn, 'turn_types': list(ex.turn_types),
        'bonus_permil': [[k, ex.bonus_permil[k]] for k in (1, 2, 3) if k in ex.bonus_permil],
        'exam_card_play_count': ex.exam_card_play_count, 'turn_card_play_count': ex.turn_card_play_count,
        'counters': dict(ex.counters), 'drinks': list(ex.drinks), 'items': [[it['_id'], it.get('_fireCount', 0)] for it in ex.items],
        'supports': [[s['_supportCardId'], s.get('_filterParameterType') or 0, s.get('_produceCardUpgradePermil', 0)] for s in ex.supports],
        'turn_used_supports': list(ex.turn_used_supports), 'clear_border': ex.clear_border, 'play_log': play_log,
        'initial_deal': bool(ex.initial_deal),
        'gimmick_schedule': [[t, {'id': g['id'], 'produceExamEffectId': g.get('produceExamEffectId') or '',
                                  'fieldStatusType': g.get('fieldStatusType', 0), 'fieldStatusValue': g.get('fieldStatusValue', 0),
                                  'fieldStatusCheckType': g.get('fieldStatusCheckType', 0),
                                  'fieldStatusProduceCardSearchId': g.get('fieldStatusProduceCardSearchId') or ''}]
                             for t, g in sorted(ex.gimmick_schedule.items())],
        'ended': bool(ex.ended), 'remaining_actions': ex.remaining_actions, 'pending_start': bool(getattr(ex, 'pending_start', False)),
        'guid_seq': ex.__dict__.get('_guid_seq', 0),
        'contest': {'id': stg.id, 'used': stg.used, 'counter': stg.counter, 'gi_direct': stg.gi_direct} if stg else None,
        'npcs': [[list(n.get('_scoreList') or []), n.get('_currentScore')] for n in ex.npcs],
    }


def to_native(ex):
    if _rs is None:
        raise RuntimeError('没装 gakumas_exam_rs（cd gakumas_exam_rs && maturin develop --release）')
    return _rs.Exam(master_path(ex.master), json.dumps(export_state(ex), ensure_ascii=False))


# ---------------------------------------------------------------- 比对
def canonical(state):
    """下标换成卡本身（两边新生成卡的表内顺序可能不同）。"""
    cards = state['cards']
    out = dict(state)
    out.pop('cards')
    out['zones'] = {z: [cards[i] for i in v] for z, v in state['zones'].items()}
    out['playing'] = cards[state['playing']] if state['playing'] is not None else None
    out['play_log'] = [cards[i]['guid'] for i in state['play_log']]
    return out


def diff(a, b, path='', out=None, limit=20):
    """两份导出局面的差异（路径 → (Python, Rust)）。"""
    if out is None:
        out = []
        a, b = canonical(a), canonical(b)
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            diff(a.get(k), b.get(k), f'{path}.{k}', out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path + '#len', len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f'{path}[{i}]', out, limit)
    elif a != b:
        out.append((path, a, b))
    return out
