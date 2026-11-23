"""选行动页体力「当前/上限」：斜杠被 Apple Vision 读成 1 / 0 时的兜底（q7 续跑第 1 天连续 5 帧 stamina=None，决策因「状态不全」选了相談）。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.action import parse_stamina, read_stamina

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "action"


@pytest.mark.parametrize(
    ("text", "want"),
    [("33/33", (33, 33)), ("8/33", (8, 33)), ("33133", (33, 33)), ("33033", (33, 33)), ("8133", (8, 33)), ("33l33", (33, 33)),
     ("3333", (33, 33)), ("331", None), ("12/9", None), ("", None)],
)  # fmt: skip
def test_parse_stamina(text, want):
    assert parse_stamina(text) == want


def test_bright_frame_still_reads():
    """画面偏亮（续跑淡入）时整块 OCR 读成「33133」：旧读法给 None，现在仍是 33/33。"""
    img = cv2.imread(str(FIX / "q7r1-00002.jpg"))
    assert read_stamina(img) == (33, 33)
    assert read_stamina(cv2.convertScaleAbs(img, alpha=1.2, beta=10)) == (33, 33)
