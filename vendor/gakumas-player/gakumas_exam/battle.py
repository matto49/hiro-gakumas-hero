"""对局接口（沿用已删除的旧引擎 Battle 的字段与方法）：规则全部由 gakumas_exam 执行，对外暴露 digital-hiro hiro.sim / produce 用到的字段。

    from gakumas_exam.battle import FidelityBattle
    env = FidelityBattle.from_save(raw_save, master, seed, turn_attributes=['Vo', 'Da', ...], draw=3)
    env.legal_actions()          # [{'kind': 'play', 'id': guid}, {'kind': 'drink', 'id': '0'}, {'kind': 'end_turn'}]
    env._apply(action)           # 执行一步；结束回合时连同下回合开始的结算一起做完（与旧引擎一致）

对应关系（旧字段 ← 新引擎）：hp ← stamina，max_hp ← max_stamina，shield ← block（元気），score，motivation ← やる気，
impression ← 好印象，focus ← 集中，actions ← remaining_actions，play_count ← exam_card_play_count，ended。
实例 id 是卡的 guid；zones 是可写视图（读出 guid 列表，赋值 / append / 下标改 / shuffle 都立即写回新引擎的牌区）。
turns 是从当前回合起的剩余回合表（draw / actions / bonus_permil / lesson），可以改写未来回合属性（确定化）：每次推进前同步回新引擎。
存档里的回合属性表含未来回合的真实值（游戏里看不到），所以构造时必须由调用方给出剩余回合属性（含本回合）。
definition() 返回旧引擎格式的卡牌定义，只供启发式估价；旧适配器不支持的卡退化成只含名称 / 类别 / 费用的定义。考试中的成长不反映在定义里。
"""
import copy
import pickle

from .errors import IllegalAction, Unsupported
from .examconfig import ATTRIBUTE_LESSON

from .rng import NativeRandom
from .save import load

ATTR_TYPE = {'Vo': 1, 'Da': 2, 'Vi': 3}


_WARNED = []


def _native(env):
    """默认换成 NativeBattle（规则推进在 Rust 的 gakumas_exam_rs 里，结果与 Python 版逐步一致）。
    GAKUMAS_EXAM_NATIVE=0 强制用 Python 版；没装 gakumas_exam_rs 时也用 Python 版（同一套规则，慢约 8 倍），日志提示一次。"""
    import os
    if os.environ.get('GAKUMAS_EXAM_NATIVE', '1') == '0':
        return env
    from . import native
    if not native.available():
        if not _WARNED:
            import logging
            logging.getLogger(__name__).warning('没装 gakumas_exam_rs，考试引擎用 Python 版（结果相同，慢约 8 倍）；'
                                                '装法：在 gakumas-player 目录 maturin develop --release -m gakumas_exam_rs/Cargo.toml（开源仓库的 scripts/setup.sh 会做）')
            _WARNED.append(True)
        return env
    from .native_battle import maybe_native
    return maybe_native(env)
TYPE_ATTR = {v: k for k, v in ATTR_TYPE.items()}
LESSON_TYPE = {'vocal': 1, 'dance': 2, 'visual': 3}
ZONES = ('deck', 'hand', 'grave', 'lost', 'hold')
LOGS = ('events', 'play_log', 'trace')


class _Library:
    """旧格式定义的缓存（按主数据提交共享，所有对局与克隆共用）。"""
    _by_commit = {}

    def __init__(self, master):
        from .carddefs import MasterAdapter
        self.master = master
        self.adapter = MasterAdapter(master.catalog)
        self.cards, self.drinks, self.keys = {}, {}, {}

    def __reduce__(self):
        return (_library_for, (self.master.commit,))

    @classmethod
    def get(cls, master):
        lib = cls._by_commit.get(master.commit)
        if lib is None:
            lib = cls._by_commit[master.commit] = cls(master)
        return lib

    def card_key(self, card):
        k = (card.card_id, card.upgrade, tuple(card.customize_counts or ()))
        key = self.keys.get(k)
        if key is None:
            key = f'{card.card_id}@{card.upgrade}' + (':c' + ''.join(map(str, k[2])) if any(k[2]) else '')
            self.keys[k] = key
            if key not in self.cards:
                self.cards[key] = self._card_def(card)
        return key

    def _card_def(self, card):
        row = self.master.card(card.card_id, card.upgrade)
        ids = row.get('produceCardCustomizeIds') or []
        custom = [dict(id=i, count=n) for i, n in zip(ids, card.customize_counts or ()) if n]
        try:
            return self.adapter.card(card.card_id, card.upgrade, custom or None)
        except Exception:  # noqa: BLE001  旧适配器不支持的卡：只给估价要用的外形
            cat = {1: 'active', 2: 'mental', 3: 'trouble'}.get(row.get('category'), 'other')
            return dict(name=row['name'], role='trouble' if cat == 'trouble' else 'bridge', once=row.get('playMovePositionType') == 2,
                        initial=row.get('isInitial', False), order=0, category=cat, rarity=row.get('rarity'), search_tag=row.get('searchTag'),
                        trouble=cat == 'trouble', end_turn_lost=row.get('isEndTurnLost', False),
                        cost=dict(kind='stamina', value=row.get('stamina', 0)), requires=[], effects=[],
                        provenance=dict(kind='fidelity-fallback', card_id=card.card_id, upgrade=card.upgrade, customization={}))

    def drink(self, drink_id):
        d = self.drinks.get(drink_id)
        if d is None:
            try:
                d = self.adapter.drink(drink_id)
            except Exception:  # noqa: BLE001
                row = self.master.drink(drink_id)
                d = dict(name=row['name'], effects=[], provenance=dict(kind='fidelity-fallback', drink_id=drink_id))
            self.drinks[drink_id] = d
        return d


def _library_for(commit):
    from .master import shared
    return _Library.get(shared(commit))


class _ZoneList(list):
    """一个牌区的 guid 列表；任何修改都写回新引擎。"""
    __slots__ = ('_owner', '_zone')

    def __init__(self, owner, zone, items):
        super().__init__(items)
        self._owner, self._zone = owner, zone

    def _sync(self):
        self._owner._write_zone(self._zone, list(self))

    def __reduce__(self):   # 序列化 / 深拷贝成普通列表
        return (list, (list(self),))


def _mutator(name):
    base = getattr(list, name)

    def f(self, *a, **k):
        r = base(self, *a, **k)
        self._sync()
        return r if name not in ('__iadd__', '__imul__') else self
    f.__name__ = name
    return f


for _n in ('__setitem__', '__delitem__', 'append', 'extend', 'insert', 'pop', 'remove', 'sort', 'reverse', 'clear', '__iadd__', '__imul__'):
    setattr(_ZoneList, _n, _mutator(_n))


class _ZonesView(dict):
    def __init__(self, owner):
        self._owner = owner
        super().__init__({z: _ZoneList(owner, z, [c.guid for c in owner.ex.zones[z]]) for z in ZONES})
        dict.__setitem__(self, 'playing', [owner.ex.playing.guid] if owner.ex.playing is not None else [])

    def __setitem__(self, zone, items):
        self._owner._write_zone(zone, list(items))
        dict.__setitem__(self, zone, _ZoneList(self._owner, zone, list(items)))


def _fields(obj):
    """状态对象的运行时字段（__dict__ 或 __slots__），跳过不变的触发 / 效果定义。"""
    names = list(getattr(obj, '__dict__', {}).keys())
    for cls in type(obj).__mro__:
        names += [n for n in getattr(cls, '__slots__', ()) if n not in names]
    return tuple((n, repr(getattr(obj, n, None))) for n in names if n not in ('trigger', 'effects', 'master', '__dict__', '__weakref__'))


class FidelityBattle:
    fidelity = True

    def __init__(self, ex, turns, turn_attributes, *, turn_offset=0, draw=3):
        self.ex = ex
        self.lib = _Library.get(ex.master)
        self.turns = turns
        self.turn_attributes = list(turn_attributes)
        self.turn_offset = turn_offset
        self.base_turn = ex.current_turn
        self.extra0 = ex.extra_turn
        self.draw = draw
        self.known_deck_prefix = []
        self.card_mutation_events = []
        self.events = []
        self.ticks, self.hooks = [], []   # 旧引擎的附魔 / 道具钩子结构：新引擎不用，启发式拿到空表（相关判断退化为「没有」）
        self.seed = None

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_save(cls, raw, master, seed, *, turn_attributes, draw=3):
        """实机存档（出牌阶段）→ 对局。turn_attributes：本回合起的剩余回合属性（'Vo' / 'Da' / 'Vi'，含追加回合）。
        山札顺序与对局随机数是看不到的信息：按 seed 重洗、重设。"""
        import random
        ex = load(raw, master)
        if ex.playing is not None:
            raise Unsupported('A card is still resolving; use settle_from_save')
        rng = random.Random(seed)
        rng.shuffle(ex.zones['deck'])
        ex.rng = NativeRandom(rng.randrange(1, 2 ** 32))
        env = cls(ex, cls._turn_table(ex, turn_attributes, draw), turn_attributes, turn_offset=ex.current_turn - 1, draw=draw)
        env.seed = seed
        env._sync_turns()
        return _native(env)

    @classmethod
    def settle_from_save(cls, raw, master, seed, *, turn_attributes, draw=3):
        """存档停在「卡牌结算中」（playingCard 有牌）：把这张牌算完（费用在写存档前已付，不再扣），得到结算后的对局。
        这次结算按存档的真实山札抽牌（抽到的牌结算后会显示，属于公开信息），算完再按 seed 重洗剩余山札。"""
        return cls._settle(load(raw, master), seed, turn_attributes, draw)

    @classmethod
    def _settle(cls, ex, seed, turn_attributes, draw):
        import random
        card = ex.playing
        if card is None:
            raise Unsupported('No card is resolving')
        ex.playing = None
        ex.zones['hand'].append(card)
        if ex.remaining_actions < 1:
            ex.remaining_actions = 1
        env = cls(ex, cls._turn_table(ex, turn_attributes, draw), turn_attributes, turn_offset=ex.current_turn - 1, draw=draw)
        env.seed = seed
        env._sync_turns()
        ex.consume_card_cost = lambda c, ctx: []
        try:
            ex.play(card)
        finally:
            del ex.consume_card_cost
        ex.advance()
        rng = random.Random(seed)
        rng.shuffle(ex.zones['deck'])
        ex.rng = NativeRandom(rng.randrange(1, 2 ** 32))
        return _native(env)

    @classmethod
    def from_observation(cls, obs, master, seed, *, turn_attributes, draw=3):
        """公开投影（gakumas_exam.snapshot.project，任意回合的出牌阶段）→ 对局：没有原始存档时用（离线快照、测试）。
        投影里没有的山札顺序、对局随机数按 seed 重来；turn_attributes 是本回合起的剩余回合属性（含追加回合）。
        有原始存档时优先 from_save：投影不含机关与对手，也不含状态创建计数。"""
        import random
        from .snapshot import unproject
        rng = random.Random(seed)
        s = obs['scalars']
        past = [obs.get('current_parameter_type') or 1] * (s['currentTurn'] - 1)
        raw = unproject(obs, rng, turn_types=past + [ATTR_TYPE[a] for a in turn_attributes])
        # 对局随机数另开一条流：山札为空时洗牌不耗随机数，同 seed 的确定化世界（determinize_hidden 第一下就取 randrange）会和本局撞同一个状态
        raw['random'] = random.Random(f'{seed}/exam-rng').randrange(1, 2 ** 32)
        ex = load(raw, master)
        if ex.playing is not None:   # 停在「卡牌结算中」：把这张牌算完（投影没有山札顺序，结算时抽的是按 seed 洗过的山札）
            return cls._settle(ex, seed, turn_attributes, draw)
        env = cls(ex, cls._turn_table(ex, turn_attributes, draw), turn_attributes, turn_offset=ex.current_turn - 1, draw=draw)
        env.seed = seed
        env._sync_turns()
        return _native(env)

    @classmethod
    def episode_from_observation(cls, obs, master, seed, *, turn_attributes, draw=3):
        """开考快照（gakumas_exam.snapshot.project 的投影，第 1 回合）→ 从开考前重新开一局（new_exam，不用模板）：
        牌组（强化 / 定制）、P 道具（发动次数清零，定制道具按 _reactionCount）、饮料、支援卡、体力、属性倍率、
        开考附魔（回忆等来源技能 _startEnchantOriginId）都取自快照；洗牌、发牌、回合开始全部按 seed 重来。
        turn_attributes：整场的回合属性（'Vo' / 'Da' / 'Vi'）。"""
        from .cards import ExamCard
        from .setup import new_exam
        s = obs['scalars']
        deck = [ExamCard(master, c['card_id'], c['native']['_baseUpgradeCount'], list(c.get('customize_counts') or []), guid=inst)
                for inst, c in sorted(obs['cards'].items())]
        skills = list(dict.fromkeys(st['data']['_startEnchantOriginId'] for st in obs['active_statuses']
                                    if st['type']['class'] == 'TriggerEffectStatusEffect' and st['data'].get('_startEnchantOriginId')))
        items = [dict(i, _fireCount=0) for i in obs['items']]
        ex = new_exam(None, master, seed, deck=deck, items=items, stamina=s['stamina'], max_stamina=s['maxStamina'],
                      turn_types=[ATTR_TYPE[a] for a in turn_attributes], supports=[dict(x) for x in obs['supports']],
                      bonus_permil={1: s['vocalBonusPermil'], 2: s['danceBonusPermil'], 3: s['visualBonusPermil']},
                      drinks=[d['_id'] for d in obs['drinks']], plan_type=s['planType'], exam_type=s['examType'],
                      setting_id=s['settingId'], skills=skills)
        env = cls(ex, cls._turn_table(ex, turn_attributes, draw), turn_attributes, turn_offset=0, draw=draw)
        env.seed = seed
        env._sync_turns()
        return _native(env)

    @staticmethod
    def _turn_table(ex, attrs, draw):
        bonus = ex.bonus_permil
        return [dict(draw=draw, actions=1, bonus_permil=bonus[ATTR_TYPE[a]], lesson=ATTRIBUTE_LESSON[a]) for a in attrs]

    # ---------------------------------------------------------------- 回合表同步
    def _sync_turns(self):
        """把 turns（剩余回合，含确定化改写的未来回合属性）写回新引擎的回合属性表；追加回合沿用最后一个回合的模板。"""
        ex = self.ex
        total = ex.limit_turn + ex.extra_turn
        need = total - self.base_turn + 1
        while len(self.turns) < need:
            self.turns.append(dict(self.turns[-1]))
        start = self.base_turn - 1
        tt = list(ex.turn_types)
        while len(tt) < start + len(self.turns):
            tt.append(tt[-1] if tt else 1)
        for i, t in enumerate(self.turns):
            tt[start + i] = LESSON_TYPE[t['lesson']]
        ex.turn_types = tt

    # ---------------------------------------------------------------- 旧接口字段
    hp = property(lambda s: s.ex.stamina, lambda s, v: setattr(s.ex, 'stamina', v))
    max_hp = property(lambda s: s.ex.max_stamina)
    shield = property(lambda s: s.ex.block, lambda s, v: setattr(s.ex, 'block', v))
    score = property(lambda s: s.ex.score, lambda s, v: setattr(s.ex, 'score', v))
    actions = property(lambda s: s.ex.remaining_actions, lambda s, v: setattr(s.ex, 'remaining_actions', v))
    play_count = property(lambda s: s.ex.exam_card_play_count)
    ended = property(lambda s: s.ex.ended)
    motivation = property(lambda s: s.ex.status.GetAggressive(), lambda s, v: s.ex.status.OverrideAggressive(v))  # 写：画面读数覆盖
    impression = property(lambda s: s.ex.status.GetReview(), lambda s, v: s.ex.status.OverrideReview(v))
    focus = property(lambda s: s.ex.status.GetLessonBuff())
    rng = property(lambda s: s.ex.rng)

    @property
    def turn(self):
        return self.ex.current_turn - self.base_turn

    @property
    def bonus(self):
        return self.ex.bonus_permil.get(self.ex.current_parameter_type())

    @property
    def extra_turns_added(self):
        return self.ex.extra_turn - self.extra0

    @property
    def remaining_turns(self):
        return self.ex.remain_turn

    @property
    def zones(self):
        return _ZonesView(self)

    def _all_cards(self):
        ex = self.ex
        out = {c.guid: c for z in ZONES for c in ex.zones[z]}
        if ex.playing is not None:
            out[ex.playing.guid] = ex.playing
        return out

    def _write_zone(self, zone, guids):
        """写回一个牌区。查卡用登记表（见过的所有卡）：shuffle 等两步交换的中间状态里，某张卡会暂时不在任何牌区。"""
        if zone == 'playing':
            return
        reg = self.__dict__.setdefault('_registry', {})
        reg.update(self._all_cards())
        pool = reg
        missing = [g for g in guids if g not in pool]
        if missing:
            raise Unsupported(f'unknown card instances for zone {zone}: {missing[:3]}')
        self.ex.zones[zone] = [pool[g] for g in guids]

    @property
    def cards(self):
        return {g: self.lib.card_key(c) for g, c in self._all_cards().items()}

    @property
    def definitions(self):
        for c in self._all_cards().values():
            self.lib.card_key(c)
        return self.lib.cards

    def _card(self, instance):
        """guid → 卡对象。索引按需重建（新生成的卡第一次查不到时）；卡对象在一局里不换身份，牌区移动不影响索引。"""
        idx = self.__dict__.get('_idx')
        c = idx.get(instance) if idx is not None else None
        if c is None:
            idx = self.__dict__['_idx'] = self._all_cards()
            c = idx[instance]
        return c

    def definition(self, instance):
        return self.lib.cards[self.lib.card_key(self._card(instance))]

    @property
    def drinks(self):
        return {str(i): d for i, d in enumerate(self.ex.drinks)}

    @property
    def drink_definitions(self):
        return {d: self.lib.drink(d) for d in set(self.ex.drinks)} | self.lib.drinks

    # ---------------------------------------------------------------- 动作
    def legal_actions(self):
        ex = self.ex
        ex.advance()
        if ex.ended:
            return []
        hand = ex.zones['hand']
        out = []
        for a in ex.legal_actions():
            if a[0] == 'play':
                out.append(dict(kind='play', id=hand[a[1]].guid))
            elif a[0] == 'drink':
                out.append(dict(kind='drink', id=str(a[1])))
            else:
                out.append(dict(kind='end_turn'))
        return out

    def _apply(self, action):
        ex = self.ex
        self._sync_turns()
        ex.advance()
        kind = action['kind']
        if kind == 'play':
            idx = next(i for i, c in enumerate(ex.zones['hand']) if c.guid == action['id'])
            ex.step(('play', idx))
        elif kind == 'drink':
            ex.step(('drink', int(action['id'])))
        elif kind == 'end_turn':
            ex.step(('end',))
        else:
            raise IllegalAction('Unknown action ' + repr(action))
        self._sync_turns()
        ex.advance()

    def step(self, action, reason='external'):
        if action not in self.legal_actions():
            raise IllegalAction('Action not legal: ' + repr(action))
        self._apply(action)

    def observe(self):
        ex = self.ex
        return dict(hp=self.hp, max_hp=self.max_hp, shield=self.shield, score=self.score, focus=self.focus,
                    motivation=self.motivation, impression=self.impression, turn=self.turn, remaining_turns=self.remaining_turns,
                    actions=self.actions, bonus_permil=self.bonus, play_count=self.play_count, ended=self.ended,
                    hand=[dict(instance=c.guid, definition_id=self.lib.card_key(c), definition=self.definition(c.guid)) for c in ex.zones['hand']],
                    deck_count=len(ex.zones['deck']), grave_count=len(ex.zones['grave']),
                    drinks={k: self.lib.drink(v) for k, v in self.drinks.items()})

    def result(self):
        return dict(score=self.score, ended=self.ended)

    # ---------------------------------------------------------------- 搜索用
    def clone(self):
        """复制运行时状态；主数据、旧格式定义库、设定共享，日志清空。"""
        ex = self.ex
        memo = {id(ex.master): ex.master, id(self.lib): self.lib, id(ex.setting): ex.setting}
        for k in LOGS:
            v = getattr(ex, k, None)
            if isinstance(v, list):
                memo[id(v)] = []
        new = object.__new__(type(self))
        d = dict(self.__dict__)
        d['ex'] = copy.deepcopy(ex, memo)
        d['turns'] = [dict(t) for t in self.turns]
        d['turn_attributes'] = list(self.turn_attributes)
        d['events'], d['card_mutation_events'] = [], []
        d['known_deck_prefix'] = list(self.known_deck_prefix)
        d.pop('_registry', None)
        d.pop('_idx', None)
        new.__dict__ = d
        return new

    def __getstate__(self):
        d = dict(self.__dict__)
        d.pop('_registry', None)
        d.pop('_idx', None)
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)

    def determinize_hidden(self, rng):
        """重排看不到的信息：对局随机数、山札顺序（检索后公开的顶部若干张保留）。回合属性由调用方按配额改写 turns。"""
        ex = self.ex
        ex.rng.state = rng.randrange(1, 2 ** 32)
        known = len([g for g in self.known_deck_prefix if any(c.guid == g for c in ex.zones['deck'])])
        rest = ex.zones['deck'][known:]
        rng.shuffle(rest)
        ex.zones['deck'][known:] = rest

    def signature(self):
        """置换表签名：标量 + 牌区（山札按序、其余按集合）+ 饮料 + 全部状态（含附魔计数、各卡成长与附魔）。"""
        ex = self.ex
        zones = tuple((z, tuple(c.guid for c in ex.zones[z]) if z == 'deck' else tuple(sorted(c.guid for c in ex.zones[z]))) for z in ZONES)
        cards = tuple(sorted((g, c.upgrade, repr(getattr(c, 'grows', None)), repr(getattr(c, 'status_effect', None)))
                             for g, c in self._all_cards().items()))
        scal = (ex.stamina, ex.max_stamina, ex.block, ex.score, ex.remaining_actions, ex.current_turn, ex.extra_turn,
                ex.exam_card_play_count, ex.turn_card_play_count, ex.ended, tuple(ex.turn_types), tuple(sorted(ex.counters.items())))
        status = repr([(type(s).__name__, _fields(s)) for s in ex.status.effects])
        return hash(pickle.dumps((scal, zones, tuple(ex.drinks), status, cards, tuple(ex.turn_used_supports)), protocol=4))
