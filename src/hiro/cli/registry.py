"""命令注册表：名字、风险等级、说明、参数。`hiro tools` 和内置 agent 的工具描述都从这里来。

风险等级：
- READ：只读（查信息、看状态），直接执行。
- ACT：会动游戏（开局、扫描），第一次执行返回「待确认」（见 hiro.cli.pending）。
花ジュエル、放弃培育的命令不存在，也不允许注册（register 时拦）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

FORBIDDEN_WORDS = ("jewel", "discard", "retire", "ジュエル", "破棄", "リタイア")


class Risk(str, Enum):
    READ = "read"
    ACT = "act"


class CliError(Exception):
    """命令失败：code 给程序判断，message / hint 用白话写给人看。"""

    def __init__(self, code: str, message: str, hint: str = "", exit_code: int = 1):
        super().__init__(message)
        self.code, self.message, self.hint, self.exit_code = code, message, hint, exit_code


@dataclass(frozen=True)
class Arg:
    name: str  # 位置参数写 "query"，选项写 "--rarity"
    help: str
    type: str = "string"  # string / int / bool
    choices: tuple[str, ...] | None = None
    required: bool = False
    default: Any = None
    agent: bool = True  # 给内置 agent 的工具描述里带不带这个参数（会卡住对话的不带）

    @property
    def positional(self) -> bool:
        return not self.name.startswith("-")

    @property
    def dest(self) -> str:
        return self.name.lstrip("-").replace("-", "_")

    def describe(self) -> dict:
        d = {
            "name": self.name,
            "help": self.help,
            "type": self.type,
            "required": self.required or self.positional,
            "agent": self.agent,
        }
        if self.choices:
            d["choices"] = list(self.choices)
        if self.default is not None:
            d["default"] = self.default
        return d


@dataclass
class Command:
    name: str  # 空格分隔的子命令，例如 "info supports"
    risk: Risk
    help: str
    args: tuple[Arg, ...] = ()
    handler: Callable[..., Any] | None = None
    confirm_summary: Callable[[dict], str] | None = None  # ACT 命令：待确认卡片上写的一句话
    precheck: Callable[[dict], None] | None = None  # ACT 命令：出待确认之前先查（明显做不了就直接报错，不让用户白确认）
    stdin: bool = False  # 从 stdin 读值（密码）；agent 不能调用
    agent: bool = True  # 是否给内置 agent 当工具
    extra: dict = field(default_factory=dict)

    @property
    def tool_name(self) -> str:
        return self.name.replace(" ", "_").replace("-", "_")

    def describe(self) -> dict:
        return {
            "name": self.name,
            "tool_name": self.tool_name,
            "risk": self.risk.value,
            "help": self.help,
            "args": [a.describe() for a in self.args],
            "agent": self.agent,
        }


COMMANDS: dict[str, Command] = {}


def command(name: str, risk: Risk, help: str, args: tuple[Arg, ...] | list[Arg] = (), **kw):
    """装饰器：注册一条命令。处理函数收参数字典，返回 data（可 JSON 化），失败抛 CliError。"""
    low = name.lower()
    if any(w in low for w in FORBIDDEN_WORDS):
        raise ValueError(f"命令 {name!r} 碰到禁止事项（花ジュエル / 放弃培育），不允许注册")

    def deco(fn):
        COMMANDS[name] = Command(name=name, risk=risk, help=help, args=tuple(args), handler=fn, **kw)
        return fn

    return deco


def resolve(argv: list[str]) -> tuple[Command | None, list[str]]:
    """最长前缀匹配命令名：["info", "supports", "--rarity", "SSR"] → (info supports, ["--rarity", "SSR"])。"""
    for n in range(len(argv), 0, -1):
        cmd = COMMANDS.get(" ".join(argv[:n]))
        if cmd:
            return cmd, argv[n:]
    return None, argv
