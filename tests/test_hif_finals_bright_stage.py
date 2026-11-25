"""A16（11-26）：H.I.F 本战舞台亮光压在左上「残りターン」后面，模板只有 0.40–0.43，差一点没到放宽线 0.45，
整帧判成 unknown / 通用 EXAM：剩余回合、体力、元气都没读（进阶集 dmmw1-00149、mainz3-00822），或按课程页的框把 12 读成 2
（核心集 p15-main-01048）。顶部本战名次条在时放宽到 0.38（非考试画面最高 0.21）。
帧在识别测试集仓库（HIRO_OCR_DATASET，缺省 ~/github/hiro-ocr-dataset），没有就跳过。"""

import os
from pathlib import Path

from hiro.vision import hif
from hiro.vision.offline import load

import cv2
import pytest

DS = Path(os.environ.get("HIRO_OCR_DATASET") or Path.home() / "github" / "hiro-ocr-dataset")


@pytest.mark.parametrize(
    "rel",
    [
        "hard/dmm-maindmmw1-20261111-215002-00149.jpg",
        "hard/dmm-mainz3-20261118-033130-00822.jpg",
        "core/mac-hif-mac-p15-main-01048.jpg",
    ],
)
def test_finals_exam_with_bright_stage_is_hif_exam(rel):
    p = DS / rel
    if not p.exists():
        pytest.skip(f"需要识别测试集的帧 {rel}")
    img = load(cv2.imread(str(p)))
    assert hif.is_finals_strip(img)
    assert hif.is_hif_exam(img)
