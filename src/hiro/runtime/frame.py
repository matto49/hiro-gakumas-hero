"""一拍的画面：截图 + 按需算的整帧文字和弹窗读数（同一拍内各模块共用，不重复 OCR）。"""

from __future__ import annotations

from functools import cached_property

from hiro.vision.ocr import Text, screen_texts

import numpy as np


class Frame:
    """一张截图。texts / boxes / dialog 第一次用到才算，算一次就缓存。"""

    def __init__(self, img: np.ndarray, t: float):
        self.img, self.t = img, t

    @cached_property
    def has_dialog(self) -> bool:
        """有没有橙黄标题条的弹窗（像素判据，约 20 ms）。中断层靠它决定要不要整帧 OCR。"""
        from hiro.vision.pages import dialog_header

        return dialog_header(self.img) is not None

    @cached_property
    def dialog(self) -> dict | None:
        """弹窗 {kind, title, body, danger, y}（hiro.vision.pages.read_dialog）；没有弹窗 None。"""
        from hiro.vision.pages import read_dialog

        return read_dialog(self.img) if self.has_dialog else None

    @cached_property
    def texts(self) -> list[Text]:
        return screen_texts(self.img)

    @property
    def boxes(self) -> list[tuple[str, float, tuple[int, int, int, int]]]:
        """texts 的 (文字, 置信度, (x1, y1, x2, y2)) 形式：loop 下 comm_error / date_change / skip_settings 收这种。"""
        return [(t.text, t.conf, tuple(t.rect)) for t in self.texts]


class Screen:
    """设备截图 → Frame；记住最近一帧（守卫和处理器看的都是它）。"""

    def __init__(self, device, clock):
        self.device, self.clock = device, clock
        self.frame: Frame | None = None

    def shot(self) -> Frame:
        self.frame = Frame(self.device.screenshot(), self.clock())
        return self.frame
