"""第二十六局轮间（协调 2026-11-04，INTERVAL_DRINK_STEP 第一次实机）：方案里的 フレッシュビネガー（强化手牌·3 档）在刷新前就买了；
随后刷新 5 次（10 + 10 + 20 + 30 + 40 = 110P）没刷到复制 / 爆发 / 抽牌。
修：方案里的饮料一律交给 drink_step；继续刷新的价值只算刷新预算内会买的饮料（档位 ≤ 2），货架按实测频率；社区默认最多刷 3 次，
之后只有模拟继续刷新的价值 > 2 SE 才刷（记偏离）。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "interval" / "main26_interval.json").read_text(encoding="utf-8"))
GAINS = {k: tuple(v) for k, v in FIX["gains"].items()}
STEP = {s["line"]: s for s in FIX["steps"]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(iv, "plan_tiered", lambda *a, **k: copy.deepcopy(FIX["plan"]))
    monkeypatch.setattr(iv, "drink_gains", lambda *a, **k: dict(GAINS))
    monkeypatch.setattr(iv, "CONTEXT", copy.deepcopy(FIX["context"]))
    monkeypatch.setattr(iv, "STATE_DIR", None)
    iv._GAINS_BG.clear()
    yield
    iv._GAINS_BG.clear()


def st(line, p=None):
    s = copy.deepcopy(STEP[line])
    return GameState(page_fields={"stamina": s["stamina"], "max_stamina": s["max_stamina"], "produce_point": p or s["produce_point"], "interval": s["interval"]},
                     produce=s["produce"])  # fmt: skip


def test_main26_plan_weak_drink_goes_through_drink_step(env):
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    assert dec.decide("HIF_ROUND_INTERVAL", st(74)).action.args == {"service": "recover"}
    d = dec.decide("HIF_ROUND_INTERVAL", st(75))
    assert d.action.args == {"index": 6} and "加行动" in d.reason, d.reason  # ホエイ：R2 目标里的加行动
    d = dec.decide(
        "HIF_ROUND_INTERVAL", st(76, p=325)
    )  # 只剩 ビネガー（3 档）、P 够：不买，刷新（实机这里按方案买了 ビネガー）
    assert d.action.kind is ActionKind.REROLL, d.reason


def test_main26_reroll_stop_loss(env, monkeypatch):
    """REROLL_HARD_CAP 关时的口径：第 4 次起要么不刷，要么带「社区止损」偏离记录。"""
    monkeypatch.setattr(iv, "REROLL_HARD_CAP", False)
    monkeypatch.setattr(iv, "REROLL_FOR_TARGETS", False)  # 这里测「超过 3 次要模拟 > 2 SE」那条路径
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec.decide("HIF_ROUND_INTERVAL", st(74))
    dec.decide("HIF_ROUND_INTERVAL", st(75))
    seq = [(76, 325), (78, None), (79, None), (80, None), (81, None)]
    kinds, warns = [], []
    for line, p in seq:
        d = dec.decide("HIF_ROUND_INTERVAL", st(line, p))
        kinds.append(d.action.kind)
        warns.append(d.warnings)
    n = kinds.count(ActionKind.REROLL)
    # 社区默认 3 次；第 4 次起要么不刷，要么带「社区止损」偏离记录（模拟继续刷新的价值 > 2 SE）
    for i, (k, w) in enumerate(zip(kinds, warns)):
        if k is ActionKind.REROLL and i >= 3:
            assert any("社区止损" in x for x in w), (i, w)
    assert n >= 3
    d = dec.decide("HIF_ROUND_INTERVAL", st(82))  # 厳選ティー（好印象·4 档）+ ホットコーヒー（やる気·2 档）
    if iv.VALUE_TABLE_SCALE:
        # 价值表（专家层 やる気 ×0.83）下 コーヒー 不再先买：先执行方案里的强化 紙吹雪（专家层 紙吹雪+ +1.8 万 ± 0.3 万）
        assert d.action.args in ({"index": 7}, {"card_id": "p_card-02-act-2_062"}), d.reason
    else:
        assert d.action.args == {"index": 7}, d.reason  # 买 コーヒー


def test_refresh_dp_uses_observed_shelf_and_tiers():
    from hiro.state.knowledge import DRINK_CLASSES, drink_class

    full = iv.RefreshDP(GAINS)
    tiered = iv.RefreshDP(GAINS, buyable=lambda d: DRINK_CLASSES[drink_class(d)]["tier"] <= 2)
    assert (
        "pdrink_02-2-006" in full.good and "pdrink_02-2-006" not in tiered.good
    )  # ハーブティー：只补位，不算进继续刷新
    assert tiered.C(3, 210, 30) < full.C(3, 210, 30)
    assert iv.shelf_weight("pdrink_02-2-006") > iv.shelf_weight(
        "pdrink_00-2-010"
    )  # 实测：ハーブティー 常见、センブリ 罕见


def test_main26_hard_cap_switch(env, monkeypatch):
    """REROLL_HARD_CAP（默认开，协调 2026-11-04）：第 3 次刷新之后不再刷（不看模拟）。REROLL_FOR_TARGETS 关时才是纯硬上限。"""
    assert iv.REROLL_HARD_CAP is True
    monkeypatch.setattr(iv, "REROLL_FOR_TARGETS", False)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec.decide("HIF_ROUND_INTERVAL", st(74))
    dec.decide("HIF_ROUND_INTERVAL", st(75))
    kinds = [
        dec.decide("HIF_ROUND_INTERVAL", st(line, p)).action.kind
        for line, p in ((76, 325), (78, None), (79, None), (80, None))
    ]
    assert kinds[:3] == [ActionKind.REROLL] * 3 and kinds[3] is not ActionKind.REROLL, kinds
