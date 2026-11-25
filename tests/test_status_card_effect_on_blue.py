"""考试状态栏：卡面偏蓝、偏淡的卡牌持续效果（ワクワクが止まらない、エウレカ！、せのびの魔法……）蓝色像素也够多，
以前一律当状态图标去配，配不上就成了「认不出的状态」（识别测试集进阶集 22 格，名字和类型都错）。
图标配不上时再按卡面配，卡面分够高（真卡牌效果 0.80–0.93）才认成卡牌效果；饮料效果、半透明的状态只能凑到 0.46–0.68，不冒充卡牌。
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
    es = status.read_status_column(load(cv2.imread(str(p))))
    return next(e for e in es if e["slot"] == k)


@pytest.mark.parametrize("frame, k, card", [("00154", 4, "ワクワクが止まらない"), ("00775", 3, "せのびの魔法"),
                                            ("00490", 5, "デイドリーミング"), ("00564", 2, "ワクワクが止まらない")])  # fmt: skip
def test_bluish_card_effect_is_read_as_card(frame, k, card):
    e = _slot(frame, k)
    assert e["kind"] == "enchant" and e["name"] == card


@pytest.mark.parametrize(
    "frame, k", [("00416", 5), ("00591", 3), ("00591", 4)]
)  # 饮料效果、半透明的「パラメータ上昇量増加」
def test_drink_and_faded_status_do_not_pose_as_card(frame, k):
    e = _slot(frame, k)
    assert e.get("card_id") is None
