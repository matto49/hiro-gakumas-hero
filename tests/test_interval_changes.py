"""轮间「チェンジ」格（index 2 / 3）：换入候选的 card_id / upgrade（决策 da6c8e5 要接进方案，总控 2026-11-03）。

card_id 一直读得到；upgrade 在橙色插画的卡上常是 None：「+」的宽松红色阈值（色相 ≤14）把橙色线条算进去了。
现在宽松阈值只认纯红（色相 ≤9），且形状要像「+」。全部轮间帧 474 格对比：只有 32 格 None → 0（都人工看过没有「+」），没有 1 / 0 互换。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.shop import read_interval_shelf

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "interval"


@pytest.mark.parametrize(
    ("name", "changes", "cards"),
    [("main18-120-changes", [(2, "p_card-02-act-1_028", "ゆるふわおしゃべり", 0, 30), (3, "p_card-02-men-1_032", "思い出し笑い", 0, 30)],
      [("ありがとうの言葉+", 1), ("イメトレ+", 1), ("ノートの端の決意", 0), ("リズミカル+", 1)]),
     ("main19-165-changes", [(2, "p_card-02-act-1_003", "手拍子", 0, 30), (3, "p_card-02-men-1_030", "リスタート", 0, 30)],
      [("ファンシーチャーム+", 1), ("ノートの端の決意+", 1), ("ファーストステップ+", 1), ("気まぐれハート+", 1)])],
)  # fmt: skip
def test_interval_change_cells(name, changes, cards):
    iv = read_interval_shelf(cv2.imread(str(FIX / f"{name}.jpg")))["interval"]
    assert [(c["index"], c["card_id"], c["name"], c["upgrade"], c["price"]) for c in iv["changes"]] == changes
    assert [(c["name"], c["upgrade"]) for c in iv["cards"]] == cards
