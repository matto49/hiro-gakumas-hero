"""hiro.devices.desktop：Windows 桌面暂时不可用（UAC / 锁屏 / 远程切桌面，读光标报「拒绝访问」）时等它恢复。
原在 hiro.loop.win_desktop（随 KAA 设备层删了），DMM 设备 hiro.devices.dmm_win32 用。"""

from hiro.devices import desktop

import pytest

pytestmark = pytest.mark.synthetic


class _Denied(Exception):
    def __init__(self):
        super().__init__(5, "GetCursorPos", "拒绝访问。")


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_access_denied_detects_winerror_5():
    assert desktop.access_denied(_Denied())
    assert not desktop.access_denied(RuntimeError("x"))


def test_wait_desktop_until_probe_stops_denying():
    clock, left = _Clock(), [3]

    def probe():
        if left[0]:
            left[0] -= 1
            raise _Denied()

    assert desktop.wait_desktop(probe, sleep=clock.sleep, clock=clock)
    assert left[0] == 0


def test_wait_desktop_gives_up_after_timeout():
    clock = _Clock()

    def probe():
        raise _Denied()

    assert not desktop.wait_desktop(probe, timeout=10, poll=2, sleep=clock.sleep, clock=clock)
    assert clock.t >= 10


def test_other_errors_pass_through():
    def probe():
        raise ValueError("别的错")

    with pytest.raises(ValueError):
        desktop.wait_desktop(probe, sleep=lambda s: None)
