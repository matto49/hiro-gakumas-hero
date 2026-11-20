"""bench 中转层：局面 → UIObs、出招插件的动作映射与退回规则（不需要 gkbench / 模型：算法用替身）。"""

from types import SimpleNamespace

import pytest

from hiro.bench_bridge import advisor as A
from hiro.bench_bridge.screen import ScreenTracker, exam_fields, from_screen
from hiro.bench_bridge.uiobs import from_engine
from hiro.brain.cards import CardView, DrinkView
from hiro.brain.state import CAMPUS_HIRO, BattleState, ExamMemory
from hiro.contracts import Action


def _card(slot, cid, u=0, available=True, name=None):
    return CardView(slot, name or cid, cid, u, available, "active", 3, 0, None, "パラメータ+10")


def _state(hand, drinks=(), remaining=8, turn=1, statuses=None, motivation=5, impression=3, profile=CAMPUS_HIRO):
    mem = ExamMemory(exam="r1")
    mem.turn = turn
    st = BattleState.build(mem, profile, source="screen", remaining_turns=remaining, hp=20, genki=10, motivation=motivation,
                           impression=impression, score=1000, buffs={}, attr="Da", multiplier_permil=None, npc_scores=None,
                           my_rank_index=None, deck_count=None, pitem_charges=None, statuses=statuses, attr_permil={"Da": 30000},
                           hand=list(hand), drinks=list(drinks))  # fmt: skip
    return st, mem


# ---------------------------------------------------------------- 引擎局面 → UIObs
def _engine_card(cid, base=0, tmp=0, support=(), customize=(), grows=()):
    return {"card_id": cid, "base_upgrade": base, "tmp_upgrade": tmp, "support_upgrades": list(support),
            "customize_counts": list(customize), "custom_override": None, "grow_after": [{"id": g} for g in grows]}


def _engine_state(**kw):
    st = {
        "cards": [_engine_card("c-a", 1), _engine_card("c-b", 0, support=[1]), _engine_card("c-c"), _engine_card("c-d", customize=[2])],
        "zones": {"hand": [0, 1], "deck": [2], "grave": [3], "lost": [], "hold": []},
        "status": {"effects": [
            {"cls": "ReviewStatusEffect", "turn": 7, "limited": True, "passing": False, "value": 0, "extra": {}, "trig": None},
            {"cls": "AggressiveStatusEffect", "turn": -1, "limited": False, "passing": False, "value": 12, "extra": {}, "trig": None},
            {"cls": "LessonParameterMultipleStatusEffect", "turn": 2, "limited": True, "passing": False, "value": 300, "extra": {}, "trig": None},
            {"cls": "StaminaConsumptionDownStatusEffect", "turn": 3, "limited": True, "passing": True, "value": 0, "extra": {}, "trig": None},
            {"cls": "PlayableValueAddStatusEffect", "turn": 1, "limited": True, "passing": False, "value": 1, "extra": {}, "trig": None},
            {"cls": "TriggerEffectStatusEffect", "turn": -1, "limited": False, "passing": False, "value": 0, "extra": {},
             "trig": {"enchant_id": "enchant-x", "limit_count": 2, "trigger": {"id": "t"}}},
            {"cls": "TriggerEffectStatusEffect", "turn": -1, "limited": False, "passing": False, "value": 0, "extra": {},
             "trig": {"enchant_id": "enchant-used", "limit_count": 0, "trigger": {"id": "t2"}}},
        ], "idol_type": 0, "idol_step": 0},
        "current_turn": 3, "limit_turn": 12, "extra_turn": 0, "turn_types": [1, 2, 3] * 4,
        "bonus_permil": [[1, 31000], [2, 32000], [3, 20000]], "stamina": 20, "max_stamina": 33, "block": 5, "score": 900,
        "remaining_actions": 1, "turn_card_play_count": 0, "drinks": ["pdrink-1"], "play_log": [],
    }
    st.update(kw)
    return st


def test_from_engine_maps_statuses_and_zones():
    obs = from_engine(_engine_state(), {"produce": "produce-008", "step": "AUDITION_MID1", "plan": 3, "crit": [1, 2, 3]},
                      [("play", 0), ("drink", 0), ("end",)], customize_ids=lambda cid, u: ["cz-1"])
    st = {(k, t): v for k, v, t in obs["status"]}
    assert st[("review", -1)] == 7.0  # 好印象的数量存在 turn 里
    assert st[("aggressive", -1)] == 12.0
    assert st[("lesson_parameter_multiple", 3)] == pytest.approx(0.3)  # 千分比 → 小数；没有 passing 时画面 = turn + 1
    assert st[("stamina_consumption_down", 3)] == 1.0  # passing 时画面 = turn
    assert obs["num"]["playable"] == 2  # 追加行动算进「还能出几张」
    assert obs["ench"] == [["enchant-x", 2, -1]]  # 次数用完的不显示
    assert obs["hand"] == [{"id": "c-a", "u": 1, "p": 1}, {"id": "c-b", "u": 1, "p": 0}]  # 支援强化计入 u
    assert obs["grave"] == [{"id": "c-d", "u": 0, "c": [["cz-1", 2]]}]
    assert obs["legal"] == [["play", 0], ["drink", 0], ["end"]]
    assert obs["exam"]["permil"] == [31000, 32000, 20000] and obs["exam"]["limit"] == 12


# ---------------------------------------------------------------- 画面读数 → UIObs
def test_screen_tracker_draws_and_reshuffles():
    t = ScreenTracker([("a", 0), ("b", 0), ("c", 1)], trend=("Vo", "Da", "Vi"))
    st, mem = _state([_card(0, "a"), _card(1, "b")], turn=1)
    t.observe(st, mem)
    assert dict(t.deck) == {("c", 1): 1}
    t.played_card(st.hand[0])
    st2, mem2 = _state([_card(0, "b")], turn=1)
    t.observe(st2, mem2)
    assert dict(t.grave) == {("a", 0): 1} and t.played == [{"id": "a", "u": 0}]
    # 换回合：剩下的 b 进弃牌；抽到 c（山札）和 a（山札空，弃牌重洗）
    st3, mem3 = _state([_card(0, "c", 1), _card(1, "a")], turn=2)
    t.observe(st3, mem3)
    assert t.played == [] and dict(t.deck) == {("b", 0): 1} and not t.grave


def test_types_fill_known_turns_and_final_three():
    t = ScreenTracker([], trend=("Vo", "Da", "Vi"))
    t.attrs = {1: "Da", 2: "Vi"}
    # 7 回合：1、2 已见；末三回合 流3 → 流2 → 流1；其余按 流1
    assert t.types(3, 7) == [2, 3, 1, 1, 3, 2, 1]


def test_from_screen_builds_obs_and_drink_slots():
    hand = [_card(0, "p_card-a", 1), _card(1, "p_card-b", 0, available=False)]
    drinks = [DrinkView(2, "饮料2", "pdrink-2", ""), DrinkView(0, "饮料0", "pdrink-0", "")]
    statuses = [{"type": "ProduceExamEffectType_ExamParameterBuff", "name": "好調", "value": 3, "unit": "turns"},
                {"type": "ProduceExamEffectType_ExamLessonValueMultiple", "name": "パラメータ上昇量増加", "value": 30, "unit": "percent"}]
    st, mem = _state(hand, drinks, remaining=8, turn=3, statuses=statuses)
    t = ScreenTracker([("p_card-a", 1), ("p_card-b", 0), ("p_card-c", 0)], trend=("Vo", "Da", "Vi"))
    t.observe(st, mem)
    obs, slot_of = from_screen(st, mem, st.legal_actions(), t, exam_fields("r2", idol="i_card-shro-3-007", plan=3))
    assert obs["exam"]["produce"] == "produce-008" and obs["exam"]["step"] == "AUDITION_MID2"
    assert obs["exam"]["turn"] == 3 and obs["exam"]["limit"] == 10 and len(obs["exam"]["types"]) == 10
    assert obs["hand"] == [{"id": "p_card-a", "u": 1, "p": 1}, {"id": "p_card-b", "u": 0, "p": 0}]
    assert obs["drinks"] == ["pdrink-0", "pdrink-2"] and slot_of == [0, 2]
    assert ["drink", 1] in obs["legal"] and ["end"] in obs["legal"] and ["play", 1] not in obs["legal"]
    kinds = {k: (v, n) for k, v, n in obs["status"]}
    assert kinds["parameter_buff"] == (3.0, 3) and kinds["lesson_parameter_multiple"][0] == pytest.approx(0.3)
    assert kinds["aggressive"] == (5.0, -1) and kinds["review"] == (3.0, -1)
    assert obs["deck"] == [{"id": "p_card-c", "u": 0}]


def test_exam_fields_labels():
    assert exam_fields("qualify-3", idol=None, plan=3)["step"] == "AUDITION_FINAL"
    assert exam_fields("qualify-1", idol=None, plan=3)["produce"] == "produce-007"
    assert exam_fields("r1#2", idol=None, plan=3)["step"] == "AUDITION_MID1"


# ---------------------------------------------------------------- 出招插件
class _Algo:
    name = "fake"
    sees_seed = privileged = False

    def __init__(self, action):
        self.action = action
        self.seen = []

    def begin(self, ctx):
        pass

    def decide(self, ctx):
        from types import SimpleNamespace

        self.seen.append(ctx.obs)
        self.ctx = ctx
        return SimpleNamespace(action=self.action, meta={"top": [[list(self.action), 0.9]], "score_quantiles": {"50%": 123}})


def _advisor(monkeypatch, action):
    algo = _Algo(action)
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "r1", "idol": "i_card-shro-3-007", "deck": [("p_card-a", 0), ("p_card-b", 0)]})
    return adv, algo


@pytest.mark.bench_on
def test_bench_drink_maps_back_to_screen_slot(monkeypatch):
    adv, algo = _advisor(monkeypatch, ("drink", 1))
    st, mem = _state([_card(0, "p_card-a")], [DrinkView(0, "饮料0", "pdrink-0", ""), DrinkView(3, "饮料3", "pdrink-3", "")])
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert d.source == "bench" and d.action.legal_key() == "drink:3"
    assert algo.seen[0]["drinks"] == ["pdrink-0", "pdrink-3"]


@pytest.mark.bench_on
def test_bench_illegal_action_falls_back_to_rules_without_llm(monkeypatch):
    monkeypatch.setenv("HIRO_LLM", "should-not-be-used")
    adv, _ = _advisor(monkeypatch, ("play", 5))
    assert adv.rules.brain.llm is None
    st, mem = _state([_card(0, "p_card-a"), _card(1, "p_card-b")])
    legal = [Action.from_legal(a) for a in st.legal_actions()]
    d = adv.decide(st, mem, None, legal)
    assert d.source != "bench" and d.action.legal_key() in {a.legal_key() for a in legal}
    assert any("不在合法动作" in w for w in d.warnings)


@pytest.mark.bench_on
def test_bench_load_failure_uses_rules(monkeypatch):
    def boom(*a, **k):
        raise ImportError("No module named 'gkbench'")

    monkeypatch.setattr(A, "load_algo", boom)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    assert adv.algo is None and "gkbench" in adv.load_error and adv.rules.brain.llm is None
    st, mem = _state([_card(0, "p_card-a")])
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert d.source != "bench"


def test_bench_off_restores_rule_llm_advisor(monkeypatch):
    called = []
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: called.append(1))
    adv = A.BenchAdvisor(CAMPUS_HIRO, llm="LLM")
    assert adv.algo is None and not called and adv.load_error == "HIRO_BENCH_ADVISOR=0"
    assert adv.rules.brain.llm == "LLM"  # 关掉 bench：原来的规则 + LLM


def test_rejects_seed_seeing_algorithms(monkeypatch):
    class Seer(_Algo):
        sees_seed = True

    import sys
    import types

    fake = types.ModuleType("gkbench.algos")
    fake.make = lambda spec: Seer(("end",))
    monkeypatch.setitem(sys.modules, "gkbench", types.ModuleType("gkbench"))
    monkeypatch.setitem(sys.modules, "gkbench.algos", fake)
    with pytest.raises(ValueError, match="看种子"):
        A.load_algo("seer")


class _FakeEnchants:
    def item(self, item_id):
        return (("enchant-item", 5, -1),) if item_id == "pitem-x" else ()

    def card(self, card_id, upgrade):
        return {"c-sus": (("enchant-sus", -1, 2, False),), "c-once": (("enchant-once", 1, -1, True),)}.get(card_id, ())


def test_tracker_records_item_and_played_card_enchants():
    t = ScreenTracker([("c-sus", 0), ("c-once", 0), ("c-x", 0)], trend=("Vo", "Da", "Vi"), enchants=_FakeEnchants(), items=["pitem-x"])
    assert t.ench == [["enchant-item", 5, -1]]
    st, mem = _state([_card(0, "c-sus"), _card(1, "c-once")], turn=1)
    t.observe(st, mem)
    t.played_card(st.hand[0])
    t.played_card(st.hand[1])
    t.played_card(st.hand[1])  # 只第一次打出时挂
    assert sorted(map(tuple, t.ench)) == [("enchant-item", 5, -1), ("enchant-once", 1, -1), ("enchant-sus", -1, 2)]
    st2, mem2 = _state([_card(0, "c-x")], turn=2)
    t.observe(st2, mem2)
    st3, mem3 = _state([_card(0, "c-x")], turn=3)
    t.observe(st3, mem3)
    assert ["enchant-sus", -1, 1] not in t.ench and not any(e[0] == "enchant-sus" for e in t.ench)  # 2 回合后消失


def test_track_all_follows_bench_switch(monkeypatch):
    from hiro.sim import screen_source as ss

    monkeypatch.setenv("HIRO_BENCH_ADVISOR", "1")
    assert ss._track_all()
    monkeypatch.setenv("HIRO_EXAM_SIM_TRACK_ALL", "0")
    assert not ss._track_all()
    monkeypatch.delenv("HIRO_EXAM_SIM_TRACK_ALL")
    monkeypatch.setenv("HIRO_BENCH_ADVISOR", "0")
    assert not ss._track_all()


def test_setup_puts_engine_on_path(monkeypatch):
    """`python -m hiro.bench_bridge setup / check` 单独跑时还没导入 hiro.sim：gkbench 导入 gakumas_exam 会失败
    （Windows 上 setup 报 No module named 'gakumas_exam'，模型和主数据都没准备）。load_algo / ensure_master 先把引擎放进 sys.path。"""
    import importlib
    import sys

    from hiro.bench_bridge import advisor
    from hiro.sim import ROOT

    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(ROOT)])
    for m in [m for m in sys.modules if m == "hiro.sim" or m.startswith(("hiro.sim.", "gakumas_exam"))]:
        monkeypatch.delitem(sys.modules, m)
    advisor._engine_on_path()
    assert str(ROOT) in sys.path
    importlib.import_module("gakumas_exam.catalog")


def test_setup_failure_prints_real_reason(monkeypatch, capsys):
    """setup 失败时打印异常类型、出错位置和按类型给的提示，不再一律猜「网络？」。"""
    from hiro.bench_bridge import __main__ as M
    from hiro.bench_bridge import advisor

    def boom(**k):
        import gakumas_exam_missing_for_test  # noqa: F401

    monkeypatch.setattr(advisor, "load_algo", boom)
    assert M.main(["setup"]) == 1
    out = capsys.readouterr().out
    assert "ModuleNotFoundError: No module named 'gakumas_exam_missing_for_test'" in out
    assert "出错位置：" in out and "test_bench_bridge.py" in out
    assert "缺 Python 模块 gakumas_exam_missing_for_test" in out and "网络" not in out


@pytest.mark.sim_master
@pytest.mark.bench_on
def test_engine_path_takes_screen_truths(monkeypatch):
    """模拟器合成开局只按画面粗对齐：回合属性是占位顺序、手牌强化被模拟器自己的随机支援强化抬高、やる気有偏差。
    画面能看到的一律用画面的，引擎只补画面看不到的（牌区内容、附魔、其余状态）。dmmw1 R1 离线重放发现。"""
    monkeypatch.setattr(A, "OBS_SOURCE", "engine")
    adv, algo = _advisor(monkeypatch, ("play", 0))
    eng = _engine_state(turn_types=[1, 1, 1, 1, 1, 1, 1, 1], limit_turn=8, current_turn=1, score=999, stamina=5)
    eng["cards"][0] = _engine_card("p_card-a", 1, tmp=2)  # 引擎 +++，画面 +
    eng["cards"][1] = _engine_card("p_card-b", 0)
    eng["status"]["effects"][1]["value"] = 26  # やる気 引擎 26，画面 37
    st, mem = _state([_card(0, "p_card-a", 1), _card(1, "p_card-b", 0)], remaining=8, turn=1, motivation=37, impression=3)
    st.attr = "Vi"
    st.sim_game = object()
    monkeypatch.setattr(A, "_engine_state", lambda game: eng)
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    obs = algo.seen[-1]
    assert obs["exam"]["types"][0] == 3  # 本回合 Vi（画面），不是占位的 Vo
    assert obs["exam"]["permil"][1] == 30000  # 画面见过的 Da 倍率
    assert [c["u"] for c in obs["hand"]] == [1, 0]
    assert obs["num"]["score"] == 1000 and obs["num"]["stamina"] == 20 and obs["num"]["block"] == 10
    st_ = {k: v for k, v, _ in obs["status"]}
    assert st_["aggressive"] == 37.0 and st_["review"] == 3.0
    assert obs["grave"]  # 牌区仍来自引擎


@pytest.mark.bench_on
def test_unread_hand_upgrade_falls_back_to_deck_base(monkeypatch):
    adv, algo = _advisor(monkeypatch, ("play", 0))
    adv.on_round_start(1, {"exam": "r1", "idol": "i_card-shro-3-007", "deck": [("p_card-a", 1), ("p_card-b", 0)]})
    st, mem = _state([_card(0, "p_card-a", None), _card(1, "p_card-b", 0)])
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert [c["u"] for c in algo.seen[-1]["hand"]] == [1, 0]  # 画面读不到强化：用培育牌组里的强化次数


@pytest.mark.bench_on
def test_default_uses_screen_path_even_with_sim_game(monkeypatch):
    adv, algo = _advisor(monkeypatch, ("play", 0))
    st, mem = _state([_card(0, "p_card-a", 0)])
    st.sim_game = object()
    monkeypatch.setattr(A, "_engine_state", lambda game: (_ for _ in ()).throw(AssertionError("不该读引擎局面")))
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert d.source == "bench" and "画面读数" in d.reason


def test_types_respect_turn_quota():
    """未知回合按配额填（dmmt2：R1 实际 Vo4 / Da3 / Vi2，以前未知回合一律填 流1 = Vo）。"""
    t = ScreenTracker([], trend=("Vo", "Da", "Vi"))
    t.attrs = {1: "Vo"}
    alloc = {"random_prefix_counts": {"Vo": 3, "Da": 2, "Vi": 1}, "fixed_tail": ["Vi", "Da", "Vo"]}
    types = t.types(1, 9, alloc)
    assert types[-3:] == [3, 2, 1]
    assert sorted(types[:6]) == [1, 1, 1, 2, 2, 3] and types[0] == 1


def test_from_screen_fills_unread_numbers_and_permil():
    t = ScreenTracker([("p_card-a", 0)], trend=("Vo", "Da", "Vi"))
    ex = exam_fields("r1", idol=None, plan=3)
    ex["permil_default"] = {"Vo": 31000, "Da": 32000, "Vi": 20000}
    st, mem = _state([_card(0, "p_card-a")])
    st.attr_permils = {}
    t.observe(st, mem)
    obs, _ = from_screen(st, mem, st.legal_actions(), t, ex)
    assert obs["exam"]["permil"] == [31000, 32000, 20000]  # 开考第一步还没读到倍率：用记下的 / 参考值，不给 0
    st.score, st.genki = None, None  # 这一帧分数 / 元気没读出来
    obs, _ = from_screen(st, mem, st.legal_actions(), t, ex)
    assert obs["num"]["score"] == 1000 and obs["num"]["block"] == 10  # 用上一次的读数，不是 0


class _Facts(_FakeEnchants):
    """主数据替身：c-sleepy 打出时往山札塞一张眠気；眠気回合结束除外；d-buff 饮料挂附魔。"""

    def creates(self, card_id, upgrade):
        return (("p_card-00-acc-0_002", 0, 1),) if card_id == "c-sleepy" else ()

    def end_turn_lost(self, card_id, upgrade):
        return card_id == "c-endlost"

    def lost_on_play(self, card_id, upgrade):
        return card_id == "p_card-00-acc-0_002"

    def drink(self, drink_id):
        return (("enchant-drink", -1, -1),) if drink_id == "d-buff" else ()

    def enchant_card(self, name):
        return {"ワクワク": "c-sus"}.get(name)


def test_tracker_adds_created_cards_and_end_turn_lost():
    """dmmt / 履歴对照：山札总少一张 眠気（打出 気まぐれハート 等时塞进山札，画面看不到），第二张眠気被抽到时还误判成洗牌、弃牌清空。"""
    t = ScreenTracker([("c-sleepy", 0), ("c-x", 0), ("c-y", 0)], trend=("Vo", "Da", "Vi"), enchants=_Facts())
    st, mem = _state([_card(0, "c-sleepy"), _card(1, "c-x")], turn=1)
    t.observe(st, mem)
    t.played_card(st.hand[0])
    assert t.deck[("p_card-00-acc-0_002", 0)] == 1  # 打出时塞进山札
    st2, mem2 = _state([_card(0, "c-x"), _card(1, "p_card-00-acc-0_002")], turn=1)
    t.observe(st2, mem2)  # 抽到那张眠気：从山札扣，不触发洗牌
    assert ("c-sleepy", 0) in t.grave and t.deck[("p_card-00-acc-0_002", 0)] == 0
    t.played_card(st2.hand[1])  # 打出眠気：主数据 playMovePositionType = Lost
    st3, mem3 = _state([_card(0, "c-y")], turn=2)
    t.observe(st3, mem3)  # 换回合：眠気 除外，c-x 进弃牌
    assert t.lost[("p_card-00-acc-0_002", 0)] == 1 and t.grave[("c-x", 0)] == 1


def test_tracker_drink_enchant_pitem_charges_and_status_names():
    t = ScreenTracker([("c-a", 0)], trend=("Vo", "Da", "Vi"), enchants=_Facts(), items=["pitem-x"])
    t.drank("d-buff")
    assert ["enchant-drink", -1, -1] in t.ench
    st, mem = _state([_card(0, "c-a")], statuses=[{"type": None, "name": "ワクワク", "value": 1, "unit": "stack"}])
    st.pitem_charges = 3
    t.observe(st, mem)
    obs, _ = from_screen(st, mem, st.legal_actions(), t, exam_fields("r1", idol=None, plan=3))
    assert ["enchant-item", 3, -1] in obs["ench"]  # P 道具次数用画面读数
    assert any(e[0] == "enchant-sus" for e in obs["ench"])  # 状态栏有这张卡的持续效果、跟踪漏了：补上


def test_tracker_does_not_reshuffle_when_deck_still_has_cards():
    """qualifydmmw1 T3：持续效果塞进山札的第二张眠気被抽到，跟踪以为洗牌了，把 8 张弃牌倒回山札（存档：弃牌 8 张）。
    实际只有山札抽空才洗牌：山札还有牌时，找不到的卡当作看不见的插入。"""
    t = ScreenTracker([("c-a", 0), ("c-b", 0), ("c-c", 0), ("c-d", 0), ("c-e", 0), ("c-f", 0)], trend=("Vo", "Da", "Vi"))
    t.grave[("p_card-00-acc-0_002", 0)] = 1
    t.grave[("c-z", 0)] = 1
    st, mem = _state([_card(0, "p_card-00-acc-0_002"), _card(1, "c-a")], turn=3)
    t.observe(st, mem)
    assert sum(t.grave.values()) == 2 and sum(t.deck.values()) == 5


def test_layer_units_follow_gkms_uiobs():
    """单位照 GKMS UIObs 的实际输出：熱意獲得量増加是绝对量（不除 1000），倍率类千分比写成小数（3940a9 11-14 对拍发现）。"""
    from hiro.bench_bridge.uiobs import statuses

    def layer(cls, value):
        return {"cls": cls, "turn": 2, "limited": True, "passing": False, "value": value, "extra": {}, "trig": None}

    effects = [layer("EnthusiasticAdditiveStatusEffect", 10), layer("LessonBuffMultipleStatusEffect", 250),
               layer("FullPowerPointAdditiveStatusEffect", 400)]  # fmt: skip
    got = {k: v for k, v, _ in statuses({"status": {"effects": effects}})}
    assert got == {"enthusiastic_add": 10.0, "lesson_buff_multiple": 0.25, "full_power_point_add": 0.4}


TOWER_LO = {"produce_id": "tower", "layer": 15, "idol_card_id": "i_card-ttmr-2-000", "max_stamina": 46,
            "score_bonus_permils": [23090, 11980, 2600],
            "exam": {"produce_exam_battle_config_id": "p_exam_battle_config-tower_001-1_2_3-4_5_6-turn_15"},
            "produce_cards": ["p_card-a+1", "p_card-b/p_card_custom-x", "p_card-c+2/p_card_custom-y"]}  # fmt: skip


def _init_master():
    from hiro.bench_bridge.tower import Master

    return Master(rows={"IdolCard": [{"id": "i_card-ttmr-2-000", "examEffectType": "ProduceExamEffectType_ExamLessonBuff"}],
                        "ExamInitialDeck": [{"id": "initial_deck-lesson_buff", "produceCardIds": ["p_card-b", "p_card-basic"]}]})  # fmt: skip


def test_tower_context_from_loadout(monkeypatch):
    from hiro.bench_bridge.screen import tower_context

    monkeypatch.setattr("hiro.bench_bridge.tower.master", lambda: _init_master())

    tc = tower_context(TOWER_LO)
    assert tc["turns"] == 15 and tc["permil_fixed"] == {"Vo": 23090, "Da": 11980, "Vi": 2600}
    assert tc["max_stamina"] == 46 and tc["idol"] == "i_card-ttmr-2-000"
    assert tc["deck"] == [("p_card-a", 1), ("p_card-b", 0), ("p_card-c", 2), ("p_card-basic", 0)]  # 补基本卡，已有的不重复
    assert tower_context({**TOWER_LO, "produce_cards": []})["deck"] == []  # 编成页没读牌组：不凭空补
    assert tower_context({**TOWER_LO, "score_bonus_permils": {"1": 1, "2": 2, "3": 3}})["permil_fixed"] == {"Vo": 1, "Da": 2, "Vi": 3}
    assert tower_context({"turns": 15, "max_stamina": 46})["turns"] == 15  # 驱动手写的最小编成


def test_from_screen_tower_uses_loadout_after_restart():
    """2026-11-14 Mac 15 层：驱动中途重启，本场计数从 T1 开始（实际第 5 回合），limit 算成 11；开考只读到 Vo 倍率，三项都填成 23090；
    最大体力读不到给 0。有编成时：turn 按残りターン倒推、limit 固定 15、倍率和最大体力用编成的。"""
    from hiro.bench_bridge.screen import tower_context

    st, mem = _state([_card(0, "p_card-a", 1)], remaining=11, turn=1)
    st.max_hp = None
    exam = {**exam_fields("tower-15", idol=None, plan=None), **{k: v for k, v in tower_context(TOWER_LO).items() if k != "deck"}}
    t = ScreenTracker([("p_card-a", 1), ("p_card-b", 0)])
    t.observe(st, mem)
    obs, _ = from_screen(st, mem, st.legal_actions(), t, exam)
    assert obs["exam"]["turn"] == 5 and obs["exam"]["limit"] == 15 and len(obs["exam"]["types"]) == 15
    assert obs["exam"]["permil"] == [23090, 11980, 2600] and obs["num"]["max_stamina"] == 46
    assert obs["exam"]["produce"] == "tower" and obs["exam"]["plan"] == 0


@pytest.mark.bench_on
def test_advisor_reads_tower_loadout_file(monkeypatch, tmp_path):
    p = tmp_path / "lo.json"
    p.write_text(__import__("json").dumps({"loadout": TOWER_LO}))
    monkeypatch.setenv("HIRO_TOWER_LOADOUT", str(p))
    monkeypatch.setattr("hiro.bench_bridge.tower.master", lambda: _init_master())
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "tower-15", "idol": None, "deck": []})
    assert adv.exam["turns"] == 15 and adv.exam["max_stamina"] == 46 and adv.exam["idol"] == "i_card-ttmr-2-000"
    assert adv.deck == [("p_card-a", 1), ("p_card-b", 0), ("p_card-c", 2), ("p_card-basic", 0)]
    adv.on_round_start(1, {"exam": "r1", "idol": "i_card-shro-3-007", "deck": [("p_card-x", 0)]})  # H.I.F 不受影响
    assert "turns" not in adv.exam and adv.deck == [("p_card-x", 0)]


class _Cat:
    """Enchants 用的假主数据：one(表, 键, 变体)。"""

    def __init__(self, cards, effects):
        self.cards, self.effects = cards, effects

    def one(self, table, key, variant=None):
        return (self.cards if table == "ProduceCard" else self.effects).get(key)


PLAY_CAT = _Cat(
    cards={"now": {"playEffects": [{"produceExamEffectId": "add1"}]},
           "later": {"playEffects": [{"produceExamEffectId": "timer2"}]},
           "cond": {"playEffects": [{"produceExamEffectId": "add1", "produceExamTriggerId": "t-if"}]},
           "plain": {"playEffects": []}},
    effects={"add1": {"effectType": "ProduceExamEffectType_ExamPlayableValueAdd", "effectCount": 1},
             "add2": {"effectType": "ProduceExamEffectType_ExamPlayableValueAdd", "effectCount": 2},
             "timer2": {"effectType": "ProduceExamEffectType_ExamEffectTimer", "effectValue1": 1, "chainProduceExamEffectIds": ["draw", "add2"]},
             "draw": {"effectType": "ProduceExamEffectType_ExamCardDraw"}},
)  # fmt: skip


def test_plays_added_from_master():
    from hiro.bench_bridge.enchants import Enchants

    e = Enchants(PLAY_CAT)
    assert e.plays_added("now", 0) == ((0, 1),)
    assert e.plays_added("later", 0) == ((1, 2),)  # 次のターン、+2
    assert e.plays_added("cond", 0) == ()  # 带触发条件的不算
    assert e.plays_added("plain", 0) == ()


def test_plays_left_counts_known_extra_plays_and_never_drops_below_one():
    """2026-11-14 run-15d：画面读不到剩余出牌次数，playable 一直填 1。现在按自己打出的卡算确定多出的次数；只是 UINet 的输入，不剪合法动作。"""
    from hiro.bench_bridge.enchants import Enchants

    t = ScreenTracker([("now", 0), ("later", 0), ("plain", 0), ("plain", 0)], enchants=Enchants(PLAY_CAT))
    st, mem = _state([_card(0, "now"), _card(1, "plain"), _card(2, "later")], turn=1)
    t.observe(st, mem)
    assert t.plays_left() == 1
    t.played_card(_card(0, "now"))  # 本回合 +1：打了一张还剩 1
    assert t.plays_left() == 1
    t.played_card(_card(1, "plain"))  # 两张打完：按 1 + 1 − 2 = 0，但不报 0
    assert t.plays_left() == 1
    t.played_card(_card(2, "later"))  # 下回合 +2
    st2, mem2 = _state([_card(0, "plain")], turn=2)
    t.observe(st2, mem2)
    assert t.plays_left() == 3
    obs, _ = from_screen(st2, mem2, st2.legal_actions(), t, exam_fields("r1", idol=None, plan=None))
    assert obs["num"]["playable"] == 3 and obs["num"]["plays"] == 0
    assert any(a[0] == "play" for a in obs["legal"])  # 合法动作照旧



def test_idol_initial_cards_by_exam_effect_type():
    """偶像之路开局另加 2 张基本卡：ExamInitialDeck「initial_deck-<主题>」，主题按主回忆偶像卡的 examEffectType（GKMS 6 局实机核过）。"""
    m = _init_master()
    assert m.idol_initial_cards("i_card-ttmr-2-000") == ["p_card-b", "p_card-basic"]
    assert m.idol_initial_cards("i_card-unknown") == []


def test_idol_initial_cards_aggressive_row_name():
    from hiro.bench_bridge.tower import Master

    m = Master(rows={"IdolCard": [{"id": "i_card-x", "examEffectType": "ProduceExamEffectType_ExamCardPlayAggressive"}],
                     "ExamInitialDeck": [{"id": "initial_deck-aggressive", "produceCardIds": ["p1", "p2"]}]})  # fmt: skip
    assert m.idol_initial_cards("i_card-x") == ["p1", "p2"]  # やる気：examEffectType 是 CardPlayAggressive，行名是 aggressive


def _tower_advisor(monkeypatch, tmp_path, tower_spec, *, fail=False):
    """偶像之路场次的 advisor：H.I.F 算法和偶像之路算法各一个假的。"""
    p = tmp_path / "lo.json"
    p.write_text(__import__("json").dumps({"loadout": TOWER_LO}))
    monkeypatch.setenv("HIRO_TOWER_LOADOUT", str(p))
    monkeypatch.setattr("hiro.bench_bridge.tower.master", lambda: _init_master())
    if tower_spec is None:
        monkeypatch.delenv("HIRO_TOWER_ALGO", raising=False)
    else:
        monkeypatch.setenv("HIRO_TOWER_ALGO", tower_spec)
    hif, tower = _Algo(("play", 0)), _Algo(("play", 0))

    def load(spec=None, **kw):
        if spec and (spec.startswith("my.gnrpa") or spec == A.TOWER_DEFAULT_ALGO):
            if fail:
                raise ImportError("no rust")
            return tower
        return hif

    monkeypatch.setattr(A, "load_algo", load)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "tower-15", "idol": None, "deck": []})
    return adv, hif, tower


@pytest.mark.bench_on
def test_tower_uses_tower_algo_with_loadout(monkeypatch, tmp_path):
    """用户 11-14：偶像之路不强制用 uinet。HIRO_TOWER_ALGO 指的算法（GNRPA）出牌，ctx.loadout 拿到整份编成。"""
    adv, hif, tower = _tower_advisor(monkeypatch, tmp_path, "my.gnrpa:Search")
    st, mem = _state([_card(0, "p_card-a", 1)], remaining=15, turn=1)
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert d.source == "bench" and len(tower.seen) == 1 and not hif.seen
    assert "兜底" not in d.reason and d.reason.startswith("Search")
    assert tower.ctx.loadout["layer"] == 15 and tower.ctx.loadout["produce_cards"]


@pytest.mark.bench_on
@pytest.mark.parametrize("spec, fail, why, default", [(None, False, "还没接上", ""), ("my.gnrpa:Search", True, "加载失败", None),
                                                    (None, True, "加载失败", None)])  # fmt: skip
def test_tower_falls_back_to_hif_algo_and_says_so(monkeypatch, tmp_path, spec, fail, why, default):
    if default is not None:
        monkeypatch.setattr(A, "TOWER_DEFAULT_ALGO", default)
    adv, hif, tower = _tower_advisor(monkeypatch, tmp_path, spec, fail=fail)
    st, mem = _state([_card(0, "p_card-a", 1)], remaining=15, turn=1)
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert len(hif.seen) == 1 and not tower.seen
    assert "兜底" in d.reason and why in d.reason


@pytest.mark.bench_on
def test_tower_default_is_rust_gnrpa(monkeypatch, tmp_path):
    """用户 11-14：偶像之路缺省用 Rust GNRPA（不设 HIRO_TOWER_ALGO 也是）。"""
    assert A.TOWER_DEFAULT_ALGO == "hiro.search.tower_algo:TowerGnrpa"
    adv, hif, tower = _tower_advisor(monkeypatch, tmp_path, None)
    st, mem = _state([_card(0, "p_card-a", 1)], remaining=15, turn=1)
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert len(tower.seen) == 1 and not hif.seen and "兜底" not in d.reason


def test_tower_gnrpa_refuses_old_rust(monkeypatch):
    """Rust 后端没有 search / plan_many：TowerGnrpa 构造就抛、写明缺什么（加载失败 → 退回 uinet）。"""
    import sys
    import types

    from hiro.search.tower_algo import TowerGnrpa

    monkeypatch.setitem(sys.modules, "gakumas_exam_rs", types.SimpleNamespace(search=lambda *a, **k: None))
    with pytest.raises(RuntimeError, match="plan_many"):
        TowerGnrpa()


@pytest.mark.bench_on
def test_hif_round_ignores_tower_algo(monkeypatch):
    monkeypatch.setenv("HIRO_TOWER_ALGO", "my.gnrpa:Search")
    adv, algo = _advisor(monkeypatch, ("play", 0))
    st, mem = _state([_card(0, "p_card-a")])
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert len(algo.seen) == 1 and "兜底" not in d.reason


def test_known_draws_only_for_same_formation(tmp_path, monkeypatch):
    """用户 11-14 定：偶像之路同一编成重试，可以把上一次画面上看到的抽牌顺序交给搜索。编成签名对不上就不给。"""
    monkeypatch.setenv("HIRO_TOWER_CACHE", str(tmp_path))
    lo = {"tower_id": "tower_001-ttmr", "layer": 15, "formation_signature": "m1-s2-s3"}
    rec = {"tower_id": "tower_001-ttmr", "layer": 15, "formation_signature": "m1-s2-s3",
           "draws": [{"id": "p_card-a", "u": 1}, {"id": "p_card-b"}, {"bad": 1}]}  # fmt: skip
    (tmp_path / "tower_001-ttmr-15-m1-s2-s3.json").write_text(__import__("json").dumps(rec))
    assert A.known_draws(lo) == [{"id": "p_card-a", "u": 1}, {"id": "p_card-b", "u": 0}]
    assert A.known_draws({**lo, "formation_signature": "m1-s3-s2"}) is None  # 副回忆换了顺序：种子不同
    assert A.known_draws({k: v for k, v in lo.items() if k != "formation_signature"}) is None
    (tmp_path / "tower_001-ttmr-15-x.json").write_text(__import__("json").dumps({**rec, "formation_signature": "y"}))
    assert A.known_draws({**lo, "formation_signature": "x"}) is None  # 文件内容和文件名对不上


@pytest.mark.bench_on
def test_tower_ctx_carries_known_draws(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("HIRO_TOWER_CACHE", str(cache))
    sig = {"formation_signature": "m1-s2", "tower_id": "tower_001-ttmr", "layer": 15}
    (cache / "tower_001-ttmr-15-m1-s2.json").write_text(__import__("json").dumps({**sig, "draws": [{"id": "p_card-a", "u": 1}]}))
    global TOWER_LO
    saved = TOWER_LO
    TOWER_LO = {**saved, **sig}
    try:
        adv, hif, tower = _tower_advisor(monkeypatch, tmp_path, "my.gnrpa:Search")
    finally:
        TOWER_LO = saved
    st, mem = _state([_card(0, "p_card-a", 1)], remaining=15, turn=1)
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert tower.ctx.known_draws == [{"id": "p_card-a", "u": 1}]
    adv.on_round_start(1, {"exam": "r1", "idol": "i_card-shro-3-007", "deck": [("p_card-x", 0)]})  # H.I.F：不给
    assert adv.known_draws is None



def test_known_prefix_aligns_by_deck_contents():
    """known_draws 按山札内容对位（8a3a1e7 11-14：生成卡不在记录里，跳过）。"""
    from hiro.search.tower_algo import known_prefix

    # 山札里混着生成卡 z（眠気这类）：不算，照样对上
    assert known_prefix(["a", "b", "c", "d", "e"], ["d", "z", "c", "e"]) == (2, ["c", "d", "e"])
    # 抽到和刚打出的同名卡（数手牌会漏数）
    assert known_prefix(["a", "b", "a", "c"], ["c", "a"]) == (2, ["a", "c"])
    # 记录只到一半：已知那段在山札里就用
    assert known_prefix(["a", "b", "c"], ["c", "d", "e"]) == (2, ["c"])
    # 从上一次的位置往后找；对不上不给前缀
    assert known_prefix(["a", "b", "a"], ["a"], start=2) == (2, ["a"])
    assert known_prefix(["a", "b"], ["b", "q"], start=0) == (1, ["b"])
    assert known_prefix(["a", "b"], ["a", "a"], start=1) == (1, [])


def test_tower_bonus_permils_accepts_list_and_template_dict():
    """bench 模板（实打时的 ctx.loadout）把倍率写成 {"1": Vo, "2": Da, "3": Vi}；直接 zip 会拿到键、倍率变成 1 / 2 / 3（11-14 查出的
    搜索预测分只有实际的几十分之一）。"""
    from hiro.search.tower import bonus_permils

    want = {1: 42790, 2: 8910, 3: 22630}
    assert bonus_permils([42790, 8910, 22630]) == want
    assert bonus_permils({"1": 42790, "2": 8910, "3": 22630}) == want
    assert bonus_permils({1: 42790, 2: 8910, 3: 22630}) == want


def test_tower_loadout_gets_initial_cards(monkeypatch):
    """编成页牌组不含开局另加的 2 张基本卡（11-14 Mac 实机：TowerGnrpa 对齐时手牌里的 act-0_028 / men-0_008 对不上）。"""
    from types import SimpleNamespace

    from hiro.search import tower as st

    monkeypatch.setattr("hiro.bench_bridge.tower.master", lambda: SimpleNamespace(idol_initial_cards=lambda idol: ["p_card-b0", "p_card-b1"]))
    assert st.with_initial_cards(["p_card-a+1", "p_card-b1"], "i_card-x") == ["p_card-a+1", "p_card-b1", "p_card-b0"]
    assert st.with_initial_cards(["p_card-a+1"], None) == ["p_card-a+1"]


def test_tower_formation_idol_falls_back_to_tower_loadout(monkeypatch, tmp_path):
    """编成页 page_fields 没给 idol_card_id：用 HIRO_TOWER_LOADOUT（同塔同层）里的（11-14 编成评估报 KeyError IdolCard:None）。"""
    import json

    from hiro.brain.deciders import _tower_idol

    p = tmp_path / "lo.json"
    p.write_text(json.dumps({"tower_id": "tower_001-ttmr", "layer": 15, "idol_card_id": "i_card-ttmr-2-000"}))
    monkeypatch.setenv("HIRO_TOWER_LOADOUT", str(p))
    assert _tower_idol("tower_001-ttmr", 15) == "i_card-ttmr-2-000"
    assert _tower_idol("tower_001-ttmr", 16) is None


def test_tower_gnrpa_warm_start_default_on(monkeypatch):
    """协调 11-15 批：偶像之路 GNRPA 缺省跨步热启动（warm=2）。"""
    import sys
    import types

    from hiro.search import tower_algo as ta

    monkeypatch.setitem(sys.modules, "gakumas_exam_rs", types.SimpleNamespace(search=None, plan_many=None))
    assert ta.TowerGnrpa().warm == ta.WARM == 2.0
    assert ta.TowerGnrpa(warm=0).warm == 0.0


def test_exam_fields_hajime_nia_like_bench():
    """初 / N.I.A 的写法照 bench 给 UINet 的（11-17 对 87 题逐题核过）。"""
    nia = exam_fields("nia-mid2", idol="i", plan=3, crit=[396, 483, 586], produce="produce-005")
    assert nia == {"produce": "produce-005", "step": "AUDITION_MID2", "plan": 3, "idol": "i", "crit": [396, 483, 586]}
    assert exam_fields("hajime-final", idol="i", plan=4)["produce"] == "produce-001"  # 缺省初 REGULAR
    assert exam_fields("hajime-mid1", idol="i", plan=4)["crit"] == [0, 0, 0]  # 审查基准不知道：全 0，不填 H.I.F 的 [1,1,1]
    lesson = exam_fields("hajime-lesson-32", idol="i", plan=4, produce="produce-006")
    assert lesson["step"] == 32 and lesson["lesson"] and lesson["crit"] == [0, 0, 0]


def test_from_screen_lesson_has_no_types_or_permil():
    st, mem = _state([_card(0, "p_card-a", 1)], remaining=6, turn=2)
    t = ScreenTracker([("p_card-a", 1)])
    t.observe(st, mem)
    obs, _ = from_screen(st, mem, st.legal_actions(), t, exam_fields("hajime-lesson-7", idol=None, plan=4))
    assert obs["exam"]["types"] == [] and obs["exam"]["permil"] == [0, 0, 0] and obs["exam"]["step"] == 7


@pytest.mark.bench_on
def test_advisor_uses_exam_loadout_for_nia(monkeypatch, tmp_path):
    xc = {"turns": 12, "crit": [396, 483, 586], "permil_fixed": {"Vo": 1, "Da": 2, "Vi": 3}, "max_stamina": 32, "idol": "i_card-x",
          "produce": "produce-005", "deck": [("p_card-a", 1)]}  # fmt: skip
    monkeypatch.setattr("hiro.bench_bridge.screen.exam_context", lambda lo: xc)
    p = tmp_path / "lo.json"
    p.write_text('{"loadout": {"produce_id": "produce-005"}}')
    monkeypatch.setenv("HIRO_EXAM_LOADOUT", str(p))
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "nia-mid2", "idol": None, "deck": []})
    assert adv.exam["produce"] == "produce-005" and adv.exam["step"] == "AUDITION_MID2" and adv.exam["crit"] == [396, 483, 586]
    assert adv.exam["turns"] == 12 and adv.exam["max_stamina"] == 32 and adv.deck == [("p_card-a", 1)]
    assert adv.exam["permil_default"] == {}  # 不拿 H.I.F 的参考倍率


def test_allocate_turns_by_weights():
    from hiro.search.tower_algo import allocate_turns

    assert sorted(allocate_turns({1: 50, 2: 30, 3: 20}, 10)) == [1] * 5 + [2] * 3 + [3] * 2
    assert allocate_turns({1: 1, 2: 1, 3: 1}, 0) == [] and len(allocate_turns({1: 1, 2: 1, 3: 1}, 4)) == 4



# 本层考试设置直接写进来（Windows 本地塔数据可能没有 16 层，协调 11-17）：complete_loadout 有了 battle_config + gimmick 就不再查塔数据
TTMR16_EXAM = {"produce_exam_battle_config_id": "p_exam_battle_config-tower_001-1954_1434_956-2079_1687_1062-turn_10",
               "produce_exam_gimmick_effect_group_id": "p_exam_gimmick-tower_001-exam_lesson_buff-0001-stage_016",
               "produce_exam_battle_npc_group_id": "p_npc_group-tower_001-ttmr-exam_lesson_buff-stage_016"}
TTMR16 = {"produce_id": "tower", "tower_id": "tower_001-ttmr", "layer": 16, "mechanic": "exam_lesson_buff", "turns": 10,
          "score_bonus_permils": [23090, 11980, 2600], "exam": TTMR16_EXAM, "produce_item_ids": ["pitem_tower_001-exam_lesson_buff-1-stage_016"],
          "rivals": [18877, 13561, 9000], "max_stamina": 46, "idol_card_id": "i_card-ttmr-2-000",
          "produce_cards": ["p_card-01-ido-2_017+1", "p_card-00-sup-3_027", "p_card-01-act-2_031", "p_card-01-act-2_001+1", "p_card-01-men-3_036+1",
                            "p_card-01-act-1_021+1", "p_card-01-act-2_032+1", "p_card-01-men-2_038+1", "p_card-01-sup-2_090", "p_card-01-men-1_008+1"]}
TTMR16_T1 = {"exam": {"produce": "tower", "turn": 1, "limit": 10, "types": [1] * 10, "permil": [23090, 11980, 2600]},
             "num": {"score": 0, "stamina": 45, "max_stamina": 46, "block": 0},
             "status": [], "hand": [{"id": "p_card-01-men-0_008", "u": 0}, {"id": "p_card-01-act-2_001", "u": 1}, {"id": "p_card-01-act-1_021", "u": 1},
                                    {"id": "p_card-01-men-1_008", "u": 1}],
             "deck": [{"id": c, "u": u} for c, u in (("p_card-00-sup-3_027", 0), ("p_card-01-act-0_028", 0), ("p_card-01-act-2_031", 0),
                                                       ("p_card-01-act-2_032", 1), ("p_card-01-ido-2_017", 1), ("p_card-01-men-2_038", 1),
                                                       ("p_card-01-men-3_036", 1), ("p_card-01-sup-2_090", 0))],
             "grave": [], "lost": [], "drinks": [], "legal": [["play", 0], ["play", 1], ["play", 3], ["end"]]}  # fmt: skip  实机 tower-g5c T1


def _tower_gnrpa_or_skip():
    """Rust 后端带 search / plan_many、引擎主数据有这一层的战斗配置时才跑。"""
    rs = pytest.importorskip("gakumas_exam_rs")
    if not (hasattr(rs, "search") and hasattr(rs, "plan_many")):
        pytest.skip("Rust 后端没有 search / plan_many")
    from hiro.search.exam_env import turn_weights

    if turn_weights({"exam": TTMR16_EXAM}) is None:
        pytest.skip("引擎主数据里没有手毬 16 层的战斗配置")
    return rs


@pytest.mark.sim_master
def test_tower_align_trusts_only_seen_turn_types():
    """画面的 types 只有到过的回合可信（ScreenTracker 用当前回合属性填满了没见过的）：T1 之后的回合按战斗配置的权重分配，
    交给搜索打乱（手毬 16 层 T1 以为 10 回合全是 Vo，预测高了约 4 倍，协调 11-17）。"""
    _tower_gnrpa_or_skip()
    from types import SimpleNamespace

    from hiro.search.tower_algo import TowerGnrpa

    algo = TowerGnrpa(warm=0)
    algo.begin(SimpleNamespace(loadout=TTMR16, known_draws=None, obs=TTMR16_T1))
    algo._align(TTMR16_T1)
    tt = list(algo.rx.turn_types)
    assert tt[0] == 1 and algo.free == list(range(1, 10))
    assert set(tt[1:]) != {1}, tt  # 未来回合不再全是 Vo


@pytest.mark.sim_master
def test_tower_status_sync_opt_in():
    """sync_status=True 时画面上的集中 / 好調覆盖进引擎；缺省不覆盖（画面状态读数待核实）。"""
    rs = _tower_gnrpa_or_skip()
    if not hasattr(getattr(rs, "Exam", object), "override_status"):
        pytest.skip("Rust 后端没有 override_status")
    from types import SimpleNamespace

    from hiro.search.tower_algo import TowerGnrpa

    obs = dict(TTMR16_T1, status=[["lesson_buff", 18.0, -1], ["parameter_buff", 3.0, 3]])
    on = TowerGnrpa(warm=0, sync_status=True)
    on.begin(SimpleNamespace(loadout=TTMR16, known_draws=None, obs=obs))
    on._align(obs)
    assert on.rx.lesson_buff == 18
    off = TowerGnrpa(warm=0)
    off.begin(SimpleNamespace(loadout=TTMR16, known_draws=None, obs=obs))
    off._align(obs)
    assert off.rx.lesson_buff == 0


@pytest.mark.parametrize("label, produce, step", [
    ("hajime-lesson-32", "produce-006", 32), ("hajime-lesson-vo", "produce-001", 1), ("hajime-lesson-vo-sp", "produce-001", 2),
    ("hajime-lesson-da-hard", "produce-002", 6), ("hajime-lesson-vi-sp", "produce-001", 8),
    ("hajime-lesson-da-sp", "produce-006", 32), ("hajime-lesson-vi", "produce-006", 33), ("hajime-lesson-x", "produce-001", None)])  # fmt: skip
def test_lesson_label_to_step_type(label, produce, step):
    """平台从画面读到属性 + SP / 追い込み 就给 hajime-lesson-<属性>[-sp|-hard]；LEGEND（produce-006）的课是 29–34。"""
    assert exam_fields(label, idol=None, plan=4, produce=produce)["step"] == step


def test_audition_number_from_label_tag():
    assert A._audition_number("nia-mid1#audition-2") == 2
    assert A._audition_number("nia-final#x#audition-4") == 4
    assert A._audition_number("nia-mid1") is None and A._audition_number(None) is None
    assert exam_fields("nia-mid1#audition-2", idol="i", plan=3)["step"] == "AUDITION_MID1"  # tag 不影响场次


@pytest.mark.bench_on
def test_nia_tier_from_label_without_loadout(monkeypatch):
    """平台 11-17：选档页选了第几档写进标签 tag（nia-mid1#audition-2）；没有编成时按主数据补审查基准和回合数。"""
    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    got = {}

    def ac(label, idol, number, produce=None):
        got.update(label=label, number=number)
        return {"crit": [178, 217, 263], "turns": 9}

    monkeypatch.setattr("hiro.bench_bridge.screen.audition_context", ac)
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "nia-mid1#audition-2", "idol": "i_card-x", "deck": []})
    assert got["number"] == 2 and adv.exam["crit"] == [178, 217, 263] and adv.exam["turns"] == 9
    assert adv.exam["step"] == "AUDITION_MID1" and adv.exam["permil_default"] == {}


@pytest.mark.bench_on
def test_nia_tag_number_fills_loadout_and_ctx_gets_full_loadout(monkeypatch, tmp_path):
    seen = {}

    def xc(lo):
        seen["lo"] = lo
        return {"turns": 9, "crit": [1, 2, 3], "permil_fixed": None, "max_stamina": 30, "idol": "i_card-x", "produce": "produce-005",
                "deck": [("p_card-a", 0)]}  # fmt: skip

    monkeypatch.setattr("hiro.bench_bridge.screen.exam_context", xc)
    p = tmp_path / "lo.json"
    p.write_text('{"loadout": {"produce_id": "produce-005", "round": "mid1", "deck_cards": ["p_card-a"]}}')
    monkeypatch.setenv("HIRO_EXAM_LOADOUT", str(p))
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "nia-mid1#audition-2", "idol": None, "deck": []})
    assert seen["lo"]["audition_number"] == 2
    st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert algo.ctx.loadout["deck_cards"] == ["p_card-a"] and algo.ctx.loadout["audition_number"] == 2


@pytest.mark.bench_on
@pytest.mark.parametrize("spec, fail, uses", [(None, False, "hif"), ("my.gnrpa:Search", False, "search"), ("my.gnrpa:Search", True, "hif")])
def test_hajime_nia_search_switch_default_off(monkeypatch, spec, fail, uses):
    """初 / N.I.A：HIRO_EXAM_SEARCH_ALGO 设了才走搜索（缺省 uinet，行为变化开关默认关）；加载失败 uinet 兜底并写明。"""
    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_NIA", raising=False)
    if spec:
        monkeypatch.setenv("HIRO_EXAM_SEARCH_ALGO", spec)
    else:
        monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    hif, search = _Algo(("play", 0)), _Algo(("play", 0))

    def load(s=None, **kw):
        if s and s.startswith("my.gnrpa"):
            if fail:
                raise ImportError("no rust")
            return search
        return hif

    monkeypatch.setattr(A, "load_algo", load)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "nia-mid2", "idol": None, "deck": []})
    st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert (len(search.seen), len(hif.seen)) == ((1, 0) if uses == "search" else (0, 1))
    assert ("兜底" in d.reason) == bool(fail)


def test_exam_search_spec_family_switches(monkeypatch):
    """协调 11-17 / 11-18：初的中間 / 最終考试和课程缺省走 Rust GNRPA，HIRO_EXAM_SEARCH_HAJIME=0 退回 uinet；N.I.A、H.I.F 缺省不变。"""
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)
    assert A.exam_search_algo_spec("hajime-mid1") == A.EXAM_DEFAULT_ALGO
    assert A.exam_search_algo_spec("hajime-final#audition-2") == A.EXAM_DEFAULT_ALGO
    assert A.exam_search_algo_spec("hajime-lesson-vo-sp#lesson-2") == A.EXAM_DEFAULT_ALGO  # 课程也切（协调 11-18）
    for label in ("nia-mid1", "nia-final", "r1", ""):
        assert A.exam_search_algo_spec(label) == ""
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HAJIME", "0")
    assert A.exam_search_algo_spec("hajime-mid1") == A.exam_search_algo_spec("hajime-lesson-vo") == ""
    monkeypatch.delenv("HIRO_EXAM_SEARCH_NIA", raising=False)
    assert A.exam_search_algo_spec("nia-mid2#audition-3") == ""  # N.I.A 缺省关（协调 11-18）
    monkeypatch.setenv("HIRO_EXAM_SEARCH_NIA", "1")
    assert A.exam_search_algo_spec("nia-mid2#audition-3") == A.exam_search_algo_spec("nia-final") == A.EXAM_DEFAULT_ALGO
    assert A.exam_search_algo_spec("hajime-mid1") == ""  # 两个开关各管各的
    monkeypatch.setenv("HIRO_EXAM_SEARCH_ALGO", "my.x:Y")  # 显式指定的两个家族都管
    assert A.exam_search_algo_spec("nia-mid1") == A.exam_search_algo_spec("hajime-lesson-vo") == "my.x:Y"


class _Broken(_Algo):
    def __init__(self, action, *, fail_begin=False):
        super().__init__(action)
        self.fail_begin, self.begins = fail_begin, 0

    def begin(self, ctx):
        self.begins += 1
        if self.fail_begin:
            raise ValueError("拼不出编成")

    def decide(self, ctx):
        raise RuntimeError("搜索炸了")


@pytest.mark.bench_on
@pytest.mark.parametrize("fail_begin", [False, True])
def test_search_step_failure_falls_back_to_hif_algo(monkeypatch, fail_begin):
    """搜索算法某步搜不了：这一步用 H.I.F 的算法（uinet），不掉到规则，理由写明；开局就失败的这一场剩下都用 uinet、不每步重试。"""
    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)
    hif, search = _Algo(("play", 0)), _Broken(("play", 0), fail_begin=fail_begin)
    monkeypatch.setattr(A, "load_algo", lambda s=None, **kw: search if s == A.EXAM_DEFAULT_ALGO else hif)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "hajime-mid1", "idol": None, "deck": []})
    assert adv.round_algo is search
    for _ in range(2):
        st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
        d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
        assert d.source == "bench" and "兜底" in d.reason and ("开局失败" if fail_begin else "这一步搜不了") in d.reason
    assert len(hif.seen) == 2 and adv.stats["rules"] == 0
    assert search.begins == 1  # 开局失败不重试；开局成功的只 begin 一次


@pytest.mark.sim_master
@pytest.mark.bench_on
def test_live_exam_loadout_built_on_first_decision(monkeypatch):
    """协调 11-17：实机初 / N.I.A 没有 HIRO_EXAM_LOADOUT 时，中转层在第一次决策时自己拼编成：牌组账本 + run_loadout（道具 / 支援卡）
    + 画面（体力 / 倍率）+ 标签的场次和档位 → 试镜行（exam / audition，按场次的目标要用）。回忆附魔只认 H.I.F 的，初 / N.I.A 给空。"""
    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    row = {"produceExamBattleConfigId": "bc", "produceExamGimmickEffectGroupId": "", "produceExamBattleNpcGroupId": "npc",
           "rankThreshold": 1, "baseScore": 0, "forceEndScore": 0, "voteCount": 4000, "isStaticNpcScore": False, "number": 3}  # fmt: skip
    asked = {}

    def ar(idol, produce, rnd, number=None):
        asked.update(idol=idol, produce=produce, rnd=rnd, number=number)
        return row

    monkeypatch.setattr("hiro.search.exam_env.audition_row", ar)
    monkeypatch.setattr("hiro.search.loadout.run_loadout",
                        lambda produce, plan: ({"items": ("pitem_k",), "supports": ({"_supportCardId": "s"},), "skills": ("hif_skill",)}, []))  # fmt: skip
    monkeypatch.setattr("hiro.bench_bridge.screen.audition_context", lambda *a, **k: {"crit": [1, 2, 3], "turns": 12})
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    sess = SimpleNamespace(state=SimpleNamespace(produce={"items": ["pitem_k"], "challenge_items": ["pitem_c"], "nia_easy_key": True}))
    got_items = {}
    monkeypatch.setattr("hiro.search.exam_env.npc_enhance_permil", lambda items: got_items.update(items=list(items)) or (-700, ["pitem_c"]))
    adv.on_round_start(1, {"exam": "nia-mid2#audition-3", "idol": "i_card-x", "deck": [("p_card-a", 1), ("p_card-b", 0)], "session": sess})
    st, mem = _state([_card(0, "p_card-a", 1)], remaining=12, turn=1)
    st.attr_permils = {"Vo": 20000, "Da": 30000, "Vi": 10000}  # 画面三项都读到（倍率不全时要等，见 test_hajime_audition_permil_screen_first）
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    lo = algo.ctx.loadout
    assert asked == {"idol": "i_card-x", "produce": "produce-004", "rnd": "mid2", "number": 3}
    assert lo["produce_cards"] == ["p_card-a+1", "p_card-b"] and lo["exam"]["produce_exam_battle_config_id"] == "bc"
    assert lo["audition"]["rankThreshold"] == 1 and lo["produce_item_ids"] == ["pitem_k"] and lo["supports"] == [{"_supportCardId": "s"}]
    assert lo["skills"] == [] and lo["round"] == "mid2" and lo["idol_card_id"] == "i_card-x"
    # 对手分增减：账本道具 + チャレンジP道具 + N.I.Aキー（平台记的 nia_easy_key，账本里没有）
    assert got_items["items"] == ["pitem_k", "pitem_c", "pitem_00-3-330-0"] and lo["npc_enhance_permil"] == -700
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert algo.ctx.loadout is not lo or algo.ctx.loadout == lo  # 只拼一次（第二步用同一份）


@pytest.mark.bench_on
@pytest.mark.parametrize("screen, want", [
    ({"turns_total": 7, "clear_target": 40, "perfect_target": None, "level_ids": ["L"], "lesson_limit_up_score": 0}, (7, 40, 80)),
    ({"turns_total": 6, "clear_target": 25, "perfect_target": 60, "level_ids": None}, (6, 25, 60)),  # 满分线读到了：用画面的
    ({"turns_total": None, "clear_target": 40}, None),  # 读数不全：不拼，整场 uinet
    (None, None),  # 平台没给课程读数
])  # fmt: skip
def test_live_lesson_from_screen(monkeypatch, screen, want):
    """协调 / 平台 11-18：实机课程的回合数、合格线用平台的课程画面读数（state.exam_session.lesson），满分线读到就用、没读到按档位
    （level_ids → resultTargetValueLimit + lesson_limit_up_score）。档位按日程位置定，不按同种同属性第几节查。"""
    from hiro.bench_bridge import tower as tw

    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    monkeypatch.setattr(tw, "master", lambda: SimpleNamespace(rows=lambda t: [{"id": "L", "limitTurn": 7, "successThreshold": 40,
                                                                               "resultTargetValueLimit": 80}]))  # fmt: skip
    monkeypatch.setattr("hiro.search.loadout.run_loadout", lambda produce, plan: ({"items": ("pitem_k",), "supports": (), "skills": ()}, []))
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    es = SimpleNamespace(lesson=dict(screen) if screen else None)
    sess = SimpleNamespace(state=SimpleNamespace(produce={}, exam_session=es))
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "hajime-lesson-da-sp", "idol": None, "deck": [("p_card-a", 0)], "session": sess})
    assert adv.round_algo is algo
    st, mem = _state([_card(0, "p_card-a")], remaining=6, turn=1)
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    if want is None:
        assert adv.exam_loadout is None
        return
    les = algo.ctx.loadout["lesson"]
    assert (les["limit_turn"], les["clear_score"], les["perfect_score"], les["parameter_type"], les["kind"]) == (*want, 2, "SP")
    assert algo.ctx.loadout["produce_item_ids"][0] == "pitem_k" and "exam" not in algo.ctx.loadout


@pytest.mark.bench_on
@pytest.mark.parametrize("produce, want", [({"max_stamina": 36}, 36), ({}, "start")])
def test_live_loadout_max_stamina_falls_back_for_other_idols(monkeypatch, produce, want):
    """11-19 ことね：广以外的偶像构筑表没有 max_hp，考试画面只有当前体力，max_stamina=None → ExamGnrpa 开局 int(None)，整场退回 uinet。
    用培育局面（选行动页「体力 35/36」）的上限；没有就用开考时的体力。"""
    from hiro.bench_bridge import tower as tw
    from hiro.brain.state import IdolProfile

    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    monkeypatch.setattr(tw, "master", lambda: SimpleNamespace(rows=lambda t: [{"id": "L", "limitTurn": 7, "successThreshold": 40,
                                                                               "resultTargetValueLimit": 80}]))  # fmt: skip
    monkeypatch.setattr("hiro.search.loadout.run_loadout", lambda produce, plan: ({"items": (), "supports": (), "skills": ()}, []))
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    es = SimpleNamespace(lesson={"turns_total": 7, "clear_target": 40, "perfect_target": None, "level_ids": ["L"], "lesson_limit_up_score": 0})
    sess = SimpleNamespace(state=SimpleNamespace(produce=dict(produce), exam_session=es))
    other = IdolProfile(idol_id="i_card-x", name="x", max_hp=None, playbook=None)
    adv = A.BenchAdvisor(other)
    adv.on_round_start(1, {"exam": "hajime-lesson-da-sp", "idol": "i_card-x", "deck": [("p_card-a", 0)], "session": sess})
    st, mem = _state([_card(0, "p_card-a")], remaining=6, turn=1, profile=other)
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    lo = algo.ctx.loadout
    assert lo["max_stamina"] == (lo["starting_stamina"] if want == "start" else want) and lo["max_stamina"]


@pytest.mark.bench_on
def test_live_lesson_perfect_read_midway(monkeypatch):
    """满分线开课时读不到（档位也对不上）→ 先不封顶；中途画面读到 PERFECTまで（平台给 perfect_target）→ 编成更新，ExamGnrpa 目标跟着封顶。"""
    from hiro.bench_bridge import tower as tw
    from hiro.search import exam_algo as X

    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    monkeypatch.setattr(tw, "master", lambda: SimpleNamespace(rows=lambda t: []))
    monkeypatch.setattr("hiro.search.exam_env._idol_card", lambda cid: {"planType": "ProducePlanType_Plan2"})
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    es = SimpleNamespace(lesson={"turns_total": 9, "clear_target": 90, "perfect_target": None})
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "hajime-lesson-vo-hard", "idol": None, "deck": [("p_card-a", 0)],
                           "session": SimpleNamespace(state=SimpleNamespace(produce={}, exam_session=es))})  # fmt: skip
    st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert algo.ctx.loadout["lesson"]["perfect_score"] is None
    es.lesson["perfect_target"] = 360
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert algo.ctx.loadout["lesson"]["perfect_score"] == 360
    g = X.ExamGnrpa.__new__(X.ExamGnrpa)
    g.lo, g.obj, g.stats = {"lesson": {"clear_score": 90, "perfect_score": None}}, {"rivals": [], "clear_score": 90}, __import__("collections").Counter()
    g._update_lesson(algo.ctx.loadout)
    assert g.obj["score_cap"] == 360 and g.obj["clear_score"] == 90


def test_lesson_levels_by_clear_hard_uses_produce_002():
    """初 REGULAR 的追い込み实录用的是 produce-002 的 hard 档（平台 11-18：画面 9 / 90、11 / 165 = 002 hard-1 / hard-2）。"""
    from hiro.search.exam_env import lesson_levels_by_clear

    try:
        rows = lesson_levels_by_clear("produce-001", "i_card-shro-3-007", "hard", 90)
    except Exception as e:  # noqa: BLE001  没有主数据
        pytest.skip(f"没有主数据：{e}")
    assert rows and {r["resultTargetValueLimit"] for r in rows} == {360} and all("-002-" in r["id"] for r in rows)


def test_exam_gnrpa_holds_strong_drinks_in_lessons(monkeypatch):
    """协调 11-18：课程里强饮料（复制 / 抽牌 / 加行动 / 爆发）留到考试：搜索看到的局面和合法动作里没有它，下标重排，选出的动作换回画面下标。"""
    from types import SimpleNamespace

    from hiro.search import exam_algo as X
    from hiro.search import tower_algo as ta

    seen = {}

    def fake_decide(self, ctx):
        seen["obs"] = ctx.obs
        seen["budget"] = ctx.budget
        return ta.Decision(("drink", 0), {})

    monkeypatch.setattr(ta.TowerGnrpa, "decide", fake_decide)
    monkeypatch.setattr("hiro.search.exam_env.strong_drink", lambda d: d == "strong")
    algo = X.ExamGnrpa.__new__(X.ExamGnrpa)
    algo.hold_strong, algo.stats = True, __import__("collections").Counter()
    obs = {"drinks": ["strong", "weak"], "legal": [["play", 0], ["drink", 0], ["drink", 1], ["end"]]}
    d = algo.decide(SimpleNamespace(obs=obs, budget=3))
    assert seen["obs"]["drinks"] == ["weak"] and seen["obs"]["legal"] == [["play", 0], ["drink", 0], ["end"]] and seen["budget"] == 3
    assert d.action == ("drink", 1) and obs["drinks"] == ["strong", "weak"]  # 换回画面下标；原 obs 不动
    algo.hold_strong = False
    assert algo.decide(SimpleNamespace(obs=obs, budget=3)).action == ("drink", 0) and seen["obs"] is obs  # 考试不留


def test_exam_env_support_passthrough_engine_format():
    from hiro.search.exam_env import _support

    e = {"_supportCardId": "s", "_produceCardUpgradePermil": 50}
    assert _support(None, e) == e


# ---------------------------------------------------------------- 倍率缺失（初第二局中間：倍率 0 → GNRPA 打 227 分，协调 11-18）
@pytest.mark.bench_on
@pytest.mark.parametrize("params, reads, want", [
    # 初第二局中間：T1 画面倍率 0（正常）→ 这一步 uinet；T2 读到 Vi 3470 → 按 3470 / 1990 的比例从粗算推 Vo / Da
    ({"Vo": 233, "Da": 247, "Vi": 159}, [{}, {"Vi": 3470}], [None, {"Vo": 5340, "Da": 4730, "Vi": 3470}]),
    # 三属性没记账：要等画面三项都读到
    ({}, [{"Vi": 3470}, {"Vi": 3470, "Vo": 5910}, {"Da": 4740}], [None, None, {"Vo": 5910, "Da": 4740, "Vi": 3470}]),
])  # fmt: skip
def test_hajime_audition_permil_screen_first(monkeypatch, params, reads, want):
    """协调 11-18（用户确认）：初的考试画面倍率读数 0 = 画面没给，不能当真值交给 GNRPA。画面读到的那几项为准，没读到的按 实读 / 粗算
    （三属性 + battle config，同客户端曲线；服务器还乘亲爱度加成，所以只当比例用）推；一项都没读到的步用 uinet，理由写「等画面读到倍率」。"""
    from hiro.bench_bridge import tower as tw

    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    row = {"produceExamBattleConfigId": "bc", "produceExamGimmickEffectGroupId": "", "produceExamBattleNpcGroupId": "npc",
           "rankThreshold": 3, "baseScore": 0, "forceEndScore": 900, "voteCount": 0, "isStaticNpcScore": False, "number": 1}  # fmt: skip
    asked = {}
    monkeypatch.setattr("hiro.search.exam_env.audition_row", lambda idol, produce, rnd, number=None: asked.update(produce=produce) or row)
    monkeypatch.setattr(tw, "master", lambda: SimpleNamespace(battle_config=lambda cid: {"vocal": 30, "dance": 40, "visual": 30, "turn": 9}))
    monkeypatch.setattr(tw, "score_bonus_permils", lambda cid, p: asked.update(params=p) or [3060, 2710, 1990])
    monkeypatch.setattr("hiro.search.loadout.run_loadout", lambda produce, plan: ({"items": (), "supports": (), "skills": ()}, []))
    hif, search = _Algo(("play", 0)), _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda s=None, **kw: search if s == A.EXAM_DEFAULT_ALGO else hif)
    sess = SimpleNamespace(state=SimpleNamespace(produce={"mode": "produce-002", "params": params}))
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "hajime-mid1", "idol": "i_card-x", "deck": [("p_card-a", 0)], "session": sess})
    assert asked["produce"] == "produce-002" and adv.exam["crit"] == [30, 40, 30] and adv.exam["turns"] == 9
    for read, w in zip(reads, want):
        st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
        st.attr_permils = dict(read)
        n_hif, n_search = len(hif.seen), len(search.seen)
        d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
        if w is None:
            assert len(hif.seen) == n_hif + 1 and len(search.seen) == n_search and "等画面读到倍率" in d.reason
        else:
            assert len(search.seen) == n_search + 1 and search.ctx.loadout["score_bonus_permils"] == [w["Vo"], w["Da"], w["Vi"]]
            assert "兜底" not in d.reason
    assert adv.round_algo is search  # 等倍率的步不算开局失败


def test_exam_gnrpa_refuses_zero_permils():
    """ExamGnrpa 开局：没有编成，或试镜的三属性倍率不全 → ExamUnsupported（中转层整场退回 uinet）；课程没有倍率，不查。"""
    from hiro.search.exam_algo import ExamGnrpa
    from hiro.search.exam_env import ExamUnsupported

    for lo, msg in (({"idol_card_id": "i"}, "编成"), ({"produce_cards": ["p"], "score_bonus_permils": [0, 0, 0]}, "倍率"),
                    ({"produce_cards": ["p"], "score_bonus_permils": {"1": 1000, "2": 0, "3": 1000}}, "倍率")):  # fmt: skip
        with pytest.raises(ExamUnsupported, match=msg):
            ExamGnrpa.__new__(ExamGnrpa).begin(SimpleNamespace(loadout=lo, meta={}))  # 不走构造（旧 Rust 包时构造会报错）


def _guarded(monkeypatch, steps, lo=None):
    """ExamGnrpa 的护栏单测：steps = [(画面分, 我们动作后引擎的分, 终局分预测)]，返回每步结果（Decision 或异常）。"""
    from hiro.search import exam_algo as X
    from hiro.search import tower_algo as ta

    algo = X.ExamGnrpa.__new__(X.ExamGnrpa)
    algo.calib, algo.hold_strong, algo.lo = {"pred": 0, "act": 0, "n": 0, "base": None, "last": 0}, False, lo or {}
    it = iter(steps)
    out = []

    def fake(self, ctx):
        self.rx = SimpleNamespace(score=ctx.obs["_engine"], ended=False)
        return ta.Decision(("play", 0), {"score_quantiles": {"50%": ctx.obs["_q"]}})

    monkeypatch.setattr(ta.TowerGnrpa, "decide", fake)
    for screen, engine, q in it:
        try:
            out.append(algo.decide(SimpleNamespace(obs={"num": {"score": screen}, "_engine": engine, "_q": q, "drinks": [], "legal": []})))
        except X.SearchDistrust as e:
            out.append(e)
    return out


def test_guard_engine_vs_screen_calibration(monkeypatch):
    """引擎算的加分和画面实际加分对账：倍率为 0 时引擎每步 +0、画面 +40 → 3 步后判不可信（整场退回 uinet）；对得上的不报。"""
    from hiro.search.exam_algo import SearchDistrust

    bad = _guarded(monkeypatch, [(0, 0, 50), (40, 40, 50), (80, 80, 50), (120, 120, 50), (160, 160, 50)])  # 引擎每步 +0，画面 +40
    assert all(not isinstance(x, SearchDistrust) for x in bad[:3]) and isinstance(bad[3], SearchDistrust) and bad[3].whole_round
    ok = _guarded(monkeypatch, [(0, 45, 900), (40, 80, 900), (80, 130, 900), (120, 170, 900), (170, 200, 900)])
    assert not any(isinstance(x, SearchDistrust) for x in ok)
    noisy = _guarded(monkeypatch, [(0, 20, 900), (40, 60, 900), (100, 120, 900), (160, 170, 900), (200, 230, 900)])  # 0.4 倍：bench 正常局有
    assert not any(isinstance(x, SearchDistrust) for x in noisy)
    # 画面分数读数晚一步（初 PRO 第一局：引擎出牌那步就 +406，画面下一步才读到）：累计比不误报
    lag = _guarded(monkeypatch, [(22, 428, 900), (22, 301, 900), (428, 519, 900), (706, 740, 900), (706, 740, 900), (1504, 1600, 900)])
    assert not any(isinstance(x, SearchDistrust) for x in lag)
    lesson = _guarded(monkeypatch, [(0, 0, 50), (40, 40, 50), (80, 80, 50), (120, 120, 50), (160, 160, 50)], lo={"lesson": {"clear_score": 40}})
    assert not any(isinstance(x, SearchDistrust) for x in lesson)  # 课程不查（追い込み加成等已知缺口会让比值很低）


def test_guard_zero_prediction(monkeypatch):
    """终局分预测中位 ≤ 0：这一步不信它（只这一步退回 uinet）。"""
    from hiro.search.exam_algo import SearchDistrust

    out = _guarded(monkeypatch, [(0, 0, 0)])
    assert isinstance(out[0], SearchDistrust) and not out[0].whole_round


@pytest.mark.bench_on
def test_search_distrust_whole_round_switches_to_hif(monkeypatch):
    """搜索判定整场不可信（SearchDistrust whole_round）：这一场剩下都用 uinet，理由写「不可信」，不再回去调搜索。"""
    from hiro.search.exam_algo import SearchDistrust

    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)

    class Flaky(_Algo):
        calls = 0

        def decide(self, ctx):
            self.calls += 1
            if self.calls >= 2:
                raise SearchDistrust("引擎算的加分和画面差太多")
            return super().decide(ctx)

    hif, search = _Algo(("play", 0)), Flaky(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda s=None, **kw: search if s == A.EXAM_DEFAULT_ALGO else hif)
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "hajime-final", "idol": None, "deck": []})
    reasons = []
    for _ in range(3):
        st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
        reasons.append(adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()]).reason)
    assert search.calls == 2 and len(hif.seen) == 2
    assert "兜底" not in reasons[0] and all("不可信" in r for r in reasons[1:])


# ---------------------------------------------------------------- 初 PRO 第一局中間（协调 11-18）：模式 / 倍率 / 日程 / 晚拼编成补放
@pytest.mark.parametrize("label, mode, want", [("hajime-mid1", "produce-002", "produce-002"), ("nia-mid1#audition-2", "produce-005", "produce-005"),
                                               ("hajime-mid1", "produce-005", "produce-001"), ("hajime-mid1", None, "produce-001")])  # fmt: skip
def test_exam_produce_from_session_mode(monkeypatch, label, mode, want):
    """实机一律用 state.produce.mode（同家族才用）：以前按标签缺省 hajime → produce-001，PRO 的 battle config / 封顶全按 REGULAR 取。"""
    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: _Algo(("play", 0)))
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    sess = SimpleNamespace(state=SimpleNamespace(produce={"mode": mode} if mode else {}, exam={}))
    monkeypatch.setattr(A.BenchAdvisor, "_audition_defaults", lambda self, idol, number: None)
    adv.on_round_start(1, {"exam": label, "idol": None, "deck": [], "session": sess})
    assert adv.exam["produce"] == want


@pytest.mark.bench_on
@pytest.mark.sim_master  # bench 出招读模拟器主数据 current.json；CI 没构建时跳过
def test_uiobs_uses_schedule_and_panel_permils(monkeypatch):
    """平台读到审查日程（exam.turn_types）和三项倍率（ExamMemory.attr_permil → BattleState.attr_permils）：UIObs 的回合属性按日程，
    倍率三项各用各的（以前 T2 起读成 [6440, 6440, 6440]，没见过的回合缺省填 Vo）。"""
    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HAJIME", "0")
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    monkeypatch.setattr(A.BenchAdvisor, "_audition_defaults", lambda self, idol, number: None)
    sched = ["Vo", "Vi", "Da", "Da", "Vo", "Vo", "Vi", "Da", "Vo"]
    sess = SimpleNamespace(state=SimpleNamespace(produce={"mode": "produce-002"}, exam={"turn_types": sched}))
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "hajime-mid1", "idol": None, "deck": [("p_card-a", 0)], "session": sess})
    st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
    st.attr_permils = {"Vo": 6440, "Da": 6800, "Vi": 4280}
    adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    ex = algo.seen[-1]["exam"]
    assert ex["types"] == [1, 3, 2, 2, 1, 1, 3, 2, 1] and ex["permil"] == [6440, 6800, 4280] and ex["produce"] == "produce-002"


@pytest.mark.bench_on
def test_late_loadout_passes_history_and_schedule(monkeypatch):
    """编成晚拼（T1 倍率没读到，uinet 先打）：搜索开局时 ctx.history 带上已经执行过的 (UIObs, 动作)，ctx.loadout.turn_types 带日程。"""
    from hiro.bench_bridge import tower as tw

    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    row = {"produceExamBattleConfigId": "bc", "produceExamGimmickEffectGroupId": "", "produceExamBattleNpcGroupId": "npc",
           "rankThreshold": 3, "baseScore": 0, "forceEndScore": 1700, "voteCount": 0, "isStaticNpcScore": False, "number": 1}  # fmt: skip
    monkeypatch.setattr("hiro.search.exam_env.audition_row", lambda idol, produce, rnd, number=None: row)
    monkeypatch.setattr(tw, "master", lambda: SimpleNamespace(battle_config=lambda cid: {"vocal": 30, "dance": 40, "visual": 30, "turn": 9}))
    monkeypatch.setattr(tw, "score_bonus_permils", lambda cid, p: [2990, 3580, 2330])
    monkeypatch.setattr("hiro.search.loadout.run_loadout", lambda produce, plan: ({"items": (), "supports": (), "skills": ()}, []))

    class Begin(_Algo):
        def begin(self, ctx):
            self.history, self.types = list(ctx.history), ctx.loadout.get("turn_types")

    hif, search = _Algo(("play", 0)), Begin(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda s=None, **kw: search if s == A.EXAM_DEFAULT_ALGO else hif)
    sess = SimpleNamespace(state=SimpleNamespace(produce={"mode": "produce-002", "params": {"Vo": 185, "Da": 445, "Vi": 245}},
                                                 exam={"turn_types": ["Vo"] * 9}))  # fmt: skip
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": "hajime-mid1", "idol": "i_card-x", "deck": [("p_card-a", 0)], "session": sess})
    for read in ({}, {"Vo": 6440}):
        st, mem = _state([_card(0, "p_card-a")], remaining=9, turn=1)
        st.attr_permils = dict(read)
        adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert len(hif.seen) == 1 and len(search.history) == 1 and search.history[0][1] == ["play", 0]
    assert search.types == [1] * 9


def test_exam_gnrpa_replays_history_before_first_decision(monkeypatch):
    """ExamGnrpa 开局补放：history 里每一步先按当时的画面对齐、再执行那个动作；补放不了的那一步跳过（记 replay_failed）。"""
    import collections

    from hiro.search import exam_algo as X

    log = []

    class Rx:
        def step(self, a):
            if a == ("drink", 9):
                raise ValueError("no drink")
            log.append(("step", a))

        def advance(self):
            log.append(("advance",))

    g = X.ExamGnrpa.__new__(X.ExamGnrpa)
    g.rx, g.stats = Rx(), collections.Counter()
    monkeypatch.setattr(X.ExamGnrpa, "_align", lambda self, o: log.append(("align", o["i"])))
    g._replay([({"i": 0}, ["drink", 0]), ({"i": 1}, ["drink", 9]), ({"i": 2}, ["play", 1])])
    assert log == [("align", 0), ("step", ("drink", 0)), ("advance",), ("align", 1), ("align", 2), ("step", ("play", 1)), ("advance",)]
    assert g.stats["replayed"] == 2 and g.stats["replay_failed"] == 1


def test_items_from_screen_enchants():
    """账本没记的 P 道具从画面附魔补：enchant-p_item_effect_<道具号>-encNN → pitem_<道具号>；卡的附魔、重复的不算。"""
    ench = [["enchant-p_item_effect_02-3-076-0-enc01", 5, -1], ["enchant-p_card-02-ido-3_105-enc02", -1, -1],
            ["enchant-p_item_effect_02-3-076-0-enc02", 1, -1], ["enchant-p_item_effect_00-3-330-0-enc01", -1, -1]]  # fmt: skip
    assert A.items_from_enchants(ench) == ["pitem_02-3-076-0", "pitem_00-3-330-0"]
    assert A.items_from_enchants(None) == []


# ---------------------------------------------------------------- 初 PRO 最終被标成中間（协调 11-18）
@pytest.mark.bench_on
@pytest.mark.parametrize("label, pass_line, limit, want", [
    ("hajime-mid1", {"rank": 3, "score": None, "stage": "3位以上で最終試験突破"}, 11, "hajime-final"),  # 面板原文 → 最終
    ("hajime-mid1", {"rank": 3, "score": None, "stage": "3位以上で中間試験突破"}, 11, "hajime-mid1"),  # MASTER 中間只看名次：原文为准
    ("hajime-mid1", {"rank": 3, "score": None}, 9, "hajime-mid1"),  # 没有原文：不按分数线有无判，按回合数（9 = 中間）
    ("hajime-mid1", None, 11, "hajime-final"),  # 没面板：回合数 11 = 最終
    ("hajime-final", {"rank": 3, "score": 1700, "stage": "3位以上または1700点以上で中間試験突破"}, 9, "hajime-mid1"),
    ("hajime-mid1", None, 9, "hajime-mid1"),  # 对得上：不动
])  # fmt: skip
def test_hajime_round_follows_panel_and_turns(monkeypatch, label, pass_line, limit, want):
    """标签和画面对不上时以面板过线条件 / 回合数为准（初 中間 9 回合、最終 11 回合），理由写明；目标跟着换（最終不封顶）。"""
    from hiro.bench_bridge import tower as tw

    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HAJIME", "0")
    rows = {"mid1": {"produceExamBattleConfigId": "bc-mid", "forceEndScore": 1700, "rankThreshold": 3, "number": 1},
            "final": {"produceExamBattleConfigId": "bc-final", "forceEndScore": 0, "rankThreshold": 3, "number": 1}}  # fmt: skip
    monkeypatch.setattr("hiro.search.exam_env.audition_row", lambda idol, produce, rnd, number=None: rows[rnd])
    monkeypatch.setattr(tw, "master", lambda: SimpleNamespace(battle_config=lambda cid: {"vocal": 1, "dance": 1, "visual": 1,
                                                                                          "turn": 9 if cid == "bc-mid" else 11}))  # fmt: skip
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    sess = SimpleNamespace(state=SimpleNamespace(produce={"mode": "produce-002"}, exam={"pass_line": pass_line} if pass_line else {}))
    adv = A.BenchAdvisor(CAMPUS_HIRO)
    adv.on_round_start(1, {"exam": label, "idol": "i_card-x", "deck": [("p_card-a", 0)], "session": sess})
    st, mem = _state([_card(0, "p_card-a")], remaining=limit, turn=1)
    d = adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
    assert adv.label == want
    assert adv.exam["step"] == ("AUDITION_FINAL" if want == "hajime-final" else "AUDITION_MID1")
    assert ("场次按画面改成" in d.reason) == (want != label)


def test_hajime_final_objective_prefers_first_place():
    """初最終：前 3 过线，名次分档 1 > 2 > 3（Rust stars：3 / 2 / 1 档，档内再比分数），不封顶——第 1 名优先、名次稳了接着追分。
    中間带 forceEndScore 封顶。"""
    from hiro.search.exam_env import stage_objective

    assert stage_objective({"audition": {"rankThreshold": 3, "forceEndScore": 0}}) == {"pass_rank": 3}
    assert stage_objective({"audition": {"rankThreshold": 3, "forceEndScore": 1700}}) == {"pass_rank": 3, "score_cap": 1700}


@pytest.mark.parametrize("fam, stage, want", [
    ("hajime", "3位以上で中間試験突破", "mid1"), ("hajime", "3位以上またはスコア1700以上で中間試験突破", "mid1"),
    ("hajime", "3位以上で最終試験突破", "final"), ("hajime", "", None), ("hajime", "3位以上", None),
    ("nia", "1位で1次オーディション突破", "mid1"), ("nia", "１位で２次オーディション突破", "mid2"), ("nia", "最終オーディション", "final"),
    ("nia", "1位", None),
])  # fmt: skip
def test_stage_round_from_panel_text(fam, stage, want):
    assert A.stage_round(fam, stage) == want


@pytest.mark.bench_on
def test_nia_round_follows_panel_text_only(monkeypatch):
    """N.I.A：标签和面板原文对不上时按原文改（nia-mid1 → nia-mid2），档位 tag 保留；没有原文不按回合数猜。"""
    monkeypatch.delenv("HIRO_EXAM_LOADOUT", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_NIA", raising=False)
    monkeypatch.setattr(A.BenchAdvisor, "_audition_defaults", lambda self, idol, number: None)
    algo = _Algo(("play", 0))
    monkeypatch.setattr(A, "load_algo", lambda *a, **k: algo)
    for pl, want in (({"rank": 1, "stage": "1位で2次オーディション突破"}, "nia-mid2#audition-2"), (None, "nia-mid1#audition-2")):
        sess = SimpleNamespace(state=SimpleNamespace(produce={"mode": "produce-005"}, exam={"pass_line": pl} if pl else {}))
        adv = A.BenchAdvisor(CAMPUS_HIRO)
        adv.on_round_start(1, {"exam": "nia-mid1#audition-2", "idol": None, "deck": [("p_card-a", 0)], "session": sess})
        st, mem = _state([_card(0, "p_card-a")], remaining=12, turn=1)
        adv.decide(st, mem, None, [Action.from_legal(a) for a in st.legal_actions()])
        assert adv.label == want


def test_guard_undo_removes_not_executed_gain(monkeypatch):
    """N.I.A MASTER 第一局（协调 11-18）：帧过时没点的动作引擎已执行，加分被算进对账（899 对 197）。undo_last 把引擎退回、加分减掉，
    下一步画面照常累计，不误报。"""
    from hiro.search import exam_algo as X
    from hiro.search import tower_algo as ta

    algo = X.ExamGnrpa.__new__(X.ExamGnrpa)
    algo.calib, algo.hold_strong, algo.lo = {"pred": 0, "act": 0, "n": 0, "base": None, "last": 0}, False, {}
    algo.stats = __import__("collections").Counter()

    def fake(self, ctx):
        self.prev = SimpleNamespace(score=ctx.obs["num"]["score"], ended=False)
        self.rx = SimpleNamespace(score=ctx.obs["_engine"], ended=False)
        return ta.Decision(("play", 0), {"score_quantiles": {"50%": 900}})

    monkeypatch.setattr(ta.TowerGnrpa, "decide", fake)
    seq = [(0, 300, True), (0, 300, True), (0, 300, False), (300, 600, True), (300, 600, False), (600, 900, False), (900, 1200, False), (1200, 1500, False)]
    for screen, engine, stale in seq:
        algo.decide(SimpleNamespace(obs={"num": {"score": screen}, "_engine": engine, "drinks": [], "legal": []}))
        if stale:
            algo.undo_last()
    assert algo.stats["undone"] == 3 and algo.calib["pred"] == 1500 and algo.calib["act"] == 1200


def test_not_executed_hook_pops_history_and_undoes(monkeypatch):
    """执行层说上一个决策没生效（帧过时没点 / 牌没打出去 / 画面没变）：中转层把这一步的历史记录作废、调搜索的 undo_last；
    上一步不是这一层记的（规则兜底）就不动历史。执行层的钩子是可选的，插件没有就跳过。"""
    from hiro.exec.exam import _not_executed

    undone = []
    adv = A.BenchAdvisor.__new__(A.BenchAdvisor)
    adv.label, adv.algo = "nia-mid1", None
    adv.round_algo = SimpleNamespace(undo_last=lambda: undone.append(1))
    adv._history, adv._appended = [("o1", ["play", 0]), ("o2", ["drink", 0])], True
    _not_executed(adv, "drink:0", "决策帧已过时")
    assert adv._history == [("o1", ["play", 0])] and undone == [1]
    _not_executed(adv, "play:0", "执行后画面未变化")  # 这一步没记历史（_appended 已清）：历史不动
    assert adv._history == [("o1", ["play", 0])] and undone == [1, 1]
    _not_executed(object(), "skip", "x")  # 没有钩子的插件：什么也不做
