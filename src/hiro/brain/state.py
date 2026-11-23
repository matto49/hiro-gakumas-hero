"""考试决策的状态：一帧画面的快照（BattleState）+ 本场考试的记忆（ExamMemory）。

画面只能读到当前数值；「持续效果还剩几次」「道具还剩几次」「本回合已出过什么」只能靠记忆推出来。
这些推断恰好是两次失败的根源（提前清空元气、复制饮料用错时机），所以由代码维护，不让 LLM 从历史里自己数。
"""

import math
import re
from dataclasses import asdict, dataclass, field

from hiro.brain.cards import CardView, DrinkView


@dataclass
class IdolProfile:
    """角色构筑里决策要用、但画面上读不到的常量。

    最大体力不在这里：它随偶像卡等级、开花、账号等级、支援卡等浮动，每局开考时从画面读（ExamMemory.max_hp）。"""

    idol_id: str
    name: str
    playbook: str | None  # prompts/idols/<playbook>.md；None = 没有专门的打法文件（profile_from_master 生成的偶像）
    item_trigger_card: str | None = None  # 触发固有道具的卡 ID 前缀
    item_charges: int = 0
    item_needs_high_hp: bool = False  # 道具要求 HP≥50% 时才触发
    # 已知的最大体力（模式家族 → 值），只在开考时培育页读不到上限时兜底（exec.exam.seed_max_hp，来源记 profile_known）。
    # 和账号、卡等级相关：只填验证过的。读到画面就以画面为准
    known_max_hp: dict = field(default_factory=dict)

    @staticmethod
    def is_half_hp(hp: int, max_hp: int | None) -> bool | None:
        """HP≤50%（固有被动得分的条件）；最大体力未知时 None（不猜）。"""
        if hp is None or not max_hp:
            return None
        return hp * 2 <= max_hp


CAMPUS_HIRO = IdolProfile(
    idol_id="i_card-shro-3-007",
    name="Campus mode!! 広",
    playbook="campus_hiro",
    item_trigger_card="p_card-02-men-1_031",
    item_charges=5,
    item_needs_high_hp=True,
    known_max_hp={
        "hif": 33
    },  # Windows 账号 1 的 H.I.F（PR #5 合并前写死的值；Mac 账号 2 初是 30，所以初 / N.I.A 不填）
)

IDOLS = {p.idol_id: p for p in [CAMPUS_HIRO]}


@dataclass
class Installed:
    """已安装、还会在后续回合开始时触发的效果。"""

    name: str
    kind: str  # sustained（N 回合）/ permanent（到考试结束）/ delayed（N 回合后一次）
    turns_left: int | None  # sustained/delayed 剩余触发次数；permanent 为 None
    note: str = ""


@dataclass
class ExamMemory:
    exam: str  # qualify-1/2/3、r1、r2、lesson
    turn: int = 0  # 已开始的回合序号，从 1 起
    last_remaining: int | None = None
    installed: list[Installed] = field(default_factory=list)
    item_charges_left: int | None = None
    used_once: list[str] = field(default_factory=list)
    this_turn: list[str] = field(default_factory=list)  # 本回合已执行的动作描述
    history: list[str] = field(default_factory=list)  # 「T3 打 デイドリーミング+」之类，只留最近若干条
    plan: list[dict] = field(default_factory=list)  # LLM 给出、尚未执行完的本回合计划
    expected_hand: list[str] | None = None  # 执行完上一个动作后预期的手牌；对不上就重新规划
    actions_left: int = 1  # 本回合剩余行动数估算：基础 1 次 + 已知追加行动
    blocked: list[str] = field(default_factory=list)  # 本回合执行不了的动作（画面定位不到），换回合清空
    last_score: int | None = None  # 最近一次采信的分数读数（考试中分数只增不减，用来识别 OCR 掉位）
    repeat_pending: bool = False  # 喝了复制饮料（下一张主动卡效果再发动一次），还没打主动卡
    repeat_target: str | None = None  # 复制饮料要接的那张主动卡（喝之前的计划里的下一张主动卡）
    attr_permil: dict = field(default_factory=dict)  # 本场考试见过的各属性回合倍率（千分比）：{Vo: 34990, …}
    turn_genki: dict = field(default_factory=dict)  # 每回合第一次看到的元气：{剩余回合（字符串）: 元气}，用来估元气增速
    # 最大体力：开考时取培育页头部 OCR 读到的「体力 x/上限」（hiro.vision.pages），随卡等级 / 开花 / 账号等级 / 支援卡浮动，
    # 不写死。考试画面只显示当前体力；看到当前体力比它大时说明读数过时，按看到的抬高（下限）。读不到就是 None。
    max_hp: int | None = None
    max_hp_source: str | None = None
    hp_over: int | None = None  # 上一帧看到的、超过最大体力的当前体力（连续两帧一致才抬上限）

    def set_max_hp(self, value: int | None, source: str) -> bool:
        """记下最大体力（> 0 才算）。返回是否采用。"""
        if not value or int(value) <= 0:
            return False
        self.max_hp, self.max_hp_source = int(value), source
        return True

    def observe_hp(self, hp: int | None) -> str | None:
        """考试中看到的当前体力。比记下的最大体力还大：最大体力读数过时 / 读错，连续两帧读到同一个数才抬到当前体力（协调 11-20：
        单帧 OCR 读错会把半血线永久抬高），返回说明。"""
        if hp is None or self.max_hp is None or hp <= self.max_hp:
            self.hp_over = None
            return None
        if hp > self.max_hp * 3 // 2:  # 远超上限：多半是 OCR 多读了一位（39 → 391），不采信，免得半血线永久跑偏
            self.hp_over = None
            return f"考试中体力读数 {hp} 远超最大体力 {self.max_hp}，按读错处理，最大体力不变"
        if self.hp_over != hp:
            self.hp_over = int(hp)
            return f"考试中体力读数 {hp} 超过最大体力 {self.max_hp}，等下一帧确认"
        old = self.max_hp
        self.max_hp, self.max_hp_source, self.hp_over = int(hp), "exam_hp_floor", None
        return f"考试中体力连续两帧都是 {hp}，超过记下的最大体力 {old}（读数过时），最大体力按 {hp} 算"

    def genki_gain_per_turn(self, remaining: int | None) -> float | None:
        """最近两回合（回合开始时）元气的平均增量；读数不够时 None。清零后的下降不算增长（按 0）。"""
        if remaining is None or str(remaining) not in self.turn_genki:
            return None
        now = self.turn_genki[str(remaining)]
        for k in (2, 1):
            before = self.turn_genki.get(str(remaining + k))
            if before is not None:
                return max(0.0, (now - before) / k)
        return None

    def check_score(self, score: int | None) -> tuple[int | None, str | None]:
        """分数合理性：考试中分数只增不减。比上一次采信的读数还小，多半是 OCR 掉位（37811→3771），不采信。"""
        if score is None:
            return None, None
        if self.last_score is not None and score < self.last_score:
            return (
                None,
                f"分数读数 {score} 比上一次的 {self.last_score} 还小（考试中分数不会下降），疑为识别掉位，按未识别处理",
            )
        self.last_score = score
        return score, None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExamMemory":
        d = dict(d)
        d["installed"] = [Installed(**e) for e in d.get("installed") or []]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def block(self, action: str) -> None:
        if action not in self.blocked:
            self.blocked.append(action)

    def observe_turn(self, remaining: int | None) -> bool:
        """根据剩余回合数判断是否进入了新回合；新回合时推进持续效果的计数。返回是否换回合。"""
        if remaining is None:
            return False
        new_turn = self.last_remaining is None or remaining < self.last_remaining
        # 加回合卡会让剩余回合数变大，此时不算新回合
        self.last_remaining = remaining
        if not new_turn:
            return False
        if self.turn > 0:
            for e in self.installed:
                if e.kind in ("sustained", "delayed") and e.turns_left is not None:
                    e.turns_left -= 1
            self.installed = [e for e in self.installed if e.turns_left is None or e.turns_left > 0]
        self.turn += 1
        self.this_turn = []
        self.plan = []
        self.expected_hand = None
        self.actions_left = 1
        self.blocked = []
        return True

    def record_play(self, card: CardView, *, hp_before: int | None, profile: IdolProfile | None) -> None:
        t = card.tags
        if t.sustained_turns:
            self.installed.append(Installed(card.name, "sustained", t.sustained_turns))
        if t.permanent_trigger:
            self.installed.append(Installed(card.name, "permanent", None, note=card.text))
        if t.delayed_turns:
            self.installed.append(Installed(card.name, "delayed", t.delayed_turns))
        if t.once:
            self.used_once.append(card.name)
        if self.repeat_pending and card.category == "active":
            self.repeat_pending, self.repeat_target = False, None  # 复制效果被这张主动卡用掉
        self.actions_left += t.extra_actions - 1
        if (
            profile
            and profile.item_trigger_card
            and card.card_id
            and card.card_id.startswith(profile.item_trigger_card)
            and self.item_charges_left
            and (not profile.item_needs_high_hp or IdolProfile.is_half_hp(hp_before, self.max_hp) is False)
        ):
            self.item_charges_left -= 1
            self.actions_left += 1  # 固有道具的追加行动
        self._log(f"打 {card.name}")

    def record_drink(self, drink: DrinkView) -> None:
        if "スキルカード使用数追加" in drink.text:
            self.actions_left += 1
        if drink.repeats_card:
            self.repeat_pending = True
            nxt = next((s.get("card") for s in self.plan if s.get("action") == "play" and s.get("card")), None)
            self.repeat_target = nxt or self.repeat_target
        self._log(f"喝 {drink.name}")

    def record_skip(self) -> None:
        self._log("跳过回合")

    def _log(self, what: str) -> None:
        self.this_turn.append(what)
        self.history.append(f"T{self.turn} {what}")
        del self.history[:-30]

    def pending_genki_output(self, remaining: int | None) -> list[Installed]:
        """本回合之后还会按元气得分的已安装效果。清空元气会让它们落空。"""
        if remaining is not None and remaining <= 1:
            return []
        out = []
        for e in self.installed:
            if e.kind == "permanent" or (e.turns_left or 0) > 0:
                out.append(e)
        return out


@dataclass
class BattleState:
    """一次决策看到的全部信息。None 表示这一帧没读到，不是 0。"""

    exam: str
    turn: int
    remaining_turns: int | None
    hp: int | None
    max_hp: int | None
    genki: int | None
    hand: list[CardView]
    drinks: list[DrinkView] = field(default_factory=list)
    motivation: int | None = None  # やる気
    impression: int | None = None  # 好印象
    score: int | None = None
    buffs: dict = field(default_factory=dict)  # 其余状态栏条目，键为状态名
    target_score: int | None = None  # 本场需要的分数（对手/过线），已知时填
    public_info: str | None = None  # 其他公开信息（牌堆/弃牌组成等），读到时填
    action_previews: list[dict] | None = None  # 本地模拟器对每个合法动作的预演（hiro.sim.preview.prompt_block）
    item_charges_left: int | None = None
    actions_left: int | None = None
    installed: list[Installed] = field(default_factory=list)
    this_turn: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # 本回合画面定位不到、执行不了的动作（不进合法动作）
    # HIF_EXAM 契约字段（docs/contracts.md）；None = 这一帧没读到
    attr: str | None = None  # 本回合属性 Vo / Da / Vi
    multiplier_permil: int | None = None  # 本回合属性倍率（千分比）
    npc_scores: list | None = None  # 对手分数（画面从左到右）
    my_rank_index: int | None = None  # 自己的名次（1 起）
    deck_count: int | None = None  # 山札张数（H.I.F 画面上没有，截图来源恒为 None）
    pitem_charges: int | None = None  # P 道具图标上的剩余发动次数（截图读数，重启后也可靠）
    statuses: list | None = None  # 状态栏 [{type, name, value, unit}]
    source: str = "screen"  # 局面来源：save（本地存档）/ screen（截图识别）
    repeat_pending: bool = False  # 复制效果待生效：下一张主动卡效果再发动一次
    repeat_target: str | None = None  # 复制效果应该接的牌（计划里的下一张主动卡）
    score_note: str | None = None  # 分数读数被判为不可信时的说明
    max_hp_note: str | None = None  # 最大体力读数被考试中的体力推翻时的说明
    item_trigger_card: str | None = None  # 固有道具的触发牌 ID 前缀（打出时可能追加行动）
    item_needs_high_hp: bool = False
    final_turn_permil: int | None = (
        None  # 最后一回合的属性倍率（千分比）：存档 / 模拟器给出，或按 流1 从本场见过的倍率推
    )
    now_turn_permil: int | None = None  # 本回合倍率（模拟器来源；画面来源用 multiplier_permil）
    genki_gain: float | None = None  # 最近每回合元气增量（ExamMemory.genki_gain_per_turn）
    badge_notes: list = field(default_factory=list)  # 判为读错、改用公式的徽章
    attr_permils: dict = field(default_factory=dict)  # 本场见过的三属性倍率（千分比）
    trend: tuple | None = None  # 偶像的属性流向（流1, 流2, 流3）：末三回合固定为 流3 → 流2 → 流1
    piles: dict | None = (
        None  # 公开牌堆：{deck: [卡名], grave: [卡名], draw: 每回合抽牌数}（存档 / 模拟器来源；截图来源为 None）
    )

    def gives_action(self, card: CardView) -> bool:
        """打出这张牌后行动数不减：自带使用数追加，或会触发固有道具（えいえいおー，HP≥50% 且有剩余次数）。"""
        if card.tags.extra_actions:
            return True
        if (
            self.item_trigger_card
            and card.card_id
            and card.card_id.startswith(self.item_trigger_card)
            and self.item_charges_left
        ):
            return not self.item_needs_high_hp or (self.half_hp is False)
        return False

    @property
    def copy_pending(self) -> bool:
        """复制效果待生效：记忆里喝过复制饮料，或状态栏里有「スキルカード追加発動」（重启后记忆丢失时以画面为准）。"""
        return self.repeat_pending or any("追加発動" in str(k) for k in (self.buffs or {}))

    def half_line(self) -> int | None:
        """半血线：HP ≤ 它算「体力 50% 以下」（エウレカ 回合开始触发、固有道具要求 HP 高于它）。"""
        return self.max_hp // 2 if self.max_hp else None

    def item_trigger_in_hand(self) -> list[CardView]:
        return [
            c
            for c in self.hand
            if c.available and self.item_trigger_card and c.card_id and c.card_id.startswith(self.item_trigger_card)
        ]

    def eureka_check(self) -> dict | None:
        """エウレカ（回合开始 HP ≤ 50% 时按元气得分并回血）已装时：下回合开始会不会触发、还有哪些扣血手段、道具次数够不够。
        第 3 局复盘：道具 5 次早早用完，最后三回合 HP 一直是 20，エウレカ 没触发（约 11k）。"""
        installed = any(e.kind == "permanent" and "体力が50%以下" in e.note for e in self.installed)
        installed = installed or any("エウレカ" in str(k) for k in (self.buffs or {}))  # 重启后记忆丢失时以状态栏为准
        if not installed or self.hp is None or not self.max_hp:
            return None
        line = self.half_line()
        drains = [f"{c.name}（体力 -{c.hp_cost}）" for c in self.hand if c.available and c.hp_cost > 0]
        if self.item_charges_left and self.hp > line:
            drains += [
                f"{c.name}（HP>{line} 时触发道具，体力 -{max(1, self.max_hp * 15 // 100)}，用掉 1 次道具）"
                for c in self.item_trigger_in_hand()
            ]
        out = {"触发条件": f"回合开始 HP ≤ {line}", "现在 HP": self.hp, "不再扣血的话下回合开始 HP": self.hp,
               "下回合会触发": self.hp <= line, "能扣血的手段": drains or None,
               "道具剩余次数": self.item_charges_left}  # fmt: skip
        if self.remaining_turns is not None and self.remaining_turns > 1 and self.item_charges_left is not None:
            out["道具预算"] = (
                f"剩余 {self.remaining_turns} 回合、道具 {self.item_charges_left} 次：道具次数的用途是把 HP 压到半血线以下让エウレカ触发；"
                "这次用了能让下回合进半血区就用，压不下去时才省着（最后 4 回合尽量还剩 2 次）"
                if self.remaining_turns > 4
                else "已进最后 4 回合：道具次数用来把 HP 压到半血线以下"
            )
        return out

    def troubles_in_hand(self) -> int:
        return sum(1 for c in self.hand if c.tags.is_sleep or c.category == "trouble")

    def cash_out_check(self) -> list[dict] | None:
        """手里能把元气 / 好印象换成分数的牌（即时 / 延迟 / 持续），条件是否满足、预计得分、剩余回合来不来得及。
        预计得分 = 元气（或好印象）× 比例 × 本回合倍率，不含パラメータ上昇量増加，实际只会更高。"""
        mult = self.multiplier_permil / 1000 if self.multiplier_permil else None
        rem = self.remaining_turns
        out = []
        for c in self.hand:
            t = c.tags
            if t.genki_score_pct or t.impression_score_pct:
                res, pct = ("元気", t.genki_score_pct) if t.genki_score_pct else ("好印象", t.impression_score_pct)
                amount = self.genki if res == "元気" else self.impression
                row = {"card": c.name, "slot": c.slot, "类型": "即时兑现", "按": f"{res}的{pct}%"}
                if c.badge_score is not None:
                    row["预计得分"], row["来源"] = c.badge_score, "游戏徽章（以它为准）"
                elif amount is None:
                    row["预计得分"] = f"未知（{res}未识别）"
                elif mult:
                    row["预计得分"] = math.ceil(amount * pct / 100 * mult)
                else:
                    row["预计得分"] = f"约 {amount * pct // 100} × 本回合倍率"
                ok = True
                if t.genki_min is not None and self.genki is not None and self.genki < t.genki_min:
                    row["条件"], ok = f"元気≥{t.genki_min} 才能用，现在 {self.genki}", False
                if t.score_condition_troubles is not None:
                    k = self.troubles_in_hand()
                    if k >= t.score_condition_troubles:
                        row["条件"] = f"{t.score_condition}：满足（手里已有 {k} 张眠気）"
                    else:
                        row["条件"] = f"{t.score_condition}：手里只看到 {k} 张，牌堆 / 弃牌里的看不到，可能满足"
                if t.clears_genki:
                    row["清空元气"] = True
                row["现在能打"] = bool(c.available and ok)
                out.append(row)
            elif t.delayed_turns and "元気の" in c.text:
                fits = None if rem is None else rem - 1 >= t.delayed_turns
                row = {"card": c.name, "slot": c.slot, "类型": f"延迟兑现（{t.delayed_turns} 回合后）",
                       "来得及": fits if fits is not None else "剩余回合未知"}  # fmt: skip
                if fits:
                    row.update(self._delayed_timing(c, t.delayed_turns))
                out.append(row)
            elif t.sustained_turns:
                times = None if rem is None else min(t.sustained_turns, max(0, rem - 1))
                row = {"card": c.name, "slot": c.slot, "类型": f"持续兑现（之后 {t.sustained_turns} 回合的回合开始时）",
                       "还能触发": times if times is not None else "剩余回合未知"}  # fmt: skip
                pct = re.search(r"ターン開始時、元気の(\d+)%分", c.text)
                if times and pct and self.genki is not None and mult:
                    row["按现在元气的预计总得分"] = math.ceil(times * self.genki * int(pct.group(1)) / 100 * mult)
                    row["说明"] = "元气之后还会涨，实际更高；元气还低、离末回合还远时，晚点装覆盖的元气更多"
                if pct and rem is not None and self.genki is not None:
                    row.update(self._sustained_timing(c, t.sustained_turns, int(pct.group(1))))
                out.append(row)
        return out or None

    def turn_permil(self, rem: int) -> int | None:
        """剩 rem 回合（含本回合）那一回合的属性倍率：末三回合固定 流3 → 流2 → 流1（剩 3 / 2 / 1，knowledge.IDOL_TREND），
        更早的回合属性随机，返回 None。"""
        if not self.trend or rem not in (1, 2, 3):
            return None
        return (self.attr_permils or {}).get(self.trend[rem - 1])

    def _turn_label(self, rem: int) -> str:
        p = self.turn_permil(rem)
        return f"剩 {rem}（{self.trend[rem - 1]} {p / 1000:g}×）" if p else f"剩 {rem}（属性随机）"

    def _sustained_timing(self, card: CardView, turns: int, pct: int) -> dict:
        """持续兑现（デイドリーミング+：以降の 4 ターン、ターン開始時 元気の 60%）有回合数上限：太早装，触发都落在元気还低的回合。
        按元気每回合线性增长（genki_gain）和各回合倍率（末三回合已知，其余按本场倍率均值），比较「现在装」和「等 w 回合再装」。
        第八局 R2：剩 8 回合、元気 54 时就装上，四次触发都在前段。"""
        rem, g = self.remaining_turns, self.genki
        gain = self.genki_gain or 0.0
        known = [v for v in (self.attr_permils or {}).values() if v]
        avg = sum(known) / len(known) if known else (self.multiplier_permil or self.now_turn_permil)
        if not avg or rem is None or rem <= 1:
            return {}

        def value(w: int) -> float:
            total = 0.0
            for k in range(w + 1, min(w + turns, rem - 1) + 1):  # 第 k 回合后的回合开始时触发
                total += (g + gain * k) * pct / 100 * (self.turn_permil(rem - k) or avg) / 1000
            return total

        options = {w: value(w) for w in range(max(1, rem - 1))}
        best = max(options, key=lambda w: options[w])
        out = {"现在装的预计总得分": math.ceil(options[0]),
               "按元気增速": f"每回合约 +{gain:g}" if gain else "未知（按不增长算，会低估晚装的收益）"}  # fmt: skip
        if best > 0 and options[best] > options[0] * 1.15:
            out["更好的装备时机"] = (
                f"再等 {best} 回合（剩 {rem - best} 回合时装），预计 {math.ceil(options[best]):,}（现在装 {math.ceil(options[0]):,}）"
            )
            if self.piles is not None:
                out["等到那时再抽到的概率"] = round(
                    redraw_chance(self.piles, card.name, [(c.name, c.tags.once) for c in self.hand], best), 2
                )
        return out

    def _delayed_timing(self, card: CardView, delay: int) -> dict:
        """延迟兑现（せのびの魔法：4 回合后按元气结算）的时机：最晚剩 delay+1 回合时打，正好在最后一回合开始结算。
        早于最晚回合时，留着要在那之前再抽到它。第 5 局本战 R1（9 回合）剩 6 回合时按「剩 5 回合再打」留着，之后没抽到，整场没结算。"""
        rem = self.remaining_turns
        last = delay + 1
        out = {
            "现在打的结算时机": f"剩 {rem - delay} 回合开始时",
            "最晚打出": f"剩 {last} 回合（结算在最后一回合开始）",
        }
        # 结算落在哪个属性回合（第八局 R2：剩 7 回合打 せのび，结算落在 Vi 20.24×，而最后一回合是 Vo 35.19×）
        now_p, last_p = self.turn_permil(rem - delay), self.turn_permil(1)
        out["现在打的结算回合"] = self._turn_label(rem - delay) if rem - delay >= 1 else "考试结束前结算不到"
        out["最晚打的结算回合"] = self._turn_label(1)
        reach = [s for s in range(max(1, rem - delay), 0, -1) if self.turn_permil(s)]
        if reach:
            best = max(reach, key=lambda s: self.turn_permil(s))
            out["倍率最高的结算回合"] = f"{self._turn_label(best)}：要在剩 {best + delay} 回合时打"
            if now_p and last_p and now_p < self.turn_permil(best):
                out["倍率比"] = (
                    f"晚打结算在 {self._turn_label(best)}，倍率是现在结算回合的 {self.turn_permil(best) / now_p:.2f} 倍（元気也更多）"
                )
        wait = rem - last
        if wait <= 0:
            out["留着"] = "这已经是最晚一回合，不打就结算不到"
            return out
        out["留到最晚要再抽到它的回合数"] = wait
        if self.piles is not None:
            p = redraw_chance(self.piles, card.name, [(c.name, c.tags.once) for c in self.hand], wait)
            out["留到最晚前再抽到的概率"] = round(p, 2)
        else:
            out["留到最晚前再抽到的概率"] = "未知（读不到牌堆）"
        if wait <= 2:
            out["说明"] = (
                f"只剩 {wait} 回合的抽牌机会；抽不回来就整场不结算，而现在打只比最晚早 {wait} 回合结算，再抽到的机会不大时现在就打"
            )
        else:
            out["说明"] = f"现在打要比最晚早 {wait} 回合结算，按的元气少得多；剩余回合还多，牌组会循环回来，一般留着"
        return out

    @property
    def half_hp(self) -> bool | None:
        if self.hp is None or not self.max_hp:
            return None
        return self.hp * 2 <= self.max_hp

    def legal_actions(self) -> list[str]:
        acts = [f"play:{c.slot}" for c in self.hand if c.available and c.card_id]
        acts += [f"drink:{d.slot}" for d in self.drinks]
        acts.append("skip")
        return [a for a in acts if a not in self.blocked]

    def finisher_check(self) -> list[dict] | None:
        """手里每张清空元气的终结牌：现在打的话会落空什么、下回合 HP 会在哪。"""
        out = []
        last = self.remaining_turns is not None and self.remaining_turns <= 1
        for c in self.hand:
            if not c.tags.clears_genki:
                continue
            hp_after = self.hp - c.hp_cost if self.hp is not None else None
            lost = []
            for e in self.installed:
                if last:
                    break
                if e.kind in ("sustained", "delayed") and (e.turns_left or 0) > 0:
                    lost.append(f"{e.name}（还剩 {e.turns_left} 次）")
                elif e.kind == "permanent":
                    if "体力が50%以下" in e.note and hp_after is not None and self.max_hp:
                        if hp_after * 2 <= self.max_hp:
                            lost.append(f"{e.name}（下回合半血会按元气得分）")
                    else:
                        lost.append(f"{e.name}（常驻效果，是否按元气得分需看文本）")
            row = {
                "card": c.name,
                "badge_score": c.badge_score,
                "last_turn": last,
                "pending_genki_output": lost,
                "hp_after": hp_after,
                "half_hp_next_turn": (hp_after * 2 <= self.max_hp) if hp_after is not None and self.max_hp else None,
            }  # fmt: skip
            if not last:
                row.update(self._hold_cost(c, lost))
            out.append(row)
        return out or None

    def _hold_cost(self, card: CardView, lost: list[str]) -> dict:
        """非末回合：这张终结牌留着不打，之后还能不能再拿到；清空元气实际会损失多少（第 4 局本战：R1 两次、R2 一次手握 届いて！
        （徽章 2.3–3.4 万）留到最后一回合，最后一回合都没抽到，几百元气作废）。"""
        out = {}
        future = (self.remaining_turns or 0) - 1
        if self.piles is not None and self.remaining_turns is not None:
            p = redraw_chance(self.piles, card.name, [(c.name, c.tags.once) for c in self.hand], future)
            out["redraw_chance"] = round(p, 2)
            out["redraw_note"] = (f"不打的话，这张牌回合结束进弃牌堆；之后 {future} 回合按正常抽牌（每回合 {self.piles.get('draw', 3)} 张，"
                                  f"山札 {len(self.piles['deck'])} 张、弃牌 {len(self.piles['grave'])} 张）再抽到它的概率约 {p:.0%}"
                                  "（用后除外的牌按会被打掉算，没算抽牌牌）")  # fmt: skip
        else:
            out["redraw_chance"] = "未知（读不到牌堆）"
        cur = self.multiplier_permil or self.now_turn_permil
        if cur and self.final_turn_permil:
            ratio = self.final_turn_permil / cur
            out["final_turn_ratio"] = round(ratio, 2)
            if card.badge_score is not None:
                out["final_turn_value_est"] = math.ceil(card.badge_score * ratio)
                out["final_turn_note"] = (f"最后一回合倍率是本回合的 {ratio:.2f} 倍：留到最后一回合再打，按现在的元气约 {out['final_turn_value_est']:,}"
                                          "（元气还会涨，实际更高；前提是那时它在手里）")  # fmt: skip
        gain = self.genki_gain
        if gain is not None and self.genki:
            growth = (self.genki + gain * future) / self.genki
            out["genki_growth_est"] = round(growth, 2)
            out["genki_growth_note"] = (
                f"最近每回合元气约 +{gain:.0f}：照这个速度，最后一回合元气约 {self.genki + gain * future:.0f}（现在 {self.genki}）"
            )
        mult = self.multiplier_permil / 1000 if self.multiplier_permil else None
        pct = sum(_genki_pct(e.note) * (future if e.kind == "permanent" else min(e.turns_left or 0, future))
                  for e in self.installed if e.kind != "delayed")  # fmt: skip
        if lost and pct:
            out["keep_genki_pct"] = pct  # 留着的话已装效果最多再按元气结算的总百分比（与终结牌的元气 N% 同单位）
        if lost and pct and self.genki is not None and mult:
            out["keep_value_upper"] = math.ceil(self.genki * pct / 100 * mult)
            out["keep_value_note"] = ("保留元气让已装效果继续结算的上限（按本回合倍率、每回合都触发、元气不再增长；エウレカ 只在回合开始半血时触发），"
                                      "清空后元气还会重新堆起来，实际损失更小")  # fmt: skip
        return out

    CORE = ("remaining_turns", "hp", "genki", "motivation", "impression", "score")

    def unknown_fields(self) -> list[str]:
        return [n for n in self.CORE if getattr(self, n) is None]

    def to_prompt_dict(self) -> dict:
        """给 LLM 的精简视图：只留决策相关字段，去掉空值。"""
        d = {
            "exam": self.exam,
            "turn": self.turn,
            "remaining_turns_including_this": self.remaining_turns,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "half_hp_now": self.half_hp,
            "genki": self.genki,
            "motivation": self.motivation,
            "impression": self.impression,
            "score": self.score,
            "target_score": self.target_score,
            "buffs": self.buffs or None,
            "public_info": self.public_info,
            "item_charges_left": self.item_charges_left,
            "actions_left_estimate": self.actions_left,
            "action_budget": self.action_budget(),
            "badge_misread": self.badge_notes or None,
            "finisher_check": self.finisher_check(),
            "cash_out": self.cash_out_check(),
            "eureka_check": self.eureka_check(),
            "copy_pending": (
                f"复制效果待生效：下一张主动卡效果再发动一次，应接 {self.repeat_target}"
                if self.repeat_target
                else "复制效果待生效：下一张主动卡效果再发动一次，接价值最高的那张"
            )
            if self.copy_pending
            else None,
            "score_note": self.score_note,
            "max_hp_note": self.max_hp_note,
            "installed_effects": [asdict(e) for e in self.installed] or None,
            "done_this_turn": self.this_turn or None,
            "recent_history": self.history[-12:] or None,
            "hand": [
                {k: v for k, v in c.to_dict().items() if k not in ("card_id", "upgrade") and v not in (None, {}, "")}
                for c in self.hand
            ],
            "drinks": [{"slot": d.slot, "name": d.name, "text": d.text} for d in self.drinks] or None,
            "turn_attr": self.attr,
            "turn_multiplier": f"{self.multiplier_permil / 1000:g}×" if self.multiplier_permil else None,
            "opponent_scores": self.npc_scores or None,
            "my_rank": self.my_rank_index,
            "deck_count": self.deck_count,
            "pitem_charges": self.pitem_charges,
            "unknown": self.unknown_fields() or None,
            "legal_actions": self.legal_actions(),
            "blocked_this_turn": [f"{a}（画面上定位不到，本回合不能执行）" for a in self.blocked] or None,
            "action_previews": self.action_previews,
        }
        out = {k: v for k, v in d.items() if v is not None}
        unknown = self.unknown_fields()
        for n in unknown:  # 没读到的核心字段显式标出来，不能省略（省略容易被当成 0 或「没有」）
            out[_PROMPT_NAME.get(n, n)] = UNKNOWN
        if unknown:
            out["unknown"] = f"{'、'.join(unknown)} 这一帧没有识别出来，值未知（不是 0），判断时不要假设它们的数值"
        if ({"motivation", "impression"} & set(unknown)) and status_icons_missing():
            out["effect_names_unavailable"] = (
                "考试效果名整场都读不到（使用者没有自建效果图标 icons/status/，见 docs/assets.md 的 capture_icons）："
                "やる気 / 好印象 的层数一直未知。不要靠它们判断兑现时机或叠层是否够，"
                "优先选不依赖效果层数也稳妥的动作（按卡面效果和剩余回合判断）"
            )
        return out

    @classmethod
    def build(cls, memory: ExamMemory, profile: IdolProfile | None, **observed) -> "BattleState":
        if observed.get("pitem_charges") is not None:
            # P 道具图标上的剩余次数（截图读数，重启后也可靠）为准，记忆里的计数只在读不到时兜底
            memory.item_charges_left = observed["pitem_charges"]
        if observed.get("max_hp"):  # 观测直接给了最大体力（模拟器 / 将来的识别）：以它为准
            memory.set_max_hp(observed.pop("max_hp"), "observed")
        observed.pop("max_hp", None)
        if (note := memory.observe_hp(observed.get("hp"))) is not None:
            observed["max_hp_note"] = note
        if "score" in observed:
            observed["score"], note = memory.check_score(observed["score"])
            observed.setdefault("score_note", note)
        rem, genki = observed.get("remaining_turns"), observed.get("genki")
        if rem is not None and genki is not None:
            memory.turn_genki.setdefault(str(rem), genki)
        known = observed.pop("attr_permil", None)
        attr = observed.get("attr")
        if known:
            # exam.attr_permil（考前说明页一次读全三属性 + 考试页同帧互验，识别-01 提议）为准：不再从同一帧的 (attr, multiplier) 学，
            # 那一对可能错配（reduce 保留旧倍率、attr 是新读的：例如 Vi 配上 Da 的 26980）
            memory.attr_permil.update({k: int(v) for k, v in known.items() if v})
            if attr and memory.attr_permil.get(attr):
                observed["multiplier_permil"] = memory.attr_permil[attr]
        elif attr and observed.get("multiplier_permil"):
            memory.attr_permil[attr] = observed["multiplier_permil"]  # 没有 attr_permil（旧状态 / 模拟器）时才从同帧学
        if observed.get("multiplier_permil") is None and attr and memory.attr_permil.get(attr):
            observed["multiplier_permil"] = memory.attr_permil[attr]
        observed.setdefault("genki_gain", memory.genki_gain_per_turn(rem))
        observed.setdefault("attr_permils", dict(memory.attr_permil))
        if profile and "trend" not in observed:
            from hiro.state.knowledge import idol_trend

            try:  # noqa: SIM105
                observed["trend"] = tuple(idol_trend(profile.idol_id)[1])
            except (KeyError, IndexError):
                pass
        if observed.get("final_turn_permil") is None and profile:
            # 考试最后 3 回合固定为 流3 → 流2 → 流1（knowledge.IDOL_TREND，wiki「ターン割」；第 4 局本战两轮实机为 Vi → Da → Vo）
            from hiro.state.knowledge import idol_trend

            try:  # noqa: SIM105
                observed["final_turn_permil"] = memory.attr_permil.get(idol_trend(profile.idol_id)[1][0])
            except KeyError:
                pass
        return cls(
            exam=memory.exam,
            turn=memory.turn,
            max_hp=memory.max_hp,
            item_charges_left=memory.item_charges_left,
            actions_left=memory.actions_left,
            installed=list(memory.installed),
            this_turn=list(memory.this_turn),
            history=list(memory.history),
            blocked=list(memory.blocked),
            repeat_pending=memory.repeat_pending,
            item_trigger_card=profile.item_trigger_card if profile else None,
            item_needs_high_hp=bool(profile and profile.item_needs_high_hp),
            repeat_target=memory.repeat_target,
            **observed,
        )._check_badges()

    def cash_formula(self, card: CardView) -> int | None:
        """即时兑现牌按公式的预计得分：元気（好印象）× 比例 × 本回合倍率（不含パラメータ上昇量増加，实际只会更高）。"""
        t = card.tags
        pct = t.genki_score_pct or t.impression_score_pct
        amount = self.genki if t.genki_score_pct else self.impression
        permil = self.multiplier_permil or self.now_turn_permil
        if not pct or amount is None or not permil:
            return None
        return math.ceil(amount * pct / 100 * permil / 1000)

    def _check_badges(self) -> "BattleState":
        """徽章读数与公式估算对不上（比公式的一半还小，或大于 4 倍）时按读错处理，改用公式值（第八局 R2 #155：届いて！+ 徽章
        106,83x 少读一位成 10,683，公式约 9.7 万；识别-01：6 位以上的徽章读不全）。公式不含パラメータ上昇量増加，徽章正常应 ≥ 公式。"""
        for c in self.hand:
            f = self.cash_formula(c)
            if c.badge_score is None or not f:
                continue
            if c.badge_score < f / 2 or c.badge_score > f * 4:
                self.badge_notes.append(
                    f"{c.name} 徽章读数 {c.badge_score:,} 与公式估算 {f:,} 对不上（位数读错），按公式"
                )
                c.badge_score = None
        return self

    def action_budget(self) -> dict:
        """本回合还能打几张：耗行动的牌最多 actions_left 张；自带使用数追加 / 触发固有道具的牌不减行动（代码计算，不交给 LLM 猜）。
        第八局 R2 最后一回合：LLM 以为 ゆめみごこち+ → 紙吹雪+ → 届いて！+ 三张都能打，实际只够两张，届いて！+ 没打出去。"""
        n = self.actions_left if self.actions_left is not None else 1
        free = [c.name for c in self.hand if c.available and self.gives_action(c)]
        costly = [c.name for c in self.hand if c.available and not self.gives_action(c)]
        return {"耗行动的牌还能打": n, "不耗行动的牌": free or None, "耗行动的牌": costly or None,
                "说明": (f"耗行动的牌里只能再打 {n} 张" + ("，先打不耗行动的牌" if free else "")) if costly and len(costly) > n else None}  # fmt: skip


def _genki_pct(note: str) -> int:
    m = re.search(r"元気の(\d+)%分", note or "")
    return int(m.group(1)) if m else 0


def _base(name: str) -> str:
    return (name or "").rstrip("+")


def _miss(n: int, k: int, d: int) -> float:
    """n 张里有 k 张目标，无放回抽 d 张一张都没抽到的概率。"""
    if k <= 0 or d <= 0 or n <= 0:
        return 1.0
    d = min(d, n)
    return math.comb(n - k, d) / math.comb(n, d) if n - k >= d else 0.0


def redraw_chance(piles: dict, name: str, hand: list[tuple[str, bool]], future_turns: int) -> float:
    """手里这张牌本回合不打（回合结束进弃牌堆），之后 future_turns 回合按正常抽牌再抽到同名牌的概率。
    hand：本回合手牌 [(卡名, 用后除外)]；piles["once"]：牌堆里用后除外的卡名。
    山札里的同名牌按无放回抽；山札抽空后，弃牌 + 本回合手牌 + 抽出过的山札洗回再抽，其中用后除外的牌按「会被打掉」不算进去
    （bench r2 种子 18：不剔除时估 27%，实际留着再抽到、多拿 15 万）。不算抽牌牌，偏保守。
    只用公开信息：牌堆组成可在游戏里查看，顺序不可见。"""
    if future_turns <= 0:
        return 0.0
    deck, grave = list(piles.get("deck") or []), list(piles.get("grave") or [])
    once = set(piles.get("once") or [])
    draws = future_turns * int(piles.get("draw") or 3)
    key = _base(name)
    n = len(deck)
    k_deck = sum(1 for x in deck if _base(x) == key)
    miss = _miss(n, k_deck, draws)
    left = draws - n
    if left > 0:
        others = [x for x in grave + deck if _base(x) != key] + [h for h, _ in hand if _base(h) != key]
        lost = {h for h, o in hand if o} | once
        pool_others = sum(1 for x in others if x not in lost)
        k = sum(1 for x in grave + deck if _base(x) == key) + 1
        miss *= _miss(pool_others + k, k, left)
    return 1.0 - miss


UNKNOWN = "未识别"


def status_icons_missing() -> bool:
    """识别层没有考试效果图标（开源默认素材下 やる気 / 好印象 等效果名读不到）。识别层没装时按「不缺」处理，不加提示。"""
    try:
        from hiro.vision.assets import available
    except ImportError:
        return False
    try:
        return not available("status")
    except Exception:  # noqa: BLE001
        return False


_PROMPT_NAME = {"remaining_turns": "remaining_turns_including_this"}
EXAM_FIELDS = ("remaining_turns", "hp", "genki", "motivation", "impression", "score", "buffs", "attr", "multiplier_permil",
               "npc_scores", "my_rank_index", "deck_count", "pitem_charges", "statuses")  # fmt: skip
_ATTR = {"vocal": "Vo", "dance": "Da", "visual": "Vi", "Vo": "Vo", "Da": "Da", "Vi": "Vi"}


def exam_kwargs(exam: dict | None) -> dict:
    """GameState.exam（HIF_EXAM 契约字段，JSON）→ BattleState.build 的观测参数。

    只取契约里有的字段，缺的保持 None（不补默认值）。兼容识别层尚未转换的旧格式：attr 为 vocal/dance/visual 时转成 Vo/Da/Vi；
    只有 multiplier_pct 时换算成千分比（×10）。"""
    from hiro.brain.cards import CardView, DrinkView

    exam = exam or {}
    out = {k: exam.get(k) for k in EXAM_FIELDS}
    out["buffs"] = dict(exam.get("buffs") or {})
    out["attr"] = _ATTR.get(exam.get("attr")) if exam.get("attr") else None
    if out["multiplier_permil"] is None and exam.get("multiplier_pct") is not None:
        out["multiplier_permil"] = int(exam["multiplier_pct"]) * 10
    if exam.get("attr_permil"):
        out["attr_permil"] = {_ATTR.get(k, k): v for k, v in exam["attr_permil"].items() if v}
    out["hand"] = [CardView.from_dict(c) for c in exam.get("hand") or []]
    out["drinks"] = [DrinkView.from_dict(d) for d in exam.get("drinks") or []]
    return out


def profile_for(idol: str | None) -> IdolProfile | None:
    """KAA 培育方案里的偶像 ID（可能是 skin ID，如 i_card-skin-shro-3-007）→ 构筑常量。
    手写的构筑表（IDOLS，现在只有 Campus 広）优先；别的偶像按主数据生成（profile_from_master，协调 11-19 多偶像）。"""
    if not idol:
        return None
    cid = idol.replace("i_card-skin-", "i_card-")
    return IDOLS.get(cid) or profile_from_master(cid)


def profile_from_master(idol_card_id: str) -> IdolProfile | None:
    """主数据 IdolCard → 构筑常量。只填主数据给得出的：id、名字（卡名 + 角色）。
    最大体力（带支援卡 / 潜能，账号相关）、固有道具的触发卡 / 次数 / 条件、打法文件都给 None / 0，不猜。查不到这张卡返回 None。"""
    try:
        from hiro.state.context import catalog

        row = catalog().one("IdolCard", idol_card_id)
    except Exception:  # noqa: BLE001  主数据不可用 / 没有这张卡
        return None
    if not row:
        return None
    return IdolProfile(
        idol_id=idol_card_id,
        name=f"{row.get('name') or idol_card_id}（{row.get('characterId') or '?'}）",
        playbook=None,
    )
