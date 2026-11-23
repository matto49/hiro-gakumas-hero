"""考试出牌的决策编排：规则 → 本回合计划 → LLM → 硬约束复核 → 兜底。

每回合通常只在开始时问一次 LLM，拿到「现在做什么 + 本回合后续打算」。后续动作若和计划一致就直接执行，
只有手牌出现计划外的变化（抽牌、随机生成）时才重新问。
"""

import json
import logging
from collections import Counter
from dataclasses import asdict

from hiro.brain import rules as _rules
from hiro.brain.llm import LLMAdapter, load_prompt, prompt_version
from hiro.brain.rules import Decision, fallback, final_turn_override, soft_warnings, trivial, violations
from hiro.brain.state import BattleState, ExamMemory, IdolProfile

logger = logging.getLogger(__name__)


class BattleBrain:
    def __init__(self, profile: IdolProfile | None, llm: LLMAdapter | None = None, *, max_retries: int = 1):
        self.profile = profile
        self.llm = llm
        self.max_retries = max_retries
        prompts = ["exam.md"] + ([f"idols/{profile.playbook}.md"] if profile and profile.playbook else [])
        self.system = load_prompt(*prompts)
        self.prompt_version = prompt_version(*prompts)
        self.calls: list[dict] = []  # 每次 LLM 调用的耗时与结果，写进记录

    def new_memory(self, exam: str) -> ExamMemory:
        return ExamMemory(exam=exam, item_charges_left=self.profile.item_charges if self.profile else None)

    def decide(self, state: BattleState, memory: ExamMemory) -> Decision:
        if d := trivial(state):
            return d
        if d := self._follow_plan(state, memory):
            d.warnings = soft_warnings(state, d)
            return self._final_turn(state, memory, d)
        d = _rules.playbook_rules(state) if _rules.EXAM_PLAYBOOK else None
        if d is None:
            d = self._ask(state) if self.llm else None
        if d is None:
            d = fallback(state)
        d.warnings = soft_warnings(state, d)
        memory.plan = d.plan
        return self._final_turn(state, memory, d)

    def _final_turn(self, state: BattleState, memory: ExamMemory, d: Decision) -> Decision:
        """最后一回合的兑现由代码按真实得分决定（第八局 R2：届いて！+ 在手没打出去）。"""
        if (o := final_turn_override(state, d)) is None:
            return d
        o.warnings = d.warnings + [f"覆盖了 {d.source} 的 {d.action}：{d.reason[:80]}"]
        memory.plan = o.plan
        return o

    def commit(self, state: BattleState, memory: ExamMemory, d: Decision) -> None:
        """动作已在游戏里执行成功后调用：更新记忆与预期手牌。"""
        hand = [c.name for c in state.hand]
        if d.kind == "play":
            card = next(c for c in state.hand if c.slot == d.slot)
            memory.record_play(card, hp_before=state.hp, profile=self.profile)
            hand.remove(card.name)
            # 抽牌会改变手牌，之后无法预期；往牌堆生成眠气不影响手牌
            memory.expected_hand = None if card.tags.draws else hand
        elif d.kind == "drink":
            drink = next(x for x in state.drinks if x.slot == d.slot)
            memory.record_drink(drink)
            memory.expected_hand = None if "引く" in drink.text or "入れ替える" in drink.text else hand
            if "入れ替える" in drink.text:
                memory.plan = []  # ミックススムージー：整手牌换掉，喝之前的本回合计划作废（引擎核查 R5）
        else:
            memory.record_skip()
            memory.expected_hand = None
        if d.source == "plan":
            memory.plan = memory.plan[1:]

    def _follow_plan(self, state: BattleState, memory: ExamMemory) -> Decision | None:
        if not memory.plan:
            return None
        step = memory.plan[0]
        if step.get("action") == "play":
            card = next((c for c in state.hand if c.name == step.get("card") and c.available), None)
            action = f"play:{card.slot}" if card else None
        elif step.get("action") == "drink":
            drink = next((x for x in state.drinks if x.name == step.get("drink")), None)
            action = f"drink:{drink.slot}" if drink else None
        else:
            action = "skip" if step.get("action") == "skip" else None
        if action is None or not self._hand_as_expected(state, memory):
            memory.plan = []
            return None
        d = Decision(action, "按本回合计划继续", "plan", plan=memory.plan)
        if violations(state, d):
            memory.plan = []
            return None
        return d

    def _hand_as_expected(self, state: BattleState, memory: ExamMemory) -> bool:
        if memory.expected_hand is None:
            return False
        return Counter(c.name for c in state.hand) == Counter(memory.expected_hand)

    def _ask(self, state: BattleState) -> Decision | None:
        payload = state.to_prompt_dict()
        user = "当前状态：\n" + json.dumps(payload, ensure_ascii=False)
        for _attempt in range(self.max_retries + 1):
            res = self.llm.ask_json(self.system, user)
            # 发给 LLM 的局面一并记下（系统提示按 prompt_version 可还原），之后换模型 / 改提示词可以原样重放（tools.audit.exam_replay）
            self.calls.append(res.record(prompt_version=self.prompt_version, prompt_state=payload))
            if res.data is None:
                logger.warning("LLM 调用失败：%s", res.error)
                return None
            if not isinstance(res.data.get("action"), str):
                logger.warning("LLM 回复缺少 action 字段：%s", res.raw[:200])
                return None
            d = Decision(
                action=str(res.data.get("action", "")).strip(),
                reason=str(res.data.get("reason", "")),
                source="llm",
                plan=[s for s in res.data.get("plan") or [] if isinstance(s, dict)],
                line=str(res.data.get("line", "")),
            )
            bad = violations(state, d)
            if not bad:
                return d
            logger.info("LLM 决策被拦截：%s", bad)
            user += "\n\n你上一次的选择 " + d.action + " 违反了约束：" + "；".join(bad) + "。请重新选择。"
        return None


def decision_record(state: BattleState, d: Decision) -> dict:
    """写进 events.jsonl 的一条决策记录。"""
    return {"state": state.to_prompt_dict(), "decision": asdict(d)}
