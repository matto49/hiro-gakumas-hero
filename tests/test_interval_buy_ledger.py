"""轮间（HIF_ROUND_INTERVAL）买卡要记进牌组账本（p13 复盘：アイドル宣言+、アイドル魂 没记，R2 开局重建报
「画面上的 p_card-00-men-3_003 在模拟器里找不到」，退回 uinet）。"""

from hiro.contracts import GameState
from hiro.loop.events import apply_event, events_from_step

SHELF = {"interval": {"cards": [{"index": 0, "price": 50, "card_id": "p_card-02-men-2_008", "upgrade": 1},
                                {"index": 5, "price": 50, "card_id": "p_card-00-men-3_003", "upgrade": 0}],
                      "drinks": [{"index": 6, "price": 100, "drink_id": "pdrink_00-3-005"}]}}  # fmt: skip


def test_interval_card_buy_emits_shop_buy():
    ev = events_from_step(
        "HIF_ROUND_INTERVAL", {"kind": "IntervalBuy", "args": {"index": 5}}, True, SHELF, {"deck": []}, seq=1
    )
    assert [(e.kind, e.args.get("card_id"), e.args.get("upgrade"), e.args.get("price")) for e in ev] == [
        ("SHOP_BUY", "p_card-00-men-3_003", 0, 50)
    ]


def test_interval_card_buy_updates_deck():
    s = GameState(produce={"deck": [{"card_id": "p_card-02-act-3_039", "upgrade": 1}]})
    for e in events_from_step(
        "HIF_ROUND_INTERVAL", {"kind": "IntervalBuy", "args": {"index": 5}}, True, SHELF, s.produce, seq=1
    ):
        s, _ = apply_event(s, e)
    assert [c["card_id"] for c in s.produce["deck"]] == ["p_card-02-act-3_039", "p_card-00-men-3_003"]


def test_interval_drink_and_recover_no_deck_change():
    ev = events_from_step(
        "HIF_ROUND_INTERVAL", {"kind": "IntervalBuy", "args": {"index": 6}}, True, SHELF, {"deck": []}, seq=1
    )
    assert [(e.kind, e.args.get("kind"), e.args.get("drink_id")) for e in ev] == [
        ("SHOP_BUY", "drink", "pdrink_00-3-005")
    ]
    assert (
        events_from_step(
            "HIF_ROUND_INTERVAL",
            {"kind": "IntervalBuy", "args": {"service": "recover"}},
            True,
            SHELF,
            {"deck": []},
            seq=1,
        )
        == []
    )


def test_failed_interval_buy_records_nothing():
    assert (
        events_from_step(
            "HIF_ROUND_INTERVAL", {"kind": "IntervalBuy", "args": {"index": 5}}, False, SHELF, {"deck": []}, seq=1
        )
        == []
    )
