"""日初倒计时「選抜試験2まで 4日」：以前认不出、连点 (12,12)，切到选行动页时正好点进左上「選抜試験Nまで」框打开日程表
（DMM dmm2 qualifydmm2-20261111-002000-00229）。认出来不点；等久了点两平台都空白的 SAFE_BLANK_TAP。"""

from pathlib import Path

from hiro.vision import quick

import cv2
import numpy as np
import pytest

FIX = Path(__file__).parent / "fixtures"


def _img(rel):
    f = FIX / rel
    if not f.exists():
        pytest.skip(f"需要实机截图 {f}")
    return cv2.imread(str(f))


def test_countdown_recognized_without_tap():
    r = quick.quick_page(_img("dmm/qualifydmm2-20261111-002000-00229.jpg"))
    assert r["page"] == "TRANSITION" and r["fields"]["kind"] == "day_countdown" and not r.get("tap")
    assert "まで" in r["fields"]["text"]


def test_action_select_not_countdown():
    """选行动页左上框里也有「選抜試験Nまで N日」，但有 HUD（審査基準），不走倒计时判断。"""
    for rel in ("special_guidance_dmm/1b-selected-not-confirmed.jpg",):
        img = _img(rel)
        r = quick.quick_page(img)
        assert r is None or r["fields"].get("kind") != "day_countdown"


def test_day_countdown_ocr_band(monkeypatch):
    import hiro.vision.ocr as ocrmod

    monkeypatch.setattr(ocrmod, "ocr", lambda im, **kw: [("選抜試験2まで", 0.9), ("4", 0.9), ("日", 0.9)])
    assert quick.day_countdown(np.zeros((1280, 720, 3), np.uint8)) == "選抜試験2まで4日"
    monkeypatch.setattr(ocrmod, "ocr", lambda im, **kw: [("プロデュース", 0.9)])
    assert quick.day_countdown(np.zeros((1280, 720, 3), np.uint8)) is None
