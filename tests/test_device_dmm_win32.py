"""DMM 版（Windows）设备：自己调 Win32（不依赖 MaaFramework / KAA）。截图 PrintWindow（缺省，同 KAA）或 GDI，
点击 / 滑动用「挪窗口让目标点落在光标下 + PostMessage」（同 KAA 的 SendMessageImpl）。系统调用由假的 Win32 接口代替。"""

from hiro.devices.base import DeviceGone, DeviceUnavailable
from hiro.devices.dmm_win32 import WM_ACTIVATE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, DmmWin32Device

import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


def bgr(w, h, value=90):
    return np.full((h, w, 3), value, np.uint8)


class FakeWin32:
    """一个 541x961 客户区的游戏窗口：外框左上角 (100, 50)，客户区比外框偏 (8, 31)；光标停在 (1000, 800)。"""

    def __init__(self, image=None, hwnd=42):
        self.hwnd = hwnd
        self.image = bgr(541, 961) if image is None else image
        self.win = [100, 50, 557, 1000]  # x, y, w, h
        self.cursor = (1000, 800)
        self.posted: list[tuple[int, int, int]] = []
        self.captures: list[str] = []
        self.cursor_errors = 0
        self.capture_fail = 0

    def find(self, title):
        return self.hwnd

    def is_window(self, hwnd):
        return hwnd is not None and hwnd == self.hwnd

    def client_origin(self, hwnd):
        return self.win[0] + 8, self.win[1] + 31

    def window_rect(self, hwnd):
        return tuple(self.win)

    def move_window(self, hwnd, x, y, w, h):
        self.win = [x, y, w, h]

    def cursor_pos(self):
        if self.cursor_errors:
            self.cursor_errors -= 1
            err = OSError(5, "GetCursorPos", "拒绝访问。")
            err.winerror = 5
            raise err
        return self.cursor

    def post(self, hwnd, msg, wparam, lparam):
        self.posted.append((msg, wparam, lparam))

    def capture(self, hwnd, method):
        self.captures.append(method)
        if self.capture_fail:
            self.capture_fail -= 1
            raise OSError("PrintWindow failed")
        return self.image


def lparam(x, y):
    return (y << 16) | (x & 0xFFFF)


def device(api, **kw):
    return DmmWin32Device(api=api, sleep=lambda s: None, **kw)


def test_screenshot_tap_aligns_window_under_cursor(monkeypatch):
    monkeypatch.delenv("HIRO_DMM_CAPTURE", raising=False)
    api = FakeWin32()
    dev = device(api)
    dev.connect()
    assert dev.name == "dmm-win32"
    assert dev.screenshot().shape == (1280, 720, 3)  # 原生 541x961 拉伸到 720x1280（同 KAA）
    assert set(api.captures) == {"printwindow"}  # 缺省 PrintWindow，和 KAA 一样
    dev.tap(360, 1280)
    nx, ny = 270, 960  # 换回原生像素、夹在画面内
    # 窗口挪到客户区 (270, 960) 正好落在光标 (1000, 800) 下
    assert api.client_origin(42)[0] + nx == 1000 and api.client_origin(42)[1] + ny == 800
    msgs = [m for m, _, _ in api.posted]
    assert msgs == [WM_ACTIVATE, WM_LBUTTONDOWN, WM_LBUTTONUP]
    assert api.posted[1][2] == lparam(nx, ny) and api.posted[2][2] == lparam(nx, ny)
    assert dev.alive()


def test_swipe_holds_at_the_end_then_releases():
    api = FakeWin32()
    dev = device(api)
    dev.connect()
    api.posted.clear()
    dev.swipe(100, 900, 100, 300, duration=0.05, hold=0.05)
    msgs = [m for m, _, _ in api.posted]
    assert msgs[1] == WM_LBUTTONDOWN and msgs[-1] == WM_LBUTTONUP and msgs.count(WM_MOUSEMOVE) >= 3
    assert api.posted[1][2] == lparam(75, 676) and api.posted[-2] == (WM_MOUSEMOVE, 1, lparam(75, 225))
    # 终点也对齐在光标下
    ox, oy = api.client_origin(42)
    assert (ox + 75, oy + 225) == api.cursor


def test_capture_method_from_env(monkeypatch):
    monkeypatch.setenv("HIRO_DMM_CAPTURE", "gdi")
    api = FakeWin32()
    device(api).connect()
    assert set(api.captures) == {"gdi"}
    monkeypatch.setenv("HIRO_DMM_CAPTURE", "dxgi")
    with pytest.raises(ValueError, match="HIRO_DMM_CAPTURE"):
        DmmWin32Device(api=FakeWin32())


def test_connect_refuses_missing_window_and_black_frames():
    api = FakeWin32()
    api.hwnd = None
    with pytest.raises(DeviceUnavailable, match="找不到游戏窗口"):
        device(api).connect()
    with pytest.raises(DeviceUnavailable, match="全黑"):
        device(FakeWin32(image=np.zeros((961, 541, 3), np.uint8))).connect()


def test_capture_failure_retries_once_then_reports_gone_or_unavailable():
    api = FakeWin32()
    dev = device(api)
    dev.connect()
    api.capture_fail = 1
    assert dev.screenshot().shape == (1280, 720, 3)  # 失败一次，重新找窗口再截成功
    api.capture_fail = 2
    with pytest.raises(DeviceUnavailable):  # 窗口还在，截两次都失败
        dev.screenshot()
    api.hwnd = None
    with pytest.raises(DeviceGone):  # 窗口没了（游戏关了 / 闪退）
        dev.screenshot()
    assert not dev.alive()


def test_tap_waits_while_desktop_is_unavailable():
    """UAC / 锁屏 / 远程切桌面时读光标报「拒绝访问」：等桌面恢复再点，不让主循环连崩（hiro.devices.desktop）。"""
    api = FakeWin32()
    dev = device(api)
    dev.connect()
    api.posted.clear()
    api.cursor_errors = 2
    dev.tap(360, 640)
    assert [m for m, _, _ in api.posted][-2:] == [WM_LBUTTONDOWN, WM_LBUTTONUP]


def test_open_device_dmm_has_no_maa():
    import subprocess
    import sys

    from hiro.devices import open_device

    assert open_device("dmm").name == "dmm-win32"
    code = (
        "import sys, hiro.devices, hiro.devices.dmm_win32;"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('maa', 'kotonebot', 'kaa'));"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
