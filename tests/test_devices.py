"""hiro.devices：设备统一接口的三个实现（MaaTools / DMM Win32 / adb），全部用假设备测，不需要真机。DMM 的见 test_device_dmm_win32。"""

import socket
import struct
import subprocess
import sys
import threading

from hiro.devices import Device, DeviceGone, DeviceUnavailable, open_device
from hiro.devices.adb import AdbDevice, focus_package, parse_screencap
from hiro.devices.base import LogicalScreen, check_aspect, logical_size
from hiro.devices.maatools import MaaToolsDevice, MaaToolsImpl

import numpy as np
import pytest


def bgr(w, h, value=128):
    img = np.zeros((h, w, 3), np.uint8)
    img[...] = value
    return img


# ---------------------------------------------------------------- 逻辑坐标


def test_logical_size_follows_orientation():
    assert logical_size(1081, 1921) == (720, 1280)
    assert logical_size(1921, 1081) == (1280, 720)


def test_frame_stretches_like_kaa_and_taps_map_per_axis():
    screen = LogicalScreen()
    out = screen.frame(bgr(1081, 1921))
    assert out.shape == (1280, 720, 3)
    assert screen.orientation == "portrait"
    # 各按各的比例：x·1081/720、y·1921/1280，右下角夹在画面内
    assert screen.to_native(360, 640) == (round(360 * 1081 / 720), round(640 * 1921 / 1280))
    assert screen.to_native(720, 1280) == (1080, 1920)
    assert screen.to_native(0, 0) == (0, 0)


def test_landscape_frame_and_taps():
    screen = LogicalScreen()
    assert screen.frame(bgr(1921, 1081)).shape == (720, 1280, 3)
    assert screen.orientation == "landscape"
    assert screen.to_native(1280, 0) == (1920, 0)


def test_native_size_follows_each_screenshot():
    screen = LogicalScreen()
    screen.frame(bgr(720, 1280))
    assert screen.to_native(100, 200) == (100, 200)
    screen.frame(bgr(1440, 2560))  # 窗口被拉大
    assert screen.to_native(100, 200) == (200, 400)


def test_bad_aspect_is_refused():
    with pytest.raises(DeviceUnavailable):
        check_aspect(1000, 1000)
    with pytest.raises(DeviceUnavailable):
        LogicalScreen().frame(bgr(800, 1000))
    with pytest.raises(DeviceUnavailable):
        LogicalScreen().to_native(1, 1)  # 没截过图


# ---------------------------------------------------------------- MaaTools（假服务端走真协议）


class FakeMaaTools:
    """模拟 PlayTools 的 MaaTools 服务：握手 MAA\\0 → OKAY；BGR\\x01 → 宽高长度 + 像素；TUCH 记下来。"""

    def __init__(self, w=1170, h=2080):
        self.w, self.h = w, h
        self.touches = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _recv(self, c, n):
        buf = b""
        while len(buf) < n:
            chunk = c.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            try:
                assert self._recv(c, 4) == b"MAA\x00"
                c.sendall(b"OKAY")
                while True:
                    (n,) = struct.unpack(">H", self._recv(c, 2))
                    body = self._recv(c, n)
                    if body[:4] == b"BGR\x01":
                        pix = bytes(self.w * self.h * 3)
                        c.sendall(struct.pack(">III", self.w, self.h, len(pix)) + pix)
                    elif body[:4] == b"TUCH":
                        self.touches.append(struct.unpack(">BHHB", body[4:]))
            except (ConnectionError, OSError, AssertionError):
                c.close()

    def close(self):
        self.srv.close()


@pytest.fixture
def maatools():
    srv = FakeMaaTools()
    yield srv
    srv.close()


def test_maatools_device_end_to_end(maatools):
    dev = MaaToolsDevice(maatools.port, reconnect_timeout=1, reconnect_interval=0.1)
    dev.connect()
    assert isinstance(dev, Device) and dev.name == "maatools"
    assert dev.screenshot().shape == (1280, 720, 3)
    dev.tap(360, 640)
    deadline = __import__("time").monotonic() + 2
    while len(maatools.touches) < 2 and __import__("time").monotonic() < deadline:
        pass
    began, ended = maatools.touches[:2]
    # 逻辑 → 截图像素（1170/720 = 1.625）→ 除以 Catalyst 的 0.77
    assert began[0] == MaaToolsImpl.TOUCH_BEGAN and ended[0] == MaaToolsImpl.TOUCH_ENDED
    assert began[1:3] == (round(585 / 0.77), round(1040 / 0.77))
    assert dev.alive()
    dev.close()


def test_maatools_swipe_passes_hold_and_resets_it():
    class Client:
        _sock = None
        swipe_hold = 0.0

        def screenshot(self):
            return bgr(720, 1280)

        def swipe(self, *a):
            self.seen = (a, self.swipe_hold)

    client = Client()
    dev = MaaToolsDevice(client=client)
    dev.swipe(100, 900, 100, 300, duration=0.4, hold=0.3)
    assert client.seen == ((100, 900, 100, 300, 0.4), 0.3)
    assert client.swipe_hold == 0.0


def test_maatools_errors_map_to_device_errors():
    class Client:
        _sock = None

        def start(self):
            raise ConnectionRefusedError("no")

        def screenshot(self):
            raise ConnectionError("gone")

    dev = MaaToolsDevice(port=1, client=Client())
    with pytest.raises(DeviceUnavailable):
        dev.connect()
    with pytest.raises(DeviceGone):
        dev.screenshot()


def test_maatools_empty_frames_retry_then_black_then_gone(monkeypatch):
    """11-17 Mac p15c：LandscapeGuard 刚切全屏，MaaTools 返回空帧，原来直接抛 DeviceUnavailable、整局崩掉。
    现在：短暂空帧重试后用新帧；一直空就给上一帧尺寸的黑帧（这一拍当没截到）；连续 EMPTY_MAX_S 秒才报 DeviceGone。"""
    from hiro.devices import maatools as mt

    now = [0.0]
    seq = []

    class Client:
        _sock = None

        def screenshot(self):
            item = seq.pop(0) if seq else None
            if item is None:
                raise RuntimeError("MaaTools 返回空帧")
            return item

    dev = MaaToolsDevice(port=1, client=Client())
    dev._clock = lambda: now[0]
    dev._sleep = lambda s: now.__setitem__(0, now[0] + s)
    with pytest.raises(DeviceUnavailable):  # 还没截到过画面：不知道尺寸，照旧报不可用
        dev.screenshot()
    seq[:] = [bgr(720, 1280)]
    assert dev.screenshot().shape == (1280, 720, 3)
    seq[:] = [None, None, bgr(1280, 720)]  # 两次空帧后恢复（横屏 LIVE）
    assert dev.screenshot().max() == 128 and dev._empty_since is None  # 真画面，不是黑帧
    seq[:] = []  # 一直空：重试 EMPTY_RETRY_S 后给黑帧
    img = dev.screenshot()
    assert img.max() == 0 and dev._empty_since is not None
    now[0] += mt.EMPTY_MAX_S
    with pytest.raises(DeviceGone, match="空帧"):
        dev.screenshot()


# ---------------------------------------------------------------- adb（假 adb 命令）


def raw_screencap(w, h, header=12, value=(10, 20, 30, 255)):
    body = np.tile(np.array(value, np.uint8), w * h).tobytes()
    head = struct.pack("<III", w, h, 1) + (struct.pack("<I", 0) if header == 16 else b"")
    return head + body


class FakeAdb:
    def __init__(self, w=1080, h=1920, header=12, focus="com.bandainamcoent.idolmaster_gakuen"):
        self.cmds, self.offline = [], 0
        self.w, self.h, self.header, self.focus = w, h, header, focus

    def __call__(self, argv, timeout):
        args = argv[3:] if argv[1] == "-s" else argv[1:]
        self.cmds.append(args)
        if args[0] == "connect":
            return 0, b"already connected to x", b""
        if self.offline:
            self.offline -= 1
            return 1, b"", b"error: device offline"
        if args == ["get-state"]:
            return 0, b"device\n", b""
        if args == ["exec-out", "screencap"]:
            return 0, raw_screencap(self.w, self.h, self.header), b""
        if args[0] == "shell" and "dumpsys window" in args[1]:
            return 0, f"  mCurrentFocus=Window{{abc u0 {self.focus}/.Main}}\n".encode(), b""
        return 0, b"", b""


def test_parse_screencap_handles_both_headers_and_converts_to_bgr():
    for header in (12, 16):
        img = parse_screencap(raw_screencap(4, 6, header))
        assert img.shape == (6, 4, 3) and tuple(img[0, 0]) == (30, 20, 10)
    with pytest.raises(DeviceUnavailable):
        parse_screencap(b"\x00" * 8)


def test_adb_device_tap_swipe_and_alive():
    fake = FakeAdb()
    dev = AdbDevice("127.0.0.1:16384", adb="adb", run=fake)
    dev.connect()
    assert dev.screenshot().shape == (1280, 720, 3)
    dev.tap(360, 640)
    assert fake.cmds[-1] == ["shell", "input tap 540 960"]
    dev.swipe(100, 900, 100, 300, duration=0.3)
    assert fake.cmds[-1] == ["shell", "input swipe 150 1350 150 450 300"]
    dev.swipe(100, 900, 100, 300, duration=0.3, hold=0.3)
    script = fake.cmds[-1][1]
    assert script.startswith("input motionevent DOWN 150 1350") and "sleep 0.30" in script
    assert script.endswith("input motionevent UP 150 450")
    assert dev.alive()
    fake.focus = "com.android.launcher3"
    assert not dev.alive()


def test_adb_device_reconnects_then_gives_up():
    fake = FakeAdb()
    dev = AdbDevice("127.0.0.1:16384", adb="adb", run=fake)
    fake.offline = 1
    dev.screenshot()  # 掉线一次：adb connect 后成功
    assert ["connect", "127.0.0.1:16384"] in fake.cmds
    fake.offline = 2
    with pytest.raises(DeviceGone):
        dev.screenshot()


def test_adb_connect_refuses_bad_state():
    def run(argv, timeout):
        return (0, b"already connected", b"") if "connect" in argv else (1, b"", b"error: device offline")

    with pytest.raises(DeviceUnavailable):
        AdbDevice("127.0.0.1:16384", adb="adb", run=run).connect()


def test_focus_package():
    assert focus_package("mCurrentFocus=Window{1 u0 com.a.b/.X}") == "com.a.b"
    assert focus_package("mCurrentFocus=null") is None


# ---------------------------------------------------------------- 工厂、依赖


def test_open_device_by_name(monkeypatch):
    assert open_device("maatools").name == "maatools"
    assert open_device("dmm").name == "dmm-win32"
    for n in ("adb", "mumu", "leidian"):
        assert open_device(n).name == "adb"
    with pytest.raises(ValueError):
        open_device("quartz")
    monkeypatch.setenv("HIRO_DEVICE", "mumu")
    assert open_device().name == "adb"


def test_new_device_layer_does_not_import_kaa():
    code = (
        "import sys, hiro.devices, hiro.devices.maatools, hiro.devices.adb, hiro.devices.dmm_win32;"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('kotonebot', 'kaa', 'maa'));"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_maatools_real_tap_goes_to_real_click(monkeypatch):
    import hiro.real_click as rc

    seen = []
    monkeypatch.setattr(rc, "click", lambda x, y: seen.append((x, y)))
    MaaToolsDevice(client=object()).real_tap(300, 700)
    assert seen == [(300, 700)]


def test_adb_shell_feeds_game_app_foreground():
    fake = FakeAdb()
    dev = AdbDevice("127.0.0.1:16384", adb="adb", run=fake)
    out = dev.shell("dumpsys window | grep mCurrentFocus")
    assert fake.cmds[-1] == ["shell", "dumpsys window | grep mCurrentFocus"]
    assert focus_package(out) == "com.bandainamcoent.idolmaster_gakuen"


def test_settled_frame_shared_by_all_devices():
    """重截挪进 base 三个设备共用（平台 bb66f22 原来只在 DMM 上）：比例对的第一张直接用，不对的在时限内重截。"""
    from hiro.devices.base import settled_frame

    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        t["now"] += s

    frames = iter([bgr(540, 572), bgr(540, 572), bgr(961, 541)])
    out = settled_frame(lambda: next(frames), LogicalScreen(), sleep=sleep, clock=lambda: t["now"])
    assert out.shape == (720, 1280, 3) and len(slept) == 2
    assert settled_frame(lambda: bgr(541, 961), LogicalScreen(), sleep=sleep, clock=lambda: t["now"]).shape == (
        1280,
        720,
        3,
    )
    with pytest.raises(DeviceUnavailable):
        settled_frame(lambda: bgr(540, 572), LogicalScreen(), settle_s=1.0, sleep=sleep, clock=lambda: t["now"])


@pytest.mark.parametrize("module", ["maatools", "adb", "dmm_win32"])
def test_every_device_screenshot_goes_through_settled_frame(module):
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(f"hiro.devices.{module}"))
    assert "settled_frame(" in src and "self.screen.frame(" not in src
