"""H.I.F 选行动页公开课：OCR 读成「レツスン」也认；某一格文字没认出时按色条补（11-21 DMM 麻央：主属性 Da 那格丢了几天）。"""

from hiro.vision import action as A

import cv2
import numpy as np

ATTR_BGR = {"vo": (180, 80, 240), "da": (230, 140, 40), "vi": (40, 160, 245)}  # 粉 / 蓝 / 橙（饱和度够高）


def _frame():
    img = np.full((1280, 720, 3), 235, np.uint8)
    for x, a in zip(A.OPEN_LESSON_X, ("vo", "da", "vi")):
        cv2.rectangle(img, (x - 75, 1080), (x + 75, 1108), ATTR_BGR[a], -1)  # 文字条底色
    return img


def _ocr(texts):
    return lambda im, scale=2: [(t, 0.9, (x - 60, 1094 - 840 - 12, x + 60, 1094 - 840 + 12)) for t, x in texts]


def test_tsu_variant_is_open_lesson(monkeypatch):
    monkeypatch.setattr(
        A, "ocr_boxes", _ocr([("Vo.公開レッスン", 172), ("Da公開レッスン", 359), ("Vi公開レツスン", 544)])
    )
    got = [(o.kind, o.attr) for o in A._options(_frame())]
    assert got == [("open_lesson", "vo"), ("open_lesson", "da"), ("open_lesson", "vi")]


def test_missing_slot_filled_by_band_color(monkeypatch):
    monkeypatch.setattr(A, "ocr_boxes", _ocr([("Vo.公開レッスン", 172), ("Vi.公開レッスン", 544)]))
    got = [(o.kind, o.attr, o.center[0]) for o in A._options(_frame())]
    assert ("open_lesson", "da", 359) in got and len(got) == 3


def test_no_fill_without_matching_band(monkeypatch):
    """那一格没有彩色文字条（比如只有两节课）：不凭空补。"""
    img = _frame()
    img[1070:1120, 280:440] = 235
    monkeypatch.setattr(A, "ocr_boxes", _ocr([("Vo.公開レッスン", 172), ("Vi.公開レッスン", 544)]))
    got = [(o.kind, o.attr) for o in A._options(img)]
    assert got == [("open_lesson", "vo"), ("open_lesson", "vi")]
