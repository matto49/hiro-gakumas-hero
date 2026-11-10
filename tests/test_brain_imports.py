"""策略层照搬部分：不装 KAA / kotonebot / 私有模拟器也能导入；提示词都带版本号。"""

import importlib
import sys

MODULES = ["hiro.brain.llm", "hiro.brain.rules", "hiro.brain.state", "hiro.brain.battle", "hiro.brain.cards",
           "hiro.brain.pick", "hiro.brain.pitem", "hiro.state.knowledge", "hiro.state.memory_priority"]


def test_imports_without_private_deps(monkeypatch):
    for m in ("kaa", "kotonebot", "gakumas_exam", "gakumas_produce"):
        monkeypatch.setitem(sys.modules, m, None)  # 导入这些会报 ImportError
    for m in MODULES:
        sys.modules.pop(m, None)
        importlib.import_module(m)


def test_profile_and_prompts():
    from hiro.brain.llm import prompt_version
    from hiro.brain.state import CAMPUS_HIRO

    assert CAMPUS_HIRO.name
    for name in ("exam.md", "action_select.md", "card_pick.md"):
        assert prompt_version(name)
