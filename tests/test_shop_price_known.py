"""相談货架价格按已知价格核对（实机帧在私有 fixtures）：DMM 实机 パワフル漢方ドリンク（R2 饮料，非 SALE）画面上是 75，
RapidOCR 四种读法 2:2 平票（[15] / 15 / 75 / ddddocr 75），以前取了 15、预算按 15 算，买下实扣 75。
同一帧第 0 格是 SALE 卡 24P（原价 35），卡价不规整，只设下限、不给预期价。"""

from hiro.vision import shop

from test_shop_exec import frame


def test_known_price_helpers():
    assert shop.expected_price({"kind": "drink", "drink_id": "pdrink_00-2-009"}, False) == 75
    assert shop.expected_price({"kind": "drink", "drink_id": "pdrink_02-3-007"}, True) == 70
    assert shop.expected_price({"kind": "drink", "drink_id": "pdrink_02-1-006"}, True) == 35
    assert shop.expected_price({"kind": "card", "card_id": "p_card-02-men-2_056", "upgrade": 1}, False) is None
    assert (
        shop.price_floor("drink", False) == 50
        and shop.price_floor("card", False) == 35
        and shop.price_floor("card", True) == 20
    )


def test_shelf_prices():
    shelf = shop.read_consult(frame("price75_read15"))["shelf"]
    got = {c["index"]: (c.get("card_id") or c.get("drink_id"), c["price"], c["sale"]) for c in shelf}
    assert got[7] == ("pdrink_00-2-009", 75, False)
    assert got[0][1:] == (24, True)
    assert [got[i][1] for i in range(1, 7)] == [70, 70, 77, 52, 50, 100]


def test_interval_drink_75_not_read_as_15():
    """DMM 单局测试本战轮间：ミックススムージー（R2，75P）RapidOCR 读成 15，决策按 15P 判「比继续刷新划算」，实扣 75（P 211→136）。"""
    r = shop.read_interval_shelf(frame("interval_smoothie75"))["interval"]
    assert [(d["drink_id"], d["price"]) for d in r["drinks"]] == [("pdrink_00-2-001", 75), ("pdrink_00-2-004", 75)]
    assert [c["price"] for c in r["cards"] + r["changes"]] == [30, 30, 50, 50, 30, 50]  # 轮间卡更便宜，不受饮料下限影响
