"""真实鼠标点击游戏窗口里的逻辑坐标（720×1280）。MaaTools 点了画面不变时的退路。

第二十三局：差し入れ饮料满换饮料，详情框里的「捨てる」、确认框「はい」、底部「受け取る」用 MaaTools 点都没反应，
总控用真实鼠标点才生效（参考 .temp/real_paste.py：Quartz 找 PlayCover 游戏窗口 → 按窗口宽度换算 → 鼠标按下 / 抬起）。
会把前台切到游戏、移动用户的鼠标，只在 MaaTools 点击确认没生效时用。
"""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger(__name__)

APP_ID = "jp.co.bandainamcoent.BNEI0421"
OWNERS = ("学マス", "idolmaster_gakuen")
TITLE_BAR = 28  # PlayCover 窗口标题栏高度（kCGWindowBounds 含标题栏）
LOGICAL_W = 720
PRESS_S = 0.08


def window_bounds() -> tuple[float, float, float, float]:
    """游戏窗口 (x, y, w, h)（屏幕坐标，含标题栏）。找不到抛 RuntimeError。"""
    import Quartz

    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID):
        if "学マス" in str(w.get("kCGWindowName", "")) or w.get("kCGWindowOwnerName", "") in OWNERS:
            b = w["kCGWindowBounds"]
            return b["X"], b["Y"], b["Width"], b["Height"]
    raise RuntimeError("找不到游戏窗口")


def to_screen(lx: float, ly: float, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    x, y, w, _ = bounds
    scale = w / LOGICAL_W
    return x + lx * scale, y + TITLE_BAR + ly * scale


def click(lx: int, ly: int) -> None:
    """切到游戏窗口，在逻辑坐标 (lx, ly) 处真实鼠标点一下。"""
    import Quartz

    subprocess.run(["osascript", "-e", f'tell application id "{APP_ID}" to activate'], check=False, timeout=5)
    time.sleep(0.3)
    sx, sy = to_screen(lx, ly, window_bounds())
    for t in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        e = Quartz.CGEventCreateMouseEvent(None, t, (sx, sy), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(PRESS_S)
    logger.warning("真实鼠标点击 (%d, %d) → 屏幕 (%.0f, %.0f)", lx, ly, sx, sy)
