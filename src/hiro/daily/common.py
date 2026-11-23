"""日常任务共用：按钮亮没亮、颜色找点、滚动条、取一帧，以及两个日常专用中断（说明框 / ジュエル框看守、单项超时）。

颜色找点和滚动条是 KAA 同名做法的移植（kotonebot.backend.color.find、kaa.game_ui.elements.scrollbar），不 import KAA / kotonebot。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from hiro.daily_guard import classify_popup
from hiro.vision.ocr import Box
from hiro.vision.ui_templates import normalize, primary_button_enabled

import numpy as np

logger = logging.getLogger(__name__)


def enabled(f, m) -> bool | None:
    """主按钮（橙底）亮没亮：True 亮、False 灰、None 认不出（同 KAA GakumasPrimaryButtonPrefab.q(enabled=...)）。测试替身换这一个。"""
    return primary_button_enabled(f.img, m)


def frame(rt, timeout: float = 5.0):
    """取一帧（过检查点和中断层；中断处理过的那一拍不算）。"""
    return rt.wait_until(lambda f: True, timeout)[1]


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def has_color(img: np.ndarray, color: str, rect: Box, threshold: float = 0.95) -> bool:
    """rect 里有没有接近 color（#RRGGBB）的像素。同 kotonebot color.find：HLS 空间加权距离（H 权重 2、环形），
    除以 √6 归一化，距离 ≤ 1 - threshold 算命中。"""
    import cv2

    crop = normalize(img)[rect.y1 : rect.y2, rect.x1 : rect.x2]
    if crop.size == 0:
        return False
    target = cv2.cvtColor(np.array([[_hex(color)]], np.uint8), cv2.COLOR_RGB2HLS)[0, 0].astype(np.float32)
    hls = cv2.cvtColor(crop, cv2.COLOR_BGR2HLS).astype(np.float32)
    dh = np.abs(hls[..., 0] - target[0])
    dh = np.minimum(dh, 180 - dh) / 90
    dl = np.abs(hls[..., 1] - target[1]) / 255
    ds = np.abs(hls[..., 2] - target[2]) / 255
    dist = np.sqrt((dh * 2) ** 2 + dl**2 + ds**2) / np.sqrt(6)
    return bool((dist <= 1 - threshold).any())


@dataclass(frozen=True)
class Scrollbar:
    """滚动条把手在轨道里的位置（轨道坐标）。position = 把手下沿 / 轨道高，1 表示到底。"""

    thumb_start: int
    thumb_height: int
    track_height: int

    @property
    def position(self) -> float:
        return (self.thumb_start + self.thumb_height) / self.track_height


def scrollbar(img: np.ndarray, rect: tuple[int, int, int, int]) -> Scrollbar | None:
    """rect=(x, y, w, h) 是滚动条轨道。同 KAA analyze_scrollbar_track：灰度 140–142 的像素按行求平均，取最长的连续段当把手；
    把手比轨道 5% 还短（或没有）返回 None。"""
    import cv2

    x, y, w, h = rect
    track = normalize(img)[y : y + h, x : x + w]
    if track.size == 0:
        return None
    gray = cv2.cvtColor(track, cv2.COLOR_BGR2GRAY)
    binary = cv2.inRange(gray, 140, 142)
    bar = cv2.inRange(cv2.reduce(binary, dim=1, rtype=cv2.REDUCE_AVG, dtype=cv2.CV_8U), 100, 255)
    contours, _ = cv2.findContours(bar, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    _, ty, _, th = cv2.boundingRect(max(contours, key=lambda c: cv2.boundingRect(c)[3]))
    if th <= 0 or th < track.shape[0] * 0.05:
        return None
    return Scrollbar(int(ty), int(th), int(track.shape[0]))


def scroll_by(rt, rect: tuple[int, int, int, int], bar: Scrollbar, fraction: float) -> None:
    """拖把手往下 fraction 个轨道高（同 KAA scrollbar.by：0.3 秒拖完）。"""
    x, y, w, _ = rect
    cx, src = x + w // 2, y + bar.thumb_start + bar.thumb_height // 2
    rt.device.swipe(cx, src, cx, src + int(bar.track_height * fraction), duration=0.3)


class Popup:
    """日常专用中断（替代 run_daily 的后台 PopupGuard，判据同 hiro.daily_guard.classify_popup）：
    - 含ジュエル的确认框：马上点「キャンセル」（不等第二拍：任务这一拍可能正要点确认）；
    - 只有「閉じる」的说明框（〜獲得 / お知らせ / リクエスト受け取り 等）：连续 confirm 拍都在才点，转场帧不碰。
    只在 frame.has_dialog（橙黄标题条）时 OCR。on_action(kind, img) 每次动手后回调（run_daily 用来存图）。"""

    def __init__(self, on_action: Callable[[str, object], None] | None = None, *, confirm: int = 2):
        self.on_action, self.confirm = on_action, confirm
        self.actions: list[tuple[str, tuple[int, int]]] = []
        self._last, self._hits = None, 0

    def __call__(self, f, rt) -> str | None:
        hit = classify_popup(f.boxes) if f.has_dialog else None
        if hit is None:
            self._last, self._hits = None, 0
            return None
        self._hits = self._hits + 1 if hit == self._last else 1
        self._last = hit
        kind, pos = hit
        if kind != "cancel" and self._hits < self.confirm:
            return None
        rt.device.tap(*pos)
        self.actions.append(hit)
        self._last, self._hits = None, 0
        if self.on_action is not None:
            self.on_action(kind, f.img)
        return "含ジュエル的确认框 → キャンセル" if kind == "cancel" else "说明框 → 閉じる"


class TaskTimeout(Exception):
    """日常单项超过时限（Deadline 抛）。"""


class Deadline:
    """日常专用中断：每拍看一眼，超过 seconds 就抛 TaskTimeout（替代 run_daily 靠 kotonebot flow 的打断）。start() 每项开头调。"""

    def __init__(self, seconds: float, clock: Callable[[], float]):
        self.seconds, self.clock = seconds, clock
        self.t0 = clock()

    def start(self) -> None:
        self.t0 = self.clock()

    def __call__(self, f, rt) -> str | None:
        if self.clock() - self.t0 > self.seconds:
            raise TaskTimeout(f"超时 {self.seconds:.0f}s")
        return None
