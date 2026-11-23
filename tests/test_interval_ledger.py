"""第十八局本战轮间（traces/lead35-main18-20261102-215809-r1，22:31–22:36）回放：
1. 持有饮料按本次轮间账本（买掉的格读成 None 也不丢）——原来估值里写「持有 空」；
2. 买饮料要「增益 > 价格 × 余 P 机会成本」——原来 ルイボスティー +120 ± 1 花 50P 也买了；
3. 刷新后的新货架没认出就等识别，不拿空货架去估——原来两次刷新估值一模一样；
4. 执行侧误判已离开、轮间重进时不推翻方案——原来重算成「买卡 えいえいおー+ + おしゃれ」又叠加一套；
夹具 tests/fixtures/main18_interval.json 是这几步的 page_fields / produce 原样。"""

import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

FIX = json.loads((Path(__file__).parent / "fixtures" / "main18_interval.json").read_text(encoding="utf-8"))
BOOST, AOJIRU, HERB, TOKUSEI, ROOIBOS, BLEND, TEA = ("pdrink_00-2-008", "pdrink_00-3-005", "pdrink_02-2-006", "pdrink_02-3-010", "pdrink_02-1-006",
                                                    "pdrink_02-3-007", "pdrink_02-3-006")  # fmt: skip
GAINS = {TOKUSEI: 60000, BOOST: 23000, AOJIRU: 15000, HERB: 8000, BLEND: 400, ROOIBOS: 120, TEA: 0}


def _state(seq):
    f = FIX[str(seq)]
    return GameState(page_fields=f["page_fields"], produce=dict(f["produce"], drink_capacity=4))


def _setup(monkeypatch, plans):
    # 这里测账本记账（买了什么、重进不推翻方案），饮料按方案编号买：drink_step 关（开着时方案里的饮料交给 drink_step，见 test_interval_main25–28）
    monkeypatch.setattr(ProduceDeciders, "INTERVAL_DRINK_STEP", False)

    def plan(*a, **k):
        plans.append(k)
        return {"target_hp": 24, "buy": [6, 7], "upgrade": "p_card-02-act-3_186", "labels": ["回体力到 24/33", "买饮料 ブーストエキス", "买饮料 初星スペシャル青汁", "强化 せのび"],
                "gain": 72211, "gain_se": 5636, "cost": 335, "significant": True, "final": True, "rule_labels": [], "rule_notes": [], "deviation": None,
                "rows": [], "seconds": 1.0, "policy": "community", "seeds": [0]}  # fmt: skip

    monkeypatch.setattr(iv, "plan_tiered", plan)
    monkeypatch.setattr(
        iv,
        "drink_gains",
        lambda *a, **k: {d: (GAINS.get(d, 0), 1 if GAINS.get(d, 0) < 1000 else 3000) for d in iv.SHOP_DRINK_POOL},
    )
    monkeypatch.setattr(
        iv, "CONTEXT", {"deck": FIX["finals_context"]["deck"], "permil": FIX["finals_context"]["permil"], "drinks": []}
    )


def test_main18_interval_replay(monkeypatch):
    plans = []
    _setup(monkeypatch, plans)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    assert dec.decide("HIF_ROUND_INTERVAL", _state(120)).action.args == {"service": "recover"}
    assert dec.decide("HIF_ROUND_INTERVAL", _state(121)).action.args == {"index": 6}  # ブースト
    assert dec.decide("HIF_ROUND_INTERVAL", _state(122)).action.args == {"index": 7}  # 青汁
    d = dec.decide("HIF_ROUND_INTERVAL", _state(123))  # 两格都读成 None / 已购
    assert dec._interval_ledger["drinks"] == [BOOST, AOJIRU]
    assert "持有 空" not in d.reason and ("ブーストエキス" in d.reason or d.action.kind is not ActionKind.REROLL), (
        d.reason
    )
    if d.action.kind is ActionKind.REROLL:
        d2 = dec.decide("HIF_ROUND_INTERVAL", _state(124))  # 刷新后货架两格都没认出
        assert d2.action.kind is ActionKind.WAIT, d2.reason
    # 22:33 强化失败 → 结束 → 轮间重进（seq 166：新货架 おしゃれ / 特製）：不推翻方案，账本里的饮料算持有
    dec.decide("HIF_ROUND_INTERVAL", _state(128))
    n_plans = len(plans)
    d = dec.decide("HIF_ROUND_INTERVAL", _state(166))
    assert len(plans) == n_plans or plans[-1].get("extra_drinks") == [BOOST, AOJIRU]
    assert not (d.action.kind is ActionKind.INTERVAL_BUY and d.action.args.get("index") == 4), (
        d.reason
    )  # 不再加买 えいえいおー+


def test_rooibos_not_worth_50p():
    gains = {d: (GAINS.get(d, 0), 1) for d in iv.SHOP_DRINK_POOL}
    v, picks = iv.shelf_value([(6, TEA, 100), (7, ROOIBOS, 50)], gains, 2, 200)
    assert picks == [] and v == 0  # +120 < 50P × 每 P 20 分
    v, picks = iv.shelf_value([(6, TOKUSEI, 100), (7, BLEND, 100)], {**gains, TOKUSEI: (60000, 3000)}, 2, 200)
    assert picks == [6]


def test_ledger_survives_end_and_reentry(monkeypatch):
    plans = []
    _setup(monkeypatch, plans)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    for seq in (120, 121, 122):
        dec.decide("HIF_ROUND_INTERVAL", _state(seq))
    dec.decide("HIF_ROUND_INTERVAL", _state(123))
    led = dec._interval_ledger
    assert led["drinks"] == [BOOST, AOJIRU] and led["upgraded"] == []  # 强化还没成交
    # 重算时 plan_tiered 收到账本
    dec._interval_plan["shelf"] = set()  # 逼一次重算
    dec._interval_plan["rerolling"] = False
    dec.decide("HIF_ROUND_INTERVAL", _state(166))
    assert plans[-1]["extra_drinks"] == [BOOST, AOJIRU]


def test_reference_deck_blocks_cross_tier(monkeypatch):
    """牌组 / 倍率是参考值时不允许跨档偏离（22:20 拿 おしゃれハーブティー 的那次）。"""
    from hiro.brain import drinks as dk
    from hiro.sim import resource as rv

    monkeypatch.setattr(dk, "SIM", True)
    monkeypatch.setattr(dk, "log_deviation", lambda rec: None)
    monkeypatch.setattr(
        rv,
        "_eval",
        lambda setups, seeds, workers=4: [
            {"mean": 100, "gain": 0, "gain_se": 0},
            {"mean": 7000, "gain": 6386, "gain_se": 582},
        ],
    )
    opts = [dk.Option("skip", "不拿", [TOKUSEI, "pdrink_00-3-001", TOKUSEI, "pdrink_00-2-010"]),
            dk.Option("pick2", "拿 おしゃれハーブティー", [TOKUSEI, "pdrink_00-3-001", TOKUSEI, "pdrink_00-2-010", HERB])]  # fmt: skip
    res = dk.choose(opts, "r1", 5, sim_context={"exam": "r1", "deck": None, "hp": 33, "max_hp": 33, "permil": None})
    assert res["pick"].key == "skip" and res["deviation"] is None and "不跨档偏离" in res["why"]
