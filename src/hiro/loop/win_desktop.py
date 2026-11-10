"""DMM 版（Windows 后台点击）：桌面暂时不可用时等它恢复，而不是让主循环连崩。

KAA / kotonebot 的 DMM 后台输入（SendMessageImpl）每次点击、滑动都要读真实光标：先等光标静止（_wait_cursor_idle），
再把游戏窗口挪到「目标点落在光标下」（_align_window），然后发鼠标消息——DMM 版游戏认的是真实光标位置，读不到就点不准。
调用线程的桌面不是当前输入桌面时（UAC 安全桌面、锁屏、远程连接切桌面），GetCursorPos 报 (5, 拒绝访问)。

2026-11-10 DMM 本战 dmm1m 21:16：另一个会话在 Windows 上弹了 UAC（切到安全桌面），正好撞上换卡点击，
主循环 40 秒内连崩 5 次退出（EXIT 1）；UAC 取消后桌面马上就恢复了。

这里给 SendMessageImpl.click / swipe 包一层：遇到「拒绝访问」就每 POLL_S 秒试一次 GetCursorPos，桌面恢复后重做这一下；
等满 DESKTOP_WAIT_S 还不行，抛 DesktopUnavailable（讲清原因，由人处理）。其他错误照常抛出。

滑动另改一处（HIRO_DMM_SWIPE_HOLD，缺省开）：kotonebot 的拖拽匀速移到终点后立刻松开，游戏的滚动列表按松手瞬间的速度
惯性继续滑，选卡对话框一次多滑几十到一百多像素（行距 147、一屏露 3 行），相邻两屏没有重叠的行 → 牌组拼接不可信
（2026-11-10 dmm1m 本战相談删 眠気：「第 2 屏和上一屏没有重叠的行」「拼出 38 张，记账 22 张」，重复无效 3 次停机）。
改成到终点后原地停 HOLD_S 秒（继续发同一位置的移动消息）再松手，松手速度≈0，不再惯性滑动——和人手「拖到位、停一下、再抬手」一样。
"""

from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)

DESKTOP_WAIT_S = 600.0
POLL_S = 2.0
HOLD_S = 0.3  # 拖到终点后原地停多久再松手（60 Hz 下约 18 帧，滚动列表的速度衰减到几乎为 0）
FPS = 60


class DesktopUnavailable(RuntimeError):
    """Windows 桌面长时间不可用（UAC / 锁屏 / 远程切桌面），后台点击读不到光标。"""


def access_denied(err: BaseException) -> bool:
    """pywintypes.error(5, 'GetCursorPos', '拒绝访问。') 之类：winerror 5 / 第一个参数是 5。"""
    code = getattr(err, "winerror", None)
    if code is None and getattr(err, "args", None):
        code = err.args[0]
    return code == 5


def wait_desktop(probe: Callable[[], object], *, timeout: float = DESKTOP_WAIT_S, poll: float = POLL_S,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> bool:  # fmt: skip
    """反复调 probe（读光标），直到不再报拒绝访问；timeout 内恢复返回 True。其他错误照常抛出。"""
    t0 = clock()
    while True:
        try:
            probe()
            return True
        except Exception as e:  # noqa: BLE001
            if not access_denied(e):
                raise
        if clock() - t0 >= timeout:
            return False
        sleep(poll)


def guard(method: Callable, probe: Callable[[], object], *, name: str, **wait_kw) -> Callable:
    """包一个输入方法：拒绝访问时等桌面恢复再重做一次。"""

    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if not access_denied(e):
                raise
            logger.warning("DMM 后台%s读不到光标（%r）：桌面暂时不可用（UAC / 锁屏 / 远程切桌面？），等它恢复", name, e)
            t0 = time.monotonic()
            if not wait_desktop(probe, **wait_kw):
                raise DesktopUnavailable(f"Windows 桌面 {wait_kw.get('timeout', DESKTOP_WAIT_S):.0f} 秒都不可用（UAC 确认框 / 锁屏 / "
                                         f"远程连接切桌面？），DMM 后台{name}读不到光标。处理掉后用 resume 续跑") from e  # fmt: skip
            logger.info("桌面恢复（等了 %.0f 秒），重做这一下%s", time.monotonic() - t0, name)
            return method(self, *args, **kwargs)

    wrapped._hiro_desktop_guard = True  # type: ignore[attr-defined]
    return wrapped


def swipe_hold(self, x1: int, y1: int, x2: int, y2: int, duration: float | None = None, *, hold: float = HOLD_S,
               sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
    """SendMessageImpl.swipe 的替换：同 kotonebot 的拖拽（对齐窗口 → 按下 → 60 Hz 插值移动），到终点后原地停 hold 秒再松开。"""
    w = self._require_wrapper()
    duration = 0.5 if duration is None else duration
    w._send_activate()
    w._align_window(x1, y1)
    if not w._send_mouse_button(x1, y1, "left", down=True):
        raise RuntimeError("Swipe operation failed（按下）")
    steps = max(1, int(duration * FPS))
    for i in range(1, steps + 1):
        t = i / steps
        xi, yi = int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t)
        w._send_mouse_move(xi, yi, button="left")
        w._align_window(xi, yi)
        sleep(duration / steps)
    for _ in range(max(1, int(hold * FPS))):  # 原地停：继续发同一位置的移动，让滚动列表的速度衰减到 0
        w._send_mouse_move(x2, y2, button="left")
        w._align_window(x2, y2)
        sleep(1 / FPS)
    if not w._send_mouse_button(x2, y2, "left", down=False):
        raise RuntimeError("Swipe operation failed（松开）")


_APPLIED = False


def apply() -> bool:
    """给 kotonebot 的 SendMessageImpl.click / swipe 装上等待（和滑动停顿）；不是 Windows / 没有这个实现时什么都不做。
    返回是否装上。重复调用只装一次。"""
    global _APPLIED
    if _APPLIED:
        return True
    try:
        import win32gui  # noqa: F401
        from kotonebot.client.implements.windows.send_message import SendMessageImpl
    except Exception:  # noqa: BLE001  非 Windows / 没装 pywin32
        return False
    import os

    import win32gui

    if os.environ.get("HIRO_DMM_SWIPE_HOLD", "1") != "0":
        SendMessageImpl.swipe = swipe_hold
    for attr, name in (("click", "点击"), ("swipe", "滑动")):
        fn = getattr(SendMessageImpl, attr, None)
        if fn is not None and not getattr(fn, "_hiro_desktop_guard", False):
            setattr(SendMessageImpl, attr, guard(fn, win32gui.GetCursorPos, name=name))
    _APPLIED = True
    return True
