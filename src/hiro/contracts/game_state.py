"""GameState：状态 → 决策。全局状态，只由 reduce(state, obs) 修改（hiro.state.reduce，M-B）。

produce 直接存 ProduceState 的字段（hiro.state.produce，协议名），允许部分字段为 None（尚未观测到）；
exam 存 BattleState.build 的观测参数（hiro.vision.observe.battle_observation 的输出）。
sources 记每个字段最后一次写入的来源与 seq，来源优先级 save > screen > infer。
"""

from dataclasses import dataclass, field
from typing import Any

from hiro.contracts.base import VERSION, Contract
from hiro.contracts.events import Event

MODES = ("home", "produce", "contest", "daily", "unknown")
PRIORITY = {
    "save": 4,
    "review": 3,
    "event": 2,
    "screen": 2,
    "infer": 1,
}  # 1.2：event（已确认事件）与已互验的 screen 同级


@dataclass
class PageState:
    name: str = "unknown"
    area: str = "unknown"
    since_seq: int = 0  # 从哪一帧起处于这个页面
    stable: bool = False  # 已连续多帧确认
    frames: int = 0  # 处于本页面的连续帧数


@dataclass
class Source:
    origin: str  # save / screen / infer
    seq: int
    by: str | None = None  # 1.3：插件写的值是「插件 id@版本」


@dataclass
class ExamSession:
    """考试内与 ExamMemory 对应、且需要落盘的少量字段；完整记忆仍在 hiro.brain.state.ExamMemory。"""

    label: str | None = None  # qualify-1/2/3、r1、r2
    turn: int = 0
    last_remaining: int | None = None
    brief_permil: dict[str, int] | None = (
        None  # 最近一次考前说明页的三属性倍率（千分比，{Vo, Da, Vi}），进考试时填 exam.attr_permil
    )
    brief_exam_no: int | None = None  # 最近一次考前说明页读到的选拔场次（選抜試験N）；续跑后计数从头算时以它为准
    # 初的练习课（hiro.runtime.hajime）：开课画面读数 + 主数据对照。{kind, attr, nth（同种同属性第几次）, turns_total, clear_target,
    # perfect_target, param_start, level_ids, exam_extra_turn, lesson_limit_up_score}；读不到 / 对不上的项是 None
    lesson: dict | None = None


@dataclass
class GameState(Contract):
    """全局状态。"""

    mode: str = "unknown"
    page: PageState = field(default_factory=PageState)
    produce: dict[str, Any] | None = None  # ProduceState 字段子集
    exam: dict[str, Any] | None = None  # BattleState.build 的观测参数
    exam_session: ExamSession = field(default_factory=ExamSession)
    page_fields: dict[str, Any] = field(
        default_factory=dict
    )  # 只属于当前页面的字段（options / candidates / recommended / complete / advice），换页即清空
    sources: dict[str, Source] = field(default_factory=dict)  # "produce.stamina" → Source
    seq: int = 0  # 最后一次 reduce 用到的观测 seq
    pending_page: PageState | None = None  # 防抖中的候选页面（转移不合法、尚未连续 N 帧）
    conflicts: list[str] = field(default_factory=list)  # 来源冲突日志（最近若干条）
    field_status: dict[str, str] = field(
        default_factory=dict
    )  # 1.2：字段路径 → verified / predicted / observed / pending / unknown
    pending_review: list[str] = field(default_factory=list)  # 1.2：待复核字段，非空时触发主动查看
    ledger: list[Event] = field(default_factory=list)  # 1.2：本局已应用的事件（最近 N 条，完整的在 trace）
    hidden: dict[str, Any] = field(
        default_factory=dict
    )  # 1.3：数据来源插件给的、画面上看不到的字段（用过的对局打 uses_hidden）
    version: str = VERSION

    def problems(self) -> list[str]:
        errs = super().problems()
        if self.mode not in MODES:
            errs.append(f"$.mode: {self.mode!r} 不在 {MODES}")
        for k, s in self.sources.items():
            if s.origin not in PRIORITY:
                errs.append(f"$.sources.{k}: 来源 {s.origin!r} 不在 {tuple(PRIORITY)}")
        return errs

    def produce_state(self):
        """produce 字段齐全时构造 hiro.state.produce.ProduceState；否则返回 None。"""
        from dataclasses import MISSING, fields

        from hiro.state.produce import ProduceState

        if not self.produce:
            return None
        from hiro.state.modes import HAJIME, NIA, family

        required = [f.name for f in fields(ProduceState) if f.default is MISSING and f.default_factory is MISSING]
        if family(self.produce.get("mode")) not in (HAJIME, NIA):
            required.append("star")  # H.I.F 必须有星星；初 / N.I.A 没有星星（用户 11-17），不要求
        if any(self.produce.get(k) is None for k in required):
            return None
        known = {f.name for f in fields(ProduceState)}
        return ProduceState.from_dict({k: v for k, v in self.produce.items() if k in known and v is not None})

    def unobserved_produce_fields(self) -> list[str]:
        """ProduceState 里从未被观测到、produce_state() 会用 dataclass 默认值补上的字段。
        决策 / 预演应把它们当作「未知」，不要当事实（例如 deck=[] 不是 0 张，rerolls_left=0 不是没有刷新次数）。"""
        from dataclasses import fields

        from hiro.state.produce import ProduceState

        have = {k for k, v in (self.produce or {}).items() if v is not None}
        return [f.name for f in fields(ProduceState) if f.name not in have]

    def source_of(self, key: str) -> Source | None:
        return self.sources.get(key)
