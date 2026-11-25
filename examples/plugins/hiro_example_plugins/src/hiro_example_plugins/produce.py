"""示例育成插件：只接授業（STUDY）页，总选第一项。只用于演示接口，别拿来实战。"""

from __future__ import annotations

from typing import Any

from hiro.contracts import Action, ActionKind, Decision


class StudyFirstOption:
    id = "example_study"
    version = "0.1.0"
    contracts = "1"  # 认的契约主版本（hiro.contracts.VERSION 的第一段）
    pages = frozenset({"STUDY"})

    def begin(self, context: dict[str, Any]) -> None:
        # context：mode（培育 id）、idol、profile、main_attr、trace_dir。要跨段保存状态就写进 trace_dir。
        self.idol = context.get("idol")

    def decide(self, page: str, state: Any, *, n_options: int | None, kw: dict[str, Any]) -> Decision | None:
        if page != "STUDY" or not n_options:
            return None  # 不管：交回内置决策器
        return Decision(Action(ActionKind.EVENT_SELECT, {"select": 0}), reason="示例插件：总选第一项", source="rule")

    def end(self, result: dict[str, Any] | None) -> None:
        pass
