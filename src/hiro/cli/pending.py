"""待确认（设计稿 §12a）：会动游戏的命令（Risk.ACT）第一次执行不真的做，返回一个待确认编号。

- 应用把它显示成确认卡，用户点了才调用 `hiro confirm <编号>` 真正执行；外部 agent 拿到待确认时要先问用户。
- 编号一次性（确认时先把记录原子地拿走再执行），10 分钟过期。
- 命令行没有「直接带确认执行」的参数：唯一的执行入口是 `hiro confirm`。内置 agent 的工具里没有 confirm（agent=False）。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from hiro.cli.home import ensure_home
from hiro.cli.registry import CliError

TTL = 600
_ID = re.compile(r"^[0-9a-f]{12}$")


def _now() -> float:
    return time.time()


def _dir() -> Path:
    d = ensure_home() / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(command: str, args: dict, summary: str, access: str = "all") -> dict:
    pid = secrets.token_hex(6)
    t = _now()
    rec = {
        "id": pid,
        "command": command,
        "args": args,
        "summary": summary,
        "access": access,
        "created_at": t,
        "expires_at": t + TTL,
    }
    tmp = _dir() / f"{pid}.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_dir() / f"{pid}.json")
    return rec


def public(rec: dict) -> dict:
    """给 agent / 界面看的待确认：带上参数（args），确认卡按种类和参数画（11-25 review：只有 command 时界面认不出种类）。"""
    return {k: rec.get(k) for k in ("id", "command", "args", "access", "summary", "expires_at")}


def take(pid: str) -> dict:
    """拿走一条待确认（之后同一编号再确认就是 pending_unknown）。过期的也拿走，但报 pending_expired。"""
    if not _ID.match(pid or ""):
        raise CliError("pending_unknown", f"没有这条待确认：{pid}", "待确认只能用一次；重新发起这条命令")
    src = _dir() / f"{pid}.json"
    claimed = _dir() / f"{pid}.{os.getpid()}.claimed"
    try:
        src.replace(claimed)  # 原子：两个进程同时确认，只有一个拿得到
    except FileNotFoundError:
        raise CliError("pending_unknown", f"没有这条待确认：{pid}", "待确认只能用一次；重新发起这条命令") from None
    try:
        rec = json.loads(claimed.read_text(encoding="utf-8"))
    finally:
        claimed.unlink(missing_ok=True)
    if _now() > rec["expires_at"]:
        raise CliError("pending_expired", "这条待确认已经过期（超过 10 分钟）", "重新发起这条命令")
    return rec


def cancel(pid: str) -> bool:
    if not _ID.match(pid or ""):
        return False
    try:
        (_dir() / f"{pid}.json").unlink()
        return True
    except FileNotFoundError:
        return False


def listing() -> list[dict]:
    out = []
    t = _now()
    for p in sorted(_dir().glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if t > rec.get("expires_at", 0):
            p.unlink(missing_ok=True)
            continue
        out.append(public(rec))
    return out
