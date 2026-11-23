"""考试手牌可打判定遇到过场白光（「スキルカードサポート」「もう一枚使用できます」）：hand_layout="overlay"，观测里手牌给 None。

第二十七局 #168（协调 D26）：压暗的两张牌字母亮度被白光抬到 182 / 177，越过阈值 170 被判可打；第二十八局 #178 可打的 イメトレ 也是 177，
所以改阈值修不好。全部 4398 帧考试画面：压暗 90–120、可打 220–240，140–210 只出现在这类过场帧。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.hif import read_hif_exam
from hiro.vision.observe import observation
from hiro.vision.offline import load

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "exam_overlay"


@pytest.mark.parametrize(
    ("name", "layout"),
    [("m27-168-dimmed", "overlay"),  # 三张都不可打（存档：结束回合是唯一合法动作），原来全报可打
     ("m28-178-playable", "overlay"),  # イメトレ 可打（下一帧打出了），亮度也是 177
     ("m25r2-133-mixed", "overlay"),  # 「もう一枚使用できます」：中间那张压暗
     ("m27-167-normal", "ok")],  # 前一帧，没有过场
)  # fmt: skip
def test_overlay_hand_unknown(name, layout):
    raw = read_hif_exam(load(cv2.imread(str(FIX / f"{name}.jpg"))), cache=None)
    assert raw["hand_layout"] == layout
    obs = observation(1, 0.0, "HIF_EXAM", raw)
    assert (obs.fields.get("hand") is None) == (layout == "overlay")
