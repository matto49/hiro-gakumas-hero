"""「研修案内」框（练习课 FAILED 后あさり先生建议「プロデュースを一時中断し、研修を開始しますか？」）：中断层点「キャンセル」继续培育。
11-25 账号 3 初 Vi 课没到目标值：框没有处理器，LLM 兜底不敢点，空等到 180 秒停机。"""

import os
from pathlib import Path

from hiro.runtime import interrupts
from hiro.runtime.frame import Frame
from hiro.vision.ocr import Box, Text

import cv2
import numpy as np
import pytest
from test_runtime import frame as fake_frame
from test_runtime import runtime


def _dialog_frame(title: str, texts: list[tuple[str, int, int]]):
    f = Frame(np.zeros((1280, 720, 3), np.uint8), 0.0)
    f.__dict__["has_dialog"] = True
    f.__dict__["dialog"] = {"kind": None, "title": title, "body": "", "danger": False, "y": 757}
    f.__dict__["texts"] = [Text(t, Box(x - 60, y - 15, x + 60, y + 15)) for t, x, y in texts]
    return f


@pytest.mark.synthetic
def test_kenshu_guide_taps_cancel():
    rt, _ = runtime([fake_frame()])
    f = _dialog_frame(
        "研修案内",
        [("プロデュースを一時中断し、研修を開始しますか？", 340, 830), ("キャンセル", 240, 1161), ("決定", 524, 1161)],
    )
    assert "研修案内" in interrupts.check(f, rt)
    assert rt.raw_device.taps == [(240, 1161)]


@pytest.mark.synthetic
def test_other_dialog_untouched():
    rt, _ = runtime([fake_frame()])
    f = _dialog_frame("相談", [("キャンセル", 240, 1161), ("決定", 524, 1161)])
    assert interrupts.kenshu_guide(f, rt) is None and rt.raw_device.taps == []


def test_kenshu_guide_real_frame():
    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "kenshu-guide-dialog.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/kenshu-guide-dialog.png")
    rt, _ = runtime([fake_frame()])
    assert "研修案内" in interrupts.check(Frame(cv2.imread(str(path)), 0.0), rt)
    ((x, y),) = rt.raw_device.taps
    assert x < 360 and abs(y - 1160) < 25  # 左边的「キャンセル」，不是右边的「決定」
