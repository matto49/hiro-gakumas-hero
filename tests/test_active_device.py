"""当前设备门面（hiro.devices.active）：use(rt) 后转发主循环（带拦截的设备、检查点、tick_hooks）；没接上时用离线画面或报错。"""

from types import SimpleNamespace as NS

from hiro.devices import active

import pytest


@pytest.fixture(autouse=True)
def _reset():
    yield
    active.use(None)
    active.show(None)
    active.tick_hooks.clear()


def test_without_runtime_device_ops_raise_and_sleep_is_plain(monkeypatch):
    """没接上新主循环（离线工具、测试）：设备操作报 NoActiveDevice（旧的 KAA 主循环删了，不再退回 kotonebot）；sleep 是 time.sleep。"""
    import time

    import pytest

    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    monkeypatch.setattr(active, "_OFFLINE", None)
    active.use(None)
    with pytest.raises(active.NoActiveDevice):
        active.device.screenshot()
    with pytest.raises(active.NoActiveDevice):
        active.device.click(1, 2)
    active.sleep(0.25)
    assert slept == [0.25] and not active.active() and active.fallback_click() is None


def test_offline_frame_is_screenshot_and_taps_do_nothing(monkeypatch):
    """离线（hiro.vision.offline.load 喂过图、没接 Runtime）：截图返回喂的图（缩到 720x1280），点击 / 滑动不做事；接上 Runtime 后以 Runtime 为准。"""
    from hiro.vision.offline import load

    import numpy as np

    monkeypatch.setattr(active, "_OFFLINE", None)
    active.use(None)
    img = load(np.zeros((1920, 1080, 3), np.uint8))
    assert active.device.screenshot() is img and img.shape[:2] == (1280, 720)
    active.device.click(1, 2)
    active.device.swipe(1, 2, 3, 4)
    rt = FakeRt()
    active.use(rt)  # 接上 Runtime：不再用离线画面
    active.device.click(5, 6)
    assert active.device.screenshot() == "frame" and rt.taps == [(5, 6)]


class FakeRt:
    def __init__(self):
        self.t = 0.0
        self.taps, self.swipes, self.checkpoints = [], [], 0
        self.device = NS(tap=lambda x, y: self.taps.append((x, y)), swipe=lambda *a, **k: self.swipes.append((a, k)))
        self.screen = NS(shot=lambda: NS(img="frame"))

    def checkpoint(self):
        self.checkpoints += 1

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_runtime_mode_uses_guarded_device_and_checkpoints():
    rt = FakeRt()
    active.use(rt)
    hooks = []
    active.tick_hooks.append(lambda: hooks.append(rt.t))
    assert active.active() and active.device.screenshot() == "frame"
    active.device.click(5, 6)
    active.device.swipe(1, 2, 3, 4, 0.5, hold=0.3)
    assert rt.taps == [(5, 6)] and rt.swipes == [((1, 2, 3, 4), {"duration": 0.5, "hold": 0.3})]
    active.sleep(0.5)  # 每 0.2 秒过一次检查点
    assert rt.t == pytest.approx(0.5) and rt.checkpoints == 4 and len(hooks) == 4
    n = 0
    for _ in active.Loop(interval=0.1):
        n += 1  # noqa: SIM113
        if n == 3:
            break
    assert rt.checkpoints == 7


def test_tick_hook_can_interrupt():
    from hiro.loop.date_change import DateChanged

    active.use(FakeRt())

    def boom():
        raise DateChanged("日付変更")

    active.tick_hooks.append(boom)
    with pytest.raises(DateChanged):
        active.sleep(1.0)
