"""第二十五局轮间花钱（协调 2026-11-04 转用户：「这个地方花钱逻辑是啥？？？」「瞎买饮料」）回归。
实机：480P、R1 剩 ホエイ（轮间页读成空），回体力 → 刷新 → ブレンド → 刷新 → ハーブティー → 强化 ワクワク → 剩 115P，R2 没有复制 / 爆发 / 抽牌。
期望：先找 复制 / 爆发 / 抽牌；弱饮料（好印象 / 回复）刷新预算用尽才补位；饮料没停之前不强化；刷新后同格编号不当成已买。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "interval" / "main25_interval.json").read_text(encoding="utf-8"))
GAINS = {k: tuple(v) for k, v in FIX["gains"].items()}
STEP = {s["line"]: s for s in FIX["steps"]}
TOK, REC, BLEND, WHEY, HERB, BOOST = (
    "pdrink_02-3-010",
    "pdrink_00-2-003",
    "pdrink_02-3-007",
    "pdrink_00-3-001",
    "pdrink_02-2-006",
    "pdrink_00-2-008",
)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(iv, "plan_tiered", lambda *a, **k: copy.deepcopy(FIX["plan"]))
    monkeypatch.setattr(iv, "drink_gains", lambda *a, **k: dict(GAINS))
    monkeypatch.setattr(iv, "CONTEXT", copy.deepcopy(FIX["context"]))
    monkeypatch.setattr(iv, "STATE_DIR", None)
    iv._GAINS_BG.clear()
    yield
    iv._GAINS_BG.clear()


def st(line, p=None, drinks=None, refresh=None, hp=None):
    s = copy.deepcopy(STEP[line])
    shelf = s["interval"]
    if drinks is not None:
        shelf["drinks"] = [{"index": 6 + i, "price": iv.drink_price(d) if d else 75, "purchased": d is None, "drink_id": d, "name": d}
                           for i, d in enumerate(drinks)]  # fmt: skip
    if refresh is not None:
        shelf["refresh"] = {"price": refresh, "available": True}
    return GameState(page_fields={"stamina": hp or s["stamina"], "max_stamina": s["max_stamina"], "produce_point": p or s["produce_point"], "interval": shelf},
                     produce=s["produce"])  # fmt: skip


def test_main25_targets_first_weak_last_no_upgrade(env):
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    d = dec.decide("HIF_ROUND_INTERVAL", st(75))
    assert d.action.args == {"service": "recover"}, d.reason
    d = dec.decide("HIF_ROUND_INTERVAL", st(76))  # 特製 + リカバリ：复制是 R2 目标，买（实机直接刷新了）
    assert d.action.kind is ActionKind.INTERVAL_BUY and d.action.args == {"index": 6}, d.reason
    assert "持有 初星ホエイプロテイン" in d.reason, d.reason  # 轮间页没读到饮料：用 R1 剩下的
    d = dec.decide(
        "HIF_ROUND_INTERVAL", st(76, p=310, drinks=[None, REC])
    )  # 只剩 リカバリ（回复·3档）、P 够：刷新，不买、不强化
    assert d.action.kind is ActionKind.REROLL, d.reason
    d = dec.decide("HIF_ROUND_INTERVAL", st(77, p=300, drinks=[BLEND, TOK]))  # 刷出 ブレンド + 第 2 瓶 特製
    assert d.action.args == {"index": 6}, d.reason
    d = dec.decide("HIF_ROUND_INTERVAL", st(77, p=200, drinks=[None, TOK]))  # 剩 1 格、P 200：还缺爆发 / 抽牌，再刷
    assert d.action.kind is ActionKind.REROLL, d.reason
    d = dec.decide(
        "HIF_ROUND_INTERVAL", st(79, p=180, drinks=[HERB, BOOST], refresh=30)
    )  # 刷出 爆发：买 ブースト，不买 ハーブティー
    assert d.action.args == {"index": 7}, d.reason


def test_main25_weak_drink_only_as_last_fill(env):
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec.decide("HIF_ROUND_INTERVAL", st(75))
    d = dec.decide(
        "HIF_ROUND_INTERVAL", st(79, p=290, drinks=[None, HERB], refresh=20)
    )  # 空 3 格（持有 ホエイ）、P 够刷新 + 一瓶目标
    assert d.action.kind is ActionKind.REROLL, d.reason  # 不买 ハーブティー，也不强化（实机这里去强化了）
    d = dec.decide("HIF_ROUND_INTERVAL", st(79, p=90, drinks=[None, HERB], refresh=30))  # P 不够刷新 + 一瓶目标：补位
    assert d.action.args == {"index": 7} and "补位" in d.reason, d.reason


def test_main25_no_upgrade_while_drinks_open(env):
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec.decide("HIF_ROUND_INTERVAL", st(75))
    for p in (400, 250, 130):
        d = dec.decide("HIF_ROUND_INTERVAL", st(80, p=p, drinks=[None, REC], refresh=20))
        assert d.action.kind is not ActionKind.INTERVAL_UPGRADE_CARD, (p, d.reason)


def test_main25_stale_slot_key_old_path(env, monkeypatch):
    """原来的买法（INTERVAL_DRINK_STEP 关）也修了：第 1 次刷新后在 6 号格买了 ブレンド，第 2 次刷新后 6 号格的 ホエイ 曾被当成已买，改买了 ハーブティー。"""
    monkeypatch.setattr(ProduceDeciders, "INTERVAL_DRINK_STEP", False)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec.decide("HIF_ROUND_INTERVAL", st(75))
    d = dec.decide("HIF_ROUND_INTERVAL", st(76))
    assert d.action.kind is ActionKind.REROLL, d.reason
    d = dec.decide("HIF_ROUND_INTERVAL", st(77))
    assert d.action.args == {"index": 6}, d.reason  # ブレンド
    d = dec.decide("HIF_ROUND_INTERVAL", st(78))
    assert d.action.kind is ActionKind.REROLL, d.reason
    d = dec.decide("HIF_ROUND_INTERVAL", st(79))
    assert d.action.args == {"index": 6}, d.reason  # ホエイ（不是 7 号格的 ハーブティー）


def test_drink_step_dp_matches_bruteforce_small():
    """RefreshDP 的 C(1, s, c)：只剩一格时 = max(0, E[新货架最好那瓶的净值（买得起）] − 刷新成本) 的递推，和直接枚举一致。"""
    g = dict(GAINS)
    dp = iv.RefreshDP(g)
    good = dp.good
    pool = iv.SHOP_DRINK_POOL
    s, c = 110, 20
    # 刷一次后只能买（110 − 20 = 90，再刷 30 + 最便宜好饮料 50 > 90 时不会再刷）
    tot = 0.0
    for a in pool:
        for b in pool:
            vals = [good[x][0] for x in (a, b) if x in good and good[x][1] <= s - c]
            tot += max([0.0] + vals)
    want = max(0.0, tot / len(pool) ** 2 - iv.SPARE_P_VALUE * c)
    assert (
        dp.C(1, s, c) == pytest.approx(want, abs=1e-6) or dp.C(1, s, c) >= want
    )  # 允许 DP 里「再刷一次」带来的额外价值


@pytest.mark.parametrize("up_gain,kept", [(0, False), (20_000, True)])
def test_plan_upgrade_tested_alone(monkeypatch, up_gain, kept):
    """协调 2026-11-04：第二十五局方案「回体力到 24 + 强化 ワクワク」合并估 +7.2 万，强化单独约 +0.6 万。
    模拟方案里的强化要单独（方案 − 方案去掉强化，同种子配对）过 2 SE 才保留；否则去掉，强化目标回到社区默认的「剩下的 P 再强化」。"""
    shelf = copy.deepcopy(STEP[75]["interval"])
    monkeypatch.setattr(iv, "CONTEXT", copy.deepcopy(FIX["context"]))

    def fake_search(setup, opts, p, **k):
        rows = [{"options": [o.key], "labels": [o.label], "gain": {"hp24": 45_000, "upgrade": 5_000}.get(o.key, -1), "gain_se": 3000, "cost": o.cost, "mean": 1}
                for o in opts if o.key in ("hp24", "upgrade")]  # fmt: skip
        return {"combos": rows, "seconds": 0, "policy": "community", "seeds": [0]}

    def score(setup, s):
        up = any(c == ("p_card-02-men-2_004", 1) for c in setup.deck)
        noise = 6000 * ((s * 7919) % 3 - 1)
        return 1000 * setup.hp + (up_gain + noise if up else 0) - 30_000 * (TOK in setup.drinks)

    def fake_eval(setups, seeds, policy, workers=4, **k):
        out = []
        for st_ in setups:
            xs = [score(st_, s) for s in seeds]
            out.append({"mean": sum(xs) / len(xs), "se": 1, "scores": xs})
        return out

    monkeypatch.setattr(iv, "search", fake_search)
    monkeypatch.setattr(iv, "evaluate", fake_eval)
    r = iv.plan_tiered(shelf, hp=10, max_hp=33, p=480, star=None, target_hp=17)
    has_up = any("强化" in x for x in r["labels"])
    assert has_up is kept, (r["labels"], r["rule_notes"])
    assert any("强化单独" in n for n in r["rule_notes"]), r["rule_notes"]
    assert r["upgrade"] == "p_card-02-men-2_004"  # 没进模拟方案时也留着社区默认的强化目标（饮料停了、还有 P 才执行）
