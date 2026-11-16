"""控制台的数据目录（信息库、资料库、任务、待确认）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = "hiro-console"


def hiro_home() -> Path:
    """HIRO_HOME 环境变量优先；缺省 Mac ~/Library/Application Support/hiro-console，Windows %APPDATA%\\hiro-console，其他 ~/.local/share/hiro-console。"""
    env = os.environ.get("HIRO_HOME")
    if env:
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_DIR


def ensure_home() -> Path:
    h = hiro_home()
    h.mkdir(parents=True, exist_ok=True)
    return h
