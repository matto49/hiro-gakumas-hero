"""考试状态栏：同一格两行数字（上「5ターン」、下「20%」，パラメータ上昇量増加 / やる気増加量増加 等）。
以前只读下面那行的百分比，回合丢了（识别测试集进阶集 33 格）。百分比那一格另读上面一行的回合，记在 turns。
帧在识别测试集仓库（HIRO_OCR_DATASET，缺省 ~/github/hiro-ocr-dataset），没有就跳过。"""

import os
from pathlib import Path

from hiro.vision import status
from hiro.vision.offline import load

import cv2
import pytest

DS = Path(os.environ.get("HIRO_OCR_DATASET") or Path.home() / "github" / "hiro-ocr-dataset") / "hard"


def _slot(frame, k):
    p = next(DS.glob(f"*{frame}.jpg"), None) if DS.exists() else None
    if p is None:
        pytest.skip(f"需要识别测试集的帧 {frame}")
    return next(e for e in status.read_status_column(load(cv2.imread(str(p)))) if e["slot"] == k)


@pytest.mark.parametrize("frame, k, turns, pct", [("00154", 5, 3, 50), ("00154", 7, 3, 30), ("00416", 4, 5, 20),
                                                   ("00368", 3, 2, 50), ("01180", 3, 1, 10), ("00490", 6, 3, 30)])  # fmt: skip
def test_turns_above_percent(frame, k, turns, pct):
    e = _slot(frame, k)
    assert (e["value"], e["unit"], e.get("turns")) == (pct, "%", turns)


def test_single_number_slot_has_no_turns():
    e = _slot("00624", 0)  # やる気 98：只有一个数
    assert e["value"] == 98 and e.get("turns") is None
