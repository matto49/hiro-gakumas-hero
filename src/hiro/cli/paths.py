"""工具层和任务用的路径。hiro 的很多工具按当前目录找 resources/、conf/；控制台的任务在 HIRO_HOME/work 里跑、
命令行可能在任何目录被调用，所以这里把它们换成明确的绝对路径（环境变量里已经设了的不动）。"""

from __future__ import annotations

import os
from pathlib import Path

import hiro

REPO = Path(hiro.__file__).resolve().parents[2]


def plan_path(home: Path) -> Path:
    return home / "formation-plan.json"


def runtime_env(home: Path) -> dict[str, str]:
    """要补的环境变量：主数据（安装脚本下到仓库的 resources/game_data）、自建素材、编成方案（放在 HIRO_HOME）。"""
    return {
        "HIRO_KAA_GAME_DATA": str(REPO / "resources" / "game_data"),
        "HIRO_USER_ASSETS": str(REPO / "resources" / "user_assets"),
        "HIRO_FORMATION_PLAN": str(plan_path(home)),
    }


def apply_defaults(env: dict, home: Path) -> dict:
    for k, v in runtime_env(home).items():
        if not env.get(k):
            env[k] = v
    return env


def apply_to_process(home: Path) -> None:
    """命令行进程自己也要读主数据（出编成方案）时用。"""
    apply_defaults(os.environ, home)  # type: ignore[arg-type]
