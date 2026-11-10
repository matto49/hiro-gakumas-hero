"""开考配置：回合属性配额（ExamParameterModel.CalcTurnParameterType 正权重、T≥3 分支）与按种子抽样的回合属性。

原在旧引擎 gakumas_sim（turn_order.allocate、campus.document 的 campus 段、campus.turn_attributes），这里只保留考试要用的部分。
meta（exam_meta 的返回值）：bonus 三属性倍率、allocation 回合配额、draw 每回合抽牌数、first 实机第一回合属性、source 快照摘要。
"""
import math
import struct

from .errors import Unsupported

ATTRIBUTES = ('Vo', 'Da', 'Vi')
ATTR_BY_TYPE = {1: 'Vo', 2: 'Da', 3: 'Vi'}
ATTRIBUTE_LESSON = {'Vo': 'vocal', 'Da': 'dance', 'Vi': 'visual'}

_F32 = struct.Struct('<f')


def f32(value):
    return _F32.unpack(_F32.pack(value))[0]


def _checked(value, name, maximum=10000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise Unsupported(name + ' must be an integer in 0..' + str(maximum))
    return value


def allocate(turns, parameters):
    """基础回合数 + 三属性配置权重 → 前段随机部分各属性回合数、末三回合（权重升序）。"""
    _checked(turns, 'base turns', 100)
    if turns < 3:
        raise Unsupported('Native allocation branch requires at least three turns')
    if set(parameters) != set(ATTRIBUTES):
        raise Unsupported('Need exactly Vo/Da/Vi config weights')
    for attr in ATTRIBUTES:
        _checked(parameters[attr], attr + ' config weight')
        if not parameters[attr]:
            raise Unsupported('Zero config weight is outside the restored branch')
    ordered = sorted(ATTRIBUTES, key=lambda a: -parameters[a])
    p0, p1, p2 = (parameters[a] for a in ordered)
    n = turns - 3
    first = math.ceil(f32(f32(f32(n) * f32(p0)) / f32(p0 + p1 + p2)))
    rest = n - first
    second = round(f32(f32(f32(rest) * f32(p1)) / f32(p1 + p2)))
    counts = [first, second, rest - second]
    if min(counts) < 0:
        raise Unsupported('Invalid native allocation')
    return dict(ordered_attributes=ordered, random_prefix_counts=dict(zip(ordered, counts)),
                counts={a: dict(zip(ordered, counts))[a] + 1 for a in ATTRIBUTES}, fixed_tail=list(reversed(ordered)))


def exam_meta(observation, catalog=None):
    """开考 observation（snapshot.project 的投影）→ 回合配额等开考配置。"""
    s = observation['scalars']
    if catalog is None:
        from .catalog import Catalog
        c = Catalog()
        try:
            setting = c.one('ExamSetting', s['settingId'])
        finally:
            c.db.close()
    else:
        setting = catalog.one('ExamSetting', s['settingId'])
    bonus = dict(Vo=s['vocalBonusPermil'], Da=s['danceBonusPermil'], Vi=s['visualBonusPermil'])
    weights = dict(Vo=s['vocalConfigParameter'], Da=s['danceConfigParameter'], Vi=s['visualConfigParameter'])
    return dict(bonus=bonus, allocation=allocate(s['limitTurn'], weights), draw=setting['turnStartDistribute'],
                first=ATTR_BY_TYPE.get(observation.get('current_parameter_type')), source=observation.get('public_sha256'))


def turn_attributes(meta, rng, *, keep_first=True):
    """整场回合属性：前段按配额随机排列，末三回合固定；keep_first 时第一回合沿用实机观测。"""
    pool = [a for a, n in meta['allocation']['random_prefix_counts'].items() for _ in range(n)]
    first = meta['first'] if keep_first and meta['first'] in pool else None
    if first:
        pool.remove(first)
    rng.shuffle(pool)
    return ([first] if first else []) + pool + list(meta['allocation']['fixed_tail'])


def turn_attributes_for(meta, current, limit, rng, current_attr):
    """第 current 回合起到第 limit 回合的属性：末三回合固定，之前的按配额随机（本回合用观测值）。"""
    tail = list(meta['allocation']['fixed_tail'])
    prefix_len = limit - len(tail)
    if current > prefix_len:
        return tail[current - prefix_len - 1:]
    left = prefix_len - current
    pool = [a for a, n in meta['allocation']['random_prefix_counts'].items() for _ in range(n)]
    if current_attr in pool:
        pool.remove(current_attr)
    rng.shuffle(pool)
    return [current_attr] + pool[:left] + tail
