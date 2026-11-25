# 部分逻辑基于 KAA（kotones-auto-assistant / kotonebot，GPL-3.0）的 Windows 后台截图与点击（PrintWindow、SendMessageImpl）
"""DMM 版（Windows）：设备统一接口的实现，自己调 Win32（ctypes），不依赖 MaaFramework / KAA / pywin32。

截图（HIRO_DMM_CAPTURE）：
    printwindow  缺省。PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT)，和 KAA 现在的 DMM 截图同一种，窗口不用在前台。
                 游戏以管理员运行，hiro 也要以管理员跑（点击本来就要），2026-11-11 管理员下约 0.19 秒一张。
    gdi          BitBlt 窗口 DC。2026-11-11 普通权限实测约 11 ms 一张、画面正常；窗口被挡住 / 挪出屏幕的部分截不到，先作实验选项。
点击 / 滑动：DMM 版游戏认的是真实光标位置。做法同 KAA（SendMessageImpl）：等光标停稳 → 把窗口挪到「目标点落在光标下」→
    PostMessage 发鼠标消息，不动真实鼠标。滑动到终点原地停 hold 秒再松手（列表不按松手速度惯性多滑，原 hiro.loop.win_desktop 的做法）。
    读光标报「拒绝访问」（UAC / 锁屏 / 远程切桌面）时等桌面恢复再做这一下，最多 hiro.devices.desktop.DESKTOP_WAIT_S 秒。

截图给窗口原生尺寸，LogicalScreen 一次拉伸到 720x1280（同 KAA）；点击按比例换回原生像素 = 客户区坐标。

2026-11-25 hiro-w4 实机（管理员，客户区 541x960，游戏窗口被别的窗口挡着）：PrintWindow 约 34 ms 一张、画面正确；GDI 截到的是挡在上面的窗口
（普通权限）或全黑（管理员），所以不当缺省。点击：标题页 → 登录奖励 → 关弹窗 → 首页一路点通；底部导航アイドル ↔ ホーム 3/3、
首页横幅滑动 2/2（tools/devices_live_navswipe.py）。
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from .base import DeviceGone, DeviceUnavailable, LogicalScreen, settled_frame

WINDOW_TITLE = "gakumas"
WINDOW_CLASS = "UnityWndClass"
CAPTURES = ("printwindow", "gdi")
SWIPE_FPS = 60
CURSOR_IDLE_SPEED = 50  # 像素 / 秒：光标比这慢就算停稳（同 KAA）
CURSOR_SAMPLE_S = 0.05

WM_ACTIVATE, WA_ACTIVE = 0x0006, 1
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
MK_LBUTTON = 0x0001


def _lparam(x: int, y: int) -> int:
    return (y << 16) | (x & 0xFFFF)


class Win32:
    """用到的几个 Win32 调用（ctypes）。只在 Windows 上能用；测试换成假的。"""

    _ct: Any
    _wt: Any
    user32: Any
    gdi32: Any

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):  # 不写 != "win32"：类型检查按 Linux 分析时会把后面整段当成走不到
            raise DeviceUnavailable("DMM 版只能在 Windows 上用")
        import ctypes
        from ctypes import wintypes

        self._ct, self._wt = ctypes, wintypes
        dll = getattr(ctypes, "WinDLL")  # noqa: B009  按名字取：类型检查按 Linux 分析，静态访问会报不存在
        self.user32 = dll("user32", use_last_error=True)
        self.gdi32 = dll("gdi32", use_last_error=True)
        try:  # noqa: SIM105
            self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2：拿物理像素
        except Exception:  # noqa: BLE001  老系统没有这个函数
            pass
        self.user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self.user32.PostMessageW.restype = wintypes.BOOL

    def _err(self, what: str) -> OSError:
        code = self._ct.get_last_error()
        e = OSError(code, f"{what} 失败（错误码 {code}）")
        e.winerror = code  # type: ignore[attr-defined]  hiro.devices.desktop.access_denied 按它认「拒绝访问」
        return e

    def find(self, title: str) -> int | None:
        return int(self.user32.FindWindowW(WINDOW_CLASS, title) or 0) or None

    def is_window(self, hwnd: int | None) -> bool:
        return bool(hwnd) and bool(self.user32.IsWindow(hwnd))

    def client_size(self, hwnd: int) -> tuple[int, int]:
        r = self._wt.RECT()
        if not self.user32.GetClientRect(hwnd, self._ct.byref(r)):
            raise self._err("GetClientRect")
        return r.right - r.left, r.bottom - r.top

    def client_origin(self, hwnd: int) -> tuple[int, int]:
        p = self._wt.POINT(0, 0)
        if not self.user32.ClientToScreen(hwnd, self._ct.byref(p)):
            raise self._err("ClientToScreen")
        return p.x, p.y

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        r = self._wt.RECT()
        if not self.user32.GetWindowRect(hwnd, self._ct.byref(r)):
            raise self._err("GetWindowRect")
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def move_window(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        # SWP_NOREDRAW | NOACTIVATE | NOZORDER | NOCOPYBITS | NOSENDCHANGING | NOOWNERZORDER（同 KAA）
        flags = 0x0008 | 0x0010 | 0x0004 | 0x0100 | 0x0400 | 0x0200
        if not self.user32.SetWindowPos(hwnd, None, x, y, w, h, flags):
            raise self._err("SetWindowPos")

    def cursor_pos(self) -> tuple[int, int]:
        p = self._wt.POINT()
        if not self.user32.GetCursorPos(self._ct.byref(p)):
            raise self._err("GetCursorPos")
        return p.x, p.y

    def post(self, hwnd: int, msg: int, wparam: int, lparam: int) -> None:
        # 成功时 PostMessage 不清错误码，只看返回值（原 hiro.loop.win_desktop.post_message 的教训）
        if not self.user32.PostMessageW(hwnd, msg, wparam, lparam):
            raise self._err("PostMessage")

    def capture(self, hwnd: int, method: str) -> np.ndarray:
        """客户区截图（BGR）。printwindow / gdi。"""
        import cv2

        ct, u, g = self._ct, self.user32, self.gdi32
        w, h = self.client_size(hwnd)
        if w <= 0 or h <= 0:
            raise OSError(f"客户区尺寸不对：{w}x{h}")
        hdc = u.GetDC(hwnd)
        if not hdc:
            raise self._err("GetDC")
        mem = bmp = old = None
        try:
            mem = g.CreateCompatibleDC(hdc)
            bmp = g.CreateCompatibleBitmap(hdc, w, h)
            if not mem or not bmp:
                raise self._err("CreateCompatibleDC / Bitmap")
            old = g.SelectObject(mem, bmp)
            if method == "printwindow":
                ok = u.PrintWindow(hwnd, mem, 0x1 | 0x2)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            else:
                ok = g.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)  # SRCCOPY
            if not ok:
                raise self._err("PrintWindow" if method == "printwindow" else "BitBlt")

            class BITMAPINFOHEADER(ct.Structure):
                _fields_ = (("biSize", ct.c_uint32), ("biWidth", ct.c_int32), ("biHeight", ct.c_int32),
                            ("biPlanes", ct.c_uint16), ("biBitCount", ct.c_uint16), ("biCompression", ct.c_uint32),
                            ("biSizeImage", ct.c_uint32), ("biXPelsPerMeter", ct.c_int32), ("biYPelsPerMeter", ct.c_int32),
                            ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32))  # fmt: skip

            bi = BITMAPINFOHEADER(ct.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # 自上而下、BI_RGB
            buf = (ct.c_ubyte * (w * h * 4))()
            g.SelectObject(mem, old)  # GetDIBits 要求位图此时没有选进任何 DC
            old = None
            if not g.GetDIBits(mem, bmp, 0, h, buf, ct.byref(bi), 0):  # DIB_RGB_COLORS
                raise self._err("GetDIBits")
            img = np.frombuffer(buf, np.uint8).reshape(h, w, 4)
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        finally:
            if mem and old:
                g.SelectObject(mem, old)
            if bmp:
                g.DeleteObject(bmp)
            if mem:
                g.DeleteDC(mem)
            u.ReleaseDC(hwnd, hdc)


class DmmWin32Device:
    """设备统一接口：DMM 版学マス（Windows，自己调 Win32）。"""

    name = "dmm-win32"

    def __init__(self, *, capture: str | None = None, title: str = WINDOW_TITLE, api=None,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self.capture = (capture or os.environ.get("HIRO_DMM_CAPTURE") or "printwindow").strip().lower()
        if self.capture not in CAPTURES:
            raise ValueError(f"HIRO_DMM_CAPTURE={self.capture!r} 不认识；可选：{' / '.join(CAPTURES)}")
        self.title = title
        self._api_override = api  # 测试注入假的 Win32
        self._api = None
        self._hwnd: int | None = None
        self._sleep = sleep
        self.screen = LogicalScreen()

    @property
    def orientation(self) -> str:
        return self.screen.orientation

    def api(self):
        if self._api is None:
            self._api = self._api_override or Win32()
        return self._api

    # -- 连接
    def _window(self) -> int:
        """游戏窗口句柄：上次的还在就用，否则重新找（游戏重开后句柄会变）。找不到抛 DeviceGone。"""
        a = self.api()
        if self._hwnd is not None and a.is_window(self._hwnd):
            return self._hwnd
        self._hwnd = a.find(self.title)
        if self._hwnd is None:
            raise DeviceGone(
                f"找不到游戏窗口（标题 {self.title!r}、类 {WINDOW_CLASS}）：先从 DMM Game Player 启动学マス"
            )
        return self._hwnd

    def connect(self) -> None:
        try:
            image = self._grab()
        except DeviceGone as e:  # 连接时找不到窗口算「连不上」
            raise DeviceUnavailable(str(e)) from e
        if not image.any():
            other = "gdi" if self.capture == "printwindow" else "printwindow"
            raise DeviceUnavailable(
                f"截图方式 {self.capture} 截出来全黑：游戏窗口最小化了？或者换 HIRO_DMM_CAPTURE={other} 试试"
            )
        first = [image]
        settled_frame(lambda: first.pop() if first else self._grab(), self.screen)  # 连上那一刻正在横竖屏切换也等一下

    def close(self) -> None:
        self._hwnd = None

    def alive(self) -> bool:
        try:
            self._window()
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- 截图
    def _grab(self) -> np.ndarray:
        """截一张客户区；失败时重新找窗口再截一次。窗口没了抛 DeviceGone，还在但截不了抛 DeviceUnavailable。"""
        err: Exception | None = None
        for _ in (0, 1):
            hwnd = self._window()
            try:
                image = self.api().capture(hwnd, self.capture)
            except Exception as e:  # noqa: BLE001  GDI 资源、窗口正在关……都重试一次
                err, self._hwnd = e, None
                continue
            if image is None or not getattr(image, "size", 0):
                err, self._hwnd = DeviceUnavailable("截图是空的"), None
                continue
            return np.ascontiguousarray(image)
        self._window()  # 窗口没了：抛 DeviceGone
        raise DeviceUnavailable(f"截图失败（{self.capture}）：{err}")

    def screenshot(self) -> np.ndarray:
        """截图 → 逻辑分辨率；窗口正在横竖屏切换时重截（base.settled_frame）。"""
        return settled_frame(self._grab, self.screen)

    # -- 点击 / 滑动
    def _native(self, x: float, y: float) -> tuple[int, int]:
        if self.screen.native is None:
            self.screenshot()
        return self.screen.to_native(x, y)

    def _cursor(self) -> tuple[int, int]:
        """读真实光标；桌面暂时不可用（拒绝访问）时等它恢复，等不到抛 DesktopUnavailable（hiro.devices.desktop）。"""
        from hiro.devices.desktop import DESKTOP_WAIT_S, DesktopUnavailable, access_denied, wait_desktop

        a = self.api()
        try:
            return a.cursor_pos()
        except Exception as e:  # noqa: BLE001
            if not access_denied(e):
                raise
        if not wait_desktop(a.cursor_pos, sleep=self._sleep):
            raise DesktopUnavailable(f"Windows 桌面 {DESKTOP_WAIT_S:.0f} 秒都不可用（UAC 确认框 / 锁屏 / 远程连接切桌面？），"
                                     "DMM 点击读不到光标。处理掉后用 resume 续跑")  # fmt: skip
        return a.cursor_pos()

    def _wait_cursor_idle(self) -> None:
        """等真实光标停稳：窗口跟着光标挪，光标还在动时点会偏（同 KAA 的 _wait_cursor_idle）。"""
        prev, t0 = self._cursor(), time.monotonic()
        for _ in range(200):  # 最多约 10 秒：一直有人在动鼠标就不等了
            self._sleep(CURSOR_SAMPLE_S)
            cur, t1 = self._cursor(), time.monotonic()
            dt = max(t1 - t0, CURSOR_SAMPLE_S)
            if ((cur[0] - prev[0]) ** 2 + (cur[1] - prev[1]) ** 2) ** 0.5 / dt <= CURSOR_IDLE_SPEED:
                return
            prev, t0 = cur, t1

    def _align(self, hwnd: int, x: int, y: int) -> None:
        """挪窗口，让客户区 (x, y) 正好落在真实光标下。"""
        a = self.api()
        cx, cy = self._cursor()
        wx, wy, ww, wh = a.window_rect(hwnd)
        ox, oy = a.client_origin(hwnd)
        a.move_window(hwnd, cx - x - (ox - wx), cy - y - (oy - wy), ww, wh)

    def _input(self, what: str, fn: Callable[[int], None]) -> None:
        """发一组输入；失败时重新找窗口再来一次。窗口没了抛 DeviceGone。"""
        from hiro.devices.desktop import DesktopUnavailable

        err: Exception | None = None
        for _ in (0, 1):
            hwnd = self._window()
            try:
                fn(hwnd)
                return
            except DesktopUnavailable:
                raise
            except Exception as e:  # noqa: BLE001
                err, self._hwnd = e, None
        self._window()
        raise DeviceUnavailable(f"{what}失败：{err}")

    def tap(self, x: int, y: int) -> None:
        nx, ny = self._native(x, y)

        def go(hwnd: int) -> None:
            a = self.api()
            a.post(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
            self._wait_cursor_idle()
            self._align(hwnd, nx, ny)
            a.post(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, _lparam(nx, ny))
            a.post(hwnd, WM_LBUTTONUP, 0, _lparam(nx, ny))

        self._input("点击", go)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, *, duration: float = 0.3, hold: float = 0.0) -> None:
        a1, b1 = self._native(x1, y1)
        a2, b2 = self.screen.to_native(x2, y2)
        duration = duration if duration and duration > 0 else 0.3

        def go(hwnd: int) -> None:
            a = self.api()
            a.post(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
            self._wait_cursor_idle()
            self._align(hwnd, a1, b1)
            a.post(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, _lparam(a1, b1))
            steps = max(2, int(duration * SWIPE_FPS))
            for i in range(1, steps + 1):
                t = i / steps
                px, py = round(a1 + (a2 - a1) * t), round(b1 + (b2 - b1) * t)
                a.post(hwnd, WM_MOUSEMOVE, MK_LBUTTON, _lparam(px, py))
                self._align(hwnd, px, py)
                self._sleep(duration / steps)
            for _ in range(int(hold * SWIPE_FPS)):  # 原地停：继续发同一位置的移动，让滚动列表的速度衰减到 0
                a.post(hwnd, WM_MOUSEMOVE, MK_LBUTTON, _lparam(a2, b2))
                self._align(hwnd, a2, b2)
                self._sleep(1 / SWIPE_FPS)
            a.post(hwnd, WM_LBUTTONUP, 0, _lparam(a2, b2))

        self._input("滑动", go)
