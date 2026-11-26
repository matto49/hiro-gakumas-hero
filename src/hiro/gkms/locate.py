"""找本机装的 GKMS（spec 4.2）。

顺序：HIRO_GKMS_HOME → Windows %LOCALAPPDATA%\\Programs\\GKMS（本身或下一层目录）→ ~/github/_ext/GKMSProject。
要求 GakumasCLI/produce 下有 hajime_route.py、nia_route.py、pickv.py，主数据目录 GakumasCLI/ro/master/tables 存在。
Python：HIRO_GKMS_PYTHON → <home>/python/python(.exe)（Windows 安装版自带）→ <home>/.venv/bin/python → hiro 自己的解释器。
HIRO_GKMS=0 时一律当没装。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("hajime_route.py", "nia_route.py", "pickv.py")
DEFAULT_HOMES = [Path.home() / "github" / "_ext" / "GKMSProject"]

_problem: str | None = None


@dataclass(frozen=True)
class Install:
    home: Path
    python: str
    version: str | None = None


def _missing(home: Path) -> list[str]:
    prod = home / "GakumasCLI" / "produce"
    out = [m for m in REQUIRED if not (prod / m).is_file()]
    if not (home / "GakumasCLI" / "ro" / "master" / "tables").is_dir():
        out.append("GakumasCLI/ro/master/tables")
    return out


def _candidates() -> list[Path]:
    out: list[Path] = []
    if env := os.environ.get("HIRO_GKMS_HOME"):
        return [Path(env).expanduser()]
    if lad := os.environ.get("LOCALAPPDATA"):
        base = Path(lad) / "Programs" / "GKMS"
        out.append(base)
        if base.is_dir():
            out += sorted(p for p in base.iterdir() if p.is_dir())
    return out + list(DEFAULT_HOMES)


def _python(home: Path) -> str:
    if env := os.environ.get("HIRO_GKMS_PYTHON"):
        return env
    for p in (home / "python" / "python.exe", home / "python" / "python", home / ".venv" / "Scripts" / "python.exe",
              home / ".venv" / "bin" / "python"):  # fmt: skip
        if p.is_file():
            return str(p)
    return sys.executable


def _version(home: Path) -> str | None:
    """GKMS 版本：git 提交（克隆）或安装目录里的 VERSION 文件；都没有为 None。"""
    for name in ("VERSION", "version.txt"):
        f = home / name
        if f.is_file():
            return f.read_text(encoding="utf-8", errors="replace").strip()[:40] or None
    if (home / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(home), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
            return r.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def find() -> Install | None:
    """本机的 GKMS；没装、版本太旧、HIRO_GKMS=0 时返回 None，原因见 problem()。"""
    global _problem
    if os.environ.get("HIRO_GKMS") == "0":
        _problem = "HIRO_GKMS=0：GKMS 插件关着"
        return None
    old: list[str] = []
    for home in _candidates():
        if not (home / "GakumasCLI").is_dir():
            continue
        miss = _missing(home)
        if miss:
            old.append(f"{home}（缺 {'、'.join(miss)}）")
            continue
        _problem = None
        return Install(home=home, python=_python(home), version=_version(home))
    _problem = f"GKMS 版本太旧：{'；'.join(old)}" if old else "没找到 GKMS（设 HIRO_GKMS_HOME 指向 GKMSProject 目录）"
    return None


def problem() -> str | None:
    """上一次 find() 没找到的原因。"""
    return _problem
