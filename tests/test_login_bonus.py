"""第二十六局：4 点日切换后首页弹「学マ水曜日」登录奖励（ガシャチケット 获得，点空白继续共 4 屏）。开局回首页的收尾循环
在按钮都没认出时点画面中央，正中央是奖励道具框，点了不翻页，180s 后停机。现在这类画面点底部空白。"""

import pytest

pytest.importorskip("kaa")  # 操作层：要装 KAA / kotonebot（pip install -e ".[play]"）

import importlib.util as _iu
from pathlib import Path
from pathlib import Path as _P
from types import SimpleNamespace as NS

from hiro.loop.resume_rules import REWARD_TAP, reward_tap
from hiro.produce.ops import ocr_boxes

import cv2


def _real(*rel):
    """用实机截图的用例：截图放在 tests/fixtures/…（开源仓库不带，用自己的截图），识别层（hiro.vision.ocr）也要装。"""
    have = (
        all((_P(__file__).parent / "fixtures" / r).exists() for r in rel)
        and _iu.find_spec("hiro.vision.ocr") is not None
    )
    return pytest.mark.skipif(not have, reason="需要实机截图 tests/fixtures/" + ", ".join(rel) + " 和识别层")


def _boxes(pairs):
    return [NS(text=t, rect=NS(center=NS(x=(b[0] + b[2]) / 2, y=(b[1] + b[3]) / 2))) for t, b in pairs]


@_real("home/g26-login-bonus.png")
def test_real_login_bonus_frame_taps_bottom_blank():
    img = cv2.imread(str(Path(__file__).parent / "fixtures/home/g26-login-bonus.png"))
    boxes = _boxes([(t, b) for t, _, b in ocr_boxes(img)])
    assert reward_tap(boxes) == REWARD_TAP


def test_purchase_or_jewel_never_tapped():
    assert (
        reward_tap(_boxes([("ガシャチケット", (216, 763, 504, 787)), ("ジュエル購入", (200, 900, 500, 930))])) is None
    )
    assert reward_tap(_boxes([("有償ジュエル 獲得", (200, 700, 500, 730))])) is None


def test_text_near_bottom_tap_is_left_alone():
    """底部那块有文字（可能是按钮）时不点，交回原来的流程。"""
    assert (
        reward_tap(_boxes([("ログインボーナス", (200, 100, 500, 140)), ("受け取る", (300, 1160, 420, 1200))])) is None
    )


def test_not_reward_screen():
    assert reward_tap(_boxes([("プロデュース", (100, 100, 300, 140))])) is None
