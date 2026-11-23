"""第三十二局：夺冠后横屏 LIVE 开始前有一页「Campus mode!! / 篠澤広 / 作詞… / TAP TO START」（1280x720），要点一下才开始。
d88829f 后横屏帧只处理窗口、不点，停在这页约 5 分钟（看门狗把横屏算进展，不报警）。"""

from pathlib import Path

from hiro.loop.controller import live_start_tap
from hiro.produce.ops import ocr_boxes

import cv2

FIX = Path(__file__).parent / "fixtures/live"


def test_real_tap_to_start_page():
    img = cv2.imread(str(FIX / "g32-tap-to-start.png"))
    tap = live_start_tap(img, ocr_boxes(img))
    assert tap is not None and abs(tap[0] - 640) < 40 and abs(tap[1] - 610) < 30  # 总控手动点的是 (640, 610)


def test_live_in_progress_not_tapped():
    img = cv2.imread(str(FIX / "g32-live.png"))
    assert live_start_tap(img, ocr_boxes(img)) is None


def test_info_only_taps_bottom_center():
    import numpy as np

    img = np.zeros((720, 1280, 3), np.uint8)
    assert live_start_tap(img, [("作詞：田淵智也", 1.0, (495, 353, 636, 376))]) == (640, 612)
