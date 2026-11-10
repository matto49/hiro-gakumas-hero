"""考试局面与流程（ExamParameterModel + ExamSequence + ExamCardMoveController 的移植）。

流程依据（客户端 + 实机日志顺序）：
- 回合开始：CurrentTurn+1 → 抽牌（TurnDrawCommand，含支援强化）→ 回合开始阶段附魔（ExamStartTurn）→ 计时器（TurnTimer，按附魔 TurnCount）
  → 间隔回合（TurnInterval）→ 全部状态 SetPassingTurnStart → 开始后（StartPlay）附魔 → 主阶段。
- 出牌：付费 → 移入出牌区 → 出牌计数 → 出牌时附魔（ExamCardPlay 等）→ 卡效果（各自的条件在建命令时判定；重复次数来自 PlayCountBuff）
  → 出牌后检查（CardPlayAfter）→ 移到弃牌 / 除外 → 行动数。
- 回合结束：跳过时回体力 + TurnSkip 附魔 → 回合末附魔（ExamEndTurn / EndTurnInterval / EndTurnTimer）→ 手牌入弃牌
  → 好印象得分（合成 ExamLesson，值 ceil(好印象倍率×好印象)，执行 1+好印象強化次数 遍）→ SpendTurn → 下一回合。
命令执行：队列；效果执行后新触发的附魔插到队首（GetInsertEffectResultTriggerCommand / InsertExecuteCommand）。
"""
from collections import deque

from . import f32
from .cards import ExamCard, is_search_target
from .master import E, load as load_master
from .rng import NativeRandom
from .status import StatusCollection, TriggerEffect
from . import triggers
from . import difftrig

PH = E('ProduceExamPhaseType')
ET = E('ProduceExamEffectType')
POS = E('ProduceCardPositionType')
MOVE = E('ProduceCardMovePositionType')
CAT = E('ProduceCardCategory')
COST = E('ExamCostType')
RANGE = E('ProducePickRangeType')


# 卡附魔的指针变更计数（阶段 35/36）：客户端里 IncrementEnchantPhaseCount 与 ProduceCardStatusEffect.IncrementStanceChangeCount
# 各加一次（「指針を2回変更するたび」的卡会每次都触发）。客户端已逐条核过，默认 True；没有实机数据，保留开关。
CARD_STANCE_COUNT_DOUBLE = True


class Ctx:
    """ExamEffectCalculateContext：当前执行的效果来源。"""
    __slots__ = ('exam', 'card', 'enchant', 'drink', 'item', 'enchant_active', 'source', 'gimmick', 'trigger_card')

    def __init__(self, exam, card=None, enchant=None, drink=None, item=None, enchant_active=None, source='', gimmick=None):
        self.exam = exam
        self.card = card
        self.enchant = enchant
        self.drink = drink
        self.item = item
        if enchant_active is None:
            # IsEnchantTriggerActive：PlayingItem 为空，且（不是附魔触发，或附魔的 PlayingEnchantIsTriggerActive）。
            # PlayingEnchantIsTriggerActive = IsEnchantTriggerActiveEffectType(GetEffectType(附魔状态类型))：只有计时器类
            # （EffectTimer 103 / EffectTimerEndTurn 190，对应状态类型 30 / 69）为真。
            enchant_active = item is None and gimmick is None and (enchant is None or enchant.override_type in (30, 69))
        self.enchant_active = enchant_active
        self.source = source
        self.gimmick = gimmick
        # 附魔效果的「来源卡」（TriggerEffectStatusEffect._triggerCard）
        self.trigger_card = None
        if enchant is not None and getattr(enchant, 'source', None) and enchant.source.get('card_guid'):
            self.trigger_card = next((c for c in exam.all_cards() if c.guid == enchant.source['card_guid']), None)

    def IsEnchantTriggerActive(self):
        return self.enchant_active


class Exam:
    def __init__(self, master=None, setting_id='p_exam_setting-1'):
        self.master = master or load_master()
        self.setting = self.master.setting(setting_id)
        self.rng = NativeRandom(1)
        self.status = StatusCollection(self)
        self.zones = {z: [] for z in ('hand', 'deck', 'grave', 'lost', 'hold')}
        self.playing = None
        self.plan_type = 0
        self.exam_type = 1
        self.produce_id = ''
        self.idol_card_id = ''
        self.step_type = 0
        self.stamina = self.max_stamina = 0
        self.block = 0
        self.score = 0
        self.phase = 0
        self.current_turn = self.limit_turn = self.remain_turn = self.extra_turn = 0
        self.turn_types = []
        self.bonus_permil = {1: 1000, 2: 1000, 3: 1000}
        self.drink_limit = 3
        self.exam_card_play_count = 0
        self.turn_card_play_count = 0
        self.is_turn_card_play_end = False
        self.is_turn_card_grave = False
        self.is_turn_card_lost = False
        self.counters = {}
        self.parameters = {}
        self.drinks = []
        self.items = []
        self.supports = []
        self.turn_used_supports = []
        self.npcs = []
        self.clear_border = 0
        self.play_log = []        # 打出的卡（只算出牌，b__125_0）
        self.queue = deque()
        self._diff = None         # 当前命令的差分事件（note_diff）
        self._suppressed = None   # 当前命令里触发屏蔽窗口的前后观测
        self.initial_deal = True   # 第 1 回合用 SetInitialCard 发牌
        self.gimmick_schedule = {}  # 回合 → ProduceExamGimmickEffectGroup 行（ExamGimmickModel.Setup）
        self.events = []          # 引擎自己的事件日志（与游戏 userPlayLogList 对照用）
        self.log_len = 0
        self.ended = False
        self.remaining_actions = 0
        self.draw_queue = None    # 回放时强制的抽牌顺序（None = 按牌堆）
        self.trace = []

    # ------------------------------------------------------------------ 基本量
    @property
    def judge_parameter(self):
        return self.score

    def current_parameter_type(self):
        """GetCurrentParameterType：当前回合属性 1 Vo / 2 Da / 3 Vi。"""
        i = self.current_turn - 1
        if 0 <= i < len(self.turn_types):
            return self.turn_types[i]
        return self.turn_types[-1] if self.turn_types else 1

    def npc_scores(self, upto_turn=None):
        """对手分数（存档 npcDataList；HIF 为静态分数 isStaticNpcScore：_scoreList 为每回合得分）。upto_turn=None 取最终总分。"""
        out = []
        for n in self.npcs:
            sl = n.get('_scoreList') or []
            out.append(sum(sl[:upto_turn]) if upto_turn is not None else n.get('_currentScore', sum(sl)))
        return out

    def rank(self, upto_turn=None):
        """名次（1 起）：分数高于自己的 NPC 数 + 1（同分时玩家优先，见 mechanics「H.I.F 总分汇总、玩家同分优先」）。"""
        return 1 + sum(1 for x in self.npc_scores(upto_turn) if x > self.score)

    def log(self, kind, **kw):
        self.events.append(dict(kind=kind, turn=self.current_turn, **kw))

    # ------------------------------------------------------------------ 卡区与检索
    def all_cards(self):
        out = []
        for z in ('hand', 'deck', 'grave', 'lost', 'hold'):
            out += self.zones[z]
        if self.playing is not None:
            out.append(self.playing)
        return out

    def cards_at(self, pos, ctx=None):
        z = self.zones
        if pos == POS['Hand']:
            return list(z['hand'])
        if pos == POS['Deck']:
            return list(z['deck'])
        if pos == POS['Grave']:
            return list(z['grave'])
        if pos == POS['Lost']:
            return list(z['lost'])
        if pos == POS['Hold']:
            return list(z['hold'])
        if pos == POS['DeckAll']:
            return z['hand'] + z['deck'] + z['grave'] + z['hold'] + ([self.playing] if self.playing else [])
        if pos == POS['DeckGrave']:
            return z['deck'] + z['grave']
        if pos == POS['NotLost']:
            return z['hand'] + z['deck'] + z['grave'] + z['hold'] + ([self.playing] if self.playing else [])
        if pos == POS['Playing'] or pos == POS['PlayHand']:
            return [self.playing] if self.playing else []
        if pos == POS['Self'] and ctx is not None and ctx.card is not None:
            return [ctx.card]
        if pos == POS['Target'] and ctx is not None:
            # 检索 Target：出牌效果里是这张卡；附魔效果里是附魔的来源卡（_triggerCard）
            c = ctx.card or ctx.trigger_card
            return [c] if c is not None else []
        return []

    def GetSearchCardList(self, search, ctx=None):
        """ExamEffectUtility.GetSearchCardList → PickCardPositionListImpl：位置 → IsSearchTarget →
        OrderByCardOrderType（First 原序 / Last 倒序 / Random 按随机键 OrderBy）→ limitCount 截取。
        Random：每张候选的键取自考试随机数；真机抓取（atuy1219/gakumas-sim native-tenpu-move-rng，8 例）显示每张候选消耗 2 次、
        排序用第二遍的键（第一遍被丢弃），随机数状态 8/8 一致。"""
        if search is None:
            return []
        cards = [c for c in self.cards_at(search.get('cardPositionType', 0), ctx) if is_search_target(c, search)]
        order = search.get('orderType', 0)
        if order == 2:
            cards = cards[::-1]
        elif order == 3 and cards:
            for _ in cards:
                self.rng.signed()
            keys = [self.rng.signed() for _ in cards]
            cards = [c for _, _, c in sorted((k, i, c) for i, (k, c) in enumerate(zip(keys, cards)))]
        limit = search.get('limitCount', 0)
        if limit and len(cards) > limit:
            cards = cards[:limit]
        return cards

    def zone_of(self, card):
        for z, cs in self.zones.items():
            if card in cs:
                return z
        if card is self.playing:
            return 'playing'
        return None

    def remove_card(self, card):
        z = self.zone_of(card)
        if z == 'playing':
            self.playing = None
        elif z:
            self.zones[z].remove(card)
        return z

    # ------------------------------------------------------------------ 生成 / 移动（ExamCardMoveController.AddCard / MoveCard）
    def AddCard(self, cards, pos, ctx=None):
        """生成卡加入卡区。DeckRandom：每张 rng.integer(0, len(deck)) 插入（旧引擎 card_mutations.create，已对原生核对）。
        Hand：手牌未满入手（并做支援强化判定），满则放到牌堆顶（同 InsertCardTop，待核）。"""
        added_hand = []
        for c in cards:
            if pos == MOVE['DeckRandom']:
                i = self.rng.integer(0, len(self.zones['deck']))
                self.zones['deck'].insert(i, c)
            elif pos == MOVE['DeckFirst']:
                self.zones['deck'].insert(0, c)
            elif pos == MOVE['DeckLast']:
                self.zones['deck'].append(c)
            elif pos == MOVE['Hand']:
                if len(self.zones['hand']) < self.setting['handLimit']:
                    self.zones['hand'].append(c)
                    added_hand.append(c)
                else:
                    self.zones['deck'].insert(0, c)
            elif pos == MOVE['Grave']:
                self.zones['grave'].append(c)
            elif pos == MOVE['Lost']:
                self.zones['lost'].append(c)
            elif pos == MOVE['Hold']:
                self.zones['hold'].append(c)
            self.log('add_card', card=c.card_id, pos=pos)
        if added_hand:
            self.note_diff('hand_add', cards=list(added_hand), frm=0, etype=0)
        if pos != MOVE['Hand']:
            self.note_diff('move', cards=list(cards), pos=pos)
        if added_hand:
            self.support_upgrade(added_hand)

    def MoveCard(self, cards, pos, ctx=None):
        """移动已有卡。入手时手牌满则放到牌堆顶（旧引擎 tag_fetch.apply，已对实机核对）。
        DeckFirst：按来源分组（组的顺序 = 首次出现顺序，组内保持顺序）后整批放到牌堆顶、保持顺序
        （真机抓取 native-tenpu-move-rng 8 例）。"""
        if pos == MOVE['DeckFirst']:
            groups = {}
            for c in cards:
                groups.setdefault(self.zone_of(c), []).append(c)
            cards = [c for g in groups.values() for c in g]
        added_hand = []
        top = []
        if pos == MOVE['Hand']:
            # ExamCardMoveController.MoveCard：手牌放不下的卡回到原来的牌区（牌堆则在顶上）。
            # 真机抓取 atuy #5 T8：除外区的卡在手牌已满时留在除外区；广的实机存档：牌堆的卡在手牌已满时在牌堆顶
            room = self.setting['handLimit'] - len(self.zones['hand'])
            take = cards[len(cards) - room:] if 0 < room < len(cards) else (cards if room >= len(cards) else [])
            for c in cards:
                if c in take:
                    continue
                if self.zone_of(c) == 'deck':
                    self.zones['deck'].remove(c)
                    self.zones['deck'].insert(0, c)
            cards = take
        for c in cards:
            src = self.remove_card(c)
            if src == 'hand' and pos != MOVE['Hand'] and c.support_upgrades:
                # MoveCard 把卡移出手牌（保留 / 牌堆 / 弃牌 / 除外）时去掉支援强化（GKMS HIF-65 实机：MoveCard 后
                # support_upgrade_count 1 → 0）；临时强化保留。2026-11-07 对拍发现之前只在回合末弃牌和出牌时清。
                c.support_upgrades = []
                c.invalidate()
            if pos == MOVE['Hand']:
                if len(self.zones['hand']) < self.setting['handLimit']:
                    self.zones['hand'].append(c)
                    added_hand.append(c)
                else:
                    self.zones['deck'].insert(0, c)
            elif pos == MOVE['DeckFirst']:
                top.append(c)
            elif pos == MOVE['DeckLast']:
                self.zones['deck'].append(c)
            elif pos == MOVE['DeckRandom']:
                i = self.rng.integer(0, len(self.zones['deck']))
                self.zones['deck'].insert(i, c)
            elif pos == MOVE['Grave']:
                self.zones['grave'].append(c)
            elif pos == MOVE['Lost']:
                self.zones['lost'].append(c)
            elif pos == MOVE['Hold']:
                self.zones['hold'].append(c)
            self.log('move_card', card=c.card_id, src=src, pos=pos)
        if top:
            self.zones['deck'][:0] = top
        if pos != MOVE['Hand']:
            self.note_diff('move', cards=list(cards), pos=pos)
        if added_hand:
            self.note_diff('hand_add', cards=list(added_hand), frm=-1, etype=0)
            self.support_upgrade(added_hand)

    def PickSearchCards(self, search, pick_range, cmin, cmax, ctx=None, choice=None):
        """GetSearchCardList(search, pickRange, min, max, …) 简版：All = 全部；Random = 用考试随机数抽 max 张（待核）；
        Select = 玩家选择（choice 为回放时日志里的结果，缺省取前 max 张）。"""
        cards = self.GetSearchCardList(search, ctx)
        if pick_range == RANGE['All'] or not pick_range:
            return cards
        n = cmax if cmax else len(cards)
        if pick_range == RANGE['Random']:
            # PickCardPositionListImpl（旧引擎 NativeRandom.pick_one，已对原生核对）：张数 integer(min, max+1)（min == max 也掷），
            # 再给每张候选各掷一个 signed 键，升序取前 张数 张（键相同按原顺序）
            if not cards:
                return []
            k = self.rng.integer(cmin or 1, (cmax or cmin or 1) + 1)
            ranked = sorted(((self.rng.signed(), i, c) for i, c in enumerate(cards)), key=lambda x: (x[0], x[1]))
            return [c for _, _, c in ranked[:k]]
        if choice is not None:
            return [c for c in cards if c.card_id in choice][:n]
        return cards[:n]

    # ------------------------------------------------------------------ 抽牌
    def shuffle_grave_into_deck(self):
        g = self.zones['grave']
        if not g:
            return
        self.rng.shuffle(g)
        self.zones['deck'].extend(g)
        g.clear()
        self.log('shuffle')

    def DrawCard(self, count, ctx=None, is_effect=False):
        """ExamCardMoveController.DrawCard：从牌堆顶依次入手，牌堆空时洗回弃牌堆；手牌满（handLimit）则停止（待核：满手时多抽的去向）。"""
        hand_limit = self.setting['handLimit']
        received = []
        for _ in range(count):
            if len(self.zones['hand']) >= hand_limit:
                break
            if self.draw_queue is not None:
                c = self._forced_draw()
                if c is None:
                    break
            else:
                if not self.zones['deck']:
                    if not self.zones['grave']:
                        break
                    self.shuffle_grave_into_deck()
                c = self.zones['deck'].pop(0)
            self.zones['hand'].append(c)
            received.append(c)
        if received:
            self.log('draw', cards=[c.card_id for c in received], effect=is_effect)
            self.note_diff('hand_add', cards=list(received), frm=POS['Deck'], etype=ET['ExamCardDraw'] if is_effect else 0)
            # 支援强化在 GetInsertEffectResultTriggerCommand 里按 HandAdd 差分处理，只在 IsEnchantTriggerActive 时执行
            # （普通附魔 / 道具附魔触发的抽牌不掷骰；计时器附魔触发的掷骰。实机 10-19 R1 T2/T4/T6 随机数核对）
            if ctx is None or ctx.IsEnchantTriggerActive():
                self.support_upgrade(received)
        return received

    def _forced_draw(self):
        if not self.draw_queue:
            return None
        want = self.draw_queue.popleft()
        for z in ('deck', 'grave'):
            for c in self.zones[z]:
                if c.card_id == want:
                    self.zones[z].remove(c)
                    return c
        raise RuntimeError(f'强制抽牌 {want} 不在牌堆 / 弃牌')

    def support_upgrade(self, added):
        """支援卡考试内临时强化（gakumas_sim.support_upgrades.process_batch 的移植，已对原生核对）：
        按入手顺序 × 支援卡顺序，每对掷一次 integer(0,1000)；成功的支援本回合不再参与。"""
        param = self.current_parameter_type()
        planned = []
        for card in added:
            if self.master.get('ProduceCard', card.card_id, card.upgrade + 1) is None:
                continue
            for s in self.supports:
                key = s['_supportCardId']
                if key in self.turn_used_supports:
                    continue
                if s.get('_filterParameterType') not in (0, None, param):
                    continue
                roll = self.rng.integer(0, 1000)
                if roll < s['_produceCardUpgradePermil']:
                    self.turn_used_supports.append(key)
                    planned.append((card, key))
        for card, key in planned:
            if self.master.get('ProduceCard', card.card_id, card.upgrade + 1) is not None:
                card.support_upgrades.append(key)
                card.invalidate()
                self.log('support_upgrade', card=card.card_id, support=key)

    # ------------------------------------------------------------------ 触发
    def IncrementEnchantPhaseCount(self, phases, ctx=None):
        """ExamEffectUtility.IncrementEnchantPhaseCount：状态附魔的对应阶段计数 +1；
        再给 DeckAll 中卡附魔触发含这些阶段且场面条件成立的卡各自 +1（DisplayClass90_0.b__0 / b__1）。"""
        for s in self.status.effects:
            if isinstance(s, TriggerEffect):
                for p in phases:
                    if p in (s.trigger.get('phaseTypes') or []):
                        s.phase_count[p] = s.phase_count.get(p, 0) + 1
        difftrig.inc_card_enchant_phase(self, phases, lambda c, tr: triggers.trigger_field_valid(self, tr, ctx))

    def _available(self, t):
        if t.limit_count >= 0 and t.limit_count < 1:
            return False
        if t.limit_in_turn >= 0 and t.limit_in_turn_remain <= 0:
            return False
        return True

    def phase_enchants(self, phase, ctx=None):
        """GetPhaseEffectList(phase)：附魔按创建顺序，IsPhaseTrigger = 场面条件 + 阶段包含。"""
        out = []
        for s in list(self.status.effects):
            if not isinstance(s, TriggerEffect) or not self._available(s):
                continue
            if phase in (s.trigger.get('phaseTypes') or []) and triggers.trigger_field_valid(self, s.trigger, ctx):
                out.append(s)
        return out

    def timer_enchants(self, phase):
        """GetTurnTimerEffectList / GetEndTurnTimerEffectList：TurnCount == phaseValue。"""
        out = []
        for s in list(self.status.effects):
            if not isinstance(s, TriggerEffect) or not self._available(s):
                continue
            tr = s.trigger
            if phase in (tr.get('phaseTypes') or []) and triggers.trigger_field_valid(self, tr):
                v = (tr.get('phaseValues') or [0])[0]
                if s.turn_count == v:
                    out.append(s)
        return out

    def interval_enchants(self, phase):
        """GetTurnIntervalEffectList / GetEndTurnIntervalEffectList：TurnCount % phaseValue == 0。"""
        out = []
        for s in list(self.status.effects):
            if not isinstance(s, TriggerEffect) or not self._available(s):
                continue
            tr = s.trigger
            if phase in (tr.get('phaseTypes') or []) and triggers.trigger_field_valid(self, tr):
                v = (tr.get('phaseValues') or [1])[0] or 1
                if s.turn_count % v == 0:
                    out.append(s)
        return out

    def remove_timer_triggered(self, phase):
        """RemoveTurnTimerTriggeredStatus / RemoveEndTurnTimerTriggeredStatus：首个阶段为计时器且 TurnCount == phaseValue 的附魔移除。"""
        for s in list(reversed(self.status.effects)):
            if isinstance(s, TriggerEffect):
                ph = s.trigger.get('phaseTypes') or []
                if ph and ph[0] == phase and s.turn_count == (s.trigger.get('phaseValues') or [0])[0]:
                    self.status.RemoveStatusEffect(s)

    def fire(self, enchants, ctx_kind='enchant'):
        """GetTriggerEffectListCommand + GetInsertTriggerEffectListCommand：逐个记日志、扣次数、生成效果命令。返回命令列表。"""
        cmds = []
        for t in enchants:
            if not self._available(t):
                continue
            t.limit_count = t.limit_count - 1 if t.limit_count >= 0 else t.limit_count
            if t.limit_in_turn >= 0:
                t.limit_in_turn_remain -= 1
            if t.source.get('item'):
                for it in self.items:
                    if it['_id'] == t.source['item']:
                        it['_fireCount'] = it.get('_fireCount', 0) + 1
            self.log('enchant', enchant=t.enchant_id or t.trigger.get('id'), uid=t.uid)
            c = Ctx(self, enchant=t, item=t.source.get('item'), source=t.enchant_id)
            for e in t.effects:
                cmds.append(('effect', e, c))
            if t.limit_count == 0:
                pass  # 次数用完的附魔何时移除：RemoveCountLimitTriggeredStatus（待核时点）
        return cmds

    # ------------------------------------------------------------------ 命令执行
    def run(self, cmds):
        self.queue.extend(cmds)
        while self.queue:
            c = self.queue.popleft()
            self.execute(c)

    def insert(self, cmds):
        for c in reversed(cmds):
            self.queue.appendleft(c)

    def execute(self, cmd):
        kind = cmd[0]
        if kind == 'effect':
            _, effect, ctx = cmd
            from .effects import execute_effect
            self.with_result_triggers(lambda: execute_effect(self, effect, ctx), ctx)
        elif kind == 'call':
            cmd[1]()
        else:
            raise ValueError(kind)

    def with_result_triggers(self, fn, ctx, insert=True):
        """执行 fn，按前后差分生成结果触发（difftrig.result_triggers）；insert 时整体插到队首，否则返回命令。"""
        outer, outer_s = self._diff, self._suppressed
        self._diff, self._suppressed = [], []
        pre = difftrig.observe(self)
        try:
            fn()
        finally:
            events, self._diff = self._diff, outer
            windows, self._suppressed = self._suppressed, outer_s
        cmds = difftrig.result_triggers(self, difftrig.rebase(pre, windows), difftrig.observe(self), events, ctx)
        if insert:
            if cmds:
                self.insert(cmds)
            return []
        return cmds

    def card_enchant_cmds(self, pred, cards=None):
        return difftrig.card_enchant_cmds(self, lambda c, tr, se: pred(c, tr), cards)

    # ------------------------------------------------------------------ 资源
    def AddStamina(self, v):
        self.stamina = max(0, min(self.max_stamina, self.stamina + v))

    def AddCurrentTurnConsumeStamina(self, n):
        """ExamParameterModel.AddCurrentTurnConsumeStamina：本回合与累计消耗体力同时加。"""
        c = self.counters
        c['currentTurnTotalConsumeStamina'] = c.get('currentTurnTotalConsumeStamina', 0) + n
        c['totalConsumeStamina'] = c.get('totalConsumeStamina', 0) + n

    def SetBlock(self, n, is_consume=False):
        """ExamParameterModel.SetBlock：is_consume 时把减少量计入 blockConsumptionSumCount。"""
        if is_consume:
            self.counters['blockConsumptionSumCount'] = self.counters.get('blockConsumptionSumCount', 0) + max(0, self.block - n)
        if n > self.block:   # 待核：本回合获得元気（currentTurnTotalBlock）在 SetBlock 里累加（实机存档吻合）
            self.counters['currentTurnTotalBlock'] = self.counters.get('currentTurnTotalBlock', 0) + n - self.block
        self.block = n

    def CalculateDamage(self, value, penetrate, use_fix=True):
        """ExamEffectUtility.CalculateDamage → (体力伤害, 元気伤害)。
        x = value × 指针倍率（強気 1/2 段、温存 1/2 段、のんびり；全力无）× 消費体力減少（(1000−p)/1000，強化版用 DownAddPermil）
        × 消費体力増加（(1000+p)/1000，弱化版用 AddDownPermil）→ ceil（fcvtps，无 epsilon）；
        use_fix：+ max(0, 消費体力増加固定)，再 − 消費体力削減（≥1 时，结果 Max 0）；
        若结果 ≤ StaminaReduceChange 且其值 ≥1 → 改为 examStaminaReduceChange。贯通时不经元気。"""
        sc, st = self.status, self.setting
        x = f32.i2f(value)
        t, step = sc.GetIdolStatusType(), sc.GetIdolStatusStep()
        p = None
        if t == 1:
            p = st['examConcentrationStaminaMultiplePermil1'] if step == 1 else st['examConcentrationStaminaMultiplePermil2']
        elif t == 2:
            p = st['examPreservationStaminaMultiplePermil1'] if step == 1 else st['examPreservationStaminaMultiplePermil2']
        elif t == 4:
            p = st['examOverPreservationStaminaMultiplePermil']
        if p is not None:
            x = f32.mul(f32.permil(p), x)
        if sc.IsStaminaConsumptionDown():
            q = st['examStaminaConsumptionDownAddPermil'] if sc.IsStaminaConsumptionDownAdd() else st['examStaminaConsumptionDownPermil']
            x = f32.mul(x, f32.div(f32.sub(1000.0, f32.i2f(q)), 1000.0))
        if sc.IsStaminaConsumptionAdd():
            q = st['examStaminaConsumptionAddDownPermil'] if sc.IsStaminaConsumptionAddDown() else st['examStaminaConsumptionAddPermil']
            x = f32.mul(x, f32.div(f32.add(f32.i2f(q), 1000.0), 1000.0))
        v = f32.ceil_i(x)
        if use_fix:
            v += max(0, sc.GetStaminaConsumptionAddFix())
            d = sc.GetStaminaConsumptionDownFix()
            if d >= 1:
                v = max(0, v - d)
            if v <= sc.GetStaminaReduceChange(False) and sc.GetStaminaReduceChange(True) >= 1:
                v = st['examStaminaReduceChange']
        block = 0 if penetrate else self.block
        dmg = max(0, v - block)
        return dmg, v - dmg

    def DamageStamina(self, value, penetrate=False, ctx=None):
        """ExamEffectUtility.DamageStamina：元気先扣（SetBlock 计入消耗），体力 clamp 到 [0, 上限]，
        实际减少量计入 AddCurrentTurnConsumeStamina。差分：体力减少 / 体力元気混合伤害（结果触发用）。"""
        dmg, blk = self.CalculateDamage(value, penetrate, True)
        before_block, before = self.block, self.stamina
        if blk >= 1:
            self.SetBlock(max(0, self.block - blk), True)
        if dmg >= 1:
            self.stamina = max(0, min(self.max_stamina, self.stamina - dmg))
            self.AddCurrentTurnConsumeStamina(before - self.stamina)
        if dmg >= 1 or blk >= 1:
            self.note_diff('stamina', before=before, after=self.stamina, mix=(dmg >= 1 and blk >= 1),
                           block_before=before_block, block_after=self.block)

    def consume_stamina(self, amount, ignore_block=False):
        """旧接口：= DamageStamina（含倍率）。"""
        self.DamageStamina(amount, ignore_block)

    def ReduceStaminaFix(self, v, ctx=None):
        """ExamEffectUtility.ReduceStaminaFix：直接减体力（不经元気），实际减少量计入消耗。"""
        if v < 1:
            return False
        before = self.stamina
        self.stamina = max(0, self.stamina - v)
        self.AddCurrentTurnConsumeStamina(before - self.stamina)
        return self.stamina != before

    def note_diff(self, kind, **kw):
        """记一条差分事件（ExamEffectDifferenceData），当前命令执行完后由 result_triggers 处理。"""
        if self._diff is not None:
            self._diff.append((kind, kw))

    # ---- 状态费用（ExamEffectUtility.CalculateBuffCost / GetCostBuffValue / ConsumeBuffCost）
    def CalculateBuffCost(self, value):
        """× (1000 − examBuffConsumptionDownPermil)/1000（状態消費減少时）× (1000 + AddPermil)/1000（増加时），ceil。"""
        sc, st = self.status, self.setting
        x = f32.i2f(value)
        if sc.IsBuffConsumptionDown():
            x = f32.mul(f32.div(f32.sub(1000.0, f32.i2f(st['examBuffConsumptionDownPermil'])), 1000.0), x)
        if sc.IsBuffConsumptionAdd():
            x = f32.mul(x, f32.div(f32.add(f32.i2f(st['examBuffConsumptionAddPermil']), 1000.0), 1000.0))
        return f32.ceil_i(x)

    def GetCostBuffValue(self, cost_type):
        """。"""
        sc = self.status
        return {COST['ExamLessonBuff']: sc.GetLessonBuff, COST['ExamReview']: sc.GetReview,
                COST['ExamCardPlayAggressive']: sc.GetAggressive, COST['ExamParameterBuff']: sc.GetParameterBuffTurn,
                COST['ExamFullPowerPoint']: sc.GetFullPowerPoint,
                COST['ExamParameterBuffMultiplePerTurn']: sc.GetParameterBuffMultiplePerTurnTurn}.get(cost_type, lambda: 0)()

    COST_KEY = {1: 10, 2: 31, 3: 42, 4: 2, 5: 49, 6: 129}   # ExamExtensions.GetEffectType(ExamCostType)：费用 → 状态效果类型

    def ConsumeBuffCost(self, cost_type, value, ctx=None):
        """按 CalculateBuffCost 扣对应状态，变化时记状态差分（isConsume）。"""
        sc = self.status
        cost = self.CalculateBuffCost(value)
        before = self.GetCostBuffValue(cost_type)
        {COST['ExamLessonBuff']: sc.ConsumeLessonBuff, COST['ExamReview']: sc.ConsumeReview,
         COST['ExamCardPlayAggressive']: sc.ConsumeAggressive, COST['ExamParameterBuff']: sc.ConsumeParameterBuff,
         COST['ExamFullPowerPoint']: sc.ConsumeFullPowerPoint,
         COST['ExamParameterBuffMultiplePerTurn']: sc.ConsumeParameterBuffMultiplePerTurn}[cost_type](cost)
        after = self.GetCostBuffValue(cost_type)
        if after != before:
            self.note_diff('status', key=self.COST_KEY[cost_type], before=before, after=after, consume=True)

    # ------------------------------------------------------------------ 主阶段动作
    def legal_actions(self):
        acts = []
        if self.ended:
            return acts
        if self.remaining_actions > 0:
            for i, c in enumerate(self.zones['hand']):
                if self.card_playable(c):
                    acts.append(('play', i))
            for i, d in enumerate(self.drinks):
                acts.append(('drink', i))
        acts.append(('end',))
        return acts

    def card_playable(self, card):
        """ExamCardUtility.ValidateUseHandCard：非 Restrict、IsPlayable（使用条件）、未被检索出牌限制、
        ValidateCardCost（状态费用：CalculateBuffCost ≤ 现值；体力：CalculateDamage 合计 ≤ 体力 + 元気（贯通不计元気））。"""
        if card.is_restrict:
            return False
        ctx = Ctx(self, card=card)
        tr = card.play_trigger()
        if tr is not None and not triggers.trigger_field_valid(self, tr, ctx):
            return False
        if self.status.IsSearchPlayCardLimit(card) or self.status.IsPlayCardLimitPlayableValueAddLimit(card):
            return False
        if card.cost_type:
            return self.GetCostBuffValue(card.cost_type) >= self.CalculateBuffCost(card.cost_value)
        pen = card.is_penetrate
        dmg, blk = self.CalculateDamage(card.stamina_cost(), pen, True)
        return dmg + blk <= self.stamina + (0 if pen else self.block)

    def play(self, card):
        """出一张手牌（UseHand）。ExecuteCommandImplAsync 里 UseHand 分支先 ExecuteCardCommandImpl（建命令：出牌计数、
        出牌时附魔判定与扣次数、卡效果条件判定）再 ConsumeCardCost（付费），然后依次执行命令。
        所以出牌触发和效果条件看到的是付费前的体力（实机 10-19 R1 T10：体力 17→付费 15，道具「体力 50% 以上」仍触发）。"""
        ctx = Ctx(self, card=card)
        self.zones['hand'].remove(card)
        self.playing = card
        self.remaining_actions -= 1
        self.log('play', card=card.card_id)
        cmds = []
        self.increment_play_counts(card)
        cmds += self.fire(self.card_play_enchants(card, ctx))
        cmds += difftrig.card_enchant_cmds(self, lambda c, tr, se: self.card_enchant_play_valid(tr, se, card, ctx), [card])
        repeats = 1 + self.use_play_count_buff(card)
        for rep in range(repeats):
            for tr, e, once in card.play_effects():
                # ExecuteCardCommandImpl：isOncePlayEffect 的效果只在这张卡第一次打出、重复发动的第一遍执行
                if once and (rep > 0 or card.play_count > 0):
                    continue
                if tr is None or triggers.trigger_field_valid(self, tr, ctx):
                    cmds.append(('effect', e, ctx))
        # ConsumeCardCost：体力减少等结果触发插到队首（先于卡效果）；ExamBuffConsume 附魔追加在卡效果之后（AddExecuteCommand，
        # 待核：相对 UseCardAfterCheck 的先后——这里放在出牌后检查之前）
        consume = []
        def pay():
            consume.extend(self.consume_card_cost(card, ctx))
            if self.status.GetIdolStatusType() == 1:
                # 強気：付费后无视元気再扣体力（examConcentrationStaminaPenetrateReduce1/2），不发触发
                st = self.setting
                v = st['examConcentrationStaminaPenetrateReduce1'] if self.status.GetIdolStatusStep() == 1 else st['examConcentrationStaminaPenetrateReduce2']
                self.suppress_triggers(lambda: self.ReduceStaminaFix(v, ctx))
        front = self.with_result_triggers(pay, ctx, insert=False)
        # 命令栈（ExecuteCardCommandImpl + ConsumeCardCost AddExecuteCommand）：
        # [付费的结果触发] → 卡效果 → UseCardAfterCheck → MovePlayCard（+移牌结果触发）→ 状态消耗附魔 → 出牌后附魔的效果
        cmds = front + cmds
        cmds.append(('call', lambda: self._after_play(card, ctx, consume)))
        self.run(cmds)

    def consume_card_cost(self, card, ctx=None):
        """ExamSequence.ConsumeCardCost：
        状态费用：v = UseSearchPlayCardBuffConsumptionChange(card)（-1 = 无）否则 CostValue；v ≥ 1 时 ConsumeBuffCost，
          然后把 ExamBuffConsume 附魔（GetConsumeEffectTriggerEffectList(费用对应状态)）与卡附魔触发追加到命令队尾（AddExecuteCommand）。
        体力：v = UseSearchPlayCardStaminaConsumptionChange(card) 否则 GetStaminaCost；DamageStamina(v, 贯通)。返回要追加的命令。"""
        sc = self.status
        ctx = ctx or Ctx(self, card=card)
        if card.cost_type:
            v = sc.UseSearchPlayCardConsumptionChange('SearchPlayCardBuffConsumptionChangeStatusEffect', card)
            if v == -1:
                v = card.cost_value
            if v < 1:
                return []
            self.ConsumeBuffCost(card.cost_type, v, ctx)
            key = self.COST_KEY[card.cost_type]
            ench = [t for t in self.status.effects if isinstance(t, TriggerEffect) and self._available(t)
                    and triggers.is_consume_trigger(self, t.trigger, key, ctx)]
            return self.fire(ench) + self.card_enchant_cmds(lambda c, tr: triggers.is_consume_trigger(self, tr, key, ctx))
        v = sc.UseSearchPlayCardConsumptionChange('SearchPlayCardStaminaConsumptionChangeStatusEffect', card)
        if v == -1:
            v = card.stamina_cost()
        self.DamageStamina(v, card.is_penetrate, ctx)
        return []

    COUNT_PHASES = (PH['ExamPlayCountInterval'], PH['ExamPlayTurnCountInterval'], PH['ExamPlayCountIntervalAfter'])

    def increment_play_counts(self, card):
        for s in self.status.effects:
            if not isinstance(s, TriggerEffect):
                continue
            phases = s.trigger.get('phaseTypes') or []
            hit = [p for p in phases if p in self.COUNT_PHASES]
            if not hit:
                continue
            sid = s.trigger.get('produceCardSearchId')
            if sid and not is_search_target(card, self.master.search(sid)):
                continue
            for p in hit:
                s.phase_count[p] = s.phase_count.get(p, 0) + 1
        # 卡附魔的出牌计数（检索为 Target 时要求打出的卡命中）
        def hit(c, tr):
            sid = tr.get('produceCardSearchId')
            if not sid:
                return True
            srch = self.master.search(sid)
            return srch.get('isSelf') and c is card or is_search_target(card, srch)
        difftrig.inc_card_enchant_phase(self, self.COUNT_PHASES, hit)

    def card_play_enchants(self, card, ctx):
        """出牌时附魔：ExamCardPlay（GetPhaseEffectList / GetCardPlayValidEffectList）与出牌计数（IsCardPlayValidTrigger：
        计数 ≥ 1 且 计数 % phaseValue == 0）。按状态创建顺序。"""
        out = []
        for s in list(self.status.effects):
            if not isinstance(s, TriggerEffect) or not self._available(s):
                continue
            tr = s.trigger
            phases = tr.get('phaseTypes') or []
            if not triggers.target_search_ok(self, tr, card):
                continue
            if PH['ExamCardPlay'] in phases:
                if triggers.trigger_field_valid(self, tr, ctx, target_cards=[card]):
                    out.append(s)
            elif PH['ExamPlayCountInterval'] in phases or PH['ExamPlayTurnCountInterval'] in phases:
                p = PH['ExamPlayCountInterval'] if PH['ExamPlayCountInterval'] in phases else PH['ExamPlayTurnCountInterval']
                n = s.phase_count.get(p, 0)
                v = (tr.get('phaseValues') or [1])[0] or 1
                if n >= 1 and n % v == 0 and triggers.trigger_field_valid(self, tr, ctx, target_cards=[card]):
                    out.append(s)
        return out

    def card_play_after_enchants(self, card, ctx):
        out = []
        for s in list(self.status.effects):
            if not isinstance(s, TriggerEffect) or not self._available(s):
                continue
            tr = s.trigger
            phases = tr.get('phaseTypes') or []
            if not triggers.target_search_ok(self, tr, card):
                continue
            if PH['ExamCardPlayAfter'] in phases:
                if triggers.trigger_field_valid(self, tr, ctx, target_cards=[card]):
                    out.append(s)
            elif PH['ExamPlayCountIntervalAfter'] in phases:
                n = s.phase_count.get(PH['ExamPlayCountIntervalAfter'], 0)
                v = (tr.get('phaseValues') or [1])[0] or 1
                if n >= 1 and n % v == 0 and triggers.trigger_field_valid(self, tr, ctx, target_cards=[card]):
                    out.append(s)
        return out

    def card_enchant_play_valid(self, tr, se, card, ctx):
        """卡附魔版 IsCardPlayValidTrigger：ExamCardPlay，或出牌计数（卡附魔自己的计数）≥1 且整除。"""
        ph = tr.get('phaseTypes') or []
        if not triggers.target_search_ok(self, tr, card):
            return False
        if PH['ExamCardPlay'] in ph:
            return triggers.trigger_field_valid(self, tr, ctx, target_cards=[card])
        for p in (PH['ExamPlayCountInterval'], PH['ExamPlayTurnCountInterval']):
            if p in ph:
                return triggers.interval_trigger(self, tr, p, difftrig.card_phase_count(se, p), ctx)
        return False

    def card_enchant_play_after_valid(self, tr, se, card, ctx):
        ph = tr.get('phaseTypes') or []
        if not triggers.target_search_ok(self, tr, card):
            return False
        if PH['ExamCardPlayAfter'] in ph:
            return triggers.trigger_field_valid(self, tr, ctx, target_cards=[card])
        if PH['ExamPlayCountIntervalAfter'] in ph:
            return triggers.interval_trigger(self, tr, PH['ExamPlayCountIntervalAfter'],
                                             difftrig.card_phase_count(se, PH['ExamPlayCountIntervalAfter']), ctx)
        return False

    def use_play_count_buff(self, card):
        """UsePlayCountBuff：按创建顺序第一个检索符合的 PlayCountBuffStatusEffect，_count − 1（用完移除），返回追加发动次数 _value。"""
        for s in list(self.status.effects):
            if s.cls == 'PlayCountBuffStatusEffect':
                sid = s.extra.get('_searchId')
                if not sid or is_search_target(card, self.master.search(sid)):
                    s.extra['_count'] = s.extra.get('_count', 1) - 1
                    if s.extra['_count'] <= 0:
                        self.status.RemoveStatusEffect(s)
                    return s.value
        return 0

    def _after_play(self, card, ctx, consume=()):
        """UseCardAfterCheck → MovePlayCard：出牌后附魔在检查时判定、扣次数，但它们的效果命令排在队尾（AddExecuteCommand），
        所以在这张卡移到弃牌 / 除外之后才执行（实机 hif-live-resumed-01 R1 T4：先 men-2_052 入除外，再随机除外 acc）。"""
        self.exam_card_play_count += 1
        self.turn_card_play_count += 1
        card.play_count += 1
        self.play_log.append(card)
        after = self.fire(self.card_play_after_enchants(card, ctx))
        after += difftrig.card_enchant_cmds(self, lambda c, tr, se: self.card_enchant_play_after_valid(tr, se, card, ctx), [card])
        self.insert([('call', lambda: self._move_played(card, list(consume) + list(after)))])

    def _move_played(self, card, after=()):
        """MovePlayCardCommand：出牌区 → 弃牌 / 除外，随后 GetInsertEffectResultTriggerCommand（移牌触发插到队首，
        先于出牌后附魔的效果）。"""
        if self.playing is card:
            self.playing = None
        if card.support_upgrades:
            card.support_upgrades = []
            card.invalidate()
        lost = card.play_move == MOVE['Lost']
        def mv():
            if self.zone_of(card) is not None:   # 出牌中已被自己的效果移走（例如スパート 温存时保留）：不再移到弃牌 / 除外
                return
            self.zones['lost' if lost else 'grave'].append(card)
            self.note_diff('move', cards=[card], pos=MOVE['Lost'] if lost else MOVE['Grave'])
        front = self.with_result_triggers(mv, Ctx(self, card=card), insert=False)
        self.insert(front + list(after) + [('call', self._after_move)])

    def _after_move(self):
        # 追加行动（PlayableValueAdd）在出牌结束时结算
        pv = self.status.first('PlayableValueAddStatusEffect')
        if pv is not None and pv.value > 0:
            pv.value -= 1
            self.remaining_actions += 1
            if pv.value <= 0:
                self.status.RemoveStatusEffect(pv)
        if self.remaining_actions <= 0:
            self.end_turn(skipped=False)

    # ------------------------------------------------------------------ 指针
    def suppress_triggers(self, fn):
        """ctx.IsDiffDataEnchantTriggerDisable 窗口：窗口内产生的变化不发结果触发（从本命令的差分里扣掉）。"""
        pre = difftrig.observe(self)
        fn()
        post = difftrig.observe(self)
        if self._suppressed is not None:
            self._suppressed.append((pre, post))

    def _count(self, key, n=1):
        self.counters[key] = self.counters.get(key, 0) + n

    def _inc_stance_change_count(self, ctx):
        """<TryInternalSetStance>g__IncrementStanceChangeCount：变更次数 +1；附魔触发有效时阶段 36/35 计数
        （IncrementEnchantPhaseCount：状态附魔 + 场面条件成立的卡附魔），再给触发含 35/36 的卡附魔各自 +1。
        卡附魔因此对 35/36 计两次：两处都写 ProduceCardStatusEffect 的 PhaseCountData（字典，计数），
        路径上无分支；GetInsertEffectResultTriggerCommand b__12 用 GetPhaseCount(35) 判定。"""
        self._count('stanceChangeCount')
        if ctx is None or ctx.IsEnchantTriggerActive():
            phases = [PH['ExamStanceChangeCount'], PH['ExamStanceChangeCountInterval']]
            self.IncrementEnchantPhaseCount(phases, ctx)
            if not CARD_STANCE_COUNT_DOUBLE:
                return
            for c in difftrig.deck_all(self):
                se, tr = difftrig.card_enchant_trigger(self, c)
                if se and se.get('_id'):
                    tr = self.master.trigger(se.get('_produceExamTriggerId'))
                    if any(p in ((tr or {}).get('phaseTypes') or []) for p in phases):
                        difftrig.inc_card_phase_count(se, PH['ExamStanceChangeCount'])
                        difftrig.inc_card_phase_count(se, PH['ExamStanceChangeCountInterval'])

    def SetStance(self, stance, step, ctx=None, fp_consume=False):
        """ExamEffectUtility.TryInternalSetStance（SetStance = fp_consume False）。返回是否通过锁判定。"""
        sc, S = self.status, self.setting
        cur, cur_step = sc.GetIdolStatusType(), sc.GetIdolStatusStep()
        if cur == stance and sc.IsIdolStatusIsStepMax():
            return False
        if stance == 2 and cur == 4:
            return False
        if sc.GetStanceLock(True) > 0:
            return False
        if stance == 3 and sc.GetStanceLockFullPowerEffectTurn(True) > 0:
            return False
        if stance == 1 and sc.GetStanceLockConcentrationEffectTurn(True) >= 1:
            return False
        if stance in (2, 4) and sc.GetStanceLockPreservationEffectTurn(True) > 0:
            return False
        active = ctx is None or ctx.IsEnchantTriggerActive()
        if stance == 3 and fp_consume:
            sc.ConsumeFullPowerPoint(10)
        if cur in (2, 4) and stance not in (2, 4):
            self.suppress_triggers(lambda: self._preservation_release(cur, cur_step, stance, ctx))
        if stance == 4:
            if cur not in (2, 4):
                self._inc_stance_change_count(ctx)
                self._count('stancePreservationChangeCount')
                if active:
                    self.IncrementEnchantPhaseCount([PH['ExamStanceChangePreservationInterval']], ctx)
            sc.idol_type, sc.idol_step = 4, 1
        elif stance == 2:
            new_step = min(step + (1 if cur == 2 else 0), 2)
            if cur not in (2, 4):
                self._inc_stance_change_count(ctx)
                self._count('stancePreservationChangeCount')
                if active:
                    self.IncrementEnchantPhaseCount([PH['ExamStanceChangePreservationInterval']], ctx)
            sc.idol_type, sc.idol_step = 2, new_step
        elif stance == 3:
            self._inc_stance_change_count(ctx)
            self._count('stanceFullPowerChangeCount')
            if active:
                self.IncrementEnchantPhaseCount([PH['ExamStanceChangeFullPowerInterval']], ctx)
            sc.idol_type, sc.idol_step = 3, 1
            self.AddPlayableValueAdd(S['fullPowerPlayableValueAdd'], ctx)
        elif stance == 1:
            new_step = min(step + (1 if cur == 1 else 0), 2)
            if cur != 1:
                self._inc_stance_change_count(ctx)
                self._count('stanceConcentrationChangeCount')
                if active:
                    self.IncrementEnchantPhaseCount([PH['ExamStanceChangeConcentrationInterval']], ctx)
            sc.idol_type, sc.idol_step = 1, new_step
        elif stance == 0:
            sc.idol_type, sc.idol_step = 0, 0
        self.log('stance', type=sc.idol_type, step=sc.idol_step, frm=cur)
        return True

    def _preservation_release(self, cur, cur_step, stance, ctx):
        """温存 / のんびり 解除（切到非温存系时）：追加出牌、固定元気、熱意 1 回合；のんびり→全力 改为全部卡 Lesson+。"""
        from .effects import AddBlockFix
        sc, S = self.status, self.setting
        if cur == 4:
            self.AddPlayableValueAdd(S['overPreservationReleasePlayableValueAdd'], ctx)
            blk = S['overPreservationReleaseBlockAdd']
        elif cur_step == 1:
            self.AddPlayableValueAdd(S['preservationReleasePlayableValueAdd1'], ctx)
            blk = S['preservationReleaseBlockAdd1']
        else:
            self.AddPlayableValueAdd(S['preservationReleasePlayableValueAdd2'], ctx)
            blk = S['preservationReleaseBlockAdd2']
        if blk:
            AddBlockFix(self, blk, ctx)
        before = sc.GetEnthusiasticBuff(False)
        if cur == 4 and stance == 3:
            v = S['overPreservationReleaseToFullPowerGrowEffectLessonAdd']
            if v:
                from .grow import is_valid, GT
                g = {'id': 'over_preservation_release', 'effectType': GT['LessonAdd'], 'value': v}
                mult = sc._multiple('GrowEffectLessonAddAdditiveStatusEffect')
                for c in difftrig.deck_all(self):
                    if is_valid(c, g):
                        c.add_grow(g, mult)
            return
        base = (S['overPreservationReleaseEnthusiastic'] if cur == 4 else
                S['preservationReleaseEnthusiastic1'] if cur_step == 1 else S['preservationReleaseEnthusiastic2'])
        add = sc.GetEnthusiasticAdditiveBuff(True)
        mul = sc.GetEnthusiasticMultipleStatusEffectMultiple(True)
        v = base if add == 0 and abs(mul - 1.0) < 1e-6 else f32.ceil_i(f32.mul(f32.i2f(add + base), mul))
        if v and sc.TryAddEnthusiasticStatus(v, 1, ctx):
            self._count('enthusiasticGetSumCount', sc.GetEnthusiasticBuff(False) - before)

    def AddPlayableValueAdd(self, n, ctx=None):
        """ExamEffectUtility.AddPlayableValueAdd（引擎里追加行动在出牌结束时结算）。"""
        if n:
            self.status.TryAddPlayableValueAddStatus(n, ctx)

    def UnsetFullPower(self, ctx=None):
        """无条件回到无指针（不查锁、不计次数）。"""
        self.status.idol_type, self.status.idol_step = 0, 0
        self.log('stance', type=0, step=0, frm=3)

    def full_power_hold_to_hand(self, ctx=None):
        """全力时保留卡回手：前 min(handLimit − 手牌数, 2) 张保留卡 MoveCard → Hand（FullPowerEffectExecutor / 回合抽牌）。"""
        n = min(self.setting['handLimit'] - len(self.zones['hand']), 2)
        if n > 0 and self.zones['hold']:
            self.MoveCard(list(self.zones['hold'][:n]), MOVE['Hand'], ctx)

    def AddGrowEffects(self, cards, grow_ids, ctx=None, valid_check=True):
        """AddGrowEffectExecutor：每张卡先按 IsValidCardGrowEffect 过滤（以加之前的状态判定），
        再 CardPhaseStatusAffect 逐条 ExamCardData.AddGrowEffect。没有响应卡成长的触发。"""
        from .grow import is_valid
        grows = [dict(self.master.grow_effect(g)) for g in grow_ids]
        plan = [(c, [g for g in grows if not valid_check or is_valid(c, g)]) for c in cards]
        mult = self.status._multiple('GrowEffectLessonAddAdditiveStatusEffect')
        for c, gs in plan:
            for g in gs:
                c.add_grow(g, mult)
            if gs:
                self.log('grow', guid=c.guid, ids=[g['id'] for g in gs])

    def use_pool(self, card, consume_cost=False):
        """强制出牌（UsePool）：不消耗行动数（待核），按 isConsumeCost 付费。"""
        if self.zone_of(card) is None:
            return
        self.remove_card(card)
        self.zones['hand'].append(card)
        actions = self.remaining_actions
        self.remaining_actions += 1   # UsePool 不消耗行动数，也不因行动用完而结束回合
        self.play(card)
        self.remaining_actions = actions

    def drink(self, index):
        d = self.drinks.pop(index)
        row = self.master.drink(d)
        ctx = Ctx(self, drink=d)
        self.log('drink', drink=d)
        cmds = []
        for key in row['produceDrinkEffectIds']:
            de = self.master.drink_effect(key)
            if de.get('produceExamEffectId'):
                cmds.append(('effect', self.master.effect(de['produceExamEffectId']), ctx))
        self.run(cmds)

    def step(self, action):
        self.advance()
        if action[0] == 'play':
            self.play(self.zones['hand'][action[1]])
        elif action[0] == 'drink':
            self.drink(action[1])
        elif action[0] == 'end':
            self.end_turn(skipped=True)

    # ------------------------------------------------------------------ 回合
    def start_turn(self):
        self.current_turn += 1
        self.remain_turn = self.limit_turn + self.extra_turn - self.current_turn + 1
        self.turn_card_play_count = 0
        self.turn_used_supports = []
        # ExamParameterModel.ClearTurnState（待核：清零项与时点；这四项没有规则读取，对账不比较）
        for k in ('currentTurnTotalConsumeStamina', 'currentTurnTotalBlock', 'currentTurnTotalAddParameter', 'totalDrawCardCount'):
            if k in self.counters:
                self.counters[k] = 0
        for s in self.status.effects:   # 本回合出牌计数清零（ClearCountByPhaseType，待核时点）
            if isinstance(s, TriggerEffect) and PH['ExamPlayTurnCountInterval'] in s.phase_count:
                s.phase_count[PH['ExamPlayTurnCountInterval']] = 0
        self.remaining_actions = 1
        if self.current_turn == 1:
            # SpendInitialTurn：只对触发含 TurnTimer / TurnInterval / EndTurnTimer / EndTurnInterval 的附魔 TurnCount+1
            tp = {PH['ExamTurnTimer'], PH['ExamTurnInterval'], PH['ExamEndTurnTimer'], PH['ExamEndTurnInterval']}
            for t in self.status.effects:
                if isinstance(t, TriggerEffect) and tp & set(t.trigger.get('phaseTypes') or []):
                    t.IncrementTurnCount()
        self.log('turn_start', bonus=self.bonus_permil.get(self.current_parameter_type()))
        # ExamLoop TurnStart：全力在下一回合开始解除（回到无指针），全力值 ≥10 则扣 10 再进入全力
        base = Ctx(self)
        def stance_start():
            if self.status.GetIdolStatusType() == 3:
                self.UnsetFullPower(base)
            if self.status.GetFullPowerPoint() >= 10:
                self.SetStance(3, 1, base, fp_consume=True)
        self.run(self.with_result_triggers(stance_start, base, insert=False))
        if self.current_turn >= 2:
            for c in self.cards_at(POS['DeckAll']):     # 全部卡(DeckAll).SpendTurn()（卡的指定消耗等）
                c.SpendTurn()
        self.run_gimmick()
        # TurnStartAfter：ExamStartTurn(4) 附魔 + 卡附魔，执行完才发牌
        st = self.phase_enchants(PH['ExamStartTurn'], base)
        self.run(self.fire(st) + difftrig.card_enchant_cmds(self, lambda c, tr, se: triggers.phase_trigger(self, tr, PH['ExamStartTurn'], base)))
        # TurnStartDraw：触发列表在发牌前算好 —— [StartExamPlay(仅 T1), StartPlay, TurnTimer, TurnInterval(T≥2)]
        enchants = []
        if self.current_turn <= 1:
            enchants += self.phase_enchants(PH['StartExamPlay'], base)
        enchants += [t for t in self.phase_enchants(PH['StartPlay'], base) if t not in enchants]
        enchants += [t for t in self.timer_enchants(PH['ExamTurnTimer']) if t not in enchants]
        if self.current_turn >= 2:
            enchants += [t for t in self.interval_enchants(PH['ExamTurnInterval']) if t not in enchants]
        card_phases = ([PH['StartExamPlay']] if self.current_turn <= 1 else []) + [PH['StartPlay']]
        def card_start(c, tr, se):
            if any(triggers.phase_trigger(self, tr, p, base) for p in card_phases):
                return True
            v = (tr.get('phaseValues') or [0])[0]
            if PH['ExamTurnTimer'] in (tr.get('phaseTypes') or []) and v == self.current_turn:
                return triggers.trigger_field_valid(self, tr, base)
            return False
        # 发牌：GetDrawCount = max(0, turnStartDistribute − 回合开始抽牌数减少)；第 1 回合 SetInitialCard
        draw = max(0, self.setting['turnStartDistribute'] - self.status.GetStartTurnDrawDown())
        def turn_draw():
            if self.current_turn < 2 and self.initial_deal:
                self.SetInitialCard(draw, base)
            else:
                got = self.DrawCard(draw)
                if got and self.status.HasPanicStatus():
                    self.queue_after_draw.append(('call', self.panic))
            if self.status.GetIdolStatusType() == 3:
                self.full_power_hold_to_hand(base)
        self.queue_after_draw = []
        self.run(self.with_result_triggers(turn_draw, base, insert=False))
        self.run(self.queue_after_draw)
        cmds = self.fire(enchants)
        cmds += difftrig.card_enchant_cmds(self, card_start)
        self.run(cmds)
        self.remove_timer_triggered(PH['ExamTurnTimer'])
        if self.current_turn <= 1:
            self.remove_exam_start_triggered()
        self.status.SetPassingTurnStartAll()
        for c in self.cards_at(POS['DeckAll']):
            c.SetPassingTurnStart()

    def panic(self):
        """パニック命令（ExecuteCommandImplAsync，DisplayClass111_1.b__9）：手牌每张卡
        integer(0, len(候选)) 抽 produceExamPanicStaminaCandidates，SetTemporaryStaminaConsumptionFix(值, count 0, turn 1)。"""
        cand = self.setting['produceExamPanicStaminaCandidates']
        for c in list(self.zones['hand']):
            c.SetTemporaryStaminaConsumptionFix(cand[self.rng.integer(0, len(cand))], 0, 1)
        self.log('panic', cards=[c.card_id for c in self.zones['hand']])

    def setup_gimmicks(self, groups):
        """ExamGimmickModel.Setup / UpdateGimmickEffect：对每个回合 1..limitTurn，
        候选 = 回合条件成立（startTurn==回合；remainingTurn==上限−回合+1；remainingTurnPermil/1000 ≤ 回合/上限；为 0 的条件不看）
        且未用过；按 priority 升序取第一个排进该回合；全部候选标记为已用。"""
        used = set()
        self.gimmick_schedule = {}
        limit = self.limit_turn
        for turn in range(1, limit + 1):
            cand = []
            for i, g in enumerate(groups):
                if i in used:
                    continue
                if g.get('startTurn') and g['startTurn'] != turn:
                    continue
                if g.get('remainingTurn') and g['remainingTurn'] != limit - turn + 1:
                    continue
                if g.get('remainingTurnPermil') and not f32.permil(g['remainingTurnPermil']) <= f32.div(f32.i2f(turn), f32.i2f(limit)):
                    continue
                cand.append((i, g))
            if cand:
                self.gimmick_schedule[turn] = min(cand, key=lambda x: x[1].get('priority', 0))[1]   # OrderBy 稳定
                used.update(i for i, _ in cand)

    def run_gimmick(self):
        """TurnStart：本回合的机关组，场面条件成立则以机关身份执行效果（IsPlayingGimmick：不产生结果触发）。"""
        g = self.gimmick_schedule.get(self.current_turn)
        if not g:
            return
        tr = None
        if g.get('fieldStatusType'):
            tr = {'id': '', 'phaseTypes': [], 'fieldStatusTypes': [g['fieldStatusType']], 'fieldStatusValues': [g.get('fieldStatusValue', 0)],
                  'fieldStatusCheckTypes': [g.get('fieldStatusCheckType', 0)],
                  'fieldStatusProduceCardSearchIds': [g.get('fieldStatusProduceCardSearchId', '')]}
        c = Ctx(self, gimmick=g['id'])
        if tr is None or triggers.trigger_field_valid(self, tr, c):
            self.log('gimmick', id=g['id'])
            self.run([('effect', self.master.effect(g['produceExamEffectId']), c)])

    def remove_exam_start_triggered(self):
        """RemoveExamStartTriggeredStatus：第 1 回合主阶段前删掉触发阶段只含 ExamStartExam / StartExamPlay 的附魔。"""
        only = {PH['ExamStartExam'], PH['StartExamPlay']}
        for t in list(self.status.effects):
            if isinstance(t, TriggerEffect):
                ph = set(t.trigger.get('phaseTypes') or [])
                if ph and ph <= only:
                    self.status.RemoveStatusEffect(t)

    def SetInitialCard(self, draw, ctx=None):
        """ExamCardMoveController.SetInitialCard：牌堆底→顶扫初始卡入手，超出手牌上限的接到牌堆底；
        两批入手（初始卡、补抽）各自判支援强化（结果触发里按 HandAdd 顺序）。"""
        limit = self.setting['handLimit']
        to_hand, overflow = [], []
        for c in list(reversed(self.zones['deck'])):
            if not c.is_initial:
                continue
            self.zones['deck'].remove(c)
            if len(self.zones['hand']) < limit:
                self.zones['hand'].append(c)
                to_hand.append(c)
            else:
                overflow.append(c)
        self.zones['deck'].extend(overflow)
        if to_hand:
            self.log('draw', cards=[c.card_id for c in to_hand], effect=False, initial=True)
            self.note_diff('hand_add', cards=list(to_hand), frm=POS['Deck'], etype=ET['ExamCardDraw'])
            if ctx is None or ctx.IsEnchantTriggerActive():
                self.support_upgrade(to_hand)
        if len(self.zones['hand']) < draw:
            to_hand += self.DrawCard(draw - len(self.zones['hand']), ctx)
        return to_hand

    def end_turn(self, skipped):
        if skipped:
            self.AddStamina(self.setting['examTurnEndRecoveryStamina'])
            self.run(self.fire(self.phase_enchants(PH['ExamTurnSkip'])))
        enchants = self.phase_enchants(PH['ExamEndTurn'])
        if self.current_turn >= 2:
            enchants += [t for t in self.interval_enchants(PH['ExamEndTurnInterval']) if t not in enchants]
        enchants += [t for t in self.timer_enchants(PH['ExamEndTurnTimer']) if t not in enchants]
        self.run(self.fire(enchants))
        # 手牌入弃牌（ResetHand）：回合末除外的卡进除外
        for c in list(self.zones['hand']):
            self.zones['hand'].remove(c)
            if c.support_upgrades:
                c.support_upgrades = []
                c.invalidate()
            self.zones['lost' if c.end_turn_lost else 'grave'].append(c)
        # 好印象回合末得分
        review = self.status.GetReview()
        if review >= 1:
            v = f32.ceil_i(f32.mul(self.status.GetReviewMultiple(), f32.f(float(review))))
            n = 1 + self.status.GetReviewCountAdd()
            synth = dict(id='', effectType=ET['ExamLesson'], effectValue1=v, effectValue2=0, effectCount=1, effectTurn=-1)
            src = self.status.first('ReviewStatusEffect')
            self.log('review_score', value=v, times=n)
            self.run([('effect', synth, Ctx(self, source='review'))] * n)
        # RemoveCountLimitTriggeredStatus（回合结束命令末尾）：有次数上限、次数 ≤ 0、且不是 P 道具直接附魔的附魔移除
        for t in list(reversed(self.status.effects)):
            if isinstance(t, TriggerEffect) and t.IsCountLimited and t.limit_count <= 0 and not t.source.get('item'):
                self.status.RemoveStatusEffect(t)
        # 回合末衰减不写游戏日志：留一份衰减前的数值供回放比对（replay.engine_values）
        self.pre_spend = dict(hp=self.stamina, genki=self.block, score=self.score,
                              aggressive=self.status.GetAggressive(), review=self.status.GetReview())
        self.remaining_actions = 0
        if self.current_turn >= self.limit_turn + self.extra_turn:
            # 最后一回合：不做 SpendTurn，剩余回合 0（实机结果存档：好印象未减、附魔 TurnCount 未加）
            self.remain_turn = 0
            self.ended = True
            return
        self.remove_timer_triggered(PH['ExamEndTurnTimer'])
        self.status.SpendTurn()
        self.pending_start = True   # 下一回合在下一次动作前开始（advance）；流程等价，观测点与游戏日志的分段对齐

    def advance(self):
        if getattr(self, 'pending_start', False) and not self.ended:
            self.pending_start = False
            self.start_turn()
