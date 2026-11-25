"""Windows 桌面暂时不可用时等它恢复（DMM 设备 hiro.devices.dmm_win32 用）。

DMM 版游戏认真实光标位置，点击前要读光标（GetCursorPos）。调用线程的桌面不是当前输入桌面时（UAC 安全桌面、锁屏、
远程连接切桌面），读光标报 (5, 拒绝访问)。2026-11-10 DMM 本战 dmm1m：另一个会话弹了 UAC，正好撞上换卡点击，
主循环 40 秒内连崩 5 次；UAC 取消后桌面马上就恢复了。所以遇到拒绝访问就每 POLL_S 秒再试，桌面恢复后重做这一下；
等满 DESKTOP_WAIT_S 还不行抛 DesktopUnavailable（讲清原因，由人处理）。原在 hiro.loop.win_desktop，随 KAA 设备层删了。
"""

from __future__ import annotations

import time
from collections.abc import Callable

DESKTOP_WAIT_S = 600.0
POLL_S = 2.0


class DesktopUnavailable(RuntimeError):
    """Windows 桌面长时间不可用（UAC / 锁屏 / 远程切桌面），后台点击读不到光标。"""


def access_denied(err: BaseException) -> bool:
    """OSError / pywintypes.error(5, 'GetCursorPos', '拒绝访问。') 之类：winerror 5 / 第一个参数是 5。"""
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
