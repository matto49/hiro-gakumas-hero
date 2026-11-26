"""G1 第 3 轮（N32，11-26）：对话步骤行露出内部工具名「choose_idol 等你选」。
注册表里给内置 agent 的工具（加上界面自带的 choose_idol）都要在 app/labels/labels.go 的 tools 表里有中文说法。"""

import importlib
import pkgutil
import re
from pathlib import Path

import hiro.cli
from hiro.cli import registry

LABELS = Path(__file__).resolve().parents[1] / "app" / "labels" / "labels.go"


def test_every_agent_tool_has_a_chinese_label():
    for m in pkgutil.iter_modules(hiro.cli.__path__):
        importlib.import_module(f"hiro.cli.{m.name}")  # 命令在各模块导入时注册
    names = {c.tool_name for c in registry.COMMANDS.values() if c.agent} | {"choose_idol"}
    assert len(names) > 20
    src = LABELS.read_text(encoding="utf-8")
    table = src[src.index("var tools = map[string]string{") :]
    table = table[: table.index("\n}\n")]
    labelled = set(re.findall(r'"([a-z_]+)":\s*"', table))
    assert sorted(names - labelled) == []
