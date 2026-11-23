"""第二十二局：通信エラー 回标题后「再開」进轮间，「インターバル」标题徽章比 KAA 模板原位低 13px，
KAA HifInterval.Title.exists() 判不出 → 主循环 UNKNOWN 197s 停机。左上角任意位置找徽章。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.loop.controller import interval_title_visible

import cv2

FIX = Path(__file__).parent / "fixtures"


def test_low_title_after_relogin_detected():
    assert interval_title_visible(cv2.imread(str(FIX / "interval/m22-interval-title-low.jpg")))


def test_normal_interval_frame_detected():
    img = cv2.imread(str(FIX / "interval/m21-interval-title-normal.jpg"))
    if img is None:
        import pytest

        pytest.skip("样例帧不在（第二十一局本战轮间页）")
    assert interval_title_visible(img)


def test_non_interval_frames_not_detected():
    for f in ("deck_list/m20-deck-list-late.png",):
        img = cv2.imread(str(FIX / f))
        if img is not None:
            assert not interval_title_visible(cv2.resize(img, (720, 1280)))
