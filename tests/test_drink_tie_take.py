"""用户 11-17：饮料档位打平时默认收新饮料（有的卡 / P 道具拿到饮料就得分）。只管白拿的：饮料满了丢哪瓶（PDRINK_MAX）、
饮料三选一（SELECT_DRINK）；相談买饮料要花 P，不变。
DMM qualifyz1 17:16 那次（新来 おしゃれハーブティー：好印象兑现，広的打法里是 4 档）不是打平，照旧不收。"""

from hiro.brain import drinks as dk
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import GameState

import pytest

BOOST, RECOVERY, OOLONG, SENBURI, HERB, PARAM = (
    "pdrink_00-2-008",
    "pdrink_00-2-003",
    "pdrink_00-1-004",
    "pdrink_00-2-010",
    "pdrink_02-2-006",
    "pdrink_00-1-001",
)


def _overflow(held_ids, incoming):
    held = [{"index": i, "drink_id": d, "name": None} for i, d in enumerate(held_ids)]
    st = GameState(page={"name": "PDRINK_MAX"}, page_fields={"held": held, "incoming": {"drink_id": incoming}},
                   produce={"mode": "produce-007", "drink_capacity": 4, "drinks": list(held_ids)})  # fmt: skip
    return ProduceDeciders(CAMPUS_HIRO, None).decide("PDRINK_MAX", st)


@pytest.mark.master
def test_overflow_same_tier_takes_new_drink():
    """新来的 3 档（数值类），持有里最差的也是 3 档（リカバリ / 烏龍茶）：打平，收新的、丢同档里社区顺序靠后的那瓶。"""
    d = _overflow([BOOST, RECOVERY, OOLONG, SENBURI], PARAM)
    assert d.action.args["receive"] is True and d.action.args["discard"] in ([1], [2]), d.reason
    assert "打平" in d.reason


@pytest.mark.master
def test_overflow_worse_tier_still_keeps():
    """新来的比持有里最差的还低一档（qualifyz1 17:16 的真实组合）：照旧不收。"""
    d = _overflow([BOOST, RECOVERY, OOLONG, SENBURI], HERB)
    assert d.action.args["receive"] is False, d.reason


@pytest.mark.master
def test_choose_tie_default_unchanged_without_flag():
    """不开 take_on_tie（相談买饮料）：同档打平照旧不动。"""
    keep = dk.Option("keep", "不收", [BOOST, RECOVERY, OOLONG, SENBURI])
    swap = dk.Option("swap", "收", [BOOST, PARAM, OOLONG, SENBURI])
    assert dk.choose([keep, swap], "selection", 4)["pick"].key == "keep"
    assert dk.choose([keep, swap], "selection", 4, take_on_tie=True)["pick"].key == "swap"
