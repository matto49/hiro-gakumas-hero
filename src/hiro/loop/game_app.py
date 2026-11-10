"""安卓后端（adb / mumu / leidian）的游戏进程看守：游戏不在前台时认出来，按开关重新拉起。

GKMS 第五局（Windows + MuMu，2026-11-09 06:45）：可用内存只剩 150–190 MB 时学マス进程被杀，前台变成 MuMu 的安卓桌面
（横屏 1600x900）；之后 adb screencap 反复报错、截到横屏桌面，按竖屏坐标裁图裁出空图，主循环连崩 5 次退出。
- foreground()：dumpsys window 的 mCurrentFocus 是不是学マス。读不到（不是 adb 后端、adb 出错）返回 None，调用方当作不知道。
- relaunch()：resolve-activity 找启动 Activity，am start -W 拉起（monkey 在 MuMu 上有时只起进程不到前台，07:01 实测）。
  拉起后由 date_change.recover_to_home 走标题页 → 首页（含 MuMu「加速服务」提示点「不加速」）。
- 开关 HIRO_RELAUNCH_GAME=1 才自动拉起（缺省关：只认出来、报清楚原因后停下，由人重启）。
- DMM 版（HIRO_DEVICE=dmm）：没有 adb，按 Windows 上有没有标题为「gakumas」的窗口判断（alive()）。不自动拉起：
  DMM 版游戏要从 DMM Game Player 启动，而且每次都弹管理员确认，程序点不到，只能讲清原因停下。
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

PACKAGE = "com.bandainamcoent.idolmaster_gakuen"


def enabled() -> bool:
    return os.environ.get("HIRO_RELAUNCH_GAME") == "1"


class GameNotForeground(RuntimeError):
    """学マス不在前台（进程被杀 / 闪退）。"""


def adb_shell(device=None):
    """KAA 设备里的 adb shell（AdbImpl.adb.shell），没有返回 None。"""
    if device is None:
        from hiro import device as hdev

        device = getattr(hdev, "DEVICE", None)  # hiro.device.connect() 建的设备
    for name in ("_touch", "_multitouch", "commands", "_screenshot"):
        part = getattr(device, name, None)
        if type(part).__name__ == "AdbImpl" and getattr(part, "adb", None) is not None:
            return part.adb.shell
    return None


def focus_package(text: str) -> str | None:
    """「mCurrentFocus=Window{… u0 包名/Activity}」→ 包名。"""
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", text or "")
    return m.group(1) if m else None


def foreground(shell) -> bool | None:
    if shell is None:
        return None
    try:
        pkg = focus_package(shell("dumpsys window | grep mCurrentFocus"))
    except Exception as e:  # noqa: BLE001
        logger.warning("查前台应用失败：%r", e)
        return None
    if pkg is None:
        return None
    return pkg == PACKAGE


def relaunch(shell) -> bool:
    """am start -W 拉起学マス，返回 dumpsys 看到它到了前台没有。"""
    lines = [ln.strip() for ln in (shell(f"cmd package resolve-activity --brief {PACKAGE}") or "").splitlines() if ln.strip()]
    act = lines[-1] if lines else ""  # 输出两行：priority=… / 包名/Activity
    if "/" not in act:
        logger.error("找不到学マス的启动 Activity：%r", act)
        return False
    logger.warning("学マス不在前台：am start %s", act)
    out = shell(f"am start -W -n {act}") or ""
    logger.info("am start：%s", " ".join(out.split())[:200])
    return foreground(shell) is True


WINDOW_TITLE = "gakumas"  # DMM 版游戏窗口标题（KAA 的 WindowQuery 用同一个）


def is_dmm() -> bool:
    from hiro import device as hdev

    return (getattr(hdev, "BACKEND", None) or os.environ.get("HIRO_DEVICE")) == "dmm"


def dmm_window_alive(find=None) -> bool | None:
    """DMM 版游戏窗口在不在（FindWindowW 按标题找）；不是 Windows / 查不了返回 None。"""
    if find is None:
        try:
            import ctypes

            find = lambda title: ctypes.windll.user32.FindWindowW(None, title)  # noqa: E731
        except (AttributeError, OSError):
            return None
    try:
        return bool(find(WINDOW_TITLE))
    except Exception as e:  # noqa: BLE001
        logger.warning("查 DMM 游戏窗口失败：%r", e)
        return None


def alive() -> bool | None:
    """游戏还在不在：DMM 版看窗口，安卓后端看前台应用；不知道返回 None。"""
    if is_dmm():
        return dmm_window_alive()
    return foreground(adb_shell())


def is_landscape(img) -> bool:
    """截图横屏（宽 > 高）：游戏的 LIVE 演出或者安卓桌面。"""
    return img is not None and getattr(img, "ndim", 0) >= 2 and img.shape[1] > img.shape[0]
