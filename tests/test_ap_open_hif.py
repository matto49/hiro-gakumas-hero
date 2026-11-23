"""第二十七局：AP 补充要先在 H.I.F 页点「本戦」弹「AP不足」，以前只截一次图、KAA 模板没中就失败（EXIT 3），按钮其实在。
现在多试几帧，模板没中按 OCR 文字定位。"""

import pytest

pytest.importorskip("kotonebot")  # 操作层：要装 KAA / kotonebot（pip install -e ".[play]"）

import importlib.util as _iu
from pathlib import Path
from pathlib import Path as _P

from hiro.loop import ap_refill
from hiro.produce.ops import ocr_boxes

import cv2


def _real(*rel):
    """用实机截图的用例：截图放在 tests/fixtures/…（开源仓库不带，用自己的截图），识别层（hiro.vision.ocr）也要装。"""
    have = (
        all((_P(__file__).parent / "fixtures" / r).exists() for r in rel)
        and _iu.find_spec("hiro.vision.ocr") is not None
    )
    return pytest.mark.skipif(not have, reason="需要实机截图 tests/fixtures/" + ", ".join(rel) + " 和识别层")


PAGE = Path(__file__).parent / "fixtures/ap_refill/g27-hif-page.png"


def _clock():
    t = [0.0]
    return t, (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))


@_real("ap_refill/g27-hif-page.png")
def test_real_page_ocr_finds_main_button():
    img = cv2.imread(str(PAGE))
    _t, clock, sleep = _clock()
    clicks = []
    ok = ap_refill.open_hif(lambda: img, lambda x, y: clicks.append((x, y)), ocr_boxes, mode="main", try_template=lambda: False,
                            sleep=sleep, clock=clock)  # fmt: skip
    assert ok and len(clicks) == 1 and abs(clicks[0][0] - 570) < 40 and abs(clicks[0][1] - 890) < 30


@_real("ap_refill/g27-hif-page.png")
def test_real_page_qualify_button():
    img = cv2.imread(str(PAGE))
    _t, clock, sleep = _clock()
    clicks = []
    assert ap_refill.open_hif(lambda: img, lambda x, y: clicks.append((x, y)), ocr_boxes, mode="qualify", try_template=lambda: False,
                              sleep=sleep, clock=clock)  # fmt: skip
    assert abs(clicks[0][0] - 205) < 60 and abs(clicks[0][1] - 890) < 30


def test_waits_for_page_then_finds_button():
    """前几帧还在过场（没有按钮）：继续看，按钮出现就点。"""
    _t, clock, sleep = _clock()
    frames = iter([[], [], [("本戦", 1.0, (540, 874, 599, 906))]])
    clicks = []
    ok = ap_refill.open_hif(lambda: None, lambda x, y: clicks.append((x, y)), lambda img: next(frames, []), mode="main",
                            sleep=sleep, clock=clock)  # fmt: skip
    assert ok and clicks == [(569, 890)]


def test_template_first():
    _t, clock, sleep = _clock()
    ok = ap_refill.open_hif(
        lambda: None,
        lambda x, y: None,
        lambda img: [],
        mode="main",
        try_template=lambda: True,
        sleep=sleep,
        clock=clock,
    )
    assert ok


def test_gives_up_after_timeout():
    t, clock, sleep = _clock()
    assert not ap_refill.open_hif(lambda: None, lambda x, y: None, lambda img: [("本戦", 1.0, (540, 100, 599, 130))], mode="main",
                                  sleep=sleep, clock=clock)  # 「本戦」不在按钮那一行（标题等）：不点  # fmt: skip
    assert t[0] >= ap_refill.OPEN_TIMEOUT
