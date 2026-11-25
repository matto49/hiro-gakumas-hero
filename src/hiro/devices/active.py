"""当前设备：执行层（produce/*、exec/exam 等）统一从这里拿设备、睡眠和循环。

    from hiro.devices.active import Loop, device, sleep
    img = device.screenshot(); device.click(x, y); sleep(0.5)
    for _ in Loop(interval=0.1): ...

新主循环（hiro.runtime）开局时调 use(rt)（hiro.runtime.loop.Runtime）：device 转发 rt.device（带危险点击拦截），
sleep / Loop 每拍过 rt.checkpoint（录屏暂停）和 tick_hooks（如日切换检测）；use(None) 还原。
没接上 Runtime 时：hiro.vision.offline.load(img) 喂过图（离线工具、测试）就截图返回这张、点击 / 滑动不做事（同原来 KAA 的 MockDevice）；
都没有就报 NoActiveDevice。sleep / Loop 就是普通的 time.sleep。旧的 KAA 主循环 2026-11-24 删了，不再退回 kotonebot。
只给执行层用到的几个方法：screenshot / click(x, y) / click_center / double_click(x, y) / swipe。点击一律传坐标（不传 Rect）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

_RT = None  # 当前 hiro.runtime.loop.Runtime
_OFFLINE: Any = None  # 离线喂的当前画面（hiro.vision.offline.load）
tick_hooks: list[Callable[[], None]] = []  # sleep / Loop 每拍调用（抛异常即中断，如 DateChanged）


class NoActiveDevice(RuntimeError):
    """没有接上新主循环的设备（hiro.devices.active.use(rt) 还没调）。"""


def use(rt) -> None:
    """切到新主循环的 Runtime（None = 断开）。"""
    global _RT
    _RT = rt


def show(img) -> None:
    """离线：把 img 设为当前画面（None = 清掉）。只在没接上 Runtime 时用到。"""
    global _OFFLINE
    _OFFLINE = img


def active() -> bool:
    return _RT is not None


def _rt():
    if _RT is None:
        raise NoActiveDevice("没有接上设备：先由新主循环（hiro.runtime）调 hiro.devices.active.use(rt)")
    return _RT


def _tick() -> None:
    _RT.checkpoint()
    for hook in tick_hooks:
        hook()


def _offline() -> bool:
    return _RT is None and _OFFLINE is not None


class _Device:
    def screenshot(self):
        return _OFFLINE if _offline() else _rt().screen.shot().img

    def click(self, x: int, y: int) -> None:
        if _offline():
            return
        _rt().device.tap(int(x), int(y))

    def click_center(self) -> None:
        """点画面中央（720x1280 的 (360, 640)）。"""
        self.click(360, 640)

    def double_click(self, x: int, y: int, interval: float = 0.4) -> None:
        self.click(x, y)
        sleep(interval)
        self.click(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float | None = None, **kw) -> None:
        if _offline():
            return
        _rt().device.swipe(x1, y1, x2, y2, duration=0.3 if duration is None else duration, hold=kw.get("hold", 0.0))


device = _Device()


def fallback_click():
    """控件点了没反应时的兜底点击（produce.ops.Run.real_click）：当前设备的 real_tap（MaaToolsDevice：PlayCover 用真实鼠标，
    MaaTools 偶尔点不进饮料详情等弹窗）；其他设备、或没接上 Runtime 返回 None（adb / DMM 的点击没有这个问题）。"""
    fn = getattr(getattr(_RT, "raw_device", None), "real_tap", None)
    return None if fn is None else (lambda x, y: fn(int(x), int(y)))


def sleep(seconds: float) -> None:
    """睡 seconds 秒；新主循环下每 0.2 秒过一次检查点（暂停 / 日切换），暂停期间不计时。没接上 Runtime 时就是 time.sleep。"""
    if _RT is None:
        time.sleep(seconds)
        return
    end = _RT.clock() + seconds
    while True:
        _tick()
        left = end - _RT.clock()
        if left <= 0:
            return
        _RT.sleep(min(0.2, left))


def Loop(*, interval: float = 0.3) -> Iterator[None]:  # noqa: N802  名字沿用 kotonebot.Loop，调用处不用改
    """无限循环，每轮之间隔 interval 秒；调用方自己 break / 判超时。"""
    while True:
        if _RT is not None:
            _tick()
        yield None
        if _RT is None:
            time.sleep(interval)
        else:
            _RT.sleep(interval)


__all__ = ["Loop", "NoActiveDevice", "active", "device", "fallback_click", "show", "sleep", "tick_hooks", "use"]
