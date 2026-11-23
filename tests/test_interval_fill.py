"""本战轮间补到 22 张（协调 11-18 mainz4 R2 复盘；用户确认「本战轮间还是要补充到 22 张卡」）。
货架取自 Windows mainz4-20261118-133027 轮间第一页（seq 794）：P 580、体力 20/33、R1 牌组 16 张。"""

from hiro.sim import interval as iv

import pytest

DECK = [("p_card-02-act-3_039", 1), ("p_card-02-act-3_045", 1), ("p_card-02-act-3_186", 1), ("p_card-02-ido-3_105", 1), ("p_card-02-act-2_062", 1),
        ("p_card-02-act-1_037", 1), ("p_card-02-men-3_002", 1), ("p_card-02-sup-3_094", 1), ("p_card-02-men-2_004", 1), ("p_card-02-men-2_052", 1),
        ("p_card-02-men-2_111", 0), ("p_card-02-men-1_031", 1), ("p_card-02-men-1_031", 0), ("p_card-02-men-1_031", 0), ("p_card-00-sup-3_093", 1),
        ("p_card-00-men-2_012", 1)]  # fmt: skip
IMETORE, YUMEMI, NOTE, YURUFUWA = (
    "p_card-02-men-2_008",
    "p_card-02-men-2_057",
    "p_card-02-men-3_041",
    "p_card-02-act-1_028",
)
SHELF = {"cards": [{"index": 0, "price": 50, "card_id": IMETORE, "name": "イメトレ+", "upgrade": 1},
                   {"index": 1, "price": 50, "card_id": YUMEMI, "name": "ゆめみごこち+", "upgrade": 1},
                   {"index": 4, "price": 80, "card_id": NOTE, "name": "ノートの端の決意+", "upgrade": 1},
                   {"index": 5, "price": 30, "card_id": YURUFUWA, "name": "ゆるふわおしゃべり+", "upgrade": 1}],
         "drinks": [{"index": 6, "price": 100, "drink_id": "pdrink_00-3-001", "name": "初星ホエイプロテイン"},
                    {"index": 7, "price": 75, "drink_id": "pdrink_00-2-009", "name": "パワフル漢方ドリンク"}],
         "upgrade": {"price": 100, "available": True, "left": 1}, "recover": {"available": True, "price_per_2hp": 10}}  # fmt: skip


def _plan(monkeypatch, fill, recover, p=580, hp=20):
    monkeypatch.setattr(iv, "INTERVAL_FILL_22", fill)
    monkeypatch.setattr(iv, "INTERVAL_RECOVER_FIRST", recover)
    setup = iv.R2Setup(
        deck=list(DECK), drinks=[], hp=hp, max_hp=33, permil={"Vo": 32280, "Da": 30480, "Vi": 20420}, star=779
    )
    opts = iv.options(SHELF, setup, p, upgrade_target=iv.upgrade_target(setup.deck))
    chosen, notes = iv.rule_plan(SHELF, setup, opts, p, target_hp=17, drink_capacity=4)
    return [o.label for o in chosen], notes


@pytest.mark.master
def test_fill_order_skips_conflict_cards():
    """补卡顺序：一次性 / 循环件在前；ノートの端の決意（纯好印象、不带再動）是冲突卡，不拿。"""
    order = iv.fill_order([IMETORE, YUMEMI, NOTE, YURUFUWA], DECK)
    assert {c for c, _ in order} == {IMETORE, YUMEMI, YURUFUWA}
    assert all(t == 0 for _, t in order)


@pytest.mark.master
def test_rule_plan_fills_to_22(monkeypatch):
    """开着补卡：买 ゆめみごこち+ / イメトレ+ / ゆるふわおしゃべり+（不买冲突的 ノートの端の決意+），饮料照买；关掉时照旧只买核心卡。"""
    labels, notes = _plan(monkeypatch, True, False)
    assert {"买卡 ゆめみごこち+", "买卡 イメトレ+", "买卡 ゆるふわおしゃべり+"} <= set(
        labels
    ) and "买卡 ノートの端の決意+" not in labels
    assert "买饮料 初星ホエイプロテイン" in labels and any("补到 22 张" in n for n in notes)
    labels, notes = _plan(monkeypatch, False, False)
    assert [x for x in labels if x.startswith("买卡")] == ["买卡 ゆめみごこち+"] and any(
        "不为凑 22 张" in n for n in notes
    )


@pytest.mark.master
def test_rule_plan_keeps_p_for_drinks(monkeypatch):
    """P 不多时先给 R2 饮料留钱，剩下的再补卡（P 180：两瓶饮料 175P，补卡买不起）。"""
    labels, _ = _plan(monkeypatch, True, False, p=180)
    assert "买饮料 初星ホエイプロテイン" in labels and not any(x.startswith("买卡 イメトレ") for x in labels)


@pytest.mark.master
def test_recover_first_targets_29(monkeypatch):
    """回体力优先（缺省关）：体力 20 < 29 时回到 29；关着时体力 20 已过角色目标 17，不回。"""
    labels, _ = _plan(monkeypatch, True, True)
    assert "回体力到 29/33" in labels
    labels, _ = _plan(monkeypatch, True, False)
    assert not any(x.startswith("回体力") for x in labels)


def test_switch_defaults():
    """补卡缺省开（用户 11-18 定的方向）；回体力优先、刷新让位缺省关（等对比）。"""
    import importlib
    import os

    if any(
        k in os.environ for k in ("HIRO_INTERVAL_FILL_22", "HIRO_INTERVAL_RECOVER_FIRST", "HIRO_INTERVAL_REFRESH_YIELD")
    ):
        pytest.skip("环境里设了开关")
    m = importlib.reload(iv)
    assert m.INTERVAL_FILL_22 and not m.INTERVAL_RECOVER_FIRST and not m.INTERVAL_REFRESH_YIELD
