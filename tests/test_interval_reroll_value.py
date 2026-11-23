"""轮间刷新货架（用户 2026-11-02，协调转达）：第十七局轮间 580P、货架饮料只有 おしゃれハーブティー（好印象）和 ホエイ，
实机买了 ハーブティー、P 剩 435、R2 只带 1 瓶 ハーブティー。现在：买 ホエイ（计划内）之后，估值说刷新划算 → Reroll {"scope": "interval"}；
刷出 ブースト 就买；空位满了 / P 到了需要量 / 估值不划算就停。动作名和执行 23e6965 对齐。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "q16_resource.json").read_text(encoding="utf-8"))[
    "main17_interval"
]
GOOD = {
    "pdrink_02-3-010": 30000,
    "pdrink_00-2-008": 25000,
    "pdrink_00-2-010": 20000,
    "pdrink_00-3-001": 15000,
}  # 复制 / 爆发 / 抽牌 / 加行动


@pytest.fixture(autouse=True)
def _no_calibration(monkeypatch):
    """这里的增益是手设的「真值」，不再按专家口径放大（TARGET_CALIBRATION 见 test_interval_main27）。"""
    monkeypatch.setattr(iv, "TARGET_CALIBRATION", False)


def _gains(*a, **k):
    return {d: (GOOD.get(d, 500), 3000) for d in iv.SHOP_DRINK_POOL}


def _plan(*a, **k):  # plan_tiered 的结果（社区默认）：回体力到 24、买 ホエイ（index 7）、强化 紙吹雪
    return {"target_hp": 24, "buy": [7], "upgrade": "p_card-02-act-2_062", "labels": ["回体力到 24/33", "买饮料 初星ホエイプロテイン", "强化 紙吹雪"],
            "gain": 30000, "gain_se": 8000, "cost": 225, "significant": True, "final": True, "rule_labels": [], "rule_notes": [], "deviation": None,
            "rows": [], "seconds": 1.0, "policy": "heuristic", "seeds": [0]}  # fmt: skip


def _state(shelf, hp, p):
    return GameState(page_fields={"stamina": hp, "max_stamina": 33, "produce_point": p, "interval": shelf},
                     produce={"mode": "produce-008", "star": 915, "drink_capacity": 4})  # fmt: skip


def test_q17_interval_rerolls_for_r2_drinks(monkeypatch):
    monkeypatch.setattr(iv, "plan_tiered", _plan)
    monkeypatch.setattr(iv, "drink_gains", _gains)
    monkeypatch.setattr(
        iv, "CONTEXT", {"deck": FIX["finals_context"]["deck"], "permil": FIX["finals_context"]["permil"], "drinks": []}
    )
    shelf = copy.deepcopy(FIX["page_fields"]["interval"])
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 580))
    assert d.action.args == {"index": 7}, d.reason  # 计划内：ホエイ；不买 ハーブティー
    shelf["drinks"][1].update(purchased=True, drink_id=None, name=None)
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 480))
    assert d.action.kind is ActionKind.REROLL and d.action.args == {"scope": "interval"}, d.reason
    assert "刷新估值" in d.reason
    # 刷出 ブースト + 烏龍茶：买 ブースト，不买 烏龍茶
    shelf2 = copy.deepcopy(shelf)
    shelf2["drinks"] = [{"index": 6, "price": 75, "purchased": False, "drink_id": "pdrink_00-2-008", "name": "ブーストエキス"},
                        {"index": 7, "price": 50, "purchased": False, "drink_id": "pdrink_00-1-004", "name": "烏龍茶"}]  # fmt: skip
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf2, 24, 470))
    assert d.action.args == {"index": 6}, d.reason
    shelf2["drinks"][0].update(purchased=True, drink_id=None)
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf2, 24, 395))
    assert d.action.kind is ActionKind.REROLL, d.reason  # 还有空位、P 够：再刷


def test_reroll_stops_when_failed_or_p_short(monkeypatch):
    monkeypatch.setattr(iv, "plan_tiered", _plan)
    monkeypatch.setattr(iv, "drink_gains", _gains)
    monkeypatch.setattr(
        iv, "CONTEXT", {"deck": FIX["finals_context"]["deck"], "permil": FIX["finals_context"]["permil"], "drinks": []}
    )
    shelf = copy.deepcopy(FIX["page_fields"]["interval"])
    shelf["drinks"][1].update(purchased=True, drink_id=None, name=None)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 580))  # 初始化方案（ホエイ 已买）
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 480))
    assert d.action.kind is ActionKind.REROLL
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 480))  # P 和货架都没变：刷新没成 → 不再刷，去强化
    assert d.action.kind is ActionKind.INTERVAL_UPGRADE_CARD, d.reason
    # P 只够强化：用户 2026-11-03「强化的收益不如买饮料」——不为强化预留，照样刷新找饮料；P 连刷新 + 一瓶都不够时才去强化 / 结束
    dec2 = ProduceDeciders(CAMPUS_HIRO, None)
    dec2.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 580))
    d = dec2.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 105))
    assert d.action.kind is ActionKind.REROLL and "不为强化预留" in d.reason, d.reason
    dec3 = ProduceDeciders(CAMPUS_HIRO, None)
    dec3.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 580))
    d = dec3.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 40))
    assert d.action.kind is not ActionKind.REROLL, d.reason


def test_q19_drinks_before_upgrade(monkeypatch):
    """第十九局轮间：买完 厳選 / センブリ 后剩 154P、饮料栏还空 2 格，方案里有强化 せのび（100P）。原来刷新预算扣掉强化的 100P 只剩 54P，
    不刷 → 强化 → 剩 44P，R2 只带 2 瓶。现在先刷新找第 3 瓶，强化只用剩下的钱。"""
    monkeypatch.setattr(iv, "plan_tiered", _plan)
    monkeypatch.setattr(iv, "drink_gains", _gains)
    monkeypatch.setattr(
        iv, "CONTEXT", {"deck": FIX["finals_context"]["deck"], "permil": FIX["finals_context"]["permil"], "drinks": []}
    )
    shelf = copy.deepcopy(FIX["page_fields"]["interval"])
    shelf["drinks"][1].update(purchased=True, drink_id=None, name=None)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 580))
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 154))
    assert d.action.kind is ActionKind.REROLL, d.reason


def test_reroll_value_math():
    g = _gains()
    v, picks = iv.shelf_value([(6, "pdrink_02-2-006", 75), (7, "pdrink_00-3-001", 100)], g, 2, 300)
    assert (
        picks == [7] and v == 15000 - iv.SPARE_P_VALUE * 100
    )  # ハーブティー 增益不显著，不买；ホエイ 净值扣余 P 机会成本（3a4615f）
    r = iv.reroll_ev(g, 2, 300, 10, 0.0, n=2000)
    assert r["ev"] > 0 and 0 < r["p_good"] < 1
    assert iv.reroll_ev(g, 0, 300, 10, 0.0)["ev"] == float("-inf")  # 没空位
    assert iv.reroll_ev(g, 2, 5, 10, 0.0)["ev"] == float("-inf")  # P 不够刷新价


def test_q20_keep_last_slot_for_better_drink(monkeypatch):
    """第二十局轮间（总控 2026-11-03）：饮料栏 3 格只剩 1 格、刷新后的货架只有 おしゃれハーブティー（好印象，R2 增益约 +0.8 万）：
    原来买下占满栏位，随后刷出 ホエイ / 厳選ブレンド 买不了、剩 225P。现在最后一格留着，先再刷一次（新货架期望更高）。"""
    gains = dict(_gains())
    gains["pdrink_02-1-006"] = (7815, 2777)  # おしゃれハーブティー（第二十局估值）
    monkeypatch.setattr(iv, "plan_tiered", _plan)
    monkeypatch.setattr(iv, "drink_gains", lambda *a, **k: gains)
    monkeypatch.setattr(
        iv, "CONTEXT", {"deck": FIX["finals_context"]["deck"], "permil": FIX["finals_context"]["permil"], "drinks": []}
    )
    shelf = copy.deepcopy(FIX["page_fields"]["interval"])
    dec = ProduceDeciders(CAMPUS_HIRO, None)

    def st(shelf, p):
        return GameState(page_fields={"stamina": 24, "max_stamina": 33, "produce_point": p, "interval": shelf},
                         produce={"mode": "produce-008", "star": 915, "drink_capacity": 3, "drinks": ["pdrink_00-2-008"]})  # fmt: skip

    d = dec.decide("HIF_ROUND_INTERVAL", st(shelf, 580))
    assert d.action.args == {"index": 7}, d.reason  # 计划内 ホエイ（持有 ブースト + ホエイ，还剩 1 格）
    shelf["drinks"][1].update(purchased=True, drink_id=None, name=None)
    d = dec.decide("HIF_ROUND_INTERVAL", st(shelf, 480))
    assert d.action.kind is ActionKind.REROLL, d.reason
    weak = copy.deepcopy(shelf)
    weak["drinks"] = [{"index": 6, "price": 75, "purchased": False, "drink_id": "pdrink_00-1-004", "name": "フレッシュビネガー"},
                      {"index": 7, "price": 75, "purchased": False, "drink_id": "pdrink_02-1-006", "name": "おしゃれハーブティー"}]  # fmt: skip
    d = dec.decide("HIF_ROUND_INTERVAL", st(weak, 470))
    assert d.action.kind is ActionKind.REROLL and "继续刷新的价值" in d.reason, (
        d.reason
    )  # 弱饮料不占最后一格（第二十五局起按栏位机会成本 + 社区先验）
    strong = copy.deepcopy(weak)
    strong["drinks"] = [{"index": 6, "price": 75, "purchased": False, "drink_id": "pdrink_00-2-008", "name": "ブーストエキス"},
                        {"index": 7, "price": 75, "purchased": False, "drink_id": "pdrink_02-1-006", "name": "おしゃれハーブティー"}]  # fmt: skip
    d = dec.decide("HIF_ROUND_INTERVAL", st(strong, 460))
    assert d.action.args == {"index": 6}, d.reason  # 刷出 ブースト：买


def test_keep_last_slot_math():
    from hiro.brain.deciders import ProduceDeciders as P

    g = _gains()
    weak = {"drink_id": "pdrink_02-1-006", "price": 75}
    strong = {"drink_id": "pdrink_02-3-010", "price": 100}
    assert P._keep_last_slot(dict(g, **{"pdrink_02-1-006": (7815, 2777)}), weak, 300, 10)
    assert not P._keep_last_slot(g, strong, 300, 10)  # 特製（+3 万）：买
    assert not P._keep_last_slot(dict(g, **{"pdrink_02-1-006": (7815, 2777)}), weak, 80, 10)  # 刷完买不起：买现在这瓶


def test_gains_failure_still_buys_plan_drinks(monkeypatch):
    """逐瓶 / 刷新估值抛异常（DMM 本战轮间 KeyError）：方案里的饮料照方案买，不直接结束轮间。"""

    def boom(*a, **k):
        raise KeyError("p_card-02-men-2_100@3")

    monkeypatch.setattr(iv, "plan_tiered", _plan)
    monkeypatch.setattr(iv, "drink_gains", boom)
    monkeypatch.setattr(iv, "take_gains", lambda setup: None)
    monkeypatch.setattr(
        iv, "CONTEXT", {"deck": FIX["finals_context"]["deck"], "permil": FIX["finals_context"]["permil"], "drinks": []}
    )
    shelf = copy.deepcopy(FIX["page_fields"]["interval"])
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    d = dec.decide("HIF_ROUND_INTERVAL", _state(shelf, 24, 580))
    assert d.action.kind is ActionKind.INTERVAL_BUY and d.action.args == {"index": 7}, d.reason
