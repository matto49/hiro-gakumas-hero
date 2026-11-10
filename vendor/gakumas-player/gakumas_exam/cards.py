"""考试中的卡（Campus.InGame.Card.ExamCardData）与检索（ExamExtensions.IsSearchTarget）。

存档字段：_cardData{_id,_upgradeCount,_customizeCountList} _guid _fixedDeckOrder _baseUpgradeCount _tmpUpgradeCount
_supportUpgradeIdList _statusEffect _growEffectExamStartAfterList _affectGrowEffectIdList _playCount …
UpgradeCount = base + tmp + len(supportUpgradeIdList)（get_UpgradeCount）。
"""
import itertools


_SEQ = itertools.count(1)


def new_guid(ex=None):
    """考试中新生成的卡的 guid：确定序列（游戏里是随机 uuid，规则不读它）。给 ex 时按这局自己的计数（随对局一起复制，
    同一局面的克隆生成相同的 guid，推演可复现、置换表能命中）。"""
    if ex is None:
        return f'gen-{next(_SEQ):08d}'
    n = ex.__dict__.get('_guid_seq', 0) + 1
    ex._guid_seq = n
    return f'gen-{n:06d}'

from .master import E, convert

CAT = E('ProduceCardCategory')
POS = E('ProduceCardPositionType')
MOVE = E('ProduceCardMovePositionType')
SSTAT = E('ProduceCardSearchStatusType')
MINMAX = E('ConditionMinMaxType')


class ExamCard:
    __slots__ = ('master', 'card_id', 'base_upgrade', 'tmp_upgrade', 'support_upgrades', 'customize_counts', 'guid',
                 'fixed_order', 'play_count', 'grow_after', 'affect_grow', 'status_effect', 'move_used_in_turn', 'stamina_specify', 'custom_override', '_row', '_effects')

    def __init__(self, master, card_id, upgrade=0, customize_counts=None, guid=None):
        self.master = master
        self.card_id = card_id
        self.base_upgrade = upgrade
        self.tmp_upgrade = 0
        self.support_upgrades = []
        self.customize_counts = list(customize_counts or [])
        self.guid = guid or new_guid()
        self.fixed_order = 0
        self.play_count = 0
        self.grow_after = []      # _growEffectExamStartAfterList：考试中获得的成长效果（ExamAddGrowEffect）
        self.affect_grow = []
        self.status_effect = None  # 卡附魔（ProduceCardStatusEffect）
        self.move_used_in_turn = False
        self.stamina_specify = []   # _staminaConsumptionSpecifyEffectList（ExamCardValueStatusEffect）
        self.custom_override = None  # 显式定制 [{'id', 'count'}]（主数据的可定制列表里没有、但卡实际带着的定制）
        self._row = None
        self._effects = None

    @classmethod
    def from_save(cls, d, master):
        cd = d['_cardData']
        c = cls(master, cd['_id'], d['_baseUpgradeCount'], cd.get('_customizeCountList', []), d['_guid'])
        c.tmp_upgrade = d['_tmpUpgradeCount']
        c.support_upgrades = list(d.get('_supportUpgradeIdList', []))
        c.fixed_order = d.get('_fixedDeckOrder', 0)
        c.play_count = d.get('_playCount', 0)
        c.grow_after = [grow_from_save(g, master) for g in d.get('_growEffectExamStartAfterList', [])]
        c.affect_grow = list(d.get('_affectGrowEffectIdList', []))
        se = d.get('_statusEffect') or {}
        c.status_effect = dict(se) if se.get('_id') else {}
        c.move_used_in_turn = d.get('_isMoveProduceExamEffectUseInTurn', False)
        c.stamina_specify = [dict(x) for x in d.get('_staminaConsumptionSpecifyEffectList', [])]
        return c

    def copy(self):
        c = ExamCard(self.master, self.card_id, self.base_upgrade, self.customize_counts, self.guid)
        c.tmp_upgrade = self.tmp_upgrade
        c.support_upgrades = list(self.support_upgrades)
        c.fixed_order = self.fixed_order
        c.play_count = self.play_count
        c.grow_after = [dict(g) for g in self.grow_after]
        c.affect_grow = list(self.affect_grow)
        import copy as _copy
        c.status_effect = _copy.deepcopy(self.status_effect)
        c.move_used_in_turn = self.move_used_in_turn
        c.stamina_specify = [dict(x) for x in self.stamina_specify]
        c.custom_override = list(self.custom_override) if self.custom_override else None
        c._row, c._effects = self._row, self._effects
        return c

    def __deepcopy__(self, memo):
        """克隆提速：主数据与缓存的主数据行（_row / _effects，只会整体替换）共享，其余深拷贝。"""
        import copy as _copy
        new = object.__new__(ExamCard)
        memo[id(self)] = new
        for n in ExamCard.__slots__:
            if hasattr(self, n):
                v = getattr(self, n)
                setattr(new, n, v if n in ('master', '_row', '_effects') or type(v) in (int, str, bool, type(None)) else _copy.deepcopy(v, memo))
        return new

    def invalidate(self):
        self._row = None
        self._effects = None

    # ---- 基本属性
    @property
    def upgrade(self):
        return self.base_upgrade + self.tmp_upgrade + len(self.support_upgrades)

    @property
    def upgrade_raw(self):
        return self.base_upgrade + self.tmp_upgrade

    def key(self):
        se = self.status_effect or {}
        return (self.card_id, self.upgrade, tuple(self.customize_counts), tuple((g['effectType'], g['value']) for g in self.grow_after),
                se.get('_spendCount', 0) if se.get('_id') else None, tuple((x.get('_intValue'), x.get('_turn')) for x in self.stamina_specify))

    def base_row(self):
        """主数据行（当前强化等级；超过最高等级时取最高）。"""
        u = self.upgrade
        while u >= 0:
            r = self.master.get('ProduceCard', self.card_id, u)
            if r is not None:
                return r
            u -= 1
        raise KeyError(self.card_id)

    @property
    def customizations(self):
        if self.custom_override:
            return [dict(x) for x in self.custom_override]
        ids = self.base_row().get('produceCardCustomizeIds') or []
        return [dict(id=i, count=n) for i, n in zip(ids, self.customize_counts) if n]

    @property
    def customize_count(self):
        if self.custom_override:
            return sum(x['count'] for x in self.custom_override)
        return sum(self.customize_counts)

    def customize_grows(self):
        """定制成长（ProduceCardData.UpdateCustomize）：各分支取 ProduceCardCustomize(id, 当前次数) 一行的 produceCardGrowEffectIds，
        不逐级累加。成长行已转整数枚举。"""
        out = []
        for c in self.customizations:
            row = self.master.customize(c['id'], c['count'])
            if row is None:
                continue
            for gid in row.get('produceCardGrowEffectIds') or []:
                out.append(dict(self.master.grow_effect(gid)))
        return out

    def row(self):
        """两层：ProduceCardData 套定制成长，ExamCardData 在其结果上再套考试中成长（_growEffectExamStartAfterList），各层各自截断。
        卡附魔取 ExamCardData._statusEffect（考试中可被替换），效果组按最终效果 + 卡附魔重算。"""
        if self._row is None:
            from .grow import apply_card_grows
            row = apply_card_grows(self.master, dict(self.base_row()), self.customize_grows())
            if self.grow_after:
                row = apply_card_grows(self.master, row, self.grow_after)
            self._row = row
        return self._row

    def ensure_status_effect(self):
        """卡附魔（ExamCardData._statusEffect）：存档没有时按成长后的 produceCardStatusEnchantId 建（spendCount 0）。"""
        if self.status_effect is None:
            eid = self.row().get('produceCardStatusEnchantId')
            se = self.master.get('ProduceCardStatusEnchant', eid) if eid else None
            if se:
                self.status_effect = {'_id': se['id'], '_produceExamTriggerId': se['produceExamTriggerId'],
                                      '_produceCardGrowEffectIdList': list(se['produceCardGrowEffectIds']),
                                      '_effectGroupIdList': list(se.get('effectGroupIds') or []),
                                      '_triggerCount': se['triggerCount'], '_phaseCountDictionary': {'_list': []},
                                      '_spendTurn': 0, '_spendCount': 0}
            else:
                self.status_effect = {}
        return self.status_effect

    def SetTemporaryStaminaConsumptionFix(self, value, count=0, turn=1):
        """ExamCardData.SetTemporaryStaminaConsumptionFix：清空后加一条 ExamCardValueStatusEffect（指定体力消耗）。"""
        self.stamina_specify = [{'_turn': turn, '_intValue': value, '_boolValue': False, '_count': count,
                                 '_isTurnLimited': turn > 0, '_isCountLimited': count > 0, '_isPassingTurnStart': False}]

    def SpendTurn(self):
        """ExamCardData.SpendTurn：限时且已过回合开始的卡状态回合 −1，≤0 移除。"""
        keep = []
        for x in self.stamina_specify:
            if x.get('_isTurnLimited') and x.get('_isPassingTurnStart'):
                x['_turn'] -= 1
                if x['_turn'] <= 0:
                    continue
            keep.append(x)
        self.stamina_specify = keep

    def SetPassingTurnStart(self):
        for x in self.stamina_specify:
            x['_isPassingTurnStart'] = True

    def add_grow(self, g, lesson_add_multiple=1.0):
        """ExamCardData.AddGrowEffect：合并进 _growEffectExamStartAfterList，id 记入 _affectGrowEffectIdList，清缓存。"""
        from .grow import merge
        merge(self.grow_after, g, lesson_add_multiple)
        self.affect_grow.append(g['id'])
        self.invalidate()

    @property
    def name(self):
        return self.row()['name']

    @property
    def category(self):
        return self.row()['category']

    @property
    def rarity(self):
        return self.row()['rarity']

    @property
    def plan(self):
        return self.row()['planType']

    @property
    def search_tag(self):
        return self.row()['searchTag']

    @property
    def cost_type(self):
        return self.row()['costType']

    @property
    def cost_value(self):
        return self.row()['costValue']

    @property
    def stamina(self):
        return self.row()['stamina']

    @property
    def force_stamina(self):
        return self.row()['forceStamina']

    @property
    def is_penetrate(self):
        """ProduceCard.get_IsStaminaCostPenetrate：主数据 forceStamina > 0（成长不改变此标志）。"""
        return self.row()['_penetrate']

    def stamina_cost(self):
        """ExamCardData.GetStaminaCost：成长后的体力费用；有 _staminaConsumptionSpecifyEffectList（指定消耗）时取其中最小值。"""
        if self.stamina_specify:
            return min(x.get('_intValue', 0) for x in self.stamina_specify)
        r = self.row()
        return r['forceStamina'] if r['_penetrate'] else r['stamina']

    @property
    def play_move(self):
        return self.row()['playMovePositionType']

    @property
    def is_initial(self):
        return self.row()['isInitial']

    @property
    def is_restrict(self):
        return self.row()['isRestrict']

    @property
    def end_turn_lost(self):
        return self.row()['isEndTurnLost']

    @property
    def effect_group_ids(self):
        return self.row().get('effectGroupIds') or []

    def play_trigger(self):
        return self.master.trigger(self.row().get('playProduceExamTriggerId'))

    def play_effects(self):
        """[(trigger 行或 None, effect 行, isOncePlayEffect)]，成长已套用（grow.py）。"""
        if self._effects is None:
            self._effects = [(self.master.trigger(t) if t else None, e, once) for t, e, once in self.row()['_effects']]
        return self._effects

    def __repr__(self):
        return f'<{self.card_id}+{self.upgrade}{"c" if self.customize_count else ""} {self.guid[:6]}>'


def grow_from_save(d, master):
    """存档里的 ProduceCardGrowEffectData（_id/_effectType/_value/…）→ 主数据同形 dict。"""
    if isinstance(d, str):
        return dict(master.grow_effect(d))
    g = dict(master.grow_effect(d['_id'])) if d.get('_id') else {}
    g.update(id=d.get('_id', ''), effectType=d.get('_effectType', g.get('effectType', 0)), value=d.get('_value', g.get('value', 0)))
    return g


def grow_to_save(g):
    return {'_id': g['id'], '_effectType': g['effectType'], '_value': g['value'],
            '_playProduceExamTriggerId': g.get('playProduceExamTriggerId', ''),
            '_playEffectProduceExamTriggerId': g.get('playEffectProduceExamTriggerId', ''),
            '_targetPlayEffectProduceExamTriggerIdList': list(g.get('targetPlayEffectProduceExamTriggerIds') or []),
            '_playProduceExamEffectId': g.get('playProduceExamEffectId', ''),
            '_targetPlayProduceExamEffectIdList': list(g.get('targetPlayProduceExamEffectIds') or []),
            '_produceCardStatusEnchantId': g.get('produceCardStatusEnchantId', ''),
            '_playMovePositionType': g.get('playMovePositionType', 0)}


def is_search_target(card, search):
    """ExamExtensions.IsSearchTarget。search 为主数据 ProduceCardSearch 行（已转整数）。"""
    if search is None or search.get('isSelf'):
        return True
    if search.get('cardSearchTag') and card.search_tag != search['cardSearchTag']:
        return False
    ids, ups = search.get('produceCardIds') or [], search.get('upgradeCounts') or []
    if ids and card.card_id not in ids:
        return False
    if ups and card.upgrade not in ups:
        return False
    if search.get('planType') and card.plan != search['planType']:
        return False
    cats = search.get('cardCategories') or []
    if cats and card.category not in cats:
        return False
    if not _by_stamina(card, search.get('staminaMinMaxType', 0), search.get('staminaMin', 0), search.get('staminaMax', 0),
                       search.get('costType', 0)):
        return False
    st = search.get('cardStatusType', 0)
    if st and not search_status(card, st):
        return False
    et = search.get('examEffectType', 0)
    if et and not any(e['effectType'] == et for _, e, _ in card.play_effects()):
        return False
    groups = search.get('effectGroupIds') or []
    if groups and not any(g in groups for g in card.effect_group_ids):
        return False
    rar = search.get('cardRarities') or []
    if rar and card.rarity not in rar:
        return False
    if search.get('isCustomized') and card.customize_count <= 0:
        return False
    return True


def _by_stamina(card, kind, lo, hi, cost_type):
    """IsSearchTargetByStamina：costType 指定时比较该状态费用，否则比较体力消耗。MinMax / Min / Max。"""
    if not kind:
        return True
    if cost_type:
        if card.cost_type != cost_type:
            return False
        v = card.cost_value
    else:
        v = card.stamina
    if kind == MINMAX['MinMax']:
        return lo <= v <= hi
    if kind == MINMAX['Min']:
        return v >= lo
    if kind == MINMAX['Max']:
        return v <= hi
    return True


def search_status(card, st):
    """IsProduceCardSearchStatusType。"""
    if st == SSTAT['LostPlay']:
        return card.play_move == MOVE['Lost']
    if st == SSTAT['GravePlay']:
        return card.play_move == MOVE['Grave']
    if st == SSTAT['EndTurnLost']:
        return card.end_turn_lost
    if st == SSTAT['Initial']:
        return card.is_initial
    if st == SSTAT['Restrict']:
        return card.is_restrict
    return True
