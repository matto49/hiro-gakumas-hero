"""`hiro` 命令行入口：解析 → 执行 → 打印 JSON 信封。

退出码：0 成功；1 命令失败（信封 ok=false）；2 用法错误 / 未知命令。
"""

from __future__ import annotations

import argparse
import json
import sys

from hiro.cli import registry
from hiro.cli.registry import Arg, CliError, Risk, command

# 各命令模块：import 时注册
_MODULES: tuple[str, ...] = ()


def _load_all() -> None:
    import importlib

    for m in _MODULES:
        importlib.import_module(m)


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # 不打印到 stderr、不 exit：转成信封
        raise _ArgError(message)


def _parse(cmd: registry.Command, rest: list[str]) -> dict:
    p = _Parser(prog=f"hiro {cmd.name}", add_help=False)
    for a in cmd.args:
        kw: dict = {"help": a.help}
        if a.type == "bool":
            kw["action"] = "store_true"
        else:
            if a.type == "int":
                kw["type"] = int
            if a.choices:
                kw["choices"] = list(a.choices)
            if not a.positional:
                kw["required"] = a.required
                kw["default"] = a.default
            elif not a.required and a.default is not None:
                kw["nargs"] = "?"
                kw["default"] = a.default
        if a.positional:
            p.add_argument(a.dest, **kw)
        else:
            p.add_argument(a.name, dest=a.dest, **kw)
    return vars(p.parse_args(rest))


def emit(envelope: dict) -> None:
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def fail(code: str, message: str, hint: str = "") -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def run(argv: list[str]) -> tuple[int, dict]:
    _load_all()
    cmd, rest = registry.resolve(argv)
    if cmd is None:
        what = " ".join(argv) or "（空）"
        return 2, fail("unknown_command", f"没有这条命令：{what}", "运行 hiro tools 查看全部命令")
    try:
        args = _parse(cmd, rest)
    except _ArgError as e:
        return 2, fail("bad_args", f"{cmd.name} 的参数不对：{e}", f"运行 hiro tools 查看 {cmd.name} 的参数")
    try:
        data = cmd.handler(args)
    except CliError as e:
        return e.exit_code, fail(e.code, e.message, e.hint)
    return 0, {"ok": True, "data": data}


def main(argv: list[str] | None = None) -> int:
    code, env = run(sys.argv[1:] if argv is None else argv)
    emit(env)
    return code


@command("tools", Risk.READ, "列出全部命令：名字、风险等级（read 直接执行 / act 先返回待确认）、说明、参数。")
def _tools(args: dict) -> dict:
    return {"tools": [c.describe() for c in registry.COMMANDS.values()]}


if __name__ == "__main__":
    sys.exit(main())
