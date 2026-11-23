"""选行动页属性读数（hiro.vision.action.stat_boxes）：数字框按左侧属性图标定位。

第十九局本战 seq 12（总控 2026-11-03）：Da 2296 压在白衣服上、字体比对不确定，退回 ddddocr；旧固定框左边界 345 切掉了首位「2」的一半，
读成 3296，被不变量（cap 3200）拦下。全部 trace 的 1827 个读数里，旧框有 220 个读错（多数是切掉首位：1596→159、1768→76；
还有 2→3、1210→2110），按图标定位后没有一处变差。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.action import read_action_select, stat_boxes

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "params"


@pytest.mark.parametrize(
    ("name", "params"),
    [("main19-12-da2296", {"vo": 1434, "da": 2296, "vi": 1179}),  # 旧框：Da 3296（首位 2 被切半）
     ("main11-10-vi998", {"vo": 1842, "da": 1590, "vi": 998}),  # 三位数时 Vi 图标右移 12px：固定新框会把图标右缘读成 9（9998）
     ("main10-9-vo1596", {"vo": 1596, "da": 1741, "vi": 1025}),  # 旧框：Vo 159、Da 741（切掉首位 1）
     ("qualify7-324-da1210", {"vo": 1152, "da": 1210, "vi": 601})],  # 旧框：Da 2110
)  # fmt: skip
def test_params_icon_anchored(name, params):
    assert read_action_select(cv2.imread(str(FIX / f"{name}.jpg"))).params == params


def test_boxes_follow_icon():
    """数字框从图标右缘开始：Vi 图标在 432–467（四位数）或 444–480（三位数）。"""
    four = stat_boxes(cv2.imread(str(FIX / "main19-12-da2296.jpg")))
    three = stat_boxes(cv2.imread(str(FIX / "main11-10-vi998.jpg")))
    assert three["vi"][0] - four["vi"][0] >= 10
    assert all(b[0] > 180 for b in four.values()) and four["vi"][2] <= 590
