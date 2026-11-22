"""当前设备门面（hiro.devices.active）：缺省转发 kotonebot；use(rt) 后转发新主循环（带拦截的设备、检查点、tick_hooks）。"""

from types import SimpleNamespace as NS

import pytest

from hiro.devices import active


@pytest.fixture(autouse=True)
def _reset():
    yield
    active.use(None)
    active.tick_hooks.clear()


@pytest.mark.kaa
def test_default_forwards_to_kotonebot(monkeypatch):
    import kotonebot

    calls = []
    fake = NS(screenshot=lambda: "img", click=lambda x, y: calls.append(("click", x, y)),
              double_click=lambda x, y, interval=None: calls.append(("dbl", x, y, interval)), swipe=lambda *a, **k: calls.append(("swipe", a, k)))  # fmt: skip
    monkeypatch.setattr(kotonebot, "device", fake)
    monkeypatch.setattr(kotonebot, "sleep", lambda s: calls.append(("sleep", s)))
    assert active.device.screenshot() == "img"
    active.device.click(10.6, 20)
    active.device.double_click(1, 2)
    active.device.swipe(1, 2, 3, 4, 0.5)
    active.sleep(0.25)
    assert calls == [("click", 10, 20), ("dbl", 1, 2, 0.4), ("swipe", (1, 2, 3, 4), {"duration": 0.5}), ("sleep", 0.25)]
    assert not active.active()


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
        n += 1
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
