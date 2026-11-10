"""日程表覆盖层（スケジュール / 育成目標 / 編成）：DMM dmm2 过渡页点 (12,12) 落进左上「選抜試験Nまで」框把它打开，
白色时间轴又被当成底部消息气泡、继续点 (12,12)，3 分钟停机。认出来点底部 ✕。"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from hiro.vision import quick

FIX = Path(__file__).parent / "fixtures/schedule-overlay-dmm2.png"


@pytest.mark.skipif(not FIX.exists(), reason="需要实机截图 tests/fixtures/schedule-overlay-dmm2.png")
def test_schedule_overlay_closes_with_x():
    r = quick.quick_page(cv2.imread(str(FIX)))
    assert r["page"] == "TRANSITION" and r["fields"]["kind"] == "schedule" and tuple(r["tap"]) == quick.SCHEDULE_CLOSE


def test_schedule_needs_bar_and_button():
    hsv = np.zeros((1280, 720, 3), np.uint8)
    y1, y2 = quick.SCHEDULE_BAR_Y
    hsv[y1:y2, 20:400] = (92, 200, 220)  # 青色标签栏
    assert not quick._schedule_overlay(hsv)  # 没有 ✕ 圆钮
    x1, cy1, x2, cy2 = quick.SCHEDULE_CLOSE_BOX
    hsv[cy1:cy2, x1:x2] = (0, 0, 250)
    assert quick._schedule_overlay(hsv)
    hsv[y1:y2, 20:400] = (30, 200, 220)  # 黄色栏不算
    assert not quick._schedule_overlay(hsv)
