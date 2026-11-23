"""设备统一接口（去 KAA 设计稿第 4 节）：6 个方法 + 两种异常，坐标一律是逻辑坐标。

    connect() / close()
    screenshot() -> BGR ndarray       竖屏 720x1280，横屏（LIVE）1280x720；缩放在驱动里做
    tap(x, y)                         坐标和当前截图同一坐标系（竖屏 720x1280，横屏 1280x720）
    swipe(x1, y1, x2, y2, *, duration=0.3, hold=0.0)   hold > 0：到终点停这么久再松手（列表不带惯性）
    alive() -> bool                   游戏窗口 / 进程还在
    name、orientation（"portrait" | "landscape"，看最近一张截图）

失败时抛 DeviceGone（窗口没了 / 连接断了）或 DeviceUnavailable（连不上、截图全黑、设备离线）；驱动内部先重连一次。
不继承 kotonebot 的基类，也没有「不带参数的 click = 点上一次找到的东西」这种隐式状态。

缩放和 KAA 现在的做法保持一致，判页模板和 OCR 看到的画面不变：截图直接 cv2.resize 拉伸到逻辑分辨率（宽高比差
超过 10% 视为不能缩放，抛 DeviceUnavailable）。点击按 x、y 各自的比例换回原生像素（KAA 统一用短边比例；
DMM 客户区 1081x1921 不是严格 9:16，各自换算和截图的拉伸严格对应，误差在 1 像素内）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import numpy as np

PORTRAIT = (720, 1280)
LANDSCAPE = (1280, 720)
ASPECT_TOLERANCE = 0.1  # 和 KAA PortraitGameScaler 的缺省容差一样


class DeviceError(RuntimeError):
    """设备层的错误。"""


class DeviceGone(DeviceError):
    """窗口没了 / 连接断了（游戏关了、闪退、模拟器断开）。"""


class DeviceUnavailable(DeviceError):
    """连不上（端口不通、找不到窗口、设备离线、截图全黑）。"""


@runtime_checkable
class Device(Protocol):
    name: str

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def screenshot(self) -> np.ndarray: ...

    def tap(self, x: int, y: int) -> None: ...

    def swipe(self, x1: int, y1: int, x2: int, y2: int, *, duration: float = 0.3, hold: float = 0.0) -> None: ...

    def alive(self) -> bool: ...


def logical_size(width: int, height: int) -> tuple[int, int]:
    """原生尺寸 → 逻辑尺寸：竖屏 720x1280，横屏 1280x720。"""
    return LANDSCAPE if width > height else PORTRAIT


LANDSCAPE_CROP_RATIO = (
    1.5,
    1.7,
)  # 横屏宽高比落在这里（Mac 全屏 16:10：PlayCover 横屏 LIVE 全屏后截到 2940x1838）：居中裁成 16:9


def crop_box(width: int, height: int) -> tuple[int, int, int, int] | None:
    """原生截图里要用的那一块 (x0, y0, w, h)：宽高比接近 9:16 用整张；横屏 16:10 左右（全屏 LIVE）居中裁出 16:9；
    都不是返回 None。"""
    if width <= 0 or height <= 0:
        return None
    lw, lh = logical_size(width, height)
    long_side, short_side = max(width, height), min(width, height)
    if abs(long_side / short_side - max(lw, lh) / min(lw, lh)) <= ASPECT_TOLERANCE:
        return 0, 0, width, height
    if width > height and LANDSCAPE_CROP_RATIO[0] <= width / height <= LANDSCAPE_CROP_RATIO[1]:
        ch = round(width * 9 / 16)
        return 0, (height - ch) // 2, width, ch
    return None


def check_aspect(width: int, height: int) -> None:
    """宽高比和 9:16 差太多（又不是能裁的横屏 16:10）时没法按 KAA 的方式拉伸（模板会变形），直接报错。"""
    if crop_box(width, height) is None:
        lw, lh = logical_size(width, height)
        raise DeviceUnavailable(f"画面尺寸 {width}x{height} 和 9:16 差太多，不能缩放到 {lw}x{lh}")


RESIZE_SETTLE_S = 3.0  # 截到的画面宽高比不对（横竖屏切换时窗口正在改尺寸）：最多重截这么久
RESIZE_RETRY_S = 0.2


def settled_frame(grab: Callable[[], np.ndarray], screen: LogicalScreen, *, settle_s: float | None = None,
                  sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> np.ndarray:  # fmt: skip
    """截图 → 逻辑分辨率，三个设备共用。宽高比和 9:16 差太多时多半是窗口正在横竖屏切换（DMM 11-14：选拔③结束进 LIVE 时
    截到 540x572；Mac / MuMu 进 LIVE 也会转），每 RESIZE_RETRY_S 秒重截，settle_s（缺省 RESIZE_SETTLE_S）内恢复就用新的那张；
    一直不对再报 DeviceUnavailable。比例对的截图第一张就返回，不多等。"""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    deadline = clock() + (RESIZE_SETTLE_S if settle_s is None else settle_s)
    while True:
        image = grab()
        shape = getattr(image, "shape", None)
        if shape is not None and len(shape) >= 2:
            try:
                check_aspect(shape[1], shape[0])
            except DeviceUnavailable:
                if clock() >= deadline:
                    raise
                sleep(RESIZE_RETRY_S)
                continue
        return screen.frame(image)


class LogicalScreen:
    """原生截图 ↔ 逻辑坐标。每张截图都更新一次原生尺寸（窗口被拉大拉小、横竖屏切换时跟着变）。"""

    def __init__(self) -> None:
        self.native: tuple[int, int] | None = None  # 最近一张原生截图的 (宽, 高)
        self.crop: tuple[int, int, int, int] | None = (
            None  # 原生截图里用的那一块 (x0, y0, w, h)；横屏 16:10 时居中裁 16:9
        )

    @property
    def orientation(self) -> str:
        if self.native is None:
            return "portrait"
        w, h = self.native
        return "landscape" if w > h else "portrait"

    def frame(self, image: np.ndarray) -> np.ndarray:
        """原生 BGR 截图 → 逻辑分辨率（KAA 一样用 cv2.resize 缺省插值）。"""
        import cv2

        if image is None or image.ndim != 3 or image.shape[2] != 3 or not image.size:
            raise DeviceUnavailable(f"截图不是 BGR 三通道图像：{None if image is None else image.shape}")
        h, w = image.shape[:2]
        box = crop_box(w, h)
        if box is None:
            check_aspect(w, h)  # 抛 DeviceUnavailable
        self.native, self.crop = (w, h), box
        x0, y0, cw, ch = box
        if (cw, ch) != (w, h):
            image = image[y0 : y0 + ch, x0 : x0 + cw]
        size = logical_size(cw, ch)
        if (cw, ch) == size:
            return np.ascontiguousarray(image)
        return cv2.resize(image, size)

    def to_native(self, x: float, y: float) -> tuple[int, int]:
        """逻辑坐标 → 原生像素（x、y 各按各的比例，夹在画面内）。要先截过一张图。"""
        if self.native is None:
            raise DeviceUnavailable("还没截过图，不知道原生分辨率")
        w, h = self.native
        x0, y0, cw, ch = self.crop or (0, 0, w, h)
        lw, lh = logical_size(cw, ch)
        nx = min(w - 1, max(0, x0 + round(x * cw / lw)))
        ny = min(h - 1, max(0, y0 + round(y * ch / lh)))
        return nx, ny
