"""新主循环接 PlayCover 横屏 LIVE 黑屏（协调 11-16，p13：LIVE 黑屏 181 秒 LOADING 停机）：
LandscapeGuard 挂在 Runtime 上、in_live 期间看门狗不停；全屏截到 16:10 时居中裁成 16:9。"""

from hiro.devices.base import DeviceUnavailable, LogicalScreen, check_aspect, crop_box
from hiro.loop import landscape
from hiro.loop.errors import ResumeBlocked
from hiro.runtime import loop
from hiro.runtime.loop import STAY, Runtime
from hiro.vision.classify import UNKNOWN, PageHit  # noqa: F401

import numpy as np
import pytest


def test_fullscreen_16_10_is_cropped_to_16_9():
    """Mac 全屏 LIVE：MaaTools 截到 2940x1838（16:10），原来 check_aspect 直接抛 DeviceUnavailable。"""
    assert crop_box(2940, 1838) == (0, (1838 - 1654) // 2, 2940, 1654)
    check_aspect(2940, 1838)  # 不抛
    scr = LogicalScreen()
    img = np.zeros((1838, 2940, 3), np.uint8)
    img[92:1746] = 200  # 中间 16:9 那块
    out = scr.frame(img)
    assert out.shape == (720, 1280, 3) and out.mean() == 200  # 上下黑边裁掉了
    assert scr.to_native(640, 360) == (1470, 92 + 827)  # 点击按裁剪后的那块换算回原生坐标
    with pytest.raises(DeviceUnavailable):
        check_aspect(1000, 1000)  # 1:1 还是不行
    assert crop_box(1280, 720) == (0, 0, 1280, 720) and crop_box(720, 1280) == (0, 0, 720, 1280)


class FakeDevice:
    name = "fake"

    def __init__(self):
        self.taps = []

    def screenshot(self):
        return np.zeros((1280, 720, 3), np.uint8)

    def tap(self, x, y):
        self.taps.append((x, y))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def _rt(guard, img):
    from hiro.runtime.frame import Frame

    clock = Clock()
    seen = []
    rt = Runtime(FakeDevice(), lambda im: seen.append(1) or PageHit("LOADING", "pixel"), clock=clock, sleep=clock.sleep, interrupts=(),
                 landscape=guard)  # fmt: skip

    def shot():
        f = Frame(img, clock())
        rt.screen.frame = f
        return f

    rt.screen.shot = shot
    return rt, clock, seen


def test_black_live_does_not_trip_watchdog_until_live_max():
    guard = landscape.LandscapeGuard(active=False)  # 非 Mac：只用 in_live
    rt, clock, _ = _rt(guard, np.zeros((1280, 720, 3), np.uint8))
    for _ in range(40):  # 黑屏 200 秒（原来 180 秒就停机）
        assert rt.step() is STAY
        clock.now += 5
    clock.now = loop.LIVE_MAX_S + 10
    rt.step()
    clock.now += loop.STALL_S + 1
    with pytest.raises(ResumeBlocked):  # 一直黑到 LIVE_MAX_S 之后：照常停机
        rt.step()


def test_guard_window_action_skips_the_tick():
    class G:
        def tick(self, img):
            return True  # 这一拍切了全屏

        def in_live(self, img):
            return True

    rt, _, seen = _rt(G(), np.zeros((1280, 720, 3), np.uint8))
    assert rt.step() is STAY and seen == []  # 没判页、没交处理器


def test_guard_enters_then_exits_fullscreen(monkeypatch):
    """黑屏 BLACK_S 秒 → 进全屏；全屏 30 秒后又黑 EXIT_BLACK_S 秒 → 退全屏（同旧主循环）。"""
    t = {"now": 0.0}
    monkeypatch.setattr(landscape.time, "monotonic", lambda: t["now"])
    calls = []
    g = landscape.LandscapeGuard(active=True)
    monkeypatch.setattr(
        g,
        "_enter",
        lambda: (
            calls.append("enter") or g.__dict__.update(fullscreen=True, entered_at=t["now"], black_since=None) or True
        ),
    )
    monkeypatch.setattr(
        g, "_exit", lambda: calls.append("exit") or g.__dict__.update(fullscreen=False, black_since=None) or True
    )
    black = np.zeros((1280, 720, 3), np.uint8)
    for _ in range(25):
        g.tick(black)
        t["now"] += 1
    assert calls == ["enter"]
    for _ in range(45):
        g.tick(black)
        t["now"] += 1
    assert calls == ["enter", "exit"]
