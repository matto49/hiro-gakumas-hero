"""初 / N.I.A 的社区规则（src/hiro/defaults/hajime_nia_rules.json）：按编号取规则，决策理由里带出处标签。

每条规则：id、families（hajime / nia）、difficulties（None = 全部难度）、rule（中文）、source（出处简称，见 sources）、quote（原文摘录）。
设计稿：（作者本机，未发布） 第四节。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "defaults" / "hajime_nia_rules.json"


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _by_id() -> dict:
    return {r["id"]: r for r in load()["rules"]}


def rule(rule_id: str) -> dict:
    return _by_id()[rule_id]


def cite(rule_id: str) -> str:
    """决策理由里用的短引用：「规则 [出处]」。"""
    r = rule(rule_id)
    return f"{r['rule']} [{r['source']}]"


def applies(rule_id: str, family: str | None, difficulty: str | None) -> bool:
    r = rule(rule_id)
    return family in r["families"] and (r["difficulties"] is None or difficulty in r["difficulties"])


def guide(family: str | None, difficulty: str | None) -> str:
    """给 LLM 看的打法说明：这个模式 / 难度适用的全部规则，一条一行「- 规则 [出处]」。"""
    rows = [f"- {r['rule']} [{r['source']}]" for r in load()["rules"] if applies(r["id"], family, difficulty)]
    name = {"hajime": "初", "nia": "N.I.A"}.get(family or "", family or "")
    return f"## {name}{(' ' + difficulty.upper()) if difficulty else ''} 的社区规则\n\n" + "\n".join(rows)
