"""只有画面读数时，拼 UIObs 需要的、画面上一次看不全的东西：每回合属性、无序牌区、本回合打出的牌。

ScreenTracker 每场考试一个，每个决策点 observe() 一次：
- 牌区：开局 山札 = 培育牌组 − 手牌。之后按手牌变化推：新出现的牌从山札抽（山札里没有 → 弃牌重洗回山札）；
  从手牌消失的牌，是自己刚打出的 → 弃牌（レッスン中1回 → 除外），否则也记弃牌；换回合时剩下的手牌进弃牌。
  画面看不到的移动（检索、保留、生成卡）会让牌区和实机有出入，算法只把它当参考。
- 每回合属性：见过的回合照记；最后三回合按偶像流向（流3 → 流2 → 流1）；其余未知回合按 流1 填。
- 状态栏：ProduceExamEffectType_* → UIObs 状态种类；やる気 / 好印象 用 BattleState 的读数。
- 持续效果（ench）：画面上只有来源卡名。开考时放进偶像自带 P 道具的附魔；自己打出的卡挂的附魔按主数据记上（enchants.Enchants），
  有回合数的每回合减 1；触发次数画面上看不到，按初始值。没给主数据查询时不记。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from hiro.bench_bridge.uiobs import ATTR_TYPE, INF, SCHEMA, STEPS

# 画面状态栏的效果类型 → (UIObs 种类, 写法)：value 数量不限回合；turns 数值即剩余回合；flag [1, 剩余回合]；pct 百分比 → 小数
EFFECT_KIND = {
    "ProduceExamEffectType_ExamCardPlayAggressive": ("aggressive", "value"),
    "ProduceExamEffectType_ExamReview": ("review", "value"),
    "ProduceExamEffectType_ExamLessonBuff": ("lesson_buff", "value"),
    "ProduceExamEffectType_ExamFullPowerPoint": ("full_power_point", "value"),
    "ProduceExamEffectType_ExamEnthusiastic": ("enthusiastic", "value"),
    "ProduceExamEffectType_ExamAntiDebuff": ("anti_debuff", "value"),
    "ProduceExamEffectType_ExamStaminaConsumptionDownFix": ("stamina_consumption_down_fix", "value"),
    "ProduceExamEffectType_ExamParameterBuff": ("parameter_buff", "turns"),
    "ProduceExamEffectType_ExamParameterBuffMultiplePerTurn": ("parameter_buff_multiple", "flag"),
    "ProduceExamEffectType_ExamStaminaConsumptionDown": ("stamina_consumption_down", "flag"),
    "ProduceExamEffectType_ExamStaminaConsumptionAdd": ("stamina_consumption_add", "flag"),
    "ProduceExamEffectType_ExamBlockRestriction": ("block_restriction", "flag"),
    "ProduceExamEffectType_ExamBlockAddDown": ("block_add_down", "flag"),
    "ProduceExamEffectType_ExamStaminaRecoverRestriction": ("stamina_recover_restriction", "flag"),
    "ProduceExamEffectType_ExamGimmickSlump": ("slump", "flag"),
    "ProduceExamEffectType_ExamPanic": ("panic", "flag"),
    "ProduceExamEffectType_ExamGimmickParameterDebuff": ("gimmick_parameter_debuff", "flag"),
    "ProduceExamEffectType_StanceLock": ("stance_lock", "flag"),
    "ProduceExamEffectType_ExamLessonValueMultiple": ("lesson_parameter_multiple", "pct"),
    "ProduceExamEffectType_ExamLessonValueMultipleDown": ("lesson_parameter_multiple_down", "pct"),
    "ProduceExamEffectType_ExamLessonBuffMultiple": ("lesson_buff_multiple", "pct"),
    "ProduceExamEffectType_ExamReviewMultiple": ("review_multiple", "pct"),
    "ProduceExamEffectType_ExamGimmickLessonDebuff": ("lesson_debuff", "value"),
}
RESHUFFLE_AT = 2  # 山札剩这么多张以内时，抽到山札里没有的卡才当作洗牌（每回合抽 3 张，抽空才洗）
UIOBS_KINDS = {k for k, _ in EFFECT_KIND.values()} | {"parameter_buff", "stance_lock", "extra_turn"}


def _status_rows(state: Any) -> list:
    out, seen = [], set()
    for s in getattr(state, "statuses", None) or ():
        t, v, unit = s.get("type"), s.get("value"), s.get("unit")
        if v is None:
            continue
        if t in EFFECT_KIND:
            kind, how = EFFECT_KIND[t]
        elif t in UIOBS_KINDS:  # 已经是 UIObs 种类（bench 自己的转换写进 BattleState 的就是这种）
            kind, how = t, ("turns" if t == "parameter_buff" else "value")
        else:
            continue
        turns = INF
        if isinstance(unit, str) and unit.endswith("T") and unit[:-1].isdigit():
            turns = int(unit[:-1])
        if how == "value":
            if kind in ("aggressive", "review"):
                continue  # 用 BattleState 的 motivation / impression
            out.append([kind, float(v), turns])
        elif how == "turns":
            out.append([kind, float(v), int(v)])
        elif how == "flag":
            n = int(v) if unit in ("turns", None) else turns
            out.append([kind, 1.0, n if n else INF])
        elif how == "pct":
            out.append([kind, float(v) / 100.0, turns])
        seen.add(kind)
    if getattr(state, "motivation", None):
        out.append(["aggressive", float(state.motivation), INF])
    if getattr(state, "impression", None):
        out.append(["review", float(state.impression), INF])
    return out


def _key(card_id: str, u: int) -> tuple:
    return (card_id, int(u or 0))


class ScreenTracker:
    """一场考试里，靠画面手牌变化推牌区、记每回合属性和本回合打出的牌。

    手牌里的卡会被考试中强化（支援效果）临时 +1，离开手牌就清掉：每张手牌记「看到的强化次数」和「在牌区里的原始强化次数」，
    抽牌按卡 ID 找（强化次数先找相同的，再找最接近的较低 / 较高值），离开手牌按原始强化次数放进弃牌 / 除外。
    """

    def __init__(self, deck: list | None, trend: tuple | None = None, enchants=None, items: list | None = None):
        self.enchants = enchants  # enchants.Enchants（主数据查询）；None 时不记持续效果
        self.ench: list[list] = []  # [附魔 id, 次数, 剩余回合]
        self._item_ench: set = set()  # 偶像自带 P 道具的附魔（次数按画面图标上的数字）
        self._once: set = set()
        if enchants is not None:
            for it in items or ():
                for eid, n, t in enchants.item(it):
                    self.ench.append([eid, n, t])
                    self._item_ench.add(eid)
        self.all = Counter(_key(c[0], c[1]) for c in deck or () if c and c[0])
        self.deck: Counter = Counter(self.all)
        self.grave: Counter = Counter()
        self.lost: Counter = Counter()
        self.trend = tuple(trend or ())
        self.attrs: dict[int, str] = {}
        self.turn: int | None = None
        self.hand: list[list] = []  # [看到的 key, 原始 key]
        self.played: list[dict] = []
        self.extra_plays: dict[int, int] = {}  # 回合 → 确定多出的出牌次数（自己打出的卡给的，见 Enchants.plays_added）
        self.last_play: tuple | None = None
        self.last_play_once = False
        self.started = False
        self.last: dict = {}  # 最近一次读到的画面数值（分数 / 体力 / 元気）
        self.notes: list[str] = []

    @staticmethod
    def _find(pool: Counter, k: tuple) -> tuple | None:
        if pool[k] > 0:
            return k
        same = [x for x, n in pool.items() if n > 0 and x[0] == k[0]]
        if not same:
            return None
        lower = [x for x in same if x[1] < k[1]]
        return max(lower, key=lambda x: x[1]) if lower else min(same, key=lambda x: x[1])

    def _draw(self, k: tuple) -> tuple:
        """抽到 k（看到的）：从山札扣掉对应的原始卡，返回原始 key。
        山札里找不到：山札快抽空（≤ RESHUFFLE_AT 张）时才当作弃牌洗回山札；否则当作看不见的插入（持续效果 / P 道具塞进山札的卡），
        不动弃牌（qualifydmmw1：山札 12 张时把 8 张弃牌倒回去了）。"""
        base = self._find(self.deck, k)
        if base is None and self._find(self.grave, k) is not None and sum(self.deck.values()) <= RESHUFFLE_AT:
            self.deck += self.grave
            self.grave = Counter()
            base = self._find(self.deck, k)
        if base is None:
            if k[0] not in {x[0] for x in self.all}:
                self.notes.append(f"{k[0]} 不在培育牌组里（考试中生成的卡？）")
            return k
        self.deck[base] -= 1
        self.deck = +self.deck
        return base

    def _fact(self, name: str, key: tuple):
        """主数据事实（enchants.Enchants 的方法）；没给主数据或查不到时 False / 空。"""
        if self.enchants is None or not hasattr(self.enchants, name):
            return ()
        try:
            return getattr(self.enchants, name)(key[0], key[1])
        except Exception:  # noqa: BLE001
            return ()

    def _leave(self, base: tuple, once: bool) -> None:
        (self.lost if once else self.grave)[base] += 1

    def observe(self, state: Any, memory: Any) -> None:
        turn = int(getattr(memory, "turn", 0) or 0)
        shown = [_key(c.card_id, c.upgrade) for c in state.hand if c.card_id]
        if getattr(state, "attr", None) and turn:
            self.attrs[turn] = state.attr
        if self.turn is not None and turn != self.turn:
            # 换回合：自己打出的那张按去向放，其余上回合手牌进弃牌
            rest = list(self.hand)
            if self.last_play is not None:
                i = next((j for j, h in enumerate(rest) if h[0] == self.last_play), None)
                if i is not None:
                    self._leave(rest.pop(i)[1], self.last_play_once)
            for h in rest:
                self._leave(h[1], self._fact("end_turn_lost", h[1]))
            self.hand, self.played, self.last_play = [], [], None
            for e in self.ench:  # 有回合数的持续效果每回合减 1
                if e[2] > 0:
                    e[2] -= 1
            self.ench = [e for e in self.ench if e[2] != 0]
        # 先按看到的 key 配对，再按卡 ID 配对（手里被强化 / 弱化过）
        prev = list(self.hand)
        new: list = [None] * len(shown)
        for i, k in enumerate(shown):
            j = next((j for j, h in enumerate(prev) if h is not None and h[0] == k), None)
            if j is not None:
                new[i] = [k, prev[j][1]]
                prev[j] = None
        for i, k in enumerate(shown):
            if new[i] is None:
                j = next((j for j, h in enumerate(prev) if h is not None and h[1][0] == k[0]), None)
                if j is not None:
                    new[i] = [k, prev[j][1]]
                    prev[j] = None
        for h in prev:  # 从手牌消失的
            if h is None:
                continue
            if self.last_play is not None and h[0] == self.last_play:
                self._leave(h[1], self.last_play_once)
                self.last_play = None
            else:
                self._leave(h[1], False)
        for i, k in enumerate(shown):
            if new[i] is None:
                new[i] = [k, self._draw(k)]
        self.hand, self.turn, self.started = new, turn, True
        self.reconcile(state)

    def drank(self, drink_id: str | None) -> None:
        """喝下这瓶饮料：挂上它的附魔。"""
        if self.enchants is None or not drink_id or not hasattr(self.enchants, "drink"):
            return
        try:
            for eid, n, t in self.enchants.drink(drink_id):
                self.ench.append([eid, n, t])
        except Exception:  # noqa: BLE001
            pass

    def reconcile(self, state: Any) -> None:
        """状态栏按来源卡名列出的持续效果：跟踪里没有这张卡的附魔就补上（只补不删：状态栏可能显示不全）。
        P 道具次数用画面读数（state.pitem_charges，图标上的数字）。"""
        if self.enchants is None:
            return
        have = {e[0] for e in self.ench}
        for st in getattr(state, "statuses", None) or ():
            if st.get("type") or not st.get("name") or not hasattr(self.enchants, "enchant_card"):
                continue
            try:
                cid = self.enchants.enchant_card(st["name"])
                if not cid or any(cid in e for e in have):
                    continue
                for eid, n, t, _once in self.enchants.card(cid, 1 if str(st["name"]).endswith("+") else 0):
                    self.ench.append([eid, n, t])
                    have.add(eid)
            except Exception:  # noqa: BLE001
                continue
        charges = getattr(state, "pitem_charges", None)
        if charges is not None:
            for e in self.ench:
                if e[0] in self._item_ench and e[1] >= 0:
                    e[1] = int(charges)

    def plays_left(self) -> int:
        """本回合还能打几张：1 + 确定多出的次数 − 已经打的；至少 1（算少了也不当成 0，GKMS 里剩 0 时回合已经结束，
        UINet 没见过 0）。只给 UINet 当输入，不剪合法动作（2026-11-14 协调定：少算会丢牌，宁可多点一下）。
        from_screen 和规则层的估算（ExamMemory.actions_left：卡面文字的追加、固有道具、饮料）取大的；这里补的是「次のターン」计时。"""
        return max(1, 1 + self.extra_plays.get(int(self.turn or 0), 0) - len(self.played))

    def num(self, key: str, value) -> int:
        """画面数值：读到就记下；这一帧没读出来（None）就用上一次的，不当成 0。"""
        if value is not None:
            self.last[key] = int(value)
        return int(self.last.get(key, 0))

    def played_card(self, card: Any) -> None:
        """自己决定打出这张（下一次 observe 时据手牌变化确认去向）。"""
        k = _key(card.card_id, card.upgrade)
        self.last_play = k
        self.last_play_once = bool(getattr(getattr(card, "tags", None), "once", False)) or bool(
            self._fact("lost_on_play", k)
        )
        for cid, up, n in self._fact("creates", k) or ():  # 打出时往山札塞的卡（眠気 等），画面上看不到
            self.deck[(cid, int(up))] += int(n)
        self.played.append({"id": k[0], "u": k[1]})
        for delay, n in self._fact("plays_added", k) or ():
            t = int(self.turn or 0) + int(delay)
            self.extra_plays[t] = self.extra_plays.get(t, 0) + int(n)
        if self.enchants is not None:
            try:
                for eid, n, t, once in self.enchants.card(k[0], k[1]):
                    if once and (k[0], eid) in self._once:
                        continue
                    self._once.add((k[0], eid))
                    self.ench.append([eid, n, t])
            except Exception:  # noqa: BLE001  主数据查不到：不记
                pass

    def types(self, turn: int, limit: int, allocation: dict | None = None) -> list[int]:
        """每回合属性：见过的照记；末三回合按配额的固定尾（没有配额时按偶像流向 流3 → 流2 → 流1）；
        其余未知回合按配额里还没用掉的属性填（多的先填），没有配额时填 流1。"""
        tail = list((allocation or {}).get("fixed_tail") or ()) or (
            [self.trend[2], self.trend[1], self.trend[0]] if len(self.trend) >= 3 else []
        )
        prefix = max(0, limit - len(tail))
        left = Counter((allocation or {}).get("random_prefix_counts") or {})
        for t in range(1, prefix + 1):
            if t in self.attrs:
                left[self.attrs[t]] -= 1
        out = []
        for t in range(1, limit + 1):
            if t in self.attrs:
                a = self.attrs[t]
            elif t > prefix and tail:
                a = tail[t - prefix - 1]
            elif +left:
                a = max(sorted(+left), key=lambda x: left[x])
                left[a] -= 1
            elif self.trend:
                a = self.trend[0]
            else:
                a = self.attrs.get(turn) or "Vo"
            out.append(ATTR_TYPE.get(a, 1))
        return out


def _cards(c: Counter) -> list:
    return sorted(({"id": k[0], "u": k[1]} for k, n in c.items() for _ in range(n)), key=lambda x: (x["id"], x["u"]))


def _card_ref(text: str) -> tuple[str, int]:
    """题目编成里的卡写法 "p_card-03-act-2_070+1/p_card_custom-…" → (卡 id, 强化)。"""
    head = str(text).split("/", 1)[0]
    cid, _, up = head.partition("+")
    return cid, int(up or 0)


def tower_context(loadout: dict) -> dict:
    """偶像之路这一场的已知量（编成决定的，gkms.exam_loadout.v1）：回合数、三属性倍率、最大体力、偶像、开局牌组。
    画面读不全或读错时（中途重启后回合计数从 1 开始、开考只读到当前属性的倍率、最大体力读不到）以这些为准。"""
    from hiro.bench_bridge.tower import BATTLE_CONFIG

    exam = loadout.get("exam") or {}
    m = BATTLE_CONFIG.match(exam.get("produce_exam_battle_config_id") or "")
    pm = loadout.get("score_bonus_permils")
    if isinstance(pm, dict):
        pm = [pm.get(k, pm.get(int(k) if str(k).isdigit() else k)) for k in ("1", "2", "3")]
    return {
        "turns": int(m["turn"])
        if m
        else (int(loadout["turns"]) if loadout.get("turns") else None),  # 没有 battle_config 时可直接给
        "permil_fixed": {a: int(v) for a, v in zip(("Vo", "Da", "Vi"), pm)} if pm and all(pm) else None,
        "max_stamina": int(loadout["max_stamina"]) if loadout.get("max_stamina") else None,
        "idol": loadout.get("idol_card_id"),
        "deck": _with_initial_cards(
            [_card_ref(c) for c in loadout.get("produce_cards") or ()], loadout.get("idol_card_id")
        ),
    }


def _with_initial_cards(deck: list, idol: str | None) -> list:
    """编成页读到的牌组补上游戏另加的 2 张基本卡（tower.Master.idol_initial_cards）；已经在里面的不重复加（bench 题本来就带）。
    牌组是空的（编成页没读）或主数据不可用时原样返回。"""
    if not deck or not idol:
        return deck
    try:
        from hiro.bench_bridge.tower import master

        extra = master().idol_initial_cards(idol)
    except Exception:  # noqa: BLE001  没有主数据：不补，最多少 2 张
        return deck
    have = {cid for cid, _ in deck}
    return deck + [(cid, 0) for cid in extra if cid not in have]


def from_screen(state: Any, memory: Any, legal: list, tracker: ScreenTracker, exam: dict) -> tuple[dict, list]:
    """画面读数 → UIObs。返回 (UIObs, 饮料下标 → 画面饮料槽位)。legal 是 hiro 的合法动作键（play:i / drink:j / skip）。
    exam 里有 turns（偶像之路编成给的回合数）时：limit 固定、turn = limit − 残りターン + 1（不靠本场计数，中途重启也对）；
    有 permil_fixed 时三属性倍率用它（画面一次只显示当前属性那一项）；max_stamina 读不到时用 exam 的。"""
    turn = max(1, int(getattr(memory, "turn", 0) or 1))
    remaining = getattr(state, "remaining_turns", None)
    if exam.get("turns"):
        limit = int(exam["turns"])
        if remaining:
            turn = max(1, limit - int(remaining) + 1)
    else:
        limit = turn + int(remaining) - 1 if remaining else max(turn, len(tracker.attrs) or turn)
    pm = dict(exam.get("permil_default") or {})  # 开考第一步还没读到倍率时：记下的 / 参考值
    pm.update({k: v for k, v in (getattr(state, "attr_permils", None) or {}).items() if v})
    if exam.get("permil_fixed"):
        pm = dict(exam["permil_fixed"])
    known = [v for v in pm.values() if v]
    permil = [int(pm.get(a) or (min(known) if known else 0)) for a in ("Vo", "Da", "Vi")]
    max_hp = getattr(state, "max_hp", None) or exam.get("max_stamina")
    drinks = sorted(getattr(state, "drinks", None) or (), key=lambda d: d.slot)
    slot_of = [d.slot for d in drinks]
    plays, lg = set(), []
    for key in legal:
        kind, _, arg = str(key).partition(":")
        if kind == "play":
            lg.append(["play", int(arg)])
            plays.add(int(arg))
        elif kind == "drink" and int(arg) in slot_of:
            lg.append(["drink", slot_of.index(int(arg))])
        elif kind in ("skip", "end"):
            lg.append(["end"])
    base = [h[1][1] for h in tracker.hand] if len(tracker.hand) == sum(1 for c in state.hand if c.card_id) else None
    hand = []
    for i, c in enumerate(x for x in state.hand if x.card_id):
        u = c.upgrade if c.upgrade is not None else (base[i] if base else 0)  # 画面读不到强化：用培育牌组里的
        hand.append({"id": c.card_id, "u": int(u or 0), "p": int(c.slot in plays)})
    obs = {
        "v": SCHEMA,
        "exam": {
            **{k: exam.get(k) for k in ("produce", "step", "plan", "idol")},
            "turn": turn,
            "limit": limit,
            "types": [] if exam.get("lesson") else tracker.types(turn, limit, exam.get("allocation")),
            "permil": [0, 0, 0] if exam.get("lesson") else permil,
            "crit": list(exam.get("crit") or [0, 0, 0]),
        },
        "num": {
            "score": tracker.num("score", getattr(state, "score", None)),
            "stamina": tracker.num("stamina", getattr(state, "hp", None)),
            "max_stamina": tracker.num("max_stamina", max_hp),
            "block": tracker.num("block", getattr(state, "genki", None)),
            "playable": max(1, int(getattr(state, "actions_left", None) or 0), tracker.plays_left()),
            "plays": len(tracker.played),
        },
        "stance": [None, 0],
        "status": _status_rows(state),
        "ench": [list(e) for e in tracker.ench],
        "hand": hand,
        "deck": _cards(tracker.deck),
        "grave": _cards(tracker.grave),
        "hold": [],
        "lost": _cards(tracker.lost),
        "played": list(tracker.played),
        "drinks": [d.drink_id for d in drinks],
        "legal": lg,
    }
    return obs, slot_of


MODE_STEPS = {"mid1": "AUDITION_MID1", "mid2": "AUDITION_MID2", "final": "AUDITION_FINAL"}
# 初课程的 step（UIObs 写 ProduceStepType 枚举值，见 vendor/gakumas-player/gakumas_exam/enums.json）：
# 1–9 = LessonVocal/Dance/Visual × Normal / Sp / Hard（Hard = 追い込みレッスン），29–34 = LegendLesson × Normal / Sp（初 LEGEND，produce-006）
LESSON_ATTR = {"vo": 0, "da": 1, "vi": 2}
LEGEND_PRODUCES = {"produce-006"}


def lesson_step_type(rest: str, produce: str | None = None) -> int | None:
    """课程标签的后半段 → ProduceStepType。认两种写法：数字（lesson-32，bench 题）或属性 + 档（lesson-vo / lesson-da-sp /
    lesson-vi-hard，平台从画面读：课程属性、SP 角标）。produce-006（LEGEND）的普通 / SP 课是 29–34。"""
    parts = [x for x in str(rest).split("-")[1:] if x]
    if parts and parts[0].isdigit():
        return int(parts[0])
    if not parts or parts[0] not in LESSON_ATTR:
        return None
    a, kind = LESSON_ATTR[parts[0]], (parts[1] if len(parts) > 1 else "normal")
    if produce in LEGEND_PRODUCES and kind in ("normal", "sp"):
        return 29 + a * 2 + (kind == "sp")
    return 1 + a * 3 + {"normal": 0, "sp": 1, "hard": 2}.get(kind, 0)


MODE_PRODUCE = {
    "hajime": "produce-001",
    "nia": "produce-004",
}  # 缺省难度（初 REGULAR / N.I.A PRO）；编成给了 produce_id 就用编成的


def exam_fields(
    label: str | None, *, idol: str | None, plan: int | None, crit=None, produce: str | None = None
) -> dict:
    """hiro 的考试标签 → UIObs exam 的固定部分。写法都照 bench 给 UINet 的（11-17 扫 bench 逐步记录核过）：
    - qualify-1/2/3（选拔）、r1 / r2（本战）：produce-007 / 008，plan = 偶像 plan 类型 + 1；
    - tower / tower-<层>（偶像之路）：produce "tower"、step 空、plan 0、crit 全 0；
    - hajime-mid1 / hajime-final（初的中期 / 最终考试）、nia-mid1 / nia-mid2 / nia-final（N.I.A 试镜）：step AUDITION_*，
      crit 是这场的审查基准（编成给了就有，没有全 0）；
    - hajime-lesson-<step_type>（初的课程）：step 是课程的 step_type 数字、crit 全 0，另带 lesson=True（types 空、倍率全 0）。"""
    base = str(label or "").split("#", 1)[0].lower()
    if base.startswith("tower"):
        return {"produce": "tower", "step": None, "plan": 0, "idol": idol, "crit": [0, 0, 0]}
    mode = base.split("-", 1)[0]
    if mode in MODE_PRODUCE:
        rest = base.split("-", 1)[1] if "-" in base else ""
        out = {"produce": produce or MODE_PRODUCE[mode], "plan": plan, "idol": idol, "crit": list(crit or [0, 0, 0])}
        if mode == "hajime" and rest.startswith("lesson"):
            return {**out, "step": lesson_step_type(rest, out["produce"]), "crit": [0, 0, 0], "lesson": True}
        return {**out, "step": MODE_STEPS.get(rest, "AUDITION_FINAL")}
    if base.startswith("qualify"):
        n = int(base.rsplit("-", 1)[-1]) if base[-1:].isdigit() else 1
        produce, step = "produce-007", STEPS[min(max(n, 1), 3) - 1]
    else:
        produce, step = "produce-008", STEPS[1] if base == "r2" else STEPS[0]
    return {"produce": produce, "step": step, "plan": plan, "idol": idol, "crit": list(crit or [1, 1, 1])}


def audition_context(label: str, idol: str | None, number: int, produce: str | None = None) -> dict:
    """没有编成、只知道选了第几档时（实机 N.I.A 试镜选档页）：按主数据给这场的审查基准和回合数。"""
    from hiro.bench_bridge import tower as tw
    from hiro.search.exam_env import audition_row

    base = str(label or "").split("#", 1)[0].lower()
    mode, _, rest = base.partition("-")
    row = audition_row(idol, produce or MODE_PRODUCE[mode], rest, number)
    cfg = tw.master().battle_config(row["produceExamBattleConfigId"]) or {}
    return {
        "crit": [int(cfg[k]) for k in ("vocal", "dance", "visual")] if cfg else [0, 0, 0],
        "turns": int(cfg["turn"]) if cfg.get("turn") else None,
        "row": row,
    }


def exam_context(loadout: dict) -> dict:
    """初 / N.I.A 这一场的已知量（编成决定的；bench 题 gkms.hif_loadout.v1，或实机编成拼成的同样写法）：回合数、审查基准、
    三属性倍率、最大体力、偶像、开局牌组、培育 id。画面读不全时以这些为准（同 tower_context）。"""
    from hiro.bench_bridge import tower as tw
    from hiro.search.exam_env import from_hif_loadout

    x = from_hif_loadout(loadout) if loadout.get("deck_cards") is not None else dict(loadout)
    cfg = tw.master().battle_config((x.get("exam") or {}).get("produce_exam_battle_config_id") or "") or {}
    pm = x.get("score_bonus_permils")
    if isinstance(pm, dict):
        pm = [pm.get(k, pm.get(int(k))) for k in ("1", "2", "3")]
    deck = []
    for c in x.get("produce_cards") or ():
        if isinstance(c, dict):
            deck.append(
                (
                    c.get("produce_card_id") or c.get("id"),
                    int(c.get("current_upgrade_count", c.get("upgrade_count")) or 0),
                )
            )
        else:
            deck.append(_card_ref(c))
    return {
        "turns": int(cfg["turn"]) if cfg.get("turn") else None,
        "crit": [int(cfg[k]) for k in ("vocal", "dance", "visual")] if cfg else None,
        "permil_fixed": {a: int(v) for a, v in zip(("Vo", "Da", "Vi"), pm)} if pm and all(pm) else None,
        "max_stamina": int(x["max_stamina"]) if x.get("max_stamina") else None,
        "idol": x.get("idol_card_id"),
        "produce": x.get("produce_id"),
        "deck": deck,
    }
