"""契约 1.2：状态推算与识别互验（docs/state-events.md §2）。

    Event       事件：由已执行的动作推出（action）、识别认出（observed）、互验差异反推（inferred）、主动查看覆盖（review）
    Prediction  事件之后，下一个观测点各字段的期望（值或区间）
    Check       观测到来时的互验结果：consistent / read_error / model_gap / unpredicted

字段路径用点号：produce.params.Da、produce.deck、exam.score。
"""

from dataclasses import dataclass, field
from typing import Any

from hiro.contracts.base import VERSION, Contract

ORIGINS = ("action", "observed", "inferred", "review")
BASES = ("master", "community", "sim", "observe_only")
VERDICTS = ("consistent", "read_error", "model_gap", "unpredicted")
CHECK_ACTIONS = ("accept", "discard_reread", "mark_pending", "active_review")
FIELD_STATUS = ("verified", "predicted", "observed", "pending", "unknown")

# §3 事件表（只增不改名）
EVENT_KINDS = (
    "PRODUCE_START", "DAY_ADVANCE", "LESSON", "STUDY", "OUTING", "GIFT", "ALLOWANCE", "REST",
    "CARD_ACQUIRE", "CARD_REROLL", "CARD_EXCLUDE", "CARD_UPGRADE", "CARD_DELETE", "CARD_CHANGE", "CARD_AUTO_PROCESS",
    "CARD_CUSTOMIZE", "SHOP_BUY", "DRINK_ACQUIRE", "DRINK_USE", "DRINK_DISCARD", "PITEM_ACQUIRE", "PITEM_CUSTOMIZE",
    "WAPPEN_TRIGGER", "EXAM_START", "EXAM_RESULT", "ROUND_INTERVAL_BUY", "UNMODELED_CHANGE",
    "DECK_REVIEW",  # 主动查看牌组页看全（origin=review）：以读到的牌组为准
)  # fmt: skip


@dataclass
class Event(Contract):
    seq: int  # 与触发它的 Step / Observation 同序号空间
    kind: str  # EVENT_KINDS
    origin: str  # ORIGINS
    args: dict[str, Any] = field(
        default_factory=dict
    )  # card_id、upgrade、deck_index、drink_id、price、option、exam、score …
    basis: str = "observe_only"  # BASES：推算依据
    confirmed: bool | None = None  # 观测是否确认事件确实发生（None = 未确认）
    ref: dict[str, Any] = field(default_factory=dict)  # decision_seq、observation_seq、screenshot
    confidence: float | None = None  # 识别认出的事件的置信度（observed）
    note: str | None = None
    version: str = VERSION

    def problems(self) -> list[str]:
        errs = super().problems()
        if self.kind not in EVENT_KINDS:
            errs.append(f"$.kind: {self.kind!r} 不在事件表")
        if self.origin not in ORIGINS:
            errs.append(f"$.origin: {self.origin!r} 不在 {ORIGINS}")
        if self.basis not in BASES:
            errs.append(f"$.basis: {self.basis!r} 不在 {BASES}")
        return errs


@dataclass
class Expect:
    """一个字段的期望：精确值（exact=True）或区间 [min, max]；牌组等多重集用 value + exact。"""

    value: Any = None
    min: float | None = None
    max: float | None = None
    exact: bool = False
    tolerance: float | None = None
    note: str | None = None

    def admits(self, observed: Any) -> bool:
        """读数是否落在期望内（None 读数不判断，由调用方处理）。"""
        if observed is None:
            return True
        if self.exact:
            if isinstance(self.value, (int, float)) and isinstance(observed, (int, float)) and self.tolerance:
                return abs(observed - self.value) <= self.tolerance
            if isinstance(self.value, list) and isinstance(observed, list):
                from collections import Counter

                return Counter(map(str, self.value)) == Counter(map(str, observed))
            return observed == self.value
        if not isinstance(observed, (int, float)):
            return True
        lo = (
            self.min
            if self.min is not None
            else (self.value - (self.tolerance or 0) if isinstance(self.value, (int, float)) else None)
        )
        hi = (
            self.max
            if self.max is not None
            else (self.value + (self.tolerance or 0) if isinstance(self.value, (int, float)) else None)
        )
        return (lo is None or observed >= lo) and (hi is None or observed <= hi)


@dataclass
class Prediction(Contract):
    seq: int  # 基于哪个事件之后的状态
    event_seq: int  # 来源事件
    fields: dict[str, Expect] = field(default_factory=dict)  # 字段路径 → 期望
    due: str = "any"  # 在哪个页面核对：ACTION_SELECT / HIF_EXAM / any
    expires_after: int | None = None  # 超过多少帧没核对就作废
    version: str = VERSION


@dataclass
class Check(Contract):
    seq: int  # 观测序号
    field: str  # 字段路径
    predicted: Any = None  # 期望（Prediction 的 Expect.to_dict，或不变量给出的参考值）
    observed: Any = None
    verdict: str = "unpredicted"  # VERDICTS
    rule: str = "prediction"  # prediction 或不变量名（score_monotonic、digit_count、param_monotonic …）
    action: str = "accept"  # CHECK_ACTIONS
    retries: int = 0
    note: str | None = None  # 规则给的说明（例如「一次少了 2 回合：中间回合没观测到」）
    version: str = VERSION

    def problems(self) -> list[str]:
        errs = super().problems()
        if self.verdict not in VERDICTS:
            errs.append(f"$.verdict: {self.verdict!r} 不在 {VERDICTS}")
        if self.action not in CHECK_ACTIONS:
            errs.append(f"$.action: {self.action!r} 不在 {CHECK_ACTIONS}")
        return errs
