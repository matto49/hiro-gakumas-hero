"""第二十二局：通信エラー 回标题后「再開」进轮间，「インターバル」标题徽章比模板原位低 13px，
模板 exists() 判不出 → UNKNOWN 197s 停机。左上角任意位置找徽章（hiro.vision.classify.interval_title_visible）。
原来测旧主循环 hiro.loop.controller；2026-11-24 旧主循环删掉时改测新主循环的判页（之前新主循环同一帧判 unknown）。"""

from pathlib import Path

from hiro.vision.classify import classify_scene, interval_title_visible

import cv2
import pytest

FIX = Path(__file__).parent / "fixtures"


def _img(rel: str):
    img = cv2.imread(str(FIX / rel))
    if img is None:
        pytest.skip(f"样例帧不在：{rel}（私有样例）")
    return cv2.resize(img, (720, 1280))


def test_low_title_after_relogin_detected():
    img = _img("interval/m22-interval-title-low.jpg")
    assert interval_title_visible(img)
    assert classify_scene(img).name == "HIF_ROUND_INTERVAL"


def test_normal_interval_frame_detected():
    assert interval_title_visible(_img("interval/m21-interval-title-normal.jpg"))


def test_non_interval_frames_not_detected():
    assert not interval_title_visible(_img("deck_list/m20-deck-list-late.png"))
