"""Mac + PlayCover：经 hguandl/PlayCover 的 PlayTools 在游戏进程里开的 MaaTools 服务截图、注入触摸（不要求前台、不动鼠标）。

MaaToolsImpl 是协议客户端（原在 hiro.device）；
MaaToolsDevice 是设备统一接口（hiro.devices.base）的实现：逻辑坐标、DeviceGone / DeviceUnavailable。
协议见 hguandl/PlayTools 的 MaaTools.swift。
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time

import numpy as np

from .base import DeviceGone, DeviceUnavailable, LogicalScreen, settled_frame

CLICK_HOLD_SECONDS = 0.06
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
    """MaaTools 协议客户端：截图与触摸，坐标为截图物理像素。原在 hiro.device，原样挪来。"""

    TOUCH_BEGAN, TOUCH_MOVED, TOUCH_ENDED = 0, 1, 3

    def __init__(self, device=None, port: int = MAATOOLS_PORT, *, reconnect_timeout: float = RECONNECT_TIMEOUT,
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
        logger.warning(
            "MaaTools 连接断开（%s），每 %.1fs 重连一次，最多等 %.0fs",
            reason,
            self.reconnect_interval,
            self.reconnect_timeout,
        )
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


def port_open(port: int = MAATOOLS_PORT, timeout: float = 1.0) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
        return True
    except OSError:
        return False


# 空帧（0 字节）：进横屏 LIVE、切全屏时窗口重新布局，MaaTools 会短暂返回空帧（11-17 Mac p15c：LandscapeGuard 刚进全屏就碰上，
# 原来直接抛 DeviceUnavailable，整局崩掉）。先重试 EMPTY_RETRY_S；还是空就给一张和上一帧同尺寸的黑帧（这一拍当没截到：横屏时
# LandscapeGuard 当黑屏处理、主循环不点）；连续 EMPTY_MAX_S 秒都是空帧才报 DeviceGone。
EMPTY_RETRY_S, EMPTY_RETRY_GAP_S = 3.0, 0.3
EMPTY_MAX_S = (
    90.0  # 要比 LandscapeGuard 退全屏晚（进全屏 30s 后 + 黑屏 8s）：11-25 空帧 30s 先报 DeviceGone，guard 没机会退全屏
)


class MaaToolsDevice:
    """设备统一接口：Mac + PlayCover（MaaTools）。"""

    name = "maatools"

    def __init__(
        self,
        port: int = MAATOOLS_PORT,
        *,
        client: MaaToolsImpl | None = None,
        reconnect_timeout: float = RECONNECT_TIMEOUT,
        reconnect_interval: float = RECONNECT_INTERVAL,
    ):
        self.port = port
        self.client = client or MaaToolsImpl(
            None, port, reconnect_timeout=reconnect_timeout, reconnect_interval=reconnect_interval
        )
        self.screen = LogicalScreen()
        self._empty_since: float | None = None
        self._last_shape: tuple[int, ...] | None = None
        self._sleep, self._clock = time.sleep, time.monotonic

    @property
    def orientation(self) -> str:
        return self.screen.orientation

    def connect(self) -> None:
        try:
            self.client.start()
        except OSError as e:
            raise DeviceUnavailable(f"MaaTools 端口 {self.port} 连不上（PlayCover 里开了 maaTools 吗？）：{e}") from e

    def close(self) -> None:
        self.client.stop()

    def _grab_once(self) -> np.ndarray | None:
        """截一张；空帧返回 None。"""
        try:
            return self.client.screenshot()
        except OSError as e:  # ConnectionError 也是 OSError：重连等到超时还连不上
            raise DeviceGone(f"MaaTools 断开：{e}") from e
        except RuntimeError:  # 空帧
            return None

    def _grab(self) -> np.ndarray:
        deadline = self._clock() + EMPTY_RETRY_S
        while True:
            image = self._grab_once()
            if image is not None:
                if self._empty_since is not None:
                    logger.info("MaaTools 空帧恢复（%.1fs）", self._clock() - self._empty_since)
                self._empty_since, self._last_shape = None, image.shape
                return image
            now = self._clock()
            if self._empty_since is None:
                self._empty_since = now
                logger.warning("MaaTools 返回空帧（窗口在重新布局？），重试")
            if now - self._empty_since >= EMPTY_MAX_S:
                raise DeviceGone(f"MaaTools 连续 {now - self._empty_since:.0f}s 返回空帧")
            if now >= deadline:
                break
            self._sleep(EMPTY_RETRY_GAP_S)
        if self._last_shape is None:
            raise DeviceUnavailable("MaaTools 返回空帧（还没截到过画面）")
        return np.zeros(self._last_shape, np.uint8)  # 这一拍当没截到：黑帧

    def screenshot(self) -> np.ndarray:
        """截图 → 逻辑分辨率；进 LIVE 横竖屏切换时重截（base.settled_frame）。"""
        return settled_frame(self._grab, self.screen)

    def _native(self, x: float, y: float) -> tuple[int, int]:
        if self.screen.native is None:
            self.screenshot()
        return self.screen.to_native(x, y)

    def tap(self, x: int, y: int) -> None:
        nx, ny = self._native(x, y)
        try:
            self.client.click(nx, ny)
        except OSError as e:
            raise DeviceGone(f"MaaTools 断开：{e}") from e

    def swipe(self, x1: int, y1: int, x2: int, y2: int, *, duration: float = 0.3, hold: float = 0.0) -> None:
        a1, b1 = self._native(x1, y1)
        a2, b2 = self.screen.to_native(x2, y2)
        self.client.swipe_hold = max(0.0, hold)
        try:
            self.client.swipe(a1, b1, a2, b2, duration)
        except OSError as e:
            raise DeviceGone(f"MaaTools 断开：{e}") from e
        finally:
            self.client.swipe_hold = 0.0

    def real_tap(self, x: int, y: int) -> None:
        """可选方法（不在统一接口里）：真实鼠标点逻辑坐标，会把游戏切到前台、移动用户的鼠标。
        只给一个调用方用：差し入れ饮料满时 MaaTools 点了没反应的兜底（produce/drink_swap，第二十三局）。见 hiro.real_click。"""
        from hiro import real_click

        real_click.click(x, y)

    def alive(self) -> bool:
        sock = self.client._sock
        if sock is not None:
            with self.client._lock:
                return self.client._alive()
        return port_open(self.port)
