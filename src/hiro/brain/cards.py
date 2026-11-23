"""卡牌与饮料的决策视图：完整效果文本 + 从文本解析出的标签。

KAA 的 effect_display_text 会省掉持续回合数与使用条件（如「元気が30以上の場合、使用可」），
这里改用 produce_descriptions 拼出完整文本，再用正则打标签。标签只用于规则层的硬约束和给 LLM 的提示，
效果数值仍以文本为准。
"""

import json
import re
from dataclasses import asdict, dataclass, field
from functools import cache, lru_cache  # noqa: F401


def select(sql: str, *args):
    """主数据单行查询（hiro.master.rows）；拿不到主数据时返回 None。"""
    from hiro import master
    from hiro.state.catalog import CatalogUnavailable

    try:
        return next(iter(master.rows(sql, *args)), None)
    except CatalogUnavailable:
        return None


_TAG = re.compile(r"<[^>]+>")


def description_text(raw) -> str:
    """produce_descriptions（JSON 字符串或列表）→ 可读文本。"""
    descs = json.loads(raw) if isinstance(raw, str) else (raw or [])
    out = ""
    for d in descs:
        piece = _TAG.sub("", d.get("text") or "")
        # 「元気+2」「4ターン後」这类相邻数字段直接拼接会变成「元気+24ターン後」
        if out and piece and out[-1].isdigit() and piece[0].isdigit():
            out += " "
        out += piece
    return out.strip()


@dataclass
class CardTags:
    clears_genki: bool = False  # 元気を0にする：终结牌，会清掉后续持续得分的来源
    halves_genki: bool = False
    once: bool = False  # 用后除外（レッスン中1回 / Lost）
    extra_actions: int = 0  # スキルカード使用数追加
    draws: int = 0  # スキルカードを引く / N枚引く（含「次のターン」的预约抽牌）
    adds_turn: bool = False  # ターン追加
    sustained_turns: int | None = None  # 以降のNターンの間、ターン開始時、元気の…分得分（None 表示没有）
    permanent_boost: bool = False  # パラメータ上昇量増加N%，没有回合数＝整场有效
    permanent_trigger: bool = False  # レッスン終了まで、ターン開始時…
    delayed_turns: int | None = None  # Nターン後、…（预约爆发）
    makes_sleep: bool = False  # 眠気を…生成
    is_sleep: bool = False  # 眠気本身
    genki_score_pct: int = 0  # 打出时立刻按元气 N% 得分（きらきら紙吹雪 110、届いて！ 400…）；持续 / 延迟 / 附魔的不算
    impression_score_pct: int = 0  # 打出时立刻按好印象 N% 得分（みんな大好き 90…）
    score_condition: str | None = (
        None  # 即时得分的附加条件原文，例如「除外以外にあるトラブルカードが2枚以上」（がむしゃら）
    )
    score_condition_troubles: int | None = None  # 条件为「…トラブルカードが N 枚以上」时的 N
    genki_min: int | None = None  # 使用条件「元気がN以上の場合、使用可」


def parse_tags(name: str, text: str, *, once: bool = False) -> CardTags:
    t = CardTags(once=once or "レッスン中1回" in text)
    t.clears_genki = "元気を0にする" in text
    t.halves_genki = "元気を半分" in text
    t.extra_actions = sum(int(n) for n in re.findall(r"スキルカード使用数追加\+?(\d+)", text))
    t.draws = sum(int(n) for n in re.findall(r"スキルカードを(\d+)枚引く", text)) + len(
        re.findall(r"スキルカードを引く", text)
    )
    t.adds_turn = "ターン追加" in text
    # 只认按元气得分的持续效果；夢色リップ 的「ターン開始時、好印象1.1倍」不算
    if m := re.search(r"以降の(\d+)ターンの間、ターン開始時、元気の", text):
        t.sustained_turns = int(m.group(1))
    t.permanent_boost = bool(re.search(r"パラメータ上昇量増加\d+%(?!（\d+ターン）)", text))
    # 到考试结束、每回合开始触发：「レッスン終了まで、ターン開始時」或「以降、ターン開始時」（エウレカ！）；「以降のNターンの間」是持续
    t.permanent_trigger = "レッスン終了まで、ターン開始時" in text or "以降、ターン開始時" in text
    if m := re.search(r"(\d+)ターン後、", text):
        t.delayed_turns = int(m.group(1))
    t.makes_sleep = bool(re.search(r"眠気を.*生成", text))
    t.is_sleep = name.startswith("眠気")
    if m := re.search(r"元気が(\d+)以上の場合、使用可", text):
        t.genki_min = int(m.group(1))
    for m in re.finditer(r"(元気|好印象)の(\d+)%分パラメータ上昇", text):
        before = text[: m.start()]
        if any(k in before[-25:] for k in ("ターン開始時", "ターン後", "スキルカード使用時")):
            continue  # 持续 / 延迟 / 出牌附魔，不是打出时立刻得分
        if m.group(1) == "元気":
            t.genki_score_pct = max(t.genki_score_pct, int(m.group(2)))
        else:
            t.impression_score_pct = max(t.impression_score_pct, int(m.group(2)))
        if c := re.search(r"([^\d+]*?が\d+枚?以上)の場合、$", before):
            t.score_condition = c.group(1)
            if n := re.search(r"トラブルカードが(\d+)枚以上", c.group(1)):
                t.score_condition_troubles = int(n.group(1))
    return t


@dataclass
class CardView:
    """一张手牌在决策层里的样子。slot 是从左到右的手牌位置。"""

    slot: int
    name: str
    card_id: str | None
    upgrade: int
    available: bool
    category: str  # active / mental / trouble
    cost: int  # 普通费用：先扣元气，不够再扣体力
    hp_cost: int  # 体力消費：直接扣体力
    extra_cost: dict | None  # 好印象消費 / やる気消費 等
    text: str
    tags: CardTags = field(default_factory=CardTags)
    badge_score: int | None = None  # 手牌左上角游戏给的预计得分（截图读到才有；None = 没有徽章或没读全）

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tags"] = {k: v for k, v in asdict(self.tags).items() if v}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CardView":
        """to_dict 的逆：GameState.exam 里的 JSON 手牌 → CardView（tags 只存了非空项，其余取默认）。"""
        d = dict(d)
        return cls(**d | {"tags": CardTags(**(d.get("tags") or {}))})


_CATEGORY = {
    "ProduceCardCategory_ActiveSkill": "active",
    "ProduceCardCategory_MentalSkill": "mental",
    "ProduceCardCategory_Trouble": "trouble",
}


def card_view(slot: int, card, *, available: bool) -> CardView:
    """卡（hiro.master.Card，或考试手牌 / 选卡页里 KAA 给的 SkillCard，属性同名）→ CardView。card 为 None 时返回未识别占位。"""
    if card is None:
        return CardView(slot, "（未识别）", None, 0, available, "unknown", 0, 0, None, "")
    text = description_text(card.produce_descriptions) or (card.effect_display_text or "")
    once = "Lost" in str(card.play_move_position_type or "")
    extra = {"type": card.cost_type, "value": card.cost_value} if card.cost_value else None
    return CardView(
        slot=slot,
        name=card.name,
        card_id=card._id,
        upgrade=card.upgrade_count or 0,
        available=available,
        category=_CATEGORY.get(str(card.category or ""), "unknown"),
        cost=card.stamina or 0,
        hp_cost=card.force_stamina or 0,
        extra_cost=extra,
        text=text,
        tags=parse_tags(card.name, text, once=once),
    )


@dataclass
class DrinkView:
    slot: int
    name: str
    drink_id: str | None
    text: str

    @property
    def repeats_card(self) -> bool:
        # 复制饮料：下一张主动卡效果再发动一次
        return "効果をもう1回発動" in self.text or "もう一度発動" in self.text

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DrinkView":
        return cls(**d)


@cache
def _drink_text(drink_id: str) -> str:
    row = select("SELECT produceDescriptions FROM ProduceDrink WHERE id = ?;", drink_id)
    return description_text(row[0]) if row else ""


def drink_view(slot: int, drink) -> DrinkView:
    """饮料（hiro.master.Drink）→ DrinkView。"""
    return DrinkView(slot, drink.name, drink.id, _drink_text(drink.id))
