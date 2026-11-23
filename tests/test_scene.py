"""场景分类回归：文件名 <帧号>-<期望场景>.jpg，期望场景已人工核对。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.offline import load
from hiro.vision.scene import classify_current

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "scenes"
NEEDS_SIGNATURE = {
    "00041-ACTION_SELECT"
}  # 选行动页淡入（按钮、P手帳 标签半透明）：KAA 与像素兜底都判不出，靠页面签名（界面 Sprite）


def _cases():
    return [
        pytest.param(p, marks=pytest.mark.user_assets("sprites")) if p.stem in NEEDS_SIGNATURE else p
        for p in sorted(FIX.glob("*.jpg"))
    ]


@pytest.mark.parametrize("path", _cases(), ids=lambda p: p.stem)
def test_scene(path: Path):
    load(cv2.imread(str(path)))
    assert classify_current().scene == path.stem.split("-", 1)[1]
