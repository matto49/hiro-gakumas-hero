# 来源：digital-hiro hiro/device.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""连接游戏设备，统一给出 720x1280 逻辑坐标的 kotonebot Device。

后端由 HIRO_DEVICE 选（见 resolve_backend / docs）：
- maatools / quartz：macOS + PlayCover（本模块自己的实现，作者实机验证过的路线）；
- adb / mumu / leidian / dmm：交给 KAA 的设备工厂（kaa.main.kaa.KaaDeviceFactory），与 KAA 支持的平台对齐；
  KAA 后端配置由 hiro.kaa_env.init_kaa 按 HIRO_DEVICE / HIRO_ADB / HIRO_SCREENSHOT / HIRO_EMULATOR_INSTANCE 写入。

以下是 PlayCover 路线的说明。

默认走 MaaTools：hguandl/PlayCover fork 的 PlayTools 在游戏进程内开 TCP 服务（设置项 maaTools / maaToolsPort），
进程内截图、以 UITouch 注入触摸，不要求游戏在前台，也不动鼠标。协议见 hguandl/PlayTools 的 MaaTools.swift。

MaaTools 连不上时（原版 PlayCover）回退到旧方案：
截图沿用 kotonebot 的 QuartzImpl（按窗口原生像素截图并裁掉标题栏）。
输入不用 QuartzImpl 的全局 CGEvent（会移动真实鼠标，录视频时光标乱跳），改为与 MaaFramework PostToPid 相同的做法：
构造带窗口号的 NSEvent，转成 CGEvent 后用 CGEventPostToPid 直接投递给游戏进程。
实测 PlayCover 只在游戏是前台应用时接受这类事件，所以每次输入前先激活游戏；点击拆为按下、短暂停留、抬起。
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import struct
import threading
import time

import numpy as np

from kotonebot.client.device import Device
from kotonebot.client.scaler import PortraitGameScaler
from kotonebot.config.config import conf
from kotonebot.primitives.geometry import Size

BUNDLE_ID = "jp.co.bandainamcoent.BNEI0421"
CLICK_HOLD_SECONDS = 0.06
ACTIVATE_SETTLE_SECONDS = 0.3
SWIPE_FPS = 60
MAATOOLS_PORT = 1717
# 游戏以 Catalyst「按 iPad 缩放」运行，UIKit 1pt = 0.77 Mac pt；MaaTools 只把触摸坐标除以 nativeScale(2)，
# 所以截图物理像素（Mac pt × 2）要再除以 0.77 才是 UIKit 坐标 × 2。不除时点击整体偏向左上。
CATALYST_IDIOM_SCALE = 0.77
# 断线（游戏重启、PlayTools 服务重开）后按间隔重连，直到超时；游戏冷启动到 MaaTools 开端口约需十几秒
RECONNECT_TIMEOUT = 60.0
RECONNECT_INTERVAL = 1.0
SOCKET_TIMEOUT = 5.0

logger = logging.getLogger(__name__)


class MaaToolsImpl:
    """kotonebot Screenshotable + Touchable：经 MaaTools 截图与触摸，坐标为截图物理像素。"""

    TOUCH_BEGAN, TOUCH_MOVED, TOUCH_ENDED = 0, 1, 3

    def __init__(self, device: Device, port: int = MAATOOLS_PORT, *, reconnect_timeout: float = RECONNECT_TIMEOUT,
                 reconnect_interval: float = RECONNECT_INTERVAL):  # fmt: skip
        self.swipe_hold = 0.0  # >0 时 swipe 在终点停这么久再抬起（无惯性滚动）
        self.device = device
        self.port = port
        self.reconnect_timeout = reconnect_timeout
        self.reconnect_interval = reconnect_interval
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._size: tuple[int, int] | None = None
        # 已按下未抬起的触点（发送坐标）。PlayTools 按触点号保存触摸状态且跨连接共享，
        # 抬起没送到时该触点会一直占着、之后的按下全被忽略，所以重连后先补发抬起。
        self._down: tuple[int, int] | None = None

    def start(self) -> None:
        with self._lock:
            self._connect()

    def stop(self) -> None:
        with self._lock:
            self._close()

    def _connect(self) -> None:
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=SOCKET_TIMEOUT)
        sock.sendall(b"MAA\x00")
        if self._recv(sock, 4) != b"OKAY":
            sock.close()
            raise ConnectionError("MaaTools 握手失败")
        self._sock = sock

    def _close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    @staticmethod
    def _recv(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("MaaTools 连接被关闭")
            buf += chunk
        return bytes(buf)

    def _alive(self) -> bool:
        """TUCH 没有回包，对端断开后 sendall 往往仍「成功」、触摸被静默丢掉；发送前用非阻塞 peek 探测 EOF。"""
        # 带超时的 socket 会先 select 等到超时，MSG_DONTWAIT 不起作用；探测期间临时切成非阻塞
        self._sock.setblocking(False)
        try:
            return bool(self._sock.recv(1, socket.MSG_PEEK))
        except BlockingIOError:
            return True
        except OSError:
            return False
        finally:
            self._sock.settimeout(SOCKET_TIMEOUT)

    def _send(self, cmd: bytes, payload: bytes = b"") -> None:
        self._sock.sendall(struct.pack(">H", len(cmd) + len(payload)) + cmd + payload)

    def _reconnect(self, reason: object) -> None:
        """按间隔重连直到 reconnect_timeout；超时抛 ConnectionError。重连后补发未送达的抬起。"""
        self._close()
        logger.warning("MaaTools 连接断开（%s），每 %.1fs 重连一次，最多等 %.0fs", reason, self.reconnect_interval, self.reconnect_timeout)
        t0 = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                self._connect()
                break
            except OSError as e:
                waited = time.monotonic() - t0
                if waited + self.reconnect_interval > self.reconnect_timeout:
                    raise ConnectionError(f"MaaTools 重连失败：等了 {waited:.1f}s、尝试 {attempts} 次（{e}）") from e
                time.sleep(self.reconnect_interval)
        logger.warning("MaaTools 已重连：等了 %.1fs、尝试 %d 次", time.monotonic() - t0, attempts)
        if self._down is not None:
            x, y = self._down
            self._send(b"TUCH", struct.pack(">BHHB", self.TOUCH_ENDED, x, y, 0))
            self._down = None

    def _call(self, cmd: bytes, payload: bytes = b"", reply=None):
        """发一条命令；reply(sock) 读取响应。连接断开时重连（见 _reconnect）后重发一次。"""
        with self._lock:
            try:
                if self._sock is None or not self._alive():
                    raise ConnectionError("未连接或对端已关闭")
                self._send(cmd, payload)
                return reply(self._sock) if reply else None
            except OSError as e:
                self._reconnect(e)
                self._send(cmd, payload)
                return reply(self._sock) if reply else None

    def _read_bgr(self, sock: socket.socket):
        w, h, n = struct.unpack(">III", self._recv(sock, 12))
        if n == 0:
            raise RuntimeError("MaaTools 返回空帧")
        # 数据本身就是 BGR 顺序，可直接给 OpenCV
        return np.frombuffer(self._recv(sock, n), np.uint8).reshape(h, w, 3)

    @property
    def screen_size(self) -> tuple[int, int]:
        if self._size is None:
            self.screenshot()
        return self._size

    def detect_orientation(self):
        w, h = self.screen_size
        return "landscape" if w > h else "portrait"

    def screenshot(self):
        image = self._call(b"BGR\x01", reply=self._read_bgr)
        self._size = (image.shape[1], image.shape[0])
        return image

    def _touch(self, phase: int, x: int, y: int) -> None:
        tx, ty = round(x / CATALYST_IDIOM_SCALE), round(y / CATALYST_IDIOM_SCALE)
        self._call(b"TUCH", struct.pack(">BHHB", phase, tx, ty, 0))
        self._down = None if phase == self.TOUCH_ENDED else (tx, ty)

    def click(self, x: int, y: int) -> None:
        self._touch(self.TOUCH_BEGAN, x, y)
        time.sleep(CLICK_HOLD_SECONDS)
        self._touch(self.TOUCH_ENDED, x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float | None = None) -> None:
        steps = max(2, int((duration or 0.3) * SWIPE_FPS))
        self._touch(self.TOUCH_BEGAN, x1, y1)
        for i in range(1, steps + 1):
            t = i / steps
            time.sleep(1 / SWIPE_FPS)
            self._touch(self.TOUCH_MOVED, round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
        if self.swipe_hold:  # 停在终点再抬起：列表不会带惯性继续滚（回忆选择页一次滑过了好几行）
            time.sleep(self.swipe_hold)
            self._touch(self.TOUCH_MOVED, x2, y2)
        self._touch(self.TOUCH_ENDED, x2, y2)


class PostToPidTouch:
    """kotonebot Touchable：坐标为截图（内容区）物理像素。"""

    def __init__(self, device: Device, quartz: QuartzImpl):
        self.device = device
        self.quartz = quartz

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def _activate(self, pid: int) -> None:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None and not app.isActive():
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(ACTIVATE_SETTLE_SECONDS)

    def _post(self, event_type: int, x: int, y: int) -> None:
        import Quartz
        from AppKit import NSEvent

        window = self.quartz._window()
        frame = window.get_bounds()
        content_x, content_y, _, _ = self.quartz._content_bounds()
        scale = self.quartz._pixel_scale()
        # NSEvent 用窗口坐标系：原点在窗口左下角，单位为逻辑点
        local_x = (content_x - frame.x1) + x / scale
        local_y = frame.h - ((content_y - frame.y1) + y / scale)
        event = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
            event_type, (local_x, local_y), 0, time.monotonic(), window.info.id, None, 0, 1, 1.0 if event_type != Quartz.kCGEventLeftMouseUp else 0.0
        )
        Quartz.CGEventPostToPid(window.info.process_id, event.CGEvent())

    def click(self, x: int, y: int) -> None:
        import Quartz

        self._activate(self.quartz._window().info.process_id)
        self._post(Quartz.kCGEventLeftMouseDown, x, y)
        time.sleep(CLICK_HOLD_SECONDS)
        self._post(Quartz.kCGEventLeftMouseUp, x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float | None = None) -> None:
        import Quartz

        self._activate(self.quartz._window().info.process_id)
        steps = max(2, int((duration or 0.3) * SWIPE_FPS))
        self._post(Quartz.kCGEventLeftMouseDown, x1, y1)
        for i in range(1, steps + 1):
            t = i / steps
            self._post(Quartz.kCGEventLeftMouseDragged, round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
            time.sleep(1 / SWIPE_FPS)
        self._post(Quartz.kCGEventLeftMouseUp, x2, y2)


def _maatools_available(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


PLAYCOVER_BACKENDS = ("maatools", "quartz")
KAA_BACKENDS = {"adb": "custom", "mumu": "mumu12v5", "leidian": "leidian", "dmm": "dmm"}  # HIRO_DEVICE → KAA lifecycle.type
BACKENDS = ("auto", *PLAYCOVER_BACKENDS, *KAA_BACKENDS)
BACKEND: str | None = None  # connect() 实际选中的后端；PlayCover 专用的退路（横屏全屏、真实鼠标点击）看它决定开不开
DEVICE = None  # connect() 建的设备（hiro.loop.game_app 从里面取 adb shell 查前台应用 / 拉起游戏）


def requested_backend() -> str:
    """HIRO_DEVICE（缺省 auto）；写错直接报错，列出可选值。"""
    name = (os.environ.get("HIRO_DEVICE") or "auto").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"HIRO_DEVICE={name!r} 不认识；可选：{', '.join(BACKENDS)}")
    return name


def resolve_backend(name: str | None = None, *, platform: str | None = None, maatools_up=None) -> str:
    """把 auto 落到具体后端：macOS 上 MaaTools 端口可连用 maatools，否则 quartz；
    其他平台不猜（模拟器 / DMM / 真机都可能），要求显式设 HIRO_DEVICE。"""
    name = name or requested_backend()
    if name != "auto":
        return name
    if (platform or sys.platform) == "darwin":
        up = maatools_up if maatools_up is not None else _maatools_available(MAATOOLS_PORT)
        if not up:
            logger.warning("MaaTools 端口 %d 不可用，回退到 Quartz 截图 + PostToPid（需要游戏在前台）", MAATOOLS_PORT)
        return "maatools" if up else "quartz"
    raise RuntimeError("这台机器不是 macOS，请设 HIRO_DEVICE 选设备：adb（安卓设备 / 其他模拟器）、mumu、leidian、dmm。"
                       "见 AGENTS.md「设备」一节")  # fmt: skip


def is_playcover() -> bool:
    """当前连的是不是 PlayCover（maatools / quartz）。还没 connect 时按 HIRO_DEVICE 和平台推断。"""
    if BACKEND is not None:
        return BACKEND in PLAYCOVER_BACKENDS
    try:
        name = requested_backend()
    except ValueError:
        return False
    return name in PLAYCOVER_BACKENDS or (name == "auto" and sys.platform == "darwin")


def fallback_click():
    """控件点了没反应时的兜底点击（produce.ops.Run.real_click）：PlayCover 返回真实鼠标点击（MaaTools 偶尔点不进饮料详情等弹窗），
    其他设备 None（adb / DMM 的点击没有这个问题）。平台判断放在设备层，执行层只问「有没有兜底」。"""
    if not is_playcover():
        return None
    from hiro import real_click

    return real_click.click


def _kaa_config(name: str):
    """KAA 设备工厂要的配置：已 init_kaa 就用当前配置（后端已按环境变量写好）；
    否则（tap 等工具直接 connect）现建一份，后端按环境变量写。"""
    from kaa.config.schema import KaaConfig

    from hiro.kaa_env import kaa_backend_from_env

    try:
        from kaa.kaa_context import conf as kaa_conf

        config = kaa_conf()
        if config.backend.lifecycle.type == KAA_BACKENDS[name]:
            return config
    except Exception:  # noqa: BLE001  KAA 上下文还没初始化
        config = KaaConfig()
    config.backend = kaa_backend_from_env()
    return config


def _keep_swipe_duration(device) -> None:
    """模拟器后端的滑动保留时长。KotoneBot 的 AdbImpl.swipe 会丢掉 duration（日志「Swipe duration is not supported
    with AdbDevice」），快速划过去列表带惯性多滚；Windows + MuMu 第二局每次换卡翻牌组都这样。
    有 MuMu 的 nemu_ipc 触摸就交给它（按 duration 分步移动），否则用 adb 自带的「input touchscreen swipe … 毫秒」。"""
    parts = [getattr(device, a, None) for a in ("_touch", "_multitouch", "commands")]
    nemu = next((c for c in parts if type(c).__name__ == "NemuIpcImpl"), None)
    adb = next((c for c in parts if type(c).__name__ == "AdbImpl"), None)
    if nemu is None and adb is None:
        return
    plain = device.swipe

    def swipe(x1: int, y1: int, x2: int, y2: int, duration: float | None = None, **kw) -> None:
        if not duration:
            return plain(x1, y1, x2, y2, duration, **kw)
        (a1, b1), (a2, b2) = device.scaler.logic_to_physical((x1, y1)), device.scaler.logic_to_physical((x2, y2))
        a1, b1, a2, b2 = int(a1), int(b1), int(a2), int(b2)
        if nemu is not None:
            nemu.swipe(a1, b1, a2, b2, duration)
        else:
            adb.adb.shell(f"input touchscreen swipe {a1} {b1} {a2} {b2} {max(1, round(duration * 1000))}")

    device.swipe = swipe


def connect(backend: str | None = None) -> Device:
    """创建并启动连接游戏的设备，逻辑分辨率 720x1280。

    backend：缺省读 HIRO_DEVICE（auto / maatools / quartz / adb / mumu / leidian / dmm）。
    adb / mumu / leidian / dmm 交给 KAA 的设备工厂，要求先 hiro.kaa_env.init_kaa（KAA 配置里有后端设置）。
    """
    global BACKEND, DEVICE
    name = resolve_backend(backend)
    conf().device.default_scaler_factory = lambda: PortraitGameScaler()
    conf().device.default_logic_resolution = Size(720, 1280)
    if name in KAA_BACKENDS:
        from kaa.main.kaa import KaaDeviceFactory

        device = KaaDeviceFactory().create_device_for_config(_kaa_config(name))
        _keep_swipe_duration(device)
    else:
        from kotonebot.client.device import MacOSDevice
        from kotonebot.client.implements.macos import QuartzImpl
        from kotonebot.client.playcover import Playcover

        device = MacOSDevice()
        if name == "maatools":
            maatools = MaaToolsImpl(device)
            device.setup(screenshot=maatools, touch=maatools)
        else:
            app = Playcover.find(BUNDLE_ID)
            if app is None:
                raise RuntimeError("PlayCover 中找不到学マス")
            quartz = QuartzImpl(device=device, window_query=app._window_query)
            device.setup(screenshot=quartz, touch=PostToPidTouch(device, quartz))
    BACKEND, DEVICE = name, device
    logger.info("设备后端：%s", name)
    device.start()
    device.screenshot()  # 缩放器要靠首张截图确定物理分辨率，之后才能换算点击坐标
    return device
