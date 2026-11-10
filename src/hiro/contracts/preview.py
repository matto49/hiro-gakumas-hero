"""Preview：预演层输出。(GameState, Action) → 结果差异 + 期望终局分，只用公开信息。

考试内由 hiro.sim.preview.Preview 转换而来；培育页面（选行动 / 三选一）的确定性预演来自 hiro.state.context。
"""

from dataclasses import dataclass, field
from typing import Any

from hiro.contracts.action import Action
from hiro.contracts.base import VERSION, Contract


@dataclass
class Preview(Contract):
    """一个候选动作的预演结果。"""

    action: Action
    immediate: dict[str, Any] = field(default_factory=dict)  # 字段 → 确定值或 [最小, 最大]（考试：分数/元气/体力/干劲/好印象/剩余行动/抽牌）
    expected: float | None = None  # 期望终局分（考试）或预演的主指标
    spread: float | None = None
    vs_best: float | None = None
    vs_best_se: float | None = None
    turn_passed: bool | None = None
    summary: dict[str, Any] | None = None  # 给 LLM 的可读摘要（hiro.sim.preview.Preview.to_prompt 或 state.context 的行）
    engine: str = "sim"  # sim（考试引擎 gakumas_exam）/ formula（主数据公式）
    # 可观测（协调 2026-11-02 预演加速一期第 5 项）：这个动作实际算了几个世界；这一步预演的总耗时 / 计划世界数 / 实际算完的世界数；
    # 没算或中途淘汰的原因（预筛 / 淘汰赛，二期起用；一期为 None）
    worlds: int | None = None
    preview_ms: float | None = None
    worlds_planned: int | None = None
    worlds_done: int | None = None
    eliminated: str | None = None
    version: str = VERSION

    @classmethod
    def from_sim(cls, p, action: Action) -> "Preview":
        """hiro.sim.preview.Preview（引擎动作键）+ 对应的槽位动作 → 契约。"""
        return cls(
            action=action,
            immediate={k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in p.immediate.items()},
            expected=float(p.expected),
            spread=float(p.spread),
            vs_best=float(p.vs_best),
            vs_best_se=float(p.vs_best_se),
            turn_passed=bool(p.turn_passed),
            summary=p.to_prompt(),
            engine="sim",
            worlds=len(p.samples) if getattr(p, "samples", None) is not None else None,
            preview_ms=getattr(p, "preview_ms", None),
            worlds_planned=getattr(p, "worlds_planned", None),
            worlds_done=getattr(p, "worlds_done", None),
            eliminated=getattr(p, "eliminated", None),
        )

    @classmethod
    def from_slots(cls, slots: dict) -> list["Preview"]:
        """hiro.sim.preview.slot_previews 的输出（'play:0' → Preview）→ 契约列表。"""
        return [cls.from_sim(p, Action.from_legal(k)) for k, p in slots.items()]
