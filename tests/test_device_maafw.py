"""hiro.device_maafw：DMM 版的 MaaFramework 截图 / 点击（用假的 maa 包测，不需要 Windows）。"""

import sys
import types
from enum import IntEnum

import numpy as np
import pytest


class S(IntEnum):
    Null = 0
    GDI = 1
    FramePool = 2
    PrintWindow = 16


class I(IntEnum):  # noqa: E742
    Null = 0
    Seize = 1
    SendMessage = 2
    SendMessageWithWindowPos = 64


class Job:
    def __init__(self, ok=True, result=None):
        self.succeeded, self.result = ok, result

    def wait(self):
        return self

    def get(self):
        return self.result


class FakeCtrl:
    instances = []  # noqa: RUF012
    fail_screencap = 0

    def __init__(self, hwnd, screencap_method, mouse_method, keyboard_method):
        self.hwnd, self.screencap, self.mouse = hwnd, screencap_method, mouse_method
        self.calls, self.short = [], None
        FakeCtrl.instances.append(self)

    def set_screenshot_target_short_side(self, n):
        self.short = n

    def post_connection(self):
        return Job()

    def post_screencap(self):
        if FakeCtrl.fail_screencap:
            FakeCtrl.fail_screencap -= 1
            return Job(ok=False)
        return Job(result=np.zeros((1279, 720, 3), np.uint8))

    def post_click(self, x, y):
        self.calls.append(("click", x, y))
        return Job()

    def post_swipe(self, x1, y1, x2, y2, ms):
        self.calls.append(("swipe", x1, y1, x2, y2, ms))
        return Job()

    def post_touch_down(self, x, y):
        self.calls.append(("down", x, y))
        return Job()

    def post_touch_move(self, x, y):
        self.calls.append(("move", x, y))
        return Job()

    def post_touch_up(self):
        self.calls.append(("up",))
        return Job()


class Win:
    def __init__(self, hwnd, cls, name):
        self.hwnd, self.class_name, self.window_name = hwnd, cls, name


class FakeToolkit:
    windows = [Win(7, "Chrome_WidgetWin_1", "DMM GAME PLAYER"), Win(42, "UnityWndClass", "gakumas")]  # noqa: RUF012

    @staticmethod
    def init_option(path):
        return True

    @staticmethod
    def find_desktop_windows():
        return FakeToolkit.windows


@pytest.fixture
def fake_maa(monkeypatch, tmp_path):
    pkg = types.ModuleType("maa")
    mods = {
        "maa": pkg,
        "maa.controller": types.ModuleType("maa.controller"),
        "maa.define": types.ModuleType("maa.define"),
        "maa.toolkit": types.ModuleType("maa.toolkit"),
    }
    mods["maa.controller"].Win32Controller = FakeCtrl
    mods["maa.define"].MaaWin32ScreencapMethodEnum = S
    mods["maa.define"].MaaWin32InputMethodEnum = I
    mods["maa.toolkit"].Toolkit = FakeToolkit
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    monkeypatch.setenv("HIRO_MAAFW_LOG", str(tmp_path))
    for k in ("HIRO_MAAFW_SCREENCAP", "HIRO_MAAFW_INPUT", "HIRO_DMM_SWIPE_HOLD", "HIRO_DMM_DRIVER"):
        monkeypatch.delenv(k, raising=False)
    FakeCtrl.instances.clear()
    FakeCtrl.fail_screencap = 0
    FakeToolkit.windows = [Win(7, "Chrome_WidgetWin_1", "DMM GAME PLAYER"), Win(42, "UnityWndClass", "gakumas")]
    return mods


def test_driver_default_and_bad(monkeypatch):
    from hiro import device_maafw as m

    monkeypatch.delenv("HIRO_DMM_DRIVER", raising=False)
    assert m.driver() == "kaa"
    monkeypatch.setenv("HIRO_DMM_DRIVER", "MaaFW")
    assert m.driver() == "maafw"
    monkeypatch.setenv("HIRO_DMM_DRIVER", "maa")
    with pytest.raises(ValueError, match="kaa"):
        m.driver()


def test_flags_and_input():
    from hiro import device_maafw as m

    assert m.screencap_flags("FramePool|GDI", S) == 3
    assert m.input_method("SendMessageWithWindowPos", I) == 64
    with pytest.raises(ValueError, match="FramePool"):
        m.screencap_flags("Framepool", S)
    with pytest.raises(ValueError, match="SendMessage"):
        m.input_method("Null", I)


def test_connect_screenshot_click(fake_maa):
    from hiro.device_maafw import MaaFwWin32Impl

    impl = MaaFwWin32Impl(device=None)
    impl.start()
    ctrl = FakeCtrl.instances[-1]
    assert ctrl.hwnd == 42 and ctrl.screencap == S.FramePool and ctrl.mouse == I.SendMessageWithWindowPos
    assert ctrl.short == 720
    img = impl.screenshot()
    assert img.shape == (1279, 720, 3) and impl.screen_size == (720, 1279)
    assert impl.detect_orientation() == "portrait"
    impl.click(360, 640)
    assert ctrl.calls[-1] == ("click", 360, 640)


def test_env_methods(fake_maa, monkeypatch):
    from hiro.device_maafw import MaaFwWin32Impl

    monkeypatch.setenv("HIRO_MAAFW_SCREENCAP", "GDI")
    monkeypatch.setenv("HIRO_MAAFW_INPUT", "SendMessage")
    MaaFwWin32Impl(device=None).start()
    ctrl = FakeCtrl.instances[-1]
    assert ctrl.screencap == S.GDI and ctrl.mouse == I.SendMessage


def test_missing_window(fake_maa):
    from hiro.device_maafw import MaaFwWin32Impl

    FakeToolkit.windows = [Win(7, "Chrome_WidgetWin_1", "DMM GAME PLAYER")]
    with pytest.raises(RuntimeError, match="找不到游戏窗口"):
        MaaFwWin32Impl(device=None).start()


def test_screencap_fail_reconnects_once(fake_maa):
    from hiro.device_maafw import MaaFwWin32Impl

    impl = MaaFwWin32Impl(device=None)
    impl.start()
    FakeCtrl.fail_screencap = 1
    assert impl.screenshot().shape == (1279, 720, 3)
    assert len(FakeCtrl.instances) == 2  # 第一次失败后重新找窗口、建控制器
    FakeCtrl.fail_screencap = 5
    with pytest.raises(RuntimeError, match="截图失败"):
        impl.screenshot()


def test_swipe_hold_and_plain(fake_maa, monkeypatch):
    from hiro.device_maafw import MaaFwWin32Impl

    impl = MaaFwWin32Impl(device=None)
    impl.swipe_hold = 0.05
    impl.start()
    impl.swipe(100, 900, 100, 300, duration=0.05)
    calls = FakeCtrl.instances[-1].calls
    assert calls[0] == ("down", 100, 900) and calls[-1] == ("up",)
    assert calls[-2] == ("move", 100, 300)  # 停在终点
    monkeypatch.setenv("HIRO_DMM_SWIPE_HOLD", "0")
    plain = MaaFwWin32Impl(device=None)
    plain.start()
    plain.swipe(1, 2, 3, 4, duration=0.5)
    assert FakeCtrl.instances[-1].calls == [("swipe", 1, 2, 3, 4, 500)]


def test_missing_package(monkeypatch):
    from hiro import device_maafw as m

    monkeypatch.setitem(sys.modules, "maa", None)
    monkeypatch.setitem(sys.modules, "maa.controller", None)
    with pytest.raises(RuntimeError, match="pip install maafw"):
        m.MaaFwWin32Impl(device=None).start()


def test_connect_picks_maafw(fake_maa, monkeypatch):
    import hiro.device as d

    monkeypatch.setattr(d, "BACKEND", None)  # connect() 改模块全局：测完还原，别影响别的用例
    monkeypatch.setattr(d, "DEVICE", None)
    monkeypatch.setenv("HIRO_DEVICE", "dmm")
    monkeypatch.setenv("HIRO_DMM_DRIVER", "maafw")
    kaa_called = []
    monkeypatch.setitem(
        sys.modules, "kaa.main.kaa", types.SimpleNamespace(KaaDeviceFactory=lambda: kaa_called.append(1))
    )
    dev = d.connect()
    assert not kaa_called and d.BACKEND == "dmm"
    dev.click(360, 640)  # 逻辑坐标 720x1280 → 截图坐标 720x1279，经 MaaFramework 点出去
    assert FakeCtrl.instances[-1].calls[-1][0] == "click"
    assert FakeCtrl.instances and FakeCtrl.instances[-1].hwnd == 42
