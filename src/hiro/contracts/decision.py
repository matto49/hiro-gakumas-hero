"""Decision：决策层输出。动作 + 理由 + 置信度 + 来源 + 候选与预演摘要 + LLM 原始输出与耗时。

hiro.brain.rules.Decision（考试，字符串动作）与 hiro.brain.pick.PickDecision（选卡）通过 from_battle / from_pick 转换；
决策层内部结构归决策会话，主循环与 trace 只认这里的 Decision。
"""

from dataclasses import dataclass, field
from typing import Any

from hiro.contracts.action import Action
from hiro.contracts.base import VERSION, Contract

SOURCES = ("rule", "plan", "sim", "llm", "fallback", "recommended", "first", "expert", "bench", "plugin")


@dataclass
class LLMTrace:
    """一次 LLM 调用。"""

    adapter: str  # claude-cli / openai / mock
    model: str | None
    seconds: float
    raw: str  # 原始回复（截断到合理长度）
    error: str | None = None
    prompt_version: str | None = None  # 提示词文件版本（brain/prompts 头部）
    input_tokens: int | None = None  # 不含缓存的输入
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cost_usd: float | None = None


@dataclass
class Candidate:
    action: Action
    preview: dict[str, Any] | None = None  # Preview.summary
    expected: float | None = None
    note: str | None = None


@dataclass
class Decision(Contract):
    """一个决策点的输出。"""

    action: Action
    reason: str = ""
    source: str = "rule"  # rule / plan / sim / llm / fallback / recommended / first / expert（考试预演专家）/ bench（gakumas-bench 算法）
    confidence: float | None = None  # 0–1；规则 1.0，兜底 0.3，LLM 由其自报或缺省 None
    candidates: list[Candidate] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)  # 本回合后续打算（LLM 给出）
    warnings: list[str] = field(default_factory=list)
    line: str = ""  # 数字小广台词
    llm: list[LLMTrace] = field(default_factory=list)  # 本次决策的 LLM 调用（可能重试多次）
    seconds: float | None = None  # 从拿到状态到给出决策的耗时
    decider: str | None = None  # battle / pick / action_select / consult / outing …
    plugin: str | None = None  # 1.3：插件定的这一步时是「插件 id@版本」，内置为 None
    version: str = VERSION

    def problems(self) -> list[str]:
        errs = super().problems()
        if self.source not in SOURCES:
            errs.append(f"$.source: {self.source!r} 不在 {SOURCES}")
        return errs

    # ---- 与决策层现有结构互转
    @classmethod
    def from_battle(cls, d, *, calls: list[dict] | None = None, candidates: list[Candidate] | None = None,
                    seconds: float | None = None) -> "Decision":  # fmt: skip
        """hiro.brain.rules.Decision → 契约。calls 为 BattleBrain.calls 里本次新增的条目。"""
        return cls(
            action=Action.from_legal(d.action),
            reason=d.reason,
            source=d.source,
            confidence={"rule": 1.0, "plan": 0.8, "fallback": 0.3}.get(d.source),
            candidates=candidates or [],
            plan=list(d.plan or []),
            warnings=list(d.warnings or []),
            line=d.line or "",
            llm=[llm_trace_from_call(c) for c in calls or []],
            seconds=seconds,
            decider="battle",
        )

    def to_battle(self):
        """契约 → hiro.brain.rules.Decision（供 produce/battle.py 执行路径复用）。"""
        from hiro.brain.rules import Decision as BattleDecision

        key = self.action.legal_key()
        if key is None:
            raise ValueError(f"不是考试动作：{self.action}")
        return BattleDecision(key, self.reason, self.source, list(self.plan), self.line, list(self.warnings))

    @classmethod
    def from_pick(cls, d, *, candidates: list[Candidate] | None = None, seconds: float | None = None) -> "Decision":
        """hiro.brain.pick.PickDecision → 契约（三选一拿第 choice 项）。"""
        return cls(
            action=Action.activate_effect(d.choice),
            reason=d.reason,
            source=d.source,
            confidence={"recommended": 0.5, "first": 0.2}.get(d.source),
            candidates=candidates or [],
            line=d.line or "",
            llm=[llm_trace_from_call(c) for c in d.calls or []],
            seconds=seconds,
            decider="pick",
        )


def llm_trace_from_call(c: dict) -> LLMTrace:
    """BattleBrain.calls / PickDecision.calls 的条目 {seconds, error, reply} → LLMTrace。"""
    reply = c.get("reply")
    raw = reply if isinstance(reply, str) else __import__("json").dumps(reply, ensure_ascii=False)
    return LLMTrace(
        adapter=c.get("adapter", "claude-cli"),
        model=c.get("model"),
        seconds=float(c.get("seconds") or 0.0),
        raw=raw[:4000],
        error=c.get("error"),
        prompt_version=c.get("prompt_version"),
        input_tokens=c.get("input_tokens"),
        output_tokens=c.get("output_tokens"),
        cache_read_input_tokens=c.get("cache_read_input_tokens"),
        cache_creation_input_tokens=c.get("cache_creation_input_tokens"),
        cost_usd=c.get("cost_usd"),
    )
