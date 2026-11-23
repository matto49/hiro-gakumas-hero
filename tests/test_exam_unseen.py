"""HIF_EXAM：看不到 ≠ 没有（数据确认-01 第 6 条与 2026-10-31 全量重读）。

- 出牌放大 / 回合切换 / 发牌动画时画面上没有手牌：hand 给 None，不给 []（与「0 张手牌」分开）。
- 状态栏有完全没认出的槽（name、key 都没有）：buffs 给 None，不给 {}（不能当成「没有其他状态」）。
夹具取自 datasets/20261029-224103-hif-qualify。
"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.hif import read_hif_exam
from hiro.vision.observe import observation
from hiro.vision.offline import load

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures")


def _obs(rel: str):
    raw = read_hif_exam(load(cv2.imread(str(FIX / rel))), cache=None)
    return raw, observation(1, 0.0, "HIF_EXAM", raw)


@pytest.mark.parametrize("name", ["hand-play-zoom-00698", "hand-anim-01724"])
def test_hand_not_visible_is_none(name):
    raw, obs = _obs(f"exam_none/{name}.jpg")
    assert raw["hand_layout"] == "empty"
    assert obs.fields["hand"] is None
    assert obs.problems(strict=True) == []


@pytest.mark.parametrize("name", ["status-unknown-slot-00385"])
def test_unrecognized_status_slot_buffs_none(name):
    raw, obs = _obs(f"exam_none/{name}.jpg")
    assert any(not (e.get("name") or e.get("key")) for e in raw["statuses"])
    assert obs.fields["buffs"] is None


@pytest.mark.user_assets("status")
def test_normal_frame_keeps_hand_and_buffs():
    _raw, obs = _obs("hif_hud/01694.jpg")
    assert obs.fields["hand"] and isinstance(obs.fields["buffs"], dict)
