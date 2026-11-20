"""`hiro` 命令行入口：解析 → 执行 → 打印 JSON 信封。

退出码：0 成功；1 命令失败（信封 ok=false）；2 用法错误 / 未知命令。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from hiro.cli import registry
from hiro.cli.registry import Arg, CliError, Risk, command

# 各命令模块：import 时注册
_MODULES: tuple[str, ...] = ("hiro.cli.cmd_info", "hiro.cli.catalog", "hiro.cli.cmd_run", "hiro.cli.secrets", "hiro.cli.cmd_doctor", "hiro.cli.cmd_plan")


def _load_all() -> None:
    import importlib

    for m in _MODULES:
        importlib.import_module(m)


PENDING_EXIT = 3  # 待确认：信封 ok=true，data.pending 有编号
# 桌面程序以 Codex / Claude 命令行当对话后端时设 HIRO_AGENT_SANDBOX=1：这些命令一律拒绝（除了 ACT 命令）
AGENT_READONLY_DENY = {"confirm", "cancel", "secret copy", "secret set", "secret delete"}


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
    if os.environ.get("HIRO_AGENT_SANDBOX") and (cmd.risk is Risk.ACT or cmd.name in AGENT_READONLY_DENY):
        return 1, fail("agent_readonly", f"程序内置的对话模式只能查信息，不能运行 hiro {cmd.name}",
                       "要开局或扫描时，在回答里单独写一行 HIRO_ACTION: run start <参数>，程序会给玩家出确认卡")
    if cmd.risk is Risk.ACT and cmd.precheck:
        try:
            cmd.precheck(args)
        except CliError as e:
            return e.exit_code, fail(e.code, e.message, e.hint)
    if cmd.risk is Risk.ACT:
        from hiro.cli import pending

        summary = cmd.confirm_summary(args) if cmd.confirm_summary else f"执行 hiro {cmd.name}"
        rec = pending.create(cmd.name, args, summary)
        return PENDING_EXIT, {"ok": True, "data": {
            "pending": {k: rec[k] for k in ("id", "command", "summary", "expires_at")},
            "message": f"需要用户确认：{summary}。用户同意后运行 hiro confirm {rec['id']}；不要自己确认。",
        }}
    return _execute(cmd, args)


def _execute(cmd: registry.Command, args: dict) -> tuple[int, dict]:
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


@command("confirm", Risk.READ, "用户同意后执行一条待确认的命令（只能由用户发起，agent 不要调用）。",
         args=(Arg("id", "待确认编号"),), agent=False)
def _confirm(a: dict) -> dict:
    from hiro.cli import pending

    rec = pending.take(a["id"])
    cmd = registry.COMMANDS.get(rec["command"])
    if cmd is None:
        raise CliError("unknown_command", f"待确认里的命令已经不存在：{rec['command']}", "重新发起")
    code, env = _execute(cmd, rec["args"])
    if not env["ok"]:
        e = env["error"]
        raise CliError(e["code"], e["message"], e["hint"], exit_code=code)
    return env["data"]


@command("cancel", Risk.READ, "取消一条待确认（用户说「先不」时）。", args=(Arg("id", "待确认编号"),))
def _cancel(a: dict) -> dict:
    from hiro.cli import pending

    return {"cancelled": pending.cancel(a["id"])}


@command("pending list", Risk.READ, "列出还没确认、没过期的待确认。")
def _pending_list(a: dict) -> dict:
    from hiro.cli import pending

    return {"pending": pending.listing()}


if __name__ == "__main__":
    sys.exit(main())
