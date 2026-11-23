"""DMM 版（Windows）的另一套截图 / 点击：MaaFramework 的 Win32 控制器（实验，缺省关）。

    HIRO_DEVICE=dmm  HIRO_DMM_DRIVER=maafw        # 不设 HIRO_DMM_DRIVER 时照旧走 KAA（PrintWindow + SendMessage）

MaaFramework（github.com/MaaXYZ/MaaFramework，LGPL-3.0）是 MAA 系列底下的框架，Python 包 maafw。它的 Win32 控制器把截图和
输入都做成可选的方式，按名字选（多个截图方式用「|」连起来，框架会测出能用的里面最快的）：

  HIRO_MAAFW_SCREENCAP  缺省 FramePool。可选 FramePool / PrintWindow / GDI / ScreenDC / DXGI_DesktopDup_Window / DXGI_DesktopDup
  HIRO_MAAFW_INPUT      缺省 SendMessageWithWindowPos。可选 SendMessage / PostMessage / SendMessageWithCursorPos /
                        PostMessageWithCursorPos / SendMessageWithWindowPos / PostMessageWithWindowPos / AnchoredTouch / Seize ……

为什么试它：2026-11-11 在作者的 Windows（DMM 版、日文原版、游戏以管理员运行）上用 maafw 5.16 只读实测，普通权限进程截游戏窗口：
FramePool 1081×1921 约 29 ms、GDI 约 11 ms、ScreenDC 约 44 ms，画面都正常；PrintWindow 连不上（KAA 现在用的就是它，管理员下约
0.19 秒一张）；DXGI_DesktopDup_Window 截到全黑。截图快了几倍，判「画面稳了」的读数也能密得多。
点击还没有实测：游戏以管理员运行，普通权限进程发的消息会被系统挡掉（UIPI），点击测试要以管理员身份跑。缺省的
SendMessageWithWindowPos 和 hiro 现在 DMM 后台点击的做法相同（游戏认真实光标位置，先把窗口挪到目标点落在光标下再发消息，
见 hiro.loop.win_desktop），只是换成框架自带的实现。

坐标：截图按短边缩到 720（1081×1921 → 720×1279），点击坐标用缩放后截图上的坐标，框架换算回窗口像素（MaaFramework 的约定）。
工具：tools/maafw_probe.py 在 Windows 上逐个截图方式试一遍（只截图，不点）。
"""

from __future__ import annotations

import logging
import os
import tempfile
import time

logger = logging.getLogger(__name__)

WINDOW_TITLE = "gakumas"
WINDOW_CLASS = "UnityWndClass"
SHORT_SIDE = 720
DEFAULT_SCREENCAP = "FramePool"
DEFAULT_INPUT = "SendMessageWithWindowPos"
SWIPE_FPS = 60
SWIPE_HOLD_SECONDS = 0.3  # 和 hiro.loop.win_desktop.HOLD_S 一样：拖到终点停一下再松手，滚动列表不带惯性


def driver() -> str:
    """HIRO_DMM_DRIVER：kaa（缺省）或 maafw。"""
    name = (os.environ.get("HIRO_DMM_DRIVER") or "kaa").strip().lower()
    if name not in ("kaa", "maafw"):
        raise ValueError(f"HIRO_DMM_DRIVER={name!r} 不认识；可选：kaa（缺省）、maafw")
    return name


def _maa():
    try:
        from maa.controller import Win32Controller
        from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
        from maa.toolkit import Toolkit
    except ImportError as e:
        raise RuntimeError("HIRO_DMM_DRIVER=maafw 要先装 MaaFramework 的 Python 包：pip install maafw") from e
    return Win32Controller, MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum, Toolkit


def screencap_flags(spec: str, enum) -> int:
    """「FramePool|GDI」→ 位或；名字写错直接报错，列出可选值。"""
    flags = 0
    for part in (p.strip() for p in spec.split("|")):
        if not part:
            continue
        value = getattr(enum, part, None)
        if value is None or part.startswith("_"):
            names = [n for n in dir(enum) if not n.startswith("_") and n not in ("Null", "All")]
            raise ValueError(f"截图方式 {part!r} 不认识；可选：{', '.join(names)}")
        flags |= int(value)
    if not flags:
        raise ValueError("HIRO_MAAFW_SCREENCAP 是空的")
    return flags


def input_method(name: str, enum) -> int:
    value = getattr(enum, name.strip(), None)
    if value is None or name.strip().startswith("_") or name.strip() == "Null":
        names = [n for n in dir(enum) if not n.startswith("_") and n != "Null"]
        raise ValueError(f"输入方式 {name!r} 不认识；可选：{', '.join(names)}")
    return int(value)


def find_window(toolkit, title: str = WINDOW_TITLE):
    """游戏窗口：标题是 gakumas 的 Unity 窗口（DMM 版只有一个）；找不到就讲清楚。"""
    wins = [
        w
        for w in toolkit.find_desktop_windows()
        if (w.window_name or "").strip().lower() == title and (w.class_name or "") == WINDOW_CLASS
    ]
    if not wins:
        raise RuntimeError(f"找不到游戏窗口（标题 {title!r}、类 {WINDOW_CLASS}）：先从 DMM Game Player 启动学マス")
    return wins[0]


class MaaFwWin32Impl:
    """kotonebot Screenshotable + Touchable：经 MaaFramework 的 Win32 控制器截图与点击，坐标是缩放到短边 720 的截图像素。"""

    def __init__(
        self, device, *, screencap: str | None = None, input_name: str | None = None, title: str = WINDOW_TITLE
    ):
        self.device = device
        self.title = title
        self.screencap = screencap or os.environ.get("HIRO_MAAFW_SCREENCAP") or DEFAULT_SCREENCAP
        self.input_name = input_name or os.environ.get("HIRO_MAAFW_INPUT") or DEFAULT_INPUT
        self.swipe_hold = 0.0 if os.environ.get("HIRO_DMM_SWIPE_HOLD", "1") == "0" else SWIPE_HOLD_SECONDS
        self._ctrl = None
        self._hwnd = None
        self._size: tuple[int, int] | None = None

    # -- 连接
    def start(self) -> None:
        self._connect()

    def stop(self) -> None:
        self._ctrl = None

    def _connect(self) -> None:
        Win32Controller, S, I, Toolkit = _maa()  # noqa: E741
        log_dir = os.environ.get("HIRO_MAAFW_LOG") or os.path.join(tempfile.gettempdir(), "hiro-maafw")
        os.makedirs(log_dir, exist_ok=True)
        Toolkit.init_option(log_dir)  # 框架自己的日志写这里（debug/maa.log）
        win = find_window(Toolkit, self.title)
        ctrl = Win32Controller(
            win.hwnd,
            screencap_method=screencap_flags(self.screencap, S),
            mouse_method=input_method(self.input_name, I),
            keyboard_method=input_method(self.input_name, I),
        )
        ctrl.set_screenshot_target_short_side(SHORT_SIDE)
        if not ctrl.post_connection().wait().succeeded:
            raise RuntimeError(
                f"MaaFramework 连不上游戏窗口（截图 {self.screencap}、输入 {self.input_name}）；"
                "日志见 " + os.path.join(log_dir, "debug", "maa.log")
            )
        self._ctrl, self._hwnd = ctrl, win.hwnd
        logger.info("MaaFramework Win32：窗口 %s，截图 %s，输入 %s", win.hwnd, self.screencap, self.input_name)

    def _job(self, make, what: str):
        """发一个动作并等它做完；失败时重连一次（游戏重开后窗口句柄会变）再试，还不行就抛错。"""
        for attempt in (0, 1):
            if self._ctrl is None:
                self._connect()
            job = make(self._ctrl).wait()
            if job.succeeded:
                return job
            if attempt == 0:
                logger.warning("MaaFramework %s 失败，重连后再试一次", what)
                self._ctrl = None
        raise RuntimeError(f"MaaFramework {what}失败（截图 {self.screencap}、输入 {self.input_name}）")

    # -- 截图
    @property
    def screen_size(self) -> tuple[int, int]:
        if self._size is None:
            self.screenshot()
        return self._size

    def detect_orientation(self):
        w, h = self.screen_size
        return "landscape" if w > h else "portrait"

    def screenshot(self):
        import numpy as np

        image = self._job(lambda c: c.post_screencap(), "截图").get()
        if image is None or not getattr(image, "size", 0):
            raise RuntimeError("MaaFramework 截图是空的")
        image = np.ascontiguousarray(image)  # BGR，和 OpenCV 一样
        self._size = (image.shape[1], image.shape[0])
        return image

    # -- 点击
    def click(self, x: int, y: int) -> None:
        self._job(lambda c: c.post_click(int(x), int(y)), "点击")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float | None = None) -> None:
        duration = duration or 0.3
        if not self.swipe_hold:
            self._job(lambda c: c.post_swipe(int(x1), int(y1), int(x2), int(y2), int(duration * 1000)), "滑动")
            return
        # 按下、匀速移到终点、原地停 swipe_hold 秒（继续发同一位置）、松手：列表不按松手速度惯性多滑
        self._job(lambda c: c.post_touch_down(int(x1), int(y1)), "按下")
        steps = max(2, int(duration * SWIPE_FPS))
        for i in range(1, steps + 1):
            t = i / steps
            time.sleep(1 / SWIPE_FPS)
            self._job(lambda c: c.post_touch_move(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t)), "拖动")  # noqa: B023
        end = time.monotonic() + self.swipe_hold
        while time.monotonic() < end:
            time.sleep(1 / SWIPE_FPS)
            self._job(lambda c: c.post_touch_move(int(x2), int(y2)), "拖动")
        self._job(lambda c: c.post_touch_up(), "松手")
