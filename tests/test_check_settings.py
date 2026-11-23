"""开局前设置检查：读 5 个开关（改之前全 OFF / 改之后全 ON，实机截图）。"""

import sys
from pathlib import Path

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
FIX = Path(__file__).parent / "fixtures/settings"


def _read(name):
    pytest.importorskip("kotonebot", reason="读设置页要 OCR（KAA / kotonebot，pip install -e '.[play]'）")
    from hiro.produce.ops import ocr_boxes

    from check_settings import read_toggles

    img = cv2.resize(cv2.imread(str(FIX / name)), (720, 1280))
    return read_toggles(ocr_boxes(img))


def test_after_all_on():
    a, b = _read("after-01-commu.png"), _read("after-02-skip.png")
    merged = {k: a[k] or b[k] for k in a}
    assert set(merged.values()) == {"ON"}, merged


def test_before_skip_section_off():
    r = _read("03-produce-01.png")
    assert r["支援事件剧情跳过"] == "OFF" and r["开始说明剧情跳过"] == "OFF" and r["親愛度映像跳过"] == "OFF", r
