"""选卡决策：LLM 按牌组角色选卡，失败时退回游戏推荐。"""

import json
from dataclasses import dataclass, field

from hiro.brain.cards import card_view
from hiro.brain.llm import LLMAdapter, load_prompt, prompt_version
from hiro.brain.state import IdolProfile


@dataclass
class PickDecision:
    choice: int
    reason: str
    source: str  # llm / recommended / first
    role: str = ""
    line: str = ""
    calls: list[dict] = field(default_factory=list)


class CardPicker:
    def __init__(self, profile: IdolProfile | None, llm: LLMAdapter | None):
        self.llm = llm
        prompts = ["card_pick.md"] + ([f"idols/{profile.playbook}.md"] if profile and profile.playbook else [])
        self.system = load_prompt(*prompts)
        self.prompt_version = prompt_version(*prompts)
        self.picked: list[str] = []  # 本次培育里我们选过的卡，作为牌组的已知部分

    def pick(self, cards: list, *, recommended: int | None, stage: str) -> PickDecision:
        """cards：KAA SkillCard 列表（未识别为 None）；recommended：游戏推荐的 index。"""
        views = [card_view(i, c, available=True) for i, c in enumerate(cards)]
        known = [v for v in views if v.card_id]
        default = recommended if recommended is not None else (known[0].slot if known else 0)
        if self.llm is None or len(known) <= 1:
            return self._done(PickDecision(default, "无需判断或 LLM 未启用", "recommended", calls=[]), views)
        payload = {
            "stage": stage,
            "picked_so_far": self.picked or None,
            "recommended": recommended,
            "candidates": [
                {"index": v.slot, "name": v.name, "category": v.category, "text": v.text,
                 "tags": {k: x for k, x in v.to_dict()["tags"].items()}}
                for v in known
            ],
        }  # fmt: skip
        res = self.llm.ask_json(self.system, "当前选卡：\n" + json.dumps(payload, ensure_ascii=False))
        calls = [res.record(prompt_version=self.prompt_version)]
        valid = {v.slot for v in known}
        try:
            choice = int(res.data["choice"]) if res.data else None
        except (KeyError, TypeError, ValueError):
            choice = None
        if choice not in valid:
            return self._done(PickDecision(default, f"LLM 结果无效（{res.error or res.data}），退回推荐", "recommended", calls=calls), views)
        d = PickDecision(choice, str(res.data.get("reason", "")), "llm", str(res.data.get("role", "")), str(res.data.get("line", "")), calls)
        return self._done(d, views)

    def _done(self, d: PickDecision, views) -> PickDecision:
        if 0 <= d.choice < len(views) and views[d.choice].card_id:
            self.picked.append(views[d.choice].name)
        return d
