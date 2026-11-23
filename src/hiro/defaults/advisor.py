"""ExamAdvisor 默认实现：社区规则 + LLM，不需要模拟器。

- 没有预演（默认的 ScreenExamStateSource 不提供）：BattleBrain 出一步（不需要思考的情形 → 本回合计划 → LLM → 规则兜底），
  再过一遍硬规则（guard_llm：饮料预算、届いて 留到剩 ≤2 等只看局面就能判的规则），被拦就让 LLM 重选一次，还不行用规则兜底。
- 有预演（接了别的 ExamStateSource，例如 hiro.sim.screen_source，previews 不为 None）：预演写进局面（state.action_previews），
  默认（HIRO_EXAM_POLICY=llm）交给 LLM 参考；HIRO_EXAM_POLICY=expert 时按预演期望 + 同一套硬规则链选（exam_policy.expert_pick），
  规则链选不出来再退回 LLM 路径。
LLM 用 hiro.brain.llm.from_spec(环境变量 HIRO_LLM)，没配置时只用规则。
"""

from __future__ import annotations

import os
import time
from typing import Any

from hiro.brain.battle import BattleBrain
from hiro.brain.rules import fallback
from hiro.brain.state import IdolProfile, profile_for
from hiro.contracts import Action, Decision, Preview
from hiro.produce.exam_policy import expert_pick, guard_llm, policy_from_env


def _llm_from_env():
    spec = os.environ.get("HIRO_LLM")
    if not spec:
        return None
    from hiro.brain.llm import from_spec

    return from_spec(spec)


def preview_rows(previews: list[Preview], legal: list[Action]) -> list[dict]:
    """契约 Preview → 决策层 action_previews 行（期望高的在前），只保留合法动作。"""
    keys = {a.legal_key() for a in legal}
    rows = []
    for p in sorted(previews, key=lambda p: -(p.expected or 0.0)):
        key = p.action.legal_key()
        if key is None or key not in keys:
            continue
        row = dict(p.summary or {})
        row.update({"legal_action": key, "动作": row.get("动作", key)})
        if p.expected is not None:
            row["期望终局分"] = round(p.expected)
        if p.vs_best_se is not None:
            row["比最佳标准误"] = p.vs_best_se
        row.setdefault("即时结果", p.immediate or "无变化")
        rows.append(row)
    return rows


class RuleLLMAdvisor:
    """考试每一步：硬规则 + LLM；有预演时按预演 + 规则链。"""

    def __init__(self, profile: IdolProfile | None = None, llm=None):
        self.profile = profile
        self.brain = BattleBrain(profile, llm if llm is not None else _llm_from_env())
        self.round_no: int | None = None

    def on_round_start(self, round_no: int, context: dict[str, Any]) -> None:
        self.round_no = round_no
        idol = (context or {}).get("idol")
        if idol and (self.profile is None or self.profile.idol_id != idol) and (p := profile_for(idol)) is not None:
            self.profile = p
            self.brain = BattleBrain(p, self.brain.llm)

    def decide(self, state: Any, memory: Any, previews: list[Preview] | None, legal: list[Action]) -> Decision:
        t0 = time.monotonic()
        n_calls = len(self.brain.calls)
        if previews:
            state.action_previews = preview_rows(previews, legal)
            if (
                policy_from_env() == "expert"
            ):  # 同主仓库 HIRO_EXAM_POLICY：expert 按预演期望 + 硬规则链选；llm（默认）把预演放进提示词
                pick = expert_pick(state)
                if pick.decision is not None:
                    d = pick.decision
                    d.warnings = [f"预演选的动作违反规则（只记录）：{v}" for v in pick.violations or []]
                    return Decision.from_battle(d, seconds=time.monotonic() - t0)
        d = guard_llm(state, memory, self.brain, self.brain.decide(state, memory))
        out = Decision.from_battle(d, calls=self.brain.calls[n_calls:], seconds=time.monotonic() - t0)
        keys = {a.legal_key() for a in legal}
        if legal and out.action.legal_key() not in keys:
            # 只能从 legal 里选；越界时不默认选第一个（项目规则），改用规则兜底重选，兜底也越界就结束回合（不打牌最安全）
            bad = out.action.legal_key()
            fb = Decision.from_battle(fallback(state), seconds=time.monotonic() - t0)
            if fb.action.legal_key() in keys:
                fb.warnings.append(f"决策 {bad} 不在合法动作里，按规则兜底重选 {fb.action.legal_key()}")
                return fb
            if "skip" in keys:
                out.warnings.append(f"决策 {bad} 和规则兜底都不在合法动作里，结束回合")
                out.action = next(a for a in legal if a.legal_key() == "skip")
                return out
            raise RuntimeError(f"决策 {bad} 不在合法动作里，规则兜底也选不出，且不能结束回合：{sorted(keys)}")
        return out

    def on_round_end(self, result: dict[str, Any] | None) -> None:
        self.round_no = None
