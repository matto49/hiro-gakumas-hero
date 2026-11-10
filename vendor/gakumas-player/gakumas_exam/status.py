"""考试状态：ExamStatusEffectCollection 及各状态类（3.5.1）。

状态对象字段与存档 ExamSaveStatusEffectData 一致：_isTurnLimited / _turn / _isPassingTurnStart / _uid / _value …
合并规则（按客户端自动摘要 + 人工核对）：
- single-value：只有一个实例，数值累加（Max(0, v)）。例 やる気、集中、全力値。
- single-turn：只有一个实例，回合累加；已有实例不限回合（永久）时不再增加。例 好印象、好調、消費体力減少。
- turnkey-value：剩余回合相同的实例合并数值（谓词 _turn == turn，见 TryAddLessonParameterMultipleStatus 的 DisplayClass49_0），否则新建。
- new：每次新建。
新增前：IsBlockAddStatus(effectType)——效果目标类型为 2（低下）且有低下状態無効次数时，消耗一次并拦下。
回合末：SpendTurn（ExamStatusEffectExtensions.SpendTurn<T>）。
"""
import json
from pathlib import Path

from . import f32
from .master import E

ET = E('ProduceExamEffectType')
PH = E('ProduceExamPhaseType')
_CLASSES = json.loads((Path(__file__).resolve().parent / 'status_classes.json').read_text())['classes']
_TARGET = json.loads((Path(__file__).resolve().parent / 'effect_target_types.json').read_text())
DEBUFF_EFFECT_TYPES = {ET[k] for k, v in _TARGET.items() if v == 2 and k in ET}

DEBUFF_STATUS_CLASSES = {
    'StaminaConsumptionAddStatusEffect', 'StaminaConsumptionAddFixStatusEffect', 'BlockAddDownStatusEffect', 'BlockAddDownFixStatusEffect',
    'BlockRestrictionStatusEffect', 'StaminaRecoverRestrictionStatusEffect', 'LessonDebuffStatusEffect', 'ParameterDebuffStatusEffect',
    'SlumpStatusEffect', 'PanicStatusEffect', 'StartTurnCardDrawDownStatusEffect', 'LessonParameterMultipleDownStatusEffect',
    'LessonChangeSpecifyMoreThanStatusEffect', 'StanceLockStatusEffect', 'StanceLockConcentrationEffect', 'StanceLockFullPowerEffect',
    'StanceLockPreservationEffect', 'BuffConsumptionAddStatusEffect', 'SearchPlayCardLimitStatusEffect'}  # 待核：GetEffectType 静态表
INT_CLASSES = {c for c, d in _CLASSES.items() if d['base'] in ('IntParameterBuffStatusEffect',)}
TYPE_OF = {c: d['type'] for c, d in _CLASSES.items() if d['type'] is not None}


class Status:
    """ExamStatusEffectBase。value 只对 IntParameterBuffStatusEffect 子类有意义。extra 存类特有字段（_count、_searchId …）。"""
    __slots__ = ('cls', 'uid', 'turn', 'limited', 'passing', 'value', 'extra')

    def __init__(self, cls, turn=-1, value=0, limited=None, passing=False, uid=0, extra=None):
        self.cls = cls
        self.turn = turn
        self.limited = (turn >= 0) if limited is None else limited
        self.passing = passing
        self.uid = uid
        self.value = value
        self.extra = extra or {}

    @property
    def type(self):
        return TYPE_OF.get(self.cls)

    def SpendTurn(self):
        if self.limited:
            self.turn -= 1

    def ReduceTurn(self, n):
        self.turn = max(0, self.turn - n)

    def copy(self):
        s = Status(self.cls, self.turn, self.value, self.limited, self.passing, self.uid, dict(self.extra))
        return s

    def __repr__(self):
        v = f' v={self.value}' if self.cls in INT_CLASSES else ''
        return f'<{self.cls} uid={self.uid} turn={self.turn}{v}{" P" if self.passing else ""}>'


def _slot_copy(obj, memo, shared=()):
    """按 __slots__ 复制：shared 里的字段（主数据行，不可变）共享，其余深拷贝。克隆提速用。"""
    import copy as _copy
    new = object.__new__(type(obj))
    memo[id(obj)] = new
    for cls in type(obj).__mro__:
        for n in getattr(cls, '__slots__', ()):
            if hasattr(obj, n):
                v = getattr(obj, n)
                object.__setattr__(new, n, v if n in shared or type(v) in (int, str, bool, float, type(None)) else _copy.deepcopy(v, memo))
    return new


Status.__deepcopy__ = lambda self, memo: _slot_copy(self, memo)


class TriggerEffect(Status):
    """TriggerEffectStatusEffect：附魔。trigger 为主数据 ProduceExamTrigger 行（已转整数枚举），effects 为 ProduceExamEffect 行列表。"""
    __slots__ = ('trigger', 'effects', 'turn_count', 'phase_count', 'limit_count', 'limit_in_turn', 'limit_in_turn_remain',
                 'enchant_id', 'origin_type', 'source', 'override_type', 'from_effect_ids', 'from_enchant', 'encore')

    def __init__(self, trigger, effects, origin_type, turn=-1, limit_count=-1, limit_in_turn=-1, enchant_id='', source=None,
                 override_type=31, passing=False, uid=0):
        super().__init__('TriggerEffectStatusEffect', turn, 0, None, passing, uid)
        self.trigger = trigger
        self.effects = effects
        self.turn_count = 0
        self.phase_count = {}
        self.limit_count = limit_count
        self.limit_in_turn = limit_in_turn
        self.limit_in_turn_remain = limit_in_turn
        self.enchant_id = enchant_id
        self.origin_type = origin_type
        self.source = source or {}
        self.override_type = override_type
        self.from_effect_ids = []
        self.from_enchant = False
        self.encore = False

    def __deepcopy__(self, memo):
        return _slot_copy(self, memo, ('trigger', 'effects'))

    def IncrementTurnCount(self):
        self.turn_count += 1
        self.limit_in_turn_remain = self.limit_in_turn

    @property
    def IsCountLimited(self):
        return self.limit_count >= 0

    def copy(self):
        t = TriggerEffect(self.trigger, self.effects, self.origin_type, self.turn, self.limit_count, self.limit_in_turn,
                          self.enchant_id, dict(self.source), self.override_type, self.passing, self.uid)
        t.limited = self.limited
        t.turn_count = self.turn_count
        t.phase_count = dict(self.phase_count)
        t.limit_in_turn_remain = self.limit_in_turn_remain
        t.from_effect_ids = list(self.from_effect_ids)
        t.from_enchant = self.from_enchant
        t.encore = self.encore
        return t

    def __repr__(self):
        return (f'<Enchant uid={self.uid} {self.enchant_id or self.trigger.get("id")} turn={self.turn} '
                f'left={self.limit_count} tc={self.turn_count}{" P" if self.passing else ""}>')


# 每个 TryAdd 的规则：(状态类, 合并方式, IsBlockAddStatus 用的效果类型, 增加后记的阶段计数, 前置修正 (AdditiveFix 类, AdditiveMultiple 类))
S = 'single-value'; T = 'single-turn'; K = 'turnkey-value'; KT = 'turnkey-turn'; N = 'new'
RULES = {
    'TryAddAggressiveStatus': ('AggressiveStatusEffect', S, ET['ExamCardPlayAggressive'], PH['ExamAggressiveUpInterval'],
                               ('AggressiveAdditiveFixStatusEffect', 'AggressiveAdditiveStatusEffect')),
    'TryAddLessonBuffStatus': ('LessonBuffStatusEffect', S, ET['ExamLessonBuff'], PH['ExamLessonBuffUpInterval'],
                               ('LessonBuffAdditiveFixStatusEffect', 'LessonBuffAdditiveStatusEffect')),
    'TryAddFullPowerPoint': ('FullPowerPointStatusEffect', S, ET['ExamFullPowerPoint'], PH['ExamFullPowerPointUpInterval'],
                             ('FullPowerPointAdditiveFixStatusEffect', 'FullPowerPointAdditiveStatusEffect')),
    'TryAddReviewStatus': ('ReviewStatusEffect', T, ET['ExamReview'], PH['ExamReviewUpInterval'],
                           ('ReviewAdditiveFixStatusEffect', 'ReviewAdditiveStatusEffect')),
    'TryAddParameterBuffStatus': ('ParameterBuffStatusEffect', T, ET['ExamParameterBuff'], PH['ExamParameterBuffUpInterval'],
                                  ('ParameterBuffAdditiveFixStatusEffect', 'ParameterBuffAdditiveStatusEffect')),
    'TryAddAntiDebuffStatus': ('AntiDebuffStatusEffect', S, None, None, None),
    'TryAddLessonDebuffStatus': ('LessonDebuffStatusEffect', 'single-value-turn', ET['ExamGimmickLessonDebuff'], None, None),
    'TryAddStartTurnCardDrawDownStatus': ('StartTurnCardDrawDownStatusEffect', 'new-value', ET['ExamGimmickStartTurnCardDrawDown'], None, None),
    'TryAddStaminaRecoverAddStatus': ('StaminaRecoverAddStatusEffect', 'new-value', None, None, None),
    'TryAddStaminaReduceChangeStatus': ('StaminaReduceChangeStatusEffect', 'new-value', None, None, None),
    'TryAddUpliftingStatus': ('UpliftingStatusEffect', S, None, None, None),
    'TryAddBlockAddDownFixStatus': ('BlockAddDownFixStatusEffect', S, ET.get('ExamBlockAddDownFix', 107), None, None),
    'TryAddBlockAddDownRestrictionStatus': ('BlockAddDownRestrictionStatusEffect', T, 74, None, None),
    'TryAddBlockAddDownStatus': ('BlockAddDownStatusEffect', T, ET['ExamBlockAddDown'], None, None),
    'TryAddBlockRestrictionStatus': ('BlockRestrictionStatusEffect', T, ET['ExamBlockRestriction'], None, None),
    'TryAddBuffConsumptionAddStatus': ('BuffConsumptionAddStatusEffect', T, ET['ExamBuffConsumptionAdd'], None, None),
    'TryAddBuffConsumptionDownStatus': ('BuffConsumptionDownStatusEffect', T, 198, None, None),
    'TryAddLessonParameterMultipleDependReviewOrAggressiveStatus': ('LessonParameterMultipleDependReviewOrAggressiveStatusEffect', T, 181, None, None),
    'TryAddPanicStatus': ('PanicStatusEffect', T, ET['ExamPanic'], None, None),
    'TryAddParameterBuffMultiplePerTurnStatus': ('ParameterBuffMultiplePerTurnStatusEffect', T, ET['ExamParameterBuffMultiplePerTurn'], None, None),
    'TryAddParameterBuffTurnEndReduceLockStatus': ('ParameterBuffTurnEndReduceLockStatusEffect', T, 197, None, None),
    'TryAddParameterDebuffStatus': ('ParameterDebuffStatusEffect', T, ET['ExamGimmickParameterDebuff'], None, None),
    'TryAddReviewTurnEndReduceLockStatus': ('ReviewTurnEndReduceLockStatusEffect', T, 196, None, None),
    'TryAddSlumpStatus': ('SlumpStatusEffect', T, ET['ExamGimmickSlump'], None, None),
    'TryAddStaminaConsumptionAddDownStatus': ('StaminaConsumptionAddDownStatusEffect', T, 85, None, None),
    'TryAddStaminaConsumptionAddStatus': ('StaminaConsumptionAddStatusEffect', T, ET['ExamStaminaConsumptionAdd'], None, None),
    'TryAddStaminaConsumptionDownAddStatus': ('StaminaConsumptionDownAddStatusEffect', T, 89, None, None),
    'TryAddStaminaConsumptionDownStatus': ('StaminaConsumptionDownStatusEffect', T, ET['ExamStaminaConsumptionDown'], None, None),
    'TryAddStaminaRecoverRestrictionStatus': ('StaminaRecoverRestrictionStatusEffect', T, ET['ExamStaminaRecoverRestriction'], None, None),
    'TryAddStanceLockConcentrationEffect': ('StanceLockConcentrationEffect', T, ET['ExamStanceLockConcentration'], None, None),
    'TryAddStanceLockFullPowerEffect': ('StanceLockFullPowerEffect', T, ET['ExamStanceLockFullPower'], None, None),
    'TryAddStanceLockPreservationEffect': ('StanceLockPreservationEffect', T, ET['ExamStanceLockPreservation'], None, None),
    'TryAddStanceLockStatus': ('StanceLockStatusEffect', T, ET['StanceLock'], None, None),
    'TryAddGetCardUpgradeStatus': ('GetCardUpgradeStatusEffect', N, 90, None, None),
    'TryAddHandHoldStatus': ('HandHoldStatusEffect', N, 82, None, None),
    # 按剩余回合合并数值
    'TryAddAggressiveAdditiveFixStatus': ('AggressiveAdditiveFixStatusEffect', K, None, None, None),
    'TryAddAggressiveAdditiveStatus': ('AggressiveAdditiveStatusEffect', K, None, None, None),
    'TryAddConcentrationLessonMultipleAdditiveStatus': ('ConcentrationLessonMultipleAdditiveStatusEffect', K, None, None, None),
    'TryAddEnthusiasticAdditiveStatus': ('EnthusiasticAdditiveStatusEffect', K, None, None, None),
    'TryAddEnthusiasticMultipleStatus': ('EnthusiasticMultipleStatusEffect', K, None, None, None),
    'TryAddEnthusiasticStatus': ('EnthusiasticStatusEffect', K, None, None, None),
    'TryAddFullPowerLessonMultipleAdditiveStatus': ('FullPowerLessonMultipleAdditiveStatusEffect', K, None, None, None),
    'TryAddFullPowerPointAdditiveFixStatus': ('FullPowerPointAdditiveFixStatusEffect', K, None, None, None),
    'TryAddFullPowerPointAdditiveStatus': ('FullPowerPointAdditiveStatusEffect', K, None, None, None),
    'TryAddGrowEffectLessonAddAdditiveStatus': ('GrowEffectLessonAddAdditiveStatusEffect', K, None, None, None),
    'TryAddLessonBuffAdditiveFixStatus': ('LessonBuffAdditiveFixStatusEffect', K, None, None, None),
    'TryAddLessonBuffAdditiveStatus': ('LessonBuffAdditiveStatusEffect', K, None, None, None),
    'TryAddLessonBuffMultipleStatus': ('LessonBuffMultipleStatusEffect', K, None, None, None),
    'TryAddLessonParameterMultipleDownStatus': ('LessonParameterMultipleDownStatusEffect', K, ET['ExamLessonValueMultipleDown'], None, None),
    'TryAddLessonParameterMultipleStatus': ('LessonParameterMultipleStatusEffect', K, ET['ExamLessonValueMultiple'], None, None),
    'TryAddParameterBuffAdditiveFixStatus': ('ParameterBuffAdditiveFixStatusEffect', K, None, None, None),
    'TryAddParameterBuffAdditiveStatus': ('ParameterBuffAdditiveStatusEffect', K, None, None, None),
    'TryAddReviewAdditiveFixStatus': ('ReviewAdditiveFixStatusEffect', K, None, None, None),
    'TryAddReviewAdditiveStatus': ('ReviewAdditiveStatusEffect', K, None, None, None),
    'TryAddReviewCountAddStatus': ('ReviewCountAddStatusEffect', K, None, None, None),
    'TryAddReviewMultipleStatus': ('ReviewMultipleStatusEffect', K, None, None, None),
    'TryAddStaminaConsumptionAddFixStatus': ('StaminaConsumptionAddFixStatusEffect', K, ET['ExamStaminaConsumptionAddFix'], None, None),
    'TryAddStaminaConsumptionDownFixStatus': ('StaminaConsumptionDownFixStatusEffect', K, None, None, None),
    'TryAddLessonChangeSpecifyLessThanStatus': ('LessonChangeSpecifyLessThanStatusEffect', KT, None, None, None),
    'TryAddLessonChangeSpecifyMoreThanStatus': ('LessonChangeSpecifyMoreThanStatusEffect', KT, ET['ExamLessonChangeSpecifyMoreThan'], None, None),
}


class StatusCollection:
    """ExamStatusEffectCollection。effects 的顺序即 _effectList（创建顺序）。"""

    def __init__(self, exam=None):
        self.exam = exam
        self.effects = []
        self.removed = []
        self.create_count = 0
        self.idol_type = 0  # ExamIdolStatusType：1 強気(Concentration) 2 温存 3 全力 4 OverPreservation
        self.idol_step = 0

    # ---- 基础
    def AddStatus(self, st):
        """uid = ++_effectCreateCount，追加到列表末尾。"""
        self.create_count += 1
        st.uid = self.create_count
        self.effects.append(st)
        return st

    def RemoveStatusEffect(self, st):
        if st in self.effects:
            self.effects.remove(st)
            self.removed.append(st)

    def of(self, cls):
        return [s for s in self.effects if s.cls == cls]

    def first(self, cls):
        for s in self.effects:
            if s.cls == cls:
                return s
        return None

    def last(self, cls, pred=None):
        for s in reversed(self.effects):
            if s.cls == cls and (pred is None or pred(s)):
                return s
        return None

    # ---- 低下状態無効
    def IsBlockAddStatus(self, effect_type, ctx=None):
        if effect_type is None or effect_type not in DEBUFF_EFFECT_TYPES:
            return False
        a = self.first('AntiDebuffStatusEffect')
        if a is None or a.value == 0:
            return False
        self.UseAntiDebuffCount()
        return True

    def UseAntiDebuffCount(self):
        a = self.first('AntiDebuffStatusEffect')
        if a is None:
            return
        a.value -= 1
        if a.value <= 0:
            self.RemoveStatusEffect(a)

    def GetAntiDebuffCount(self, is_use=True):
        a = self.first('AntiDebuffStatusEffect')
        return a.value if a else 0

    # ---- 通用 TryAdd
    def _modify(self, value, mods, is_fix):
        """ceil(Multiple × (value + AdditiveFix))（TryAddReviewStatus 等）；isFix=true 时不修正。"""
        if is_fix or not mods:
            return value
        fix_cls, mul_cls = mods
        fix = sum(s.value for s in self.of(fix_cls))
        mul = self._multiple(mul_cls)
        return f32.ceil_i(f32.mul(mul, f32.i2f(value + fix)))

    def _multiple(self, cls):
        """Get*AdditiveMultiple：1 + Σ value/1000（float32，按实例顺序累加；同 GetLessonParameterMultiple）。"""
        r = 1.0
        for s in self.of(cls):
            r = f32.add(r, f32.permil(s.value))
        return r

    def _try_add(self, rule, value=0, turn=-1, ctx=None, is_fix=False):
        cls, kind, block_type, phase, mods = RULES[rule]
        if self.IsBlockAddStatus(block_type, ctx):
            return False
        if kind == S:
            v = self._modify(value, mods, is_fix)
            st = self.last(cls) if cls != 'AntiDebuffStatusEffect' else self.first(cls)
            if st is not None:
                st.value = max(0, st.value + v)
            else:
                self.AddStatus(Status(cls, -1, v, False))
        elif kind == T:
            t = self._modify(turn, mods, is_fix)
            st = self.first(cls)
            if st is not None:
                if st.limited:
                    st.turn += t
            else:
                self.AddStatus(Status(cls, t, 0, t >= 0))
        elif kind == K:
            st = self.last(cls, lambda s: s.turn == turn)
            if st is not None:
                st.value = max(0, st.value + value)
            else:
                # TryAddEnthusiasticStatus：新建的熱意 _isPassingTurnStart = true（获得的回合末即衰减）；其余 TryAdd 都不设
                self.AddStatus(Status(cls, turn, value, turn >= 0, cls == 'EnthusiasticStatusEffect'))
        elif kind == KT:
            st = self.last(cls, lambda s: s.turn == turn)
            if st is None:
                self.AddStatus(Status(cls, turn, value, turn >= 0))
        elif kind == N or kind == 'new-value':
            self.AddStatus(Status(cls, turn, value, turn >= 0))
        elif kind == 'single-value-turn':
            st = self.last(cls)
            if st is not None:
                st.value = max(0, st.value + value)
            else:
                self.AddStatus(Status(cls, turn, value, turn >= 0))
        if phase is not None and ctx is not None and ctx.IsEnchantTriggerActive():
            ctx.exam.IncrementEnchantPhaseCount([phase], ctx)
        return True

    def __getattr__(self, name):
        if name in RULES:
            cls, kind, _, _, _ = RULES[name]
            if kind in (S,):
                return lambda value, ctx=None, isFix=False: self._try_add(name, value=value, ctx=ctx, is_fix=isFix)
            if kind in (T, N):
                return lambda turn, ctx=None, isFix=False: self._try_add(name, turn=turn, ctx=ctx, is_fix=isFix)
            return lambda value, turn=-1, ctx=None: self._try_add(name, value=value, turn=turn, ctx=ctx)
        raise AttributeError(name)

    def TryAddEnthusiasticTurnAdditiveStatus(self, value, ctx=None):
        """熱意の効果ターン延长（待核： 只写 _turn）：给现有熱意实例回合 +value。"""
        for s in self.of('EnthusiasticStatusEffect'):
            if s.limited:
                s.turn += value
        return True

    def TryAddSearchPlayCardLimitStatus(self, search_id, turn, ctx=None):
        """打出限制（GimmickPlayCardLimit）：检索到的卡 turn 回合内不能打出（待核合并方式）。"""
        if self.IsBlockAddStatus(ET['ExamGimmickPlayCardLimit'], ctx):
            return False
        self.AddStatus(Status('SearchPlayCardLimitStatusEffect', turn, 0, turn >= 0, False, 0, {'_searchId': search_id}))
        return True

    # ---- 消耗（Consume*：直接减，下限 0；减到 0 移除。ExamStatusEffectCollection.ConsumeValue / ConsumeTurn）
    def _consume_value(self, cls, n):
        s = self.first(cls)
        if s is None:
            return
        s.value = max(0, s.value - n)
        if s.value <= 0:
            self.RemoveStatusEffect(s)

    def _consume_turn(self, cls, n):
        s = self.first(cls)
        if s is None:
            return
        s.turn = max(0, s.turn - n)
        if s.turn <= 0:
            self.RemoveStatusEffect(s)

    def ConsumeAggressive(self, n, is_use=True):
        self._consume_value('AggressiveStatusEffect', n)

    def ConsumeLessonBuff(self, n, is_use=True):
        self._consume_value('LessonBuffStatusEffect', n)

    def ConsumeReview(self, n, is_use=True, ctx=None):
        before = self.GetReview()
        self._consume_turn('ReviewStatusEffect', n)
        if self.exam is not None:
            self.exam.counters['reviewConsumptionSumCount'] = self.exam.counters.get('reviewConsumptionSumCount', 0) + before - self.GetReview()

    def ConsumeParameterBuff(self, n, is_use=True):
        self._consume_turn('ParameterBuffStatusEffect', n)

    def ConsumeParameterBuffMultiplePerTurn(self, n, is_use=True):
        self._consume_turn('ParameterBuffMultiplePerTurnStatusEffect', n)

    def ConsumeFullPowerPoint(self, n, is_use=True):
        self._consume_value('FullPowerPointStatusEffect', n)

    def TryAddPlayableValueAddStatus(self, count, ctx=None):
        """追加行动：PlayableValueAddStatusEffect。TryAddPlayableValueAddStatus：已有则 _value += 次数；
        没有则新建，_turn = 1（回合限定；实机存档 _isTurnLimited True、_turn 1）。出牌途中新建的没用完会带到下一回合，
        下一回合末衰减移除（SpendTurn 的 IsPassingTurnStart 规则）。2026-11-07 与 GKMS 对拍发现：之前建成不限回合，会一直挂着。"""
        st = self.first('PlayableValueAddStatusEffect')
        if st is not None:
            st.value += count
        else:
            self.AddStatus(Status('PlayableValueAddStatusEffect', 1, count, True))
        return True

    def TryAddTriggerEffectStatus(self, trigger, effects, origin_type, turn=-1, limit_count=-1, limit_in_turn=-1,
                                  enchant_id='', source=None, ctx=None, override_type=31):
        t = TriggerEffect(trigger, effects, origin_type, turn, limit_count, limit_in_turn, enchant_id, source, override_type)
        if ctx is not None and getattr(ctx, 'card', None) is not None and 'item' not in t.source:
            # TryAddTriggerEffectStatus：记下挂附魔时正在打出的卡（_triggerCard）；
            # アンコール等「来源卡」检索（Target + isSelf）找的就是它
            t.source['card'] = ctx.card.card_id
            t.source['card_guid'] = ctx.card.guid
        self.AddStatus(t)
        return t.uid

    # ---- 覆盖式写回（LessonDepend 消耗用）
    def SetReview(self, n):
        s = self.first('ReviewStatusEffect')
        if s is None:
            return
        s.turn = n
        if n <= 0:
            self.RemoveStatusEffect(s)

    def SetAggressive(self, n):
        s = self.first('AggressiveStatusEffect')
        if s is not None:
            s.value = n

    # ---- 画面读数覆盖（续推 / 画面重建用，不触发任何时点）：有就改，没有按 TryAdd 的形状新建，n <= 0 移除
    def OverrideAggressive(self, n):
        s = self.first('AggressiveStatusEffect')
        if s is not None and n <= 0:
            self.RemoveStatusEffect(s)
        elif s is not None:
            s.value = n
        elif n > 0:
            self.AddStatus(Status('AggressiveStatusEffect', -1, n, False))

    def OverrideReview(self, n):
        s = self.first('ReviewStatusEffect')
        if s is not None and n <= 0:
            self.RemoveStatusEffect(s)
        elif s is not None:
            s.turn = n
        elif n > 0:
            self.AddStatus(Status('ReviewStatusEffect', n, 0, True))

    def SetLessonBuff(self, n):
        s = self.first('LessonBuffStatusEffect')
        if s is not None:
            s.value = n

    def SetParameterBuff(self, n):
        s = self.first('ParameterBuffStatusEffect')
        if s is None:
            return
        s.turn = n
        if n <= 0:
            self.RemoveStatusEffect(s)

    # ---- 取值（isUse 只影响界面高亮，忽略）
    def _val(self, cls):
        s = self.first(cls)
        return s.value if s else 0

    def _turn(self, cls):
        s = self.first(cls)
        return s.turn if s else 0

    def GetAggressive(self, is_use=True):
        return self._val('AggressiveStatusEffect')

    def GetLessonBuff(self, is_use=True):
        return self._val('LessonBuffStatusEffect')

    def GetLessonDebuff(self, is_use=True):
        return sum(s.value for s in self.of('LessonDebuffStatusEffect'))

    def GetFullPowerPoint(self, is_use=True):
        return self._val('FullPowerPointStatusEffect')

    def GetReview(self, is_use=True):
        return self._turn('ReviewStatusEffect')

    def GetParameterBuffTurn(self, is_use=True):
        return self._turn('ParameterBuffStatusEffect')

    def IsParameterBuff(self, is_use=True):
        return self.first('ParameterBuffStatusEffect') is not None

    def IsParameterBuffMultiplePerTurn(self, is_use=True):
        return self.first('ParameterBuffMultiplePerTurnStatusEffect') is not None

    def IsParameterDebuff(self, is_use=True):
        return self.first('ParameterDebuffStatusEffect') is not None

    def IsSlump(self, is_use=True):
        return self.first('SlumpStatusEffect') is not None

    def IsBlockRestriction(self, is_use=True):
        return self.first('BlockRestrictionStatusEffect') is not None

    def IsBlockAddDown(self, is_use=True):
        return self.first('BlockAddDownStatusEffect') is not None

    def IsBlockAddDownRestriction(self, is_use=True):
        return self.first('BlockAddDownRestrictionStatusEffect') is not None

    def GetBlockAddDownFix(self, is_use=True):
        return sum(s.value for s in self.of('BlockAddDownFixStatusEffect'))

    def GetEnthusiasticBuff(self, is_use=True):
        """熱意：EnthusiasticStatusEffect 各实例数值和（待核：是否乘 EnthusiasticMultiple / 加 Additive）。"""
        return sum(s.value for s in self.of('EnthusiasticStatusEffect'))

    def GetLessonParameterMultiple(self, is_use=True):
        return self._multiple('LessonParameterMultipleStatusEffect')

    def GetLessonParameterMultipleDown(self, is_use=True):
        """待核：形状按 GetLessonParameterMultiple 推测为 Σ value/1000（不含基准 1）。"""
        r = 0.0
        for s in self.of('LessonParameterMultipleDownStatusEffect'):
            r = f32.add(r, f32.permil(s.value))
        return r

    def GetLessonBuffMultiple(self, is_use=True):
        return self._multiple('LessonBuffMultipleStatusEffect')

    def IsLessonParameterMultipleDependReviewOrAggressive(self, is_use=True):
        return self.first('LessonParameterMultipleDependReviewOrAggressiveStatusEffect') is not None

    def GetIdolStatusType(self):
        return self.idol_type

    def GetIdolStatusStep(self):
        return self.idol_step

    def GetFullPowerLessonMultipleAdditiveStatusEffectMultiple(self, is_use=True):
        r = 0.0
        for s in self.of('FullPowerLessonMultipleAdditiveStatusEffect'):
            r = f32.add(r, f32.permil(s.value))
        return r

    def GetConcentrationLessonMultipleAdditiveStatusEffectMultiple(self, is_use=True):
        r = 0.0
        for s in self.of('ConcentrationLessonMultipleAdditiveStatusEffect'):
            r = f32.add(r, f32.permil(s.value))
        return r

    def GetLessonChangeSpecifyMoreThanStatus(self, value):
        return -1  # 待还原

    def GetLessonChangeSpecifyLessThanStatus(self, value):
        return -1  # 待还原

    def GetParameterDebuffTurn(self, is_use=True):
        return self._turn('ParameterDebuffStatusEffect')

    def GetParameterBuffMultiplePerTurnTurn(self, is_use=True):
        return self._turn('ParameterBuffMultiplePerTurnStatusEffect')

    def GetStartTurnDrawDown(self, is_use=True):
        return sum(s.value for s in self.of('StartTurnCardDrawDownStatusEffect'))

    def GetReviewMultiple(self, is_use=True):
        return self._multiple('ReviewMultipleStatusEffect')

    def GetReviewCountAdd(self, is_use=True):
        return sum(s.value for s in self.of('ReviewCountAddStatusEffect'))

    def IsStaminaConsumptionDown(self, is_use=True):
        return self.first('StaminaConsumptionDownStatusEffect') is not None

    def IsStaminaConsumptionAdd(self, is_use=True):
        return self.first('StaminaConsumptionAddStatusEffect') is not None

    def IsStaminaConsumptionDownAdd(self, is_use=True):
        return self.first('StaminaConsumptionDownAddStatusEffect') is not None

    def IsStaminaConsumptionAddDown(self, is_use=True):
        return self.first('StaminaConsumptionAddDownStatusEffect') is not None

    def GetStaminaConsumptionDownFix(self, is_use=True):
        return sum(s.value for s in self.of('StaminaConsumptionDownFixStatusEffect'))

    def GetStaminaConsumptionAddFix(self, is_use=True):
        return sum(s.value for s in self.of('StaminaConsumptionAddFixStatusEffect'))

    # ---- 指针锁（都读 _turn，无则 0）
    def GetStanceLock(self, is_use=True):
        st = self.first('StanceLockStatusEffect')
        return st.turn if st else 0

    def _last_turn(self, cls):
        xs = self.of(cls)
        return xs[-1].turn if xs else 0

    def GetStanceLockConcentrationEffectTurn(self, is_use=True):
        return self._last_turn('StanceLockConcentrationEffect')

    def GetStanceLockFullPowerEffectTurn(self, is_use=True):
        return self._last_turn('StanceLockFullPowerEffect')

    def GetStanceLockPreservationEffectTurn(self, is_use=True):
        return self._last_turn('StanceLockPreservationEffect')

    def IsIdolStatusIsStepMax(self):
        """強気 / 温存 段数 > 1 才算满；其余类型永远算满。"""
        return self.idol_step > 1 if self.idol_type in (1, 2) else True

    def GetEnthusiasticAdditiveBuff(self, is_use=True):
        return sum(s.value for s in self.of('EnthusiasticAdditiveStatusEffect'))

    def GetEnthusiasticMultipleStatusEffectMultiple(self, is_use=True):
        return self._multiple('EnthusiasticMultipleStatusEffect')

    def HasPanicStatus(self, is_use=True):
        return self.first('PanicStatusEffect') is not None

    def GetStaminaReduceChange(self, is_use=True):
        """FirstOrDefault(StaminaReduceChangeStatusEffect)._value（无则 0）。"""
        st = self.first('StaminaReduceChangeStatusEffect')
        return st.value if st else 0

    def IsBuffConsumptionDown(self, is_use=True):
        return self.first('BuffConsumptionDownStatusEffect') is not None

    def IsBuffConsumptionAdd(self, is_use=True):
        return self.first('BuffConsumptionAddStatusEffect') is not None

    def _search_hit(self, st, card):
        from .cards import is_search_target
        sid = st.extra.get('_searchId', '')
        return is_search_target(card, self.exam.master.search(sid) if sid else None)

    def IsSearchPlayCardLimit(self, card, is_use=True):
        """任一 SearchPlayCardLimitStatusEffect 的检索命中这张卡。"""
        return any(self._search_hit(st, card) for st in self.of('SearchPlayCardLimitStatusEffect'))

    def IsPlayCardLimitPlayableValueAddLimit(self, card, is_use=True):
        """有 PlayCardLimitPlayableValueAddStatusEffect 且这张卡不在其检索内（主数据未用）。"""
        st = self.first('PlayCardLimitPlayableValueAddStatusEffect')
        return st is not None and not self._search_hit(st, card)

    def UseSearchPlayCardConsumptionChange(self, cls, card, is_forecast=False):
        """UseSearchPlayCardStaminaConsumptionChange / …BuffConsumptionChange：
        检索命中这张卡的同类状态里，优先有回合限制且剩余回合最少的（都无回合限制时取第一个）；
        非预测时 _count − 1，减到 0 移除。返回其 _value（新费用），没有命中返回 −1。"""
        best = None
        for st in self.of(cls):
            if not self._search_hit(st, card):
                continue
            if best is None:
                best = st
            elif st.limited and (not best.limited or st.turn < best.turn):
                best = st
        if best is None:
            return -1
        if not is_forecast:
            n = best.extra.get('_count', 0) - 1
            if n >= 0:
                best.extra['_count'] = n
                if n == 0:
                    self.RemoveStatusEffect(best)
        return best.value

    def IsStaminaRecoverRestriction(self, is_use=True):
        return self.first('StaminaRecoverRestrictionStatusEffect') is not None

    def GetPlayableValueAddCount(self, is_use=True):
        return sum(s.value for s in self.of('PlayableValueAddStatusEffect'))

    def IsPlayableValueAdd(self, is_use=True):
        return self.GetPlayableValueAddCount() > 0

    # ---- 回合
    def SetPassingTurnStartAll(self):
        """ExamLoopTaskAsync 的 b__94_9：主阶段开始时当前全部状态 SetPassingTurnStart。"""
        for s in self.effects:
            s.passing = True

    def SpendTurn(self, ctx=None):
        """回合末衰减（ExamStatusEffectExtensions.SpendTurn<T>）：倒序；附魔 IncrementTurnCount；
        被 IExamTurnEndReduceLockStatusEffect 锁住的类型不减；IsPassingTurnStart 的减 1，≤0 移除。
        待核：锁定类型表（ReviewTurnEndReduceLock → Review，ParameterBuffTurnEndReduceLock → ParameterBuff）。"""
        locks = set()
        if self.first('ReviewTurnEndReduceLockStatusEffect'):
            locks.add('ReviewStatusEffect')
        if self.first('ParameterBuffTurnEndReduceLockStatusEffect'):
            locks.add('ParameterBuffStatusEffect')
        for s in list(reversed(self.effects)):
            if isinstance(s, TriggerEffect):
                s.IncrementTurnCount()
            if s.cls in locks:
                continue
            if not s.limited or not s.passing:
                continue
            before = s.turn
            s.SpendTurn()
            if s.cls == 'ReviewStatusEffect' and self.exam is not None:
                # ReviewStatusEffect.SpendTurn：衰减量计入 AddReviewConsumptionSumCount
                c = self.exam.counters
                c['reviewConsumptionSumCount'] = c.get('reviewConsumptionSumCount', 0) + max(0, before - s.turn)
            if s.turn <= 0:
                self.RemoveStatusEffect(s)

    def copy(self, exam=None):
        c = StatusCollection(exam)
        c.effects = [s.copy() for s in self.effects]
        c.removed = []
        c.create_count = self.create_count
        c.idol_type, c.idol_step = self.idol_type, self.idol_step
        return c
