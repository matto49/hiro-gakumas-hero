"""当前设备（去 KAA 过渡期）：执行层（produce/*、exec/exam、loop/resume 等）统一从这里拿设备、睡眠和循环，两套主循环共用同一份执行代码。

    from hiro.devices.active import Loop, device, sleep
    img = device.screenshot(); device.click(x, y); sleep(0.5)
    for _ in Loop(interval=0.1): ...

- 缺省（KAA 主循环，HIRO_RUNTIME 不是 own）：原样转发 kotonebot 的 device / sleep / Loop，行为和以前完全一样
  （kotonebot 的暂停检查点 flow.check、Loop 每轮的 loop_callbacks 如日切换检测都还在）。
- 新主循环：run_hif 调 use(rt)（hiro.runtime.loop.Runtime）以后，device 转发 rt.device（带危险点击拦截），sleep / Loop 每拍过
  rt.checkpoint（录屏暂停）和 tick_hooks（如日切换检测）；use(None) 还原。

只给执行层用到的几个方法：screenshot / click(x, y) / click_center / double_click(x, y) / swipe。点击一律传坐标（不传 Rect）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

_RT = None  # 当前 hiro.runtime.loop.Runtime；None = 用 kotonebot
tick_hooks: list[Callable[[], None]] = []  # 新主循环下 sleep / Loop 每拍调用（抛异常即中断，如 DateChanged）


def use(rt) -> None:
    """切到新主循环的 Runtime（None = 回到 kotonebot）。"""
    global _RT
    _RT = rt


def active() -> bool:
    return _RT is not None


def _kb():
    import kotonebot

    return kotonebot


def _tick() -> None:
    _RT.checkpoint()
    for hook in tick_hooks:
        hook()


class _Device:
    def screenshot(self):
        if _RT is None:
            return _kb().device.screenshot()
        return _RT.screen.shot().img

    def click(self, x: int, y: int) -> None:
        if _RT is None:
            _kb().device.click(int(x), int(y))
        else:
            _RT.device.tap(int(x), int(y))

    def click_center(self) -> None:
        """点画面中央（720x1280 的 (360, 640)，同 kotonebot click_center）。"""
        if _RT is None:
            _kb().device.click_center()
        else:
            self.click(360, 640)

    def double_click(self, x: int, y: int, interval: float = 0.4) -> None:
        if _RT is None:
            # kotonebot 的 double_click(*args, **kwargs) 只从关键字取 interval；按位置传会被当成第三个坐标参数，
            # click 报 Invalid arguments: (x, y, 0.4)（11-18 Windows MuMu 选拔授業事件选项）
            _kb().device.double_click(int(x), int(y), interval=interval)
            return
        self.click(x, y)
        sleep(interval)
        self.click(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float | None = None, **kw) -> None:
        if _RT is None:
            if duration is None:
                _kb().device.swipe(x1, y1, x2, y2, **kw)
            else:
                _kb().device.swipe(x1, y1, x2, y2, duration=duration, **kw)
            return
        _RT.device.swipe(x1, y1, x2, y2, duration=0.3 if duration is None else duration, hold=kw.get("hold", 0.0))


device = _Device()


def sleep(seconds: float) -> None:
    """睡 seconds 秒；新主循环下每 0.2 秒过一次检查点（暂停 / 日切换），暂停期间不计时。
    旧主循环转发 kotonebot.sleep（可暂停 / 中断）；两套主循环都没起（离线工具、测试）就是普通的 time.sleep。"""
    if _RT is None:
        try:
            _kb().sleep(seconds)
        except Exception as e:  # noqa: BLE001  没装 kotonebot、KAA 上下文没初始化（ContextNotInitializedError）
            if not isinstance(e, ImportError) and type(e).__name__ != "ContextNotInitializedError":
                raise
            time.sleep(seconds)
        return
    end = _RT.clock() + seconds
    while True:
        _tick()
        left = end - _RT.clock()
        if left <= 0:
            return
        _RT.sleep(min(0.2, left))


def Loop(*, interval: float = 0.3) -> Iterator[None]:  # noqa: N802  名字同 kotonebot.Loop，调用处只换 import
    """无限循环，每轮之间隔 interval 秒；调用方自己 break / 判超时（同 kotonebot.Loop：它不检查 timeout）。"""
    if _RT is None:
        yield from _kb().Loop(interval=interval)
        return
    while True:
        _tick()
        yield None
        _RT.sleep(interval)


__all__ = ["Loop", "active", "device", "sleep", "tick_hooks", "use"]
