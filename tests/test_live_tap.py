"""第三十二局：夺冠后横屏 LIVE 开始前有一页「Campus mode!! / 篠澤広 / 作詞… / TAP TO START」（1280x720），要点一下才开始。
d88829f 后横屏帧只处理窗口、不点，停在这页约 5 分钟（看门狗把横屏算进展，不报警）。
原来测旧主循环 hiro.loop.controller.live_start_tap；2026-11-24 旧主循环删掉后改测新主循环的 hiro.runtime.scenes.live_start_tap。"""

from pathlib import Path

from hiro.runtime.scenes import live_start_tap
from hiro.vision.ocr import Box, Text, screen_texts

import cv2
import pytest

FIX = Path(__file__).parent / "fixtures/live"


def _img(name: str):
    img = cv2.imread(str(FIX / name))
    if img is None:
        pytest.skip(f"样例帧不在：fixtures/live/{name}（私有样例）")
    return img


def test_real_tap_to_start_page():
    img = _img("g32-tap-to-start.png")
    tap = live_start_tap(img, screen_texts(img))
    assert tap is not None and abs(tap[0] - 640) < 40 and abs(tap[1] - 610) < 30  # 总控手动点的是 (640, 610)


def test_live_in_progress_not_tapped():
    img = _img("g32-live.png")
    assert live_start_tap(img, screen_texts(img)) is None


def test_info_only_taps_bottom_center():
    import numpy as np

    img = np.zeros((720, 1280, 3), np.uint8)
    assert live_start_tap(img, [Text("作詞：田淵智也", Box(495, 353, 636, 376))]) == (640, 612)
