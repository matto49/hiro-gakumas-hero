"""DMM 版（Windows）：MaaFramework（LGPL-3.0，Python 包 maafw）的 Win32 控制器截图 / 点击，设备统一接口的实现。

    HIRO_MAAFW_SCREENCAP  缺省 FramePool；可写 GDI、ScreenDC，多个用「|」连起来让框架挑最快的
    HIRO_MAAFW_INPUT      缺省 SendMessageWithWindowPos（游戏认真实光标位置：先挪窗口让目标点落在光标下再发消息）
    HIRO_MAAFW_LOG        框架日志目录（缺省系统临时目录下 hiro-maafw）

2026-11-11 实测（普通权限、只截图）：GDI 约 10 ms、FramePool 约 20–30 ms，PrintWindow 连不上，DXGI 窗口方式全黑；
点击在自己的 Win32 测试窗口上 0 像素误差（含贴屏幕四边 / 四角）。DMM 游戏以管理员运行，对游戏点击要以管理员身份
跑这个驱动（UIPI），还没在游戏上实测。详见 hiro.device_maafw（kotonebot 版，HIRO_DMM_DRIVER=maafw）。

框架给窗口原生尺寸的截图（set_screenshot_use_raw_size），点击坐标也按原生像素；本类用 cv2.resize 一次拉伸到 720x1280
（同 KAA），点击按比例换回原生像素（hiro.devices.base.LogicalScreen）。不让框架先缩到短边 720：它的插值和我们再拉一次叠起来，
画面和 KAA 截的不一样，RapidOCR 跟着读偏（2026-11-14 DMM 实机：窗口 541x961，「選抜試験」读成「選友試験」、公開レッスン漏一张）。
"""

from __future__ import annotations

import os
import tempfile
import time

from hiro.device_maafw import (
    DEFAULT_INPUT,
    DEFAULT_SCREENCAP,
    SWIPE_FPS,
    WINDOW_CLASS,
    WINDOW_TITLE,
    _maa,
    input_method,
    screencap_flags,
)

import numpy as np

from .base import DeviceGone, DeviceUnavailable, LogicalScreen, settled_frame


class MaaFwWin32Device:
    """设备统一接口：DMM 版学マス（Windows，MaaFramework Win32 控制器）。"""

    name = "maafw-win32"

    def __init__(
        self, *, screencap: str | None = None, input_name: str | None = None, title: str = WINDOW_TITLE, api=None
    ):
        self.screencap = screencap or os.environ.get("HIRO_MAAFW_SCREENCAP") or DEFAULT_SCREENCAP
        self.input_name = input_name or os.environ.get("HIRO_MAAFW_INPUT") or DEFAULT_INPUT
        self.title = title
        self._api_override = api  # 测试注入：(Win32Controller, ScreencapEnum, InputEnum, Toolkit)
        self._ctrl = None
        self._hwnd = None
        self.screen = LogicalScreen()

    # -- 连接
    @property
    def orientation(self) -> str:
        return self.screen.orientation

    def _api(self):
        return self._api_override or _maa()

    def _find(self):
        _, _, _, toolkit = self._api()
        for w in toolkit.find_desktop_windows():
            if (w.window_name or "").strip().lower() == self.title and (w.class_name or "") == WINDOW_CLASS:
                return w
        return None

    def _open(self) -> None:
        controller, scap, inp, toolkit = self._api()
        log_dir = os.environ.get("HIRO_MAAFW_LOG") or os.path.join(tempfile.gettempdir(), "hiro-maafw")
        os.makedirs(log_dir, exist_ok=True)
        toolkit.init_option(log_dir)
        win = self._find()
        if win is None:
            raise DeviceGone(f"找不到游戏窗口（标题 {self.title!r}、类 {WINDOW_CLASS}）")
        ctrl = controller(
            win.hwnd,
            screencap_method=screencap_flags(self.screencap, scap),
            mouse_method=input_method(self.input_name, inp),
            keyboard_method=input_method(self.input_name, inp),
        )
        ctrl.set_screenshot_use_raw_size(True)
        if not ctrl.post_connection().wait().succeeded:
            raise DeviceUnavailable(
                f"MaaFramework 连不上游戏窗口（截图 {self.screencap}、输入 {self.input_name}），"
                f"日志见 {os.path.join(log_dir, 'debug', 'maa.log')}"
            )
        self._ctrl, self._hwnd = ctrl, win.hwnd

    def connect(self) -> None:
        try:
            self._open()
            image = self._grab()
        except DeviceGone as e:  # 连接时找不到窗口算「连不上」
            raise DeviceUnavailable(str(e)) from e
        except ImportError as e:
            raise DeviceUnavailable(f"要先装 MaaFramework：pip install maafw（{e}）") from e
        if not image.any():  # DXGI 窗口方式在这台机器上截出来全黑：换截图方式
            self._ctrl = None
            raise DeviceUnavailable(f"截图方式 {self.screencap} 截出来全黑，换 HIRO_MAAFW_SCREENCAP（GDI / FramePool）")
        first = [image]
        settled_frame(lambda: first.pop() if first else self._grab(), self.screen)  # 连上那一刻正在横竖屏切换也等一下

    def close(self) -> None:
        self._ctrl = None
        self._hwnd = None

    def alive(self) -> bool:
        try:
            return self._find() is not None
        except Exception:  # noqa: BLE001  maafw 没装、框架出错：都当不在
            return False

    def _job(self, make, what: str):
        """发一个动作并等它做完；失败时重连一次（游戏重开后窗口句柄会变）再试。窗口没了抛 DeviceGone。"""
        for _ in (0, 1):
            if self._ctrl is None:
                self._open()
            job = make(self._ctrl).wait()
            if job.succeeded:
                return job
            self._ctrl = None
        if self._find() is None:
            raise DeviceGone(f"MaaFramework {what}失败：游戏窗口没了")
        raise DeviceUnavailable(f"MaaFramework {what}失败（截图 {self.screencap}、输入 {self.input_name}）")

    # -- 截图
    def _grab(self) -> np.ndarray:
        image = self._job(lambda c: c.post_screencap(), "截图").get()
        if image is None or not getattr(image, "size", 0):
            raise DeviceUnavailable("MaaFramework 截图是空的")
        return np.ascontiguousarray(image)  # BGR

    def screenshot(self) -> np.ndarray:
        """截图 → 逻辑分辨率；窗口正在横竖屏切换时重截（base.settled_frame）。"""
        return settled_frame(self._grab, self.screen)

    # -- 点击 / 滑动（坐标先换成框架截图的像素）
    def _native(self, x: float, y: float) -> tuple[int, int]:
        if self.screen.native is None:
            self.screenshot()
        return self.screen.to_native(x, y)

    def tap(self, x: int, y: int) -> None:
        nx, ny = self._native(x, y)
        self._job(lambda c: c.post_click(nx, ny), "点击")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, *, duration: float = 0.3, hold: float = 0.0) -> None:
        a1, b1 = self._native(x1, y1)
        a2, b2 = self.screen.to_native(x2, y2)
        duration = duration if duration and duration > 0 else 0.3
        if hold <= 0:
            self._job(lambda c: c.post_swipe(a1, b1, a2, b2, int(duration * 1000)), "滑动")
            return
        # 按下、匀速移到终点、原地停 hold 秒（继续发同一位置）、松手：列表不按松手速度惯性多滑
        self._job(lambda c: c.post_touch_down(a1, b1), "按下")
        steps = max(2, int(duration * SWIPE_FPS))
        for i in range(1, steps + 1):
            t = i / steps
            time.sleep(1 / SWIPE_FPS)
            px, py = round(a1 + (a2 - a1) * t), round(b1 + (b2 - b1) * t)
            self._job(lambda c, px=px, py=py: c.post_touch_move(px, py), "拖动")
        end = time.monotonic() + hold
        while time.monotonic() < end:
            time.sleep(1 / SWIPE_FPS)
            self._job(lambda c: c.post_touch_move(a2, b2), "拖动")
        self._job(lambda c: c.post_touch_up(), "松手")
