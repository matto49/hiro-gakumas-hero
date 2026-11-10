"""FidelityBattle 的原生后端版本：对外接口完全相同（digital-hiro hiro.sim 不用改），规则推进、克隆、签名都在 Rust 里做。

    env = FidelityBattle.from_save(...)      # 照旧构造（Python 参照引擎读存档、开局）
    env = NativeBattle.of(env)               # 换成原生后端；之后 legal_actions / _apply / clone / signature 都走 Rust

FidelityBattle.from_save / settle_from_save / episode_from_observation 在原生包可用、且 GAKUMAS_EXAM_NATIVE 不是 0 时
自动返回 NativeBattle（两边逐步对拍一致；预演）。
"""
import json
import os

from . import native
from .battle import ATTR_TYPE, LESSON_TYPE, ZONES, FidelityBattle, IllegalAction, _ZoneList


def wanted():
    return native.available() and os.environ.get('GAKUMAS_EXAM_NATIVE', '1') != '0'


class _CardRef:
    """lib.card_key 只读 card_id / upgrade / customize_counts。"""
    __slots__ = ('card_id', 'upgrade', 'customize_counts', 'guid')

    def __init__(self, guid, card_id, upgrade, customize_counts):
        self.guid, self.card_id, self.upgrade, self.customize_counts = guid, card_id, upgrade, customize_counts


class _Rng:
    __slots__ = ('_rx',)

    def __init__(self, rx):
        self._rx = rx

    @property
    def state(self):
        return self._rx.rng_state

    @state.setter
    def state(self, v):
        self._rx.rng_state = v


class _NativeZones(dict):
    def __init__(self, owner):
        self._owner = owner
        rx = owner.rx
        super().__init__({z: _ZoneList(owner, z, rx.zone(z)) for z in ZONES})
        p = rx.playing
        dict.__setitem__(self, 'playing', [p] if p is not None else [])

    def __setitem__(self, zone, items):
        self._owner._write_zone(zone, list(items))
        dict.__setitem__(self, zone, _ZoneList(self._owner, zone, list(items)))


class NativeBattle(FidelityBattle):
    native = True

    def __init__(self, rx, master, turns, turn_attributes, *, turn_offset=0, draw=3, base_turn=None, extra0=None):
        # 不调 FidelityBattle.__init__（它要 Python Exam）；字段与之同名
        from .battle import _Library
        self.rx = rx
        self.master = master
        self.lib = _Library.get(master)
        self.turns = turns
        self.turn_attributes = list(turn_attributes)
        self.turn_offset = turn_offset
        self.base_turn = rx.current_turn if base_turn is None else base_turn
        self.extra0 = rx.extra_turn if extra0 is None else extra0
        self.draw = draw
        self.known_deck_prefix = []
        self.card_mutation_events = []
        self.events = []
        self.ticks, self.hooks = [], []
        self.seed = None

    @classmethod
    def of(cls, env):
        """FidelityBattle（Python 引擎）→ NativeBattle。"""
        rx = native.to_native(env.ex)
        nb = cls(rx, env.ex.master, env.turns, env.turn_attributes, turn_offset=env.turn_offset, draw=env.draw,
                 base_turn=env.base_turn, extra0=env.extra0)
        nb.known_deck_prefix = list(env.known_deck_prefix)
        nb.seed = env.seed
        return nb

    @property
    def ex(self):
        raise AttributeError('NativeBattle 没有 Python Exam（局面在原生后端里）')

    # ---------------------------------------------------------------- 回合表同步
    def _sync_turns(self):
        rx = self.rx
        total = rx.limit_turn + rx.extra_turn
        need = total - self.base_turn + 1
        while len(self.turns) < need:
            self.turns.append(dict(self.turns[-1]))
        start = self.base_turn - 1
        tt = rx.turn_types
        old = list(tt)
        while len(tt) < start + len(self.turns):
            tt.append(tt[-1] if tt else 1)
        for i, t in enumerate(self.turns):
            tt[start + i] = LESSON_TYPE[t['lesson']]
        if tt != old:
            rx.turn_types = tt

    # ---------------------------------------------------------------- 旧接口字段
    hp = property(lambda s: s.rx.stamina, lambda s, v: setattr(s.rx, 'stamina', v))
    max_hp = property(lambda s: s.rx.max_stamina)
    shield = property(lambda s: s.rx.block, lambda s, v: setattr(s.rx, 'block', v))
    score = property(lambda s: s.rx.score, lambda s, v: setattr(s.rx, 'score', v))
    actions = property(lambda s: s.rx.remaining_actions, lambda s, v: setattr(s.rx, 'remaining_actions', v))
    play_count = property(lambda s: s.rx.exam_card_play_count)
    ended = property(lambda s: s.rx.ended)
    motivation = property(lambda s: s.rx.aggressive, lambda s, v: setattr(s.rx, 'aggressive', v))  # 写：画面读数覆盖
    impression = property(lambda s: s.rx.review, lambda s, v: setattr(s.rx, 'review', v))
    focus = property(lambda s: s.rx.lesson_buff)
    rng = property(lambda s: _Rng(s.rx))

    @property
    def turn(self):
        return self.rx.current_turn - self.base_turn

    @property
    def bonus(self):
        return self.rx.bonus

    @property
    def extra_turns_added(self):
        return self.rx.extra_turn - self.extra0

    @property
    def remaining_turns(self):
        return self.rx.remain_turn

    @property
    def zones(self):
        return _NativeZones(self)

    def _all_cards(self):
        out = {}
        for g, cid, up, cc in self.rx.cards_info():
            out[g] = _CardRef(g, cid, up, cc)
        return out

    def _write_zone(self, zone, guids):
        if zone == 'playing':
            return
        from .errors import Unsupported
        try:
            self.rx.set_zone(zone, guids)
        except KeyError as e:
            raise Unsupported(f'unknown card instances for zone {zone}: {e}') from None

    def _card(self, instance):
        info = self.rx.card_info(instance)
        if info is None:
            raise KeyError(instance)
        return _CardRef(instance, *info)

    @property
    def drinks(self):
        return {str(i): d for i, d in enumerate(self.rx.drinks)}

    @property
    def drink_definitions(self):
        return {d: self.lib.drink(d) for d in set(self.rx.drinks)} | self.lib.drinks

    # ---------------------------------------------------------------- 动作
    def legal_actions(self):
        rx = self.rx
        rx.advance()
        if rx.ended:
            return []
        hand = rx.zone('hand')
        out = []
        for a in rx.legal_actions():
            if a[0] == 'play':
                out.append(dict(kind='play', id=hand[a[1]]))
            elif a[0] == 'drink':
                out.append(dict(kind='drink', id=str(a[1])))
            else:
                out.append(dict(kind='end_turn'))
        return out

    def _apply(self, action):
        rx = self.rx
        self._sync_turns()
        rx.advance()
        kind = action['kind']
        if kind == 'play':
            idx = rx.zone('hand').index(action['id'])
            rx.step(('play', idx))
        elif kind == 'drink':
            rx.step(('drink', int(action['id'])))
        elif kind == 'end_turn':
            rx.step(('end',))
        else:
            raise IllegalAction('Unknown action ' + repr(action))
        self._sync_turns()
        rx.advance()

    def observe(self):
        rx = self.rx
        hand = rx.zone('hand')
        return dict(hp=self.hp, max_hp=self.max_hp, shield=self.shield, score=self.score, focus=self.focus,
                    motivation=self.motivation, impression=self.impression, turn=self.turn, remaining_turns=self.remaining_turns,
                    actions=self.actions, bonus_permil=self.bonus, play_count=self.play_count, ended=self.ended,
                    hand=[dict(instance=g, definition_id=self.lib.card_key(self._card(g)), definition=self.definition(g)) for g in hand],
                    deck_count=len(rx.zone('deck')), grave_count=len(rx.zone('grave')),
                    drinks={k: self.lib.drink(v) for k, v in self.drinks.items()})

    def result(self):
        return dict(score=self.score, ended=self.ended)

    # ---------------------------------------------------------------- 搜索用
    def clone(self):
        new = object.__new__(type(self))
        d = dict(self.__dict__)
        d['rx'] = self.rx.clone()
        d['turns'] = [dict(t) for t in self.turns]
        d['turn_attributes'] = list(self.turn_attributes)
        d['events'], d['card_mutation_events'] = [], []
        d['known_deck_prefix'] = list(self.known_deck_prefix)
        new.__dict__ = d
        return new

    def __getstate__(self):
        d = dict(self.__dict__)
        d['rx'] = self.rx.export_json()
        d.pop('lib', None)
        return d

    def __setstate__(self, d):
        from .battle import _Library
        from .master import shared
        d = dict(d)
        state = d.pop('rx')
        master = d['master']
        d['rx'] = native._rs.Exam(native.master_path(master), state)
        d['lib'] = _Library.get(master)
        self.__dict__.update(d)

    def determinize_hidden(self, rng):
        rx = self.rx
        rx.rng_state = rng.randrange(1, 2 ** 32)
        deck = rx.zone('deck')
        known = len([g for g in self.known_deck_prefix if g in deck])
        rest = deck[known:]
        rng.shuffle(rest)
        rx.set_zone('deck', deck[:known] + rest)

    def signature(self):
        return self.rx.signature()


def maybe_native(env):
    """原生后端可用时把 FidelityBattle 换成 NativeBattle。"""
    if wanted() and not isinstance(env, NativeBattle):
        return NativeBattle.of(env)
    return env
