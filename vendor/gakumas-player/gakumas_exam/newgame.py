"""开新局与对局运行。

from_opening(save, seed)：用一份实机开考存档（T1 主阶段，开考初始化与 T1 回合开始触发已完成）开同分布新局：
  手牌收回牌堆（清支援强化）→ NativeRandom(seed) 洗牌 → 初始卡（isInitial）优先入手到手牌上限、多的放回牌堆顶
  → 补抽到 turnStartDistribute 张 → 支援强化判定。（首回合发牌沿用旧引擎已验证的规则；SetInitialCard 原生还原后替换。）
  可选 randomize_turns：按旧引擎 turn_order 规则重排前段回合属性（末三回合固定），默认保留存档里的属性序列。

play(ex, policy)：policy(ex, legal_actions) → 动作；跑到考试结束，返回最终分数。
"""
from .rng import NativeRandom
from .save import load


def from_opening(save, master, seed, randomize_turns=False):
    ex = load(save, master)
    ex.rng = NativeRandom(seed & 0xffffffff)
    if randomize_turns:
        # 开考时先定回合属性（第 1 回合属性也重抽：同分布新局），再洗牌
        _randomize_turns(ex, {1: save['vocalConfigParameter'], 2: save['danceConfigParameter'], 3: save['visualConfigParameter']})
    hand = ex.zones['hand']
    for c in hand:
        c.support_upgrades = []
        c.invalidate()
    pool = hand + ex.zones['deck']
    ex.zones['hand'] = []
    ex.rng.shuffle(pool)
    ex.zones['deck'] = pool
    ex.turn_used_supports = []
    limit = ex.setting['handLimit']
    initial, overflow = [], []
    for c in list(reversed(ex.zones['deck'])):
        if c.is_initial:
            ex.zones['deck'].remove(c)
            if len(ex.zones['hand']) < limit:
                ex.zones['hand'].append(c)
                initial.append(c)
            else:
                overflow.append(c)
    ex.zones['deck'].extend(overflow)   # SetInitialCard：超出手牌上限的初始卡接到牌堆底
    draw = max(0, ex.setting['turnStartDistribute'] - ex.status.GetStartTurnDrawDown() - len(ex.zones['hand']))
    if initial:
        ex.support_upgrade(initial)
    ex.DrawCard(draw)
    ex.remaining_actions = 1
    ex.events = []
    return ex


def turn_counts(turns, weights):
    """ExamParameterModel.CalcTurnParameterType（旧引擎 gakumas_sim.turn_order.allocate，已对原生核对）：
    按配置权重从高到低排序；前 n = turns-3 回合里 第一 = ceil(n×p0/Σ)，第二 = round(余×p1/(p1+p2))，第三 = 余下；
    末三回合固定为权重从低到高各一回合。weights = {1: Vo, 2: Da, 3: Vi}。"""
    from . import f32
    order = sorted((1, 2, 3), key=lambda a: -weights[a])
    p0, p1, p2 = (weights[a] for a in order)
    n = turns - 3
    first = f32.ceil_i(f32.div(f32.mul(f32.i2f(n), f32.i2f(p0)), f32.i2f(p0 + p1 + p2)))
    rest = n - first
    second = round(f32.div(f32.mul(f32.i2f(rest), f32.i2f(p1)), f32.i2f(p1 + p2)))
    return order, [first, second, rest - second]


def generate_turn_types(turns, weights, rng):
    order, counts = turn_counts(turns, weights)
    pool = [a for a, c in zip(order, counts) for _ in range(c)]
    prefix = []
    while pool:
        prefix.append(pool.pop(rng.integer(0, len(pool))))
    return prefix + list(reversed(order))


def _randomize_turns(ex, weights):
    ex.turn_types = generate_turn_types(ex.limit_turn, weights, ex.rng)


def play(ex, policy, max_steps=500):
    steps = 0
    while not ex.ended and steps < max_steps:
        ex.advance()
        if ex.ended:
            break
        acts = ex.legal_actions()
        ex.step(policy(ex, acts))
        steps += 1
    return ex.score


def random_policy(rng):
    def pol(ex, acts):
        return acts[rng.integer(0, len(acts))]
    return pol


def greedy_play_first(ex, acts):
    """有可打的卡就打第一张，否则结束回合（不喝饮料）。"""
    for a in acts:
        if a[0] == 'play':
            return a
    return ('end',)
