"""跑完就关游戏 / 模拟器（用户 2026-11-20：做完不管是什么都自动关；默认连模拟器一起关）。

开关 HIRO_CLOSE_GAME（缺省关，见 AGENTS.md「改变实机行为的改动默认关」）：
- off / 0 / 不设：不关；
- on / 1 / emulator：关模拟器（MuMu：MuMuManager control shutdown；雷电：ldconsole quit；DMM：关游戏 + DMM Game Player）；
- game：只关游戏（安卓后端 am force-stop / MuMuManager app close；DMM taskkill gakumas.exe）。
培育进度在服务器上，关掉游戏不会丢；下次 `run_hif resume` 接着跑。

谁来关：只由「整局的发起方」关一次（review #13）：
- tools/play_game.py 跑完整局（选拔 + 本战）后关一次；
- 单段的 python -m hiro.run_hif 不关（初星助手等把选拔、本战拆成两个任务跑，DMM 关了以后程序拉不起来，本战就开不了局）；
  别的发起方（任务收尾、自己的脚本）要关就调 close_at_exit；
- 未读剧情等人超时（hiro.runtime.unread_story）不看开关，直接按 HIRO_CLOSE_GAME 的目标关（没开时关模拟器）。
不 import kotonebot / KAA：进程收尾时 KAA 上下文可能已经没了，这里只跑外部命令。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

PACKAGE = "com.bandainamcoent.idolmaster_gakuen"
DMM_GAME_EXE = "gakumas.exe"
DMM_PLAYER_EXE = "DMMGamePlayer.exe"
MUMU_MANAGER_CANDIDATES = (
    r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",  # MuMu 12（4.x 起）
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",  # MuMu 12 旧版目录
)
MUMU_BASE_PORT, MUMU_PORT_STEP = 16384, 32  # MuMu 12 实例 N 的 adb 端口 = 16384 + 32·N

Runner = Callable[[list[str]], int]


def target(env: Mapping[str, str] | None = None) -> str:
    """HIRO_CLOSE_GAME → off / game / emulator。写错了按 off（记 WARNING），不猜。"""
    v = ((env if env is not None else os.environ).get("HIRO_CLOSE_GAME") or "off").strip().lower()
    if v in ("", "0", "off", "no", "false"):
        return "off"
    if v in ("1", "on", "yes", "true", "emulator"):
        return "emulator"
    if v == "game":
        return "game"
    logger.warning("HIRO_CLOSE_GAME=%r 不认识（off / on / emulator / game），不关", v)
    return "off"


def deferred_to_parent(env: Mapping[str, str] | None = None) -> bool:
    """由父进程（play_game）负责关：run_hif 子进程退出时不关。"""
    return (env if env is not None else os.environ).get("HIRO_CLOSE_GAME_BY_PARENT") == "1"


def device_kind(env: Mapping[str, str] | None = None) -> str:
    env = env if env is not None else os.environ
    d = (env.get("HIRO_DEVICE") or "auto").strip().lower()
    if d == "auto":
        return "maatools" if sys.platform == "darwin" else "adb"
    return d


def _instance(env: Mapping[str, str]) -> int:
    try:
        return int(env.get("HIRO_EMULATOR_INSTANCE") or 0)
    except ValueError:
        return 0


def mumu_manager(env: Mapping[str, str] | None = None, exists: Callable[[str], bool] = os.path.exists) -> str | None:
    """MuMuManager.exe：HIRO_MUMU_MANAGER → HIRO_ADB_PATH 同目录 → 常见安装位置。"""
    env = env if env is not None else os.environ
    cands = [env.get("HIRO_MUMU_MANAGER")]
    if env.get("HIRO_ADB_PATH"):
        cands.append(str(Path(env["HIRO_ADB_PATH"]).with_name("MuMuManager.exe")))
    cands += list(MUMU_MANAGER_CANDIDATES)
    return next((c for c in cands if c and exists(c)), None)


def commands(
    kind: str, what: str, env: Mapping[str, str] | None = None, exists: Callable[[str], bool] = os.path.exists
) -> list[list[str]]:
    """要跑的命令（按顺序，前一条失败照样跑后一条）。what：game / emulator。"""
    env = env if env is not None else os.environ
    inst = _instance(env)
    adb = env.get("HIRO_ADB_PATH") or "adb"
    if kind == "dmm":
        cmds = [["taskkill", "/f", "/im", DMM_GAME_EXE]]
        return cmds + ([["taskkill", "/f", "/im", DMM_PLAYER_EXE]] if what == "emulator" else [])
    if kind == "mumu":
        mgr = mumu_manager(env, exists)
        if mgr and what == "emulator":
            return [[mgr, "control", "-v", str(inst), "shutdown"]]
        if mgr:
            return [[mgr, "control", "-v", str(inst), "app", "close", "-pkg", PACKAGE]]
        serial = env.get("HIRO_ADB") or f"127.0.0.1:{MUMU_BASE_PORT + MUMU_PORT_STEP * inst}"
        logger.warning("找不到 MuMuManager.exe（可设 HIRO_MUMU_MANAGER）：只用 adb 关游戏，模拟器不关")
        return [[adb, "-s", serial, "shell", "am", "force-stop", PACKAGE]]
    if kind in ("adb", "leidian"):
        serial = env.get("HIRO_ADB") or "127.0.0.1:5555"
        cmds = [[adb, "-s", serial, "shell", "am", "force-stop", PACKAGE]]
        ld = env.get("HIRO_LDCONSOLE")
        if what == "emulator" and kind == "leidian" and ld:
            cmds.append([ld, "quit", "--index", str(inst)])
        elif what == "emulator":
            logger.warning("%s 后端不知道怎么关模拟器（雷电可设 HIRO_LDCONSOLE=ldconsole.exe 路径）：只关游戏", kind)
        return cmds
    logger.warning("%s 后端不支持自动关游戏（PlayCover 请手动关）", kind)
    return []


def _run(cmd: list[str]) -> int:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("关游戏命令失败：%s（%r）", " ".join(cmd), e)
        return -1
    out = " ".join((r.stdout + " " + r.stderr).split())[:200]
    (logger.info if r.returncode == 0 else logger.warning)("关游戏：%s → %d %s", " ".join(cmd), r.returncode, out)
    return r.returncode


def close(what: str | None = None, *, why: str = "", env: Mapping[str, str] | None = None, run: Runner = _run,
          exists: Callable[[str], bool] = os.path.exists) -> bool:  # fmt: skip
    """按 what（缺省读 HIRO_CLOSE_GAME）关游戏 / 模拟器。what 为 off 时什么都不做。返回有没有至少一条命令成功。"""
    env = env if env is not None else os.environ
    what = target(env) if what is None else what
    if what == "off":
        return False
    kind = device_kind(env)
    cmds = commands(kind, what, env, exists)
    if not cmds:
        return False
    logger.warning("关%s（%s 后端）%s", "模拟器" if what == "emulator" else "游戏", kind, f"：{why}" if why else "")
    return any([run(c) == 0 for c in cmds])  # noqa: C419  列表：每条关闭命令都要跑，不能遇到第一个成功就停


def close_at_exit(*, why: str = "跑完了", env: Mapping[str, str] | None = None, run: Runner = _run) -> bool:
    """进程收尾时调：开关开着、且不是交给父进程关的子进程，才关。"""
    env = env if env is not None else os.environ
    if deferred_to_parent(env):
        return False
    return close(why=why, env=env, run=run)
