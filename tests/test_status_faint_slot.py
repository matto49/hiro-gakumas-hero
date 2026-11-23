"""考试左侧状态栏：中间夹着淡色 / 半透明的槽时不当作到底（实机帧在私有 fixtures）。
DMM qualifydmmw1-r1 #133–135：第 2 格是饮料效果的茶杯（底色很淡，菱形分 0.23–0.47），以前在这里就停，
下面的好印象（11 / 13）和消費体力減少（4 / 2ターン）都读漏，中转层的 UIObs 好印象成 0（任务 ① 报告）。"""

from pathlib import Path

from hiro.vision import status
from hiro.vision.offline import load

import cv2
import pytest

FIX = Path(__file__).parent / "fixtures" / "exam" / "status"


def _read(name):
    p = FIX / name
    if not p.exists():
        pytest.skip(f"需要样例 {p}")
    return status.read_status_column(load(cv2.imread(str(p))))


@pytest.mark.parametrize(
    "name, review, down", [("dmmw1q-133-faint-slot.jpg", 11, 4), ("dmmw1q-135-faint-slot.jpg", 13, 2)]
)
def test_good_impression_below_faint_slot(name, review, down):
    es = _read(name)
    summary = status.status_summary(es)
    assert summary.get("examreview") == review
    assert summary.get("examstaminaconsumptiondown") == down
    faint = [e for e in es if e.get("faint")]
    assert len(faint) == 1 and faint[0]["slot"] == 2
    assert faint[0].get("card_id") is None  # 茶杯不冒充卡牌效果（以前 0.61 配成「第一印象」）


def test_stops_at_real_end():
    es = _read("dmmw1q-133-faint-slot.jpg")
    assert [e["slot"] for e in es] == [0, 1, 2, 3, 4]  # 第 5 格往后是背景
