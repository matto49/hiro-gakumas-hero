"""D41（第三十三局 18:18 #111 / 18:30 #390，识屏 2f7e2c1）：饮料溢出页最后一行被视口切掉，识别标 partial + candidates。
决策：没认出的行先用 candidates ∩ produce.drinks 认出；还认不出的不作为丢弃对象；全都认不出就不丢（WAIT，交执行滚动重读）。
page_fields 按第三十三局 trace 原样 + 识屏新字段（partial / candidates）。"""

from hiro.brain.deciders import ProduceDeciders, _resolve_held
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

import pytest

pytestmark = pytest.mark.master  # 决策器要查主数据（game.db），没有就跳过

TOKUSEI, ROOIBOS, BLEND, SENBURI, BOOST = (
    "pdrink_02-3-010",
    "pdrink_02-1-006",
    "pdrink_02-3-007",
    "pdrink_00-2-010",
    "pdrink_00-2-008",
)
KANPO, WHEY, OOLONG = "pdrink_00-2-009", "pdrink_00-3-001", "pdrink_00-1-004"


def _decide(held, incoming, drinks, incomings=None):
    pf = {"held": held, "incoming": incoming, "complete": False}
    if incomings:
        pf["incomings"] = incomings
    st = GameState(
        page={"name": "PDRINK_MAX"},
        page_fields=pf,
        produce={"mode": "produce-007", "drink_capacity": 3, "drinks": drinks},
    )
    return ProduceDeciders(CAMPUS_HIRO, None).decide("PDRINK_MAX", st)


def test_q33_111_partial_resolved_by_state_rest():
    """#111：持有 特製 / ブレンド / （切掉一行，候选 センブリ / ブースト），新来 特製。produce.drinks 还是旧行序（#107 刚把 ルイボス 换成 ブレンド）。
    原来按「未知 = 最差」丢第 3 行 → 执行找不到 → KAA「残す」丢了新来的 特製。现在第 3 行认成 センブリ，不再丢「未知」。"""
    held = [
        {"index": 0, "drink_id": TOKUSEI},
        {"index": 1, "drink_id": BLEND},
        {"index": 2, "drink_id": None, "partial": True, "candidates": [SENBURI, BOOST]},
    ]
    out, _ = _resolve_held(held, [TOKUSEI, ROOIBOS, BLEND, SENBURI])
    assert out[2]["drink_id"] == SENBURI and out[2]["method"] == "state_match_rest"
    d = _decide(held, {"index": 0, "drink_id": TOKUSEI}, [TOKUSEI, ROOIBOS, BLEND, SENBURI])
    assert d.action.kind is ActionKind.DISCARD_DRINK and d.action.args["receive"] is True, d.reason
    assert d.action.args["discard"] == [1] and "センブリ" in d.reason, (
        d.reason
    )  # 丢 ブレンド（2 档），留 特製×2 + センブリ


def test_q33_390_partial_resolved_by_same_row():
    held = [{"index": 0, "drink_id": KANPO}, {"index": 1, "drink_id": None, "partial": True, "candidates": [SENBURI]}]
    out, _ = _resolve_held(held, [KANPO, SENBURI, WHEY, BOOST])
    assert out[1]["drink_id"] == SENBURI and out[1]["method"] == "state_match"


def test_unresolved_row_not_discarded():
    held = [
        {"index": 0, "drink_id": ROOIBOS},
        {"index": 1, "drink_id": BLEND},
        {"index": 2, "drink_id": None, "partial": True, "candidates": [BOOST, WHEY]},
    ]
    d = _decide(held, {"index": 0, "drink_id": TOKUSEI}, None)
    assert d.action.kind is ActionKind.DISCARD_DRINK and 2 not in d.action.args["discard"], d.reason


def test_all_unknown_waits():
    held = [
        {"index": 0, "drink_id": None},
        {"index": 1, "drink_id": None, "partial": True, "candidates": [BOOST, WHEY]},
    ]
    d = _decide(held, {"index": 0, "drink_id": TOKUSEI}, None)
    assert d.action.kind is ActionKind.WAIT


def test_two_incoming_with_empty_slot_must_take_one():
    """Mac 账号 2 11-14 #1800：Da SP 课结束随机送 2 瓶（スムージー、初星水），持有 2 瓶（ブレンド、ホエイ）+ 空 1 格。
    页面「あと1個選択」：要留 3 瓶，「不收」不是可选项（「残す」灰着、没有「離れる」）。原来按满了算（容量 2）选了「不收」，3 次找不到「離れる」停机。"""
    SMOOTHIE, WATER = "pdrink_00-2-001", "pdrink_00-1-001"
    held = [
        {"index": 0, "drink_id": BLEND, "name": "厳選初星ブレンド"},
        {"index": 1, "drink_id": WHEY, "name": "初星ホエイプロテイン"},
    ]
    incomings = [
        {"index": 0, "drink_id": SMOOTHIE, "name": "ミックススムージー"},
        {"index": 1, "drink_id": WATER, "name": "初星水"},
    ]
    pf = {"held": held, "incoming": incomings[-1], "incomings": incomings, "select_left": 1, "complete": True}
    st = GameState(
        page={"name": "PDRINK_MAX"},
        page_fields=pf,
        produce={"mode": "produce-007", "drink_capacity": 3, "drinks": [BLEND, WHEY]},
    )
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("PDRINK_MAX", st)
    args = d.action.args
    assert d.action.kind is ActionKind.DISCARD_DRINK and args["receive"] is True, d.reason
    kept = 2 - len(args["discard"]) + len(args["receive_indexes"])
    assert kept == 3, (args, d.reason)  # 留满 3 瓶
