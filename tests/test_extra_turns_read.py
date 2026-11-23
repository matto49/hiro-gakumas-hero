"""考试「残りターン」圆盘下的「+n」额外回合（11-19 实测：圆盘 12 +「+1」= 13 回合，没读时 UINet 以为倒数第二回合就是最后一回合）。
最后一个普通回合结束后圆盘直接变成 n、胶囊消失（用户 11-21），所以剩余 = 圆盘 + n，n 不定死。"""

from hiro.vision import hif

import numpy as np
import pytest


def _img(pill: bool):
    img = np.zeros((1280, 720, 3), np.uint8)
    if pill:
        x1, y1, x2, y2 = hif.EXTRA_PILL
        img[y1:y2, x1:x2] = 255
    return img


@pytest.fixture
def ocr(monkeypatch):
    calls = []

    def fake(text):
        def boxes(crop, scale=3):
            calls.append(crop.shape)
            return [(text, 0.9, (0, 0, 10, 10))] if text else []

        monkeypatch.setattr("hiro.vision.ocr.ocr_boxes", boxes)

    return fake, calls


def test_no_pill_is_zero_without_ocr(ocr):
    fake, calls = ocr
    fake("+1")
    assert hif.read_extra_turns(_img(False)) == 0 and calls == []  # 没胶囊不跑 OCR


@pytest.mark.parametrize("text,n", [("+1", 1), ("+3", 3), ("+ 4", 4)])
def test_pill_number_read(ocr, text, n):
    fake, _ = ocr
    fake(text)
    assert hif.read_extra_turns(_img(True)) == n


def test_pill_unreadable_is_unknown(ocr):
    fake, _ = ocr
    fake("")
    assert hif.read_extra_turns(_img(True)) is None


def test_total_is_counter_plus_extra():
    assert hif._with_extra(1, 3) == 4 and hif._with_extra(12, 0) == 12
    assert hif._with_extra(None, 1) is None and hif._with_extra(5, None) is None


def test_turn_count_over_extra_turns():
    """圆盘 11 → 出了加回合卡（11+1=12，同一回合）→ … → 1+1=2 → 圆盘变 1：每次变小都是新回合，变大不算。"""
    from hiro.brain.state import ExamMemory

    m = ExamMemory(exam="qualify-3")
    for r in (11, 12, 11, 10, 2, 1):
        m.observe_turn(r)
    assert m.turn == 5  # 11 / 11(→12 同回合) / 10 / 2 / 1 共 5 个回合
