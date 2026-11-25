"""Observation：识别 → 状态。一帧截图（或一份存档）的识别结果，无状态、可离线重放。

fields 的键是决策层字段名（培育对齐 ProduceState / 客户端协议 UserProduceProgress，考试对齐 BattleState.build 的参数），
由 hiro.vision.observe 产出；读不到的字段值为 None，不出现的字段表示本页面不提供。不猜。
"""

from dataclasses import dataclass, field
from typing import Any

from hiro.contracts.base import OPTIONAL, VERSION, Contract

SOURCES = ("screen", "save", "dataset", "replay", "plugin")


@dataclass
class PageObs:
    """页面判定。name 为 hiro.vision.scene 的场景名（ACTION_SELECT / HIF_EXAM / …，unknown 表示没认出）。"""

    name: str
    area: str = "unknown"  # produce / outside / unknown
    confidence: float = 1.0
    stable_frames: int = 1  # 连续多少帧判为同一页面（采集 / 主循环维护；单帧为 1）
    # 哪一层判出的页面（去 KAA 第 0 步：统计 KAA 出手次数，只记不用）。主循环：quick（快速判页）/ kaa（KAA check_scene）/
    # 我们的补判 interval_title / exam_ocr / pixel / drink_ocr / pitem_ocr / idle_scene / produce_end_ocr / none（都没认出）；
    # classify = Session 自己分类；None = 调用方按上下文直接给了页面（钩子里的观测）
    # 新主循环（hiro.vision.classify）另有：template（自有模板判页，接替 kaa）/ signature / page_ocr / start_ocr / deck_ocr / landscape
    source: str | None = None


@dataclass
class FieldMeta:
    """某个字段是怎么读出来的。"""

    method: str  # digits / ocr / template / signature / kaa / save / derived
    confidence: float | None = None
    note: str | None = None


# 每个页面当前能提供的字段（文档与 strict 校验用）；新增页面 / 字段在这里登记并记入 docs/contracts.md
FIELD_CATALOG: dict[str, tuple[str, ...]] = {
    "ACTION_SELECT": ("mode", "step", "stamina", "max_stamina", "produce_point", "params", "growth_permil", "star",
                      "param_cap", "options", "complete", "advice", "drinks", "drink_capacity"),
    "HIF_EXAM": ("remaining_turns", "hp", "genki", "score", "hand", "drinks", "motivation", "impression", "buffs",
                 "deck_count", "attr", "multiplier_pct", "rank_line", "participants", "pitems", "unrecognized_cards", "exam_label",
                 "npc_scores", "my_rank_index", "multiplier_permil", "statuses", "drink_slots", "finals", "finals_round", "pitem_charges"),
    "SELECT_CARD": ("candidates", "recommended", "rerolls_left", "excludes_left"),
    "SELECT_DRINK": ("candidates", "held", "full"),
    "SELECT_PITEM": ("pitems", "selected", "confirm_enabled"),
    "PRODUCE_START_CONFIRM": ("supports", "main_attr"),
    "CONSULT": ("shelf", "services", "rerolls_left", "complete", "drinks", "drink_capacity", "drinks_full", "stamina", "max_stamina", "produce_point"),
    "HIF_EXAM_RESULT": ("score", "rank", "passed", "exam_no", "rank_line", "participants", "entries", "retry"),
    "STUDY": ("choices", "selected", "effect", "complete"),
    "OUTING": ("choices", "selected", "effect", "complete"),
    "EVENT_CHOICE": ("choices", "selected", "effect", "complete", "dialogue_only"),
    "DECK_LIST": ("deck", "deck_size", "cards", "complete", "problems"),
    "SKILL_CARD_REMOVAL": ("cards", "selected", "selected_card", "agree", "confirm", "deck_complete", "deck_screens", "cancelable"),
    "SKILL_CARD_ENHANCE": ("cards", "selected", "selected_card", "agree", "confirm", "upgradable", "deck_complete", "deck_screens", "cancelable", "trigger"),
    "SKILL_CARD_CHANGE_2": ("cards", "selected", "selected_card", "agree", "confirm", "deck_complete", "deck_screens"),
    "SKILL_CARD_CHANGE_1": ("candidates", "recommended", "rerolls_left", "excludes_left", "complete"),
    "PDRINK_MAX": ("held", "incoming", "select_left", "complete", "incomings"),
    "SPECIAL_GUIDANCE": ("cards", "selected", "selected_card", "agree", "scrollable", "complete", "view", "menu", "cards_left", "at_top",
                         "produce_point", "stamina", "max_stamina"),
    # 页面地图（docs/pages-hif.md）：KAA 认不出的 H.I.F 页面
    "DIALOG": ('kind', 'title', 'danger', 'confirm'),
    "ACQUIRE": ('kind', 'card_id', 'drink_id', 'pitem_id', 'pitem_ids', 'name', 'upgrade', 'agree', 'memory_effect'),
    "STAR_GAIN": ('gain', 'star', 'star_shown'),
    "HIF_EXAM_BRIEFING": ('exam_no', 'rank_line', 'participants', 'star', 'star_ok', 'params', 'multiplier_pct', 'complete', 'finals'),
    "PITEM_CUSTOMIZE": (),
    "PITEM_CUSTOMIZE_DONE": (),
    "MENU": ('kind',),
    "UPGRADE_RESULT": ('name', 'card_id', 'upgrade'),
    "TRANSITION": ('kind',),
    "GIFT": ('bag',),
    "PRODUCE_LOG": ('close',),
    "CUSTOMIZE_RESULT": ('name', 'card_id', 'upgrade'),
    "DELETE_RESULT": ('cards', 'text'),
    "HIF_FINALS_START": ('selected_memory', 'qualify_memories'),
    "HIF_FINALS_RANKING": ('kind', 'score', 'round_score', 'entries', 'next', 'my_score', 'rank', 'finals_round', 'round_result', 'retry', 'my_gain', 'final', 'outcome', 'passed'),
    "HIF_ROUND_INTERVAL": ('stamina', 'max_stamina', 'produce_point', 'interval', 'complete', 'dialog'),
}  # fmt: skip


@dataclass
class Observation(Contract):
    """一帧的识别结果。"""

    seq: int
    t: float  # time.time()
    page: PageObs
    fields: dict[str, Any] = field(default_factory=dict)
    field_meta: dict[str, FieldMeta] = field(default_factory=dict)
    screenshot: str | None = None  # 截图路径（相对 trace / 数据集目录）
    source: str = "screen"  # screen / save / dataset / replay / plugin（1.3：数据来源插件）
    timings_ms: dict[str, float] = field(default_factory=dict)  # capture / classify / extract
    by: str | None = field(default=None, metadata=OPTIONAL)  # 1.3：source=plugin 时是「插件 id@版本」；没有时不写
    trust: str | None = field(
        default=None, metadata=OPTIONAL
    )  # 1.3：source=plugin 时插件声明的档位（save / screen / infer），reduce 当来源用；没有时不写
    hidden: dict[str, Any] = field(
        default_factory=dict, metadata=OPTIONAL
    )  # 1.3：画面上看不到的字段（并进 GameState.hidden）；空时不写
    version: str = VERSION

    def known(self) -> dict[str, Any]:
        return {k: v for k, v in self.fields.items() if v is not None}

    def missing(self, names: tuple[str, ...] | list[str] | None = None) -> list[str]:
        """期望有、但这帧没读到的字段。"""
        names = names or FIELD_CATALOG.get(self.page.name, ())
        return [n for n in names if self.fields.get(n) is None]

    def problems(self, *, strict: bool = False) -> list[str]:
        errs = super().problems()
        if strict and self.page.name in FIELD_CATALOG:
            allowed = set(FIELD_CATALOG[self.page.name])
            errs += [f"$.fields: 页面 {self.page.name} 未登记的字段 {k}" for k in self.fields if k not in allowed]
        if self.source not in SOURCES:
            errs.append(f"$.source: {self.source!r} 不在 {SOURCES}")
        return errs
