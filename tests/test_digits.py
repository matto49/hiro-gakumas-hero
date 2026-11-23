"""游戏字体数字识别：要么读对，要么说不确定（交给 ddddocr），不能静默读错。"""

import json
from pathlib import Path

import hiro  # noqa: F401
import hiro.vision.hif as hif
from hiro.vision.digits import read_int, read_number
from hiro.vision.offline import load

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "hif_hud"


def _cases():
    """(帧, 字段, 框, 真值)：HUD 上位置固定的数字。"""
    for name, truth in json.loads((FIX / "truth.json").read_text(encoding="utf-8")).items():
        img = load(cv2.imread(str(FIX / f"{name}.jpg")))
        for field, box in (("remaining_turns", hif.BOX_TURNS), ("genki", hif.BOX_GENKI), ("hp", hif.BOX_HP)):
            yield img, f"{name}.{field}", box, truth[field]


@pytest.mark.user_assets("font")
def test_font_reader_never_misreads():
    unsure = 0
    cases = list(_cases())
    for img, label, box, want in cases:
        got = read_int(img, box)
        if got is None:
            unsure += 1
        else:
            assert got == want, label
    assert unsure <= len(cases) // 4  # 现状 30 个里 5 个被特效遮挡而不确定


def test_read_number_with_fallback():
    for img, label, box, want in _cases():
        assert read_number(img, box) == want, label
