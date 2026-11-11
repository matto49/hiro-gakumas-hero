"""轮间按计算选（协调 4a 第八局本战分析 B3，用户确认）：选项、配对评估、R2 开局合成与决策器接线。"""

import pytest

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import GameState
from hiro.sim import interval as iv

DECK = [("p_card-02-men-2_004", 1), ("p_card-02-act-3_186", 1), ("p_card-02-act-3_039", 1), ("p_card-02-act-1_037", 1),
        ("p_card-02-act-2_062", 1), ("p_card-00-men-3_005", 1), ("p_card-00-men-3_003", 0), ("p_card-02-men-2_057", 1),
        ("p_card-02-ido-3_105", 1), ("p_card-02-men-1_031", 0), ("p_card-02-men-2_111", 0), ("p_card-00-acc-0_002", 0)]  # fmt: skip
SETUP = iv.R2Setup(deck=DECK, drinks=[], hp=18, max_hp=33, permil={"Vo": 35190, "Da": 32360, "Vi": 20240}, star=865)
SHELF = {"cards": [{"index": 4, "price": 30, "purchased": False, "card_id": "p_card-02-act-1_071", "name": "がむしゃら+", "upgrade": 1}],
         "drinks": [{"index": 7, "price": 100, "purchased": False, "drink_id": "pdrink_02-3-010", "name": "特製ハツボシエキス"}],
         "upgrade": {"price": 100, "available": True, "left": 1}, "recover": {"available": True, "price_per_2hp": 10}}  # fmt: skip


def test_options_and_costs():
    opts = {o.key: o for o in iv.options(SHELF, SETUP, 575)}
    assert {"hp19", "hp24", "hp33", "drink7", "card4"} <= set(opts)
    assert opts["hp19"].cost == 10 and opts["hp33"].cost == 80 and len(opts["hp33"].actions) == 8
    s = opts["card4"].apply(SETUP)
    assert s.star == 865 and ("p_card-02-act-1_071", 1) in s.deck and len(s.deck) == len(DECK) + 1  # 买卡不算星星（第九局复盘）
    assert opts["drink7"].apply(SETUP).drinks == ["pdrink_02-3-010"]
    assert all(o.cost <= 60 for o in iv.options(SHELF, SETUP, 60))  # 买不起的不列


def test_star_factor_continuous():
    assert iv.star_factor(800) == 2.0 and iv.star_factor(780) < 2.0 < iv.star_factor(880) <= 3.0


@pytest.mark.sim_master  # 真跑模拟：要主数据（fetch_master.py）
def test_play_is_deterministic_and_coupled():
    a = iv.play(SETUP, 1, "expert", worlds=2, depth=1)
    assert a == iv.play(SETUP, 1, "expert", worlds=2, depth=1)  # 不设时间预算：可复现，方案之间才能配对
    more = iv._with(SETUP, deck=SETUP.deck + [("p_card-02-act-1_071", 1)])
    env, base = iv._env(more, 3), iv._env(SETUP, 3)
    iv._couple(env, base, 3)
    # 牌组不足 22 张时引擎补基本卡（多一张卡就少补一张），所以只比非基本卡：两边都有的卡相对顺序不变
    core = lambda e: [e.cards[i].split(":")[0] for i in e.zones["hand"] + e.zones["deck"] if "-0_" not in e.cards[i]]  # noqa: E731
    seq = core(env)
    seq.remove("p_card-02-act-1_071@1")
    assert seq == core(base)


@pytest.mark.sim_master  # 真跑模拟：要主数据（fetch_master.py）
def test_search_returns_auditable_table():
    opts = [o for o in iv.options(SHELF, SETUP, 575) if o.key in ("hp19", "drink7")]
    r = iv.search(SETUP, opts, 575, seeds=range(2), workers=1, worlds=2, depth=1)
    assert {tuple(x["options"]) for x in r["combos"]} >= {(), ("hp19",), ("drink7",)}
    assert all({"gain", "gain_se", "mean", "cost", "labels"} <= set(x) for x in r["combos"])


def _interval_state(hp=18, p=575):
    return GameState(page_fields={"stamina": hp, "max_stamina": 33, "produce_point": p, "interval": SHELF},
                     produce={"mode": "produce-008", "star": 865, "drink_capacity": 4})  # fmt: skip


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_decider_executes_sim_plan(monkeypatch):
    plan = {"target_hp": 24, "buy": [7], "labels": ["回体力到 24/33", "买饮料 特製ハツボシエキス"], "gain": 50000, "gain_se": 9000,
            "cost": 130, "rows": [{"labels": ["x"], "cost": 130, "mean": 300000, "gain": 50000, "gain_se": 9000}], "seconds": 1.0,
            "policy": "expert", "seeds": [0]}  # fmt: skip
    monkeypatch.setattr(iv, "plan_tiered", lambda *a, **k: plan)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    d = dec.decide("HIF_ROUND_INTERVAL", _interval_state(hp=18))
    # 用户 2026-11-02：轮间默认是社区方案（plan_tiered），没有偏离时 source=rule；偏离社区默认时才是 sim
    assert d.action.args == {"service": "recover"} and d.source == "rule" and "比不买 +50,000" in d.reason
    d = dec.decide("HIF_ROUND_INTERVAL", _interval_state(hp=24, p=545))
    assert d.action.args == {"index": 7}
    d = dec.decide("HIF_ROUND_INTERVAL", _interval_state(hp=24, p=445))  # P 点已减少：买到了（purchased 还没刷新）
    assert d.action.kind.value == "IntervalEnd"


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_rule_fallback_targets_half_hp_and_skips_junk(monkeypatch):
    monkeypatch.setattr(iv, "plan_tiered", lambda *a, **k: None)
    shelf = dict(SHELF, drinks=[])
    st = GameState(page_fields={"stamina": 12, "max_stamina": 33, "produce_point": 575, "interval": shelf},
                   produce={"mode": "produce-008", "star": 865, "drink_capacity": 4})  # fmt: skip
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    d = dec.decide("HIF_ROUND_INTERVAL", st)
    assert d.action.args == {"service": "recover"} and "目标 17" in d.reason
    st.page_fields["stamina"] = 17
    d = dec.decide("HIF_ROUND_INTERVAL", st)
    assert d.action.kind.value == "IntervalEnd"  # 不为ワッペン 买 がむしゃら+（废卡），不回满


def test_plan_runs_with_missing_attr_permil(monkeypatch):
    """第十局续跑：读回的 R1 倍率只有 Vo / Da（画面没读到 Vi 回合），plan() 曾直接返回 None、轮间走兜底什么都没买。"""
    monkeypatch.setattr(iv, "CONTEXT", {"deck": [list(c) for c in DECK], "drinks": [], "permil": {"Vo": 35680, "Da": 30870}})
    seen = {}

    def fake_search(setup, opts, p, **kw):
        seen["permil"] = setup.permil
        return {"combos": [{"options": [], "labels": [], "gain": 0, "gain_se": 0, "cost": 0, "mean": 1}], "seconds": 0, "policy": "heuristic", "seeds": [0]}

    monkeypatch.setattr(iv, "search", fake_search)
    res = iv.plan(SHELF, hp=21, max_hp=33, p=250, star=900)
    assert res is not None and seen["permil"] == {"Vo": 35680, "Da": 30870, "Vi": 30870}


def test_final_interval_buys_every_nonnegative_option():
    """第十二局 R1→R2 轮间（本战最后一个轮间，之后 P 点没用）：剩 389P、体力 20/33，旧逻辑「没有选项显著好过不买」直接结束。
    最后一个轮间改为期望 ≥ 0 就按期望从高到低买：回体力只取一档，饮料不超空位，买不起的跳过，负期望不买。"""
    shelf = {"cards": [{"index": 0, "price": 50, "card_id": "p_card-00-men-2_014", "name": "ハイテンション+", "upgrade": 1},
                       {"index": 5, "price": 50, "card_id": "p_card-02-act-2_047", "name": "ありがとうの言葉+", "upgrade": 1}],
             "drinks": [{"index": 6, "price": 100, "drink_id": "pdrink_02-3-006", "name": "厳選初星ティー"},
                        {"index": 7, "price": 50, "drink_id": "pdrink_02-1-006", "name": "ルイボスティー"}],
             "upgrade": {"price": 100, "available": True, "left": 1}, "recover": {"available": True, "price_per_2hp": 10}}  # fmt: skip
    setup = iv._with(SETUP, hp=20)
    opts = iv.options(shelf, setup, 389, upgrade_target=("p_card-02-act-3_186", 0))
    gains = {"upgrade": 9000, "drink6": 4777, "hp24": 4500, "hp33": 3000, "drink7": 800, "card5": -2000, "card0": 100}
    rows = [{"options": [k], "gain": g, "gain_se": 4000} for k, g in gains.items()]
    chosen = [o.key for o, _ in iv.greedy_final(rows, opts, 389, drinks_held=0, drink_capacity=4)]
    assert chosen == ["upgrade", "drink6", "hp24", "drink7"]  # hp33 同组只取一档；ありがとう 负期望；ハイテンション 冲突牌（小牌组）不买
    assert iv.greedy_final(rows, opts, 150, drinks_held=0, drink_capacity=4)[0][0].key == "upgrade"


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_decider_executes_final_plan_with_upgrade(monkeypatch):
    plan = {"target_hp": 24, "buy": [7], "upgrade": "p_card-02-act-3_186", "labels": ["回体力到 24/33", "买饮料 特製", "强化 せのび"],
            "gain": 25770, "gain_se": 4169, "cost": 270, "significant": True, "final": True,
            "rows": [{"labels": ["x"], "cost": 270, "mean": 300000, "gain": 25770, "gain_se": 4169}], "seconds": 1.0, "policy": "heuristic",
            "seeds": [0]}  # fmt: skip
    monkeypatch.setattr(iv, "plan_tiered", lambda *a, **k: plan)
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    assert dec.decide("HIF_ROUND_INTERVAL", _interval_state(hp=20, p=389)).action.args == {"service": "recover"}
    assert dec.interval_target_hp == 24  # 执行侧离开前的体力检查用同一个目标
    d = dec.decide("HIF_ROUND_INTERVAL", _interval_state(hp=24, p=369))
    assert d.action.args == {"index": 7}
    d = dec.decide("HIF_ROUND_INTERVAL", _interval_state(hp=24, p=269))
    assert d.action.kind.value == "IntervalUpgradeCard" and d.action.args == {"card_id": "p_card-02-act-3_186"}
    assert dec.decide("HIF_ROUND_INTERVAL", _interval_state(hp=24, p=169)).action.kind.value == "IntervalEnd"


def test_no_process_pool_when_workers_capped(monkeypatch):
    """Windows 第十五局：spawn 起的模拟子进程把内存吃满。HIRO_SIM_WORKERS=1（Windows 默认）时预热也不能起进程池。"""
    from hiro.sim import resource as rv

    monkeypatch.setenv("HIRO_SIM_WORKERS", "1")
    monkeypatch.setattr(iv, "_pool", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该起进程池")))
    iv.CONTEXT.clear()
    iv.CONTEXT.update(deck=[("p_card-02-act-3_039", 1)], permil={"Vo": 30000, "Da": 30000, "Vi": 20000})
    iv._prewarm_r2()
    assert rv.prewarm("r1", deck=[("p_card-02-act-3_039", 1)], max_hp=33) is False
    assert iv.sim_workers(4) == 1
    iv.CONTEXT.clear()
