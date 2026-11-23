"""hiro.search.exam_env：通用考试开局（初 / N.I.A 试镜、偶像之路共用）的写法换算。开局本身要主数据和引擎，另由离线脚本对 GKMS 核。"""

from types import SimpleNamespace

from hiro.search import exam_env as E
from hiro.search.loadout import parse_deck_entry

import pytest


def test_bonus_permils_list_and_dict():
    assert E.bonus_permils([1, 2, 3]) == {1: 1, 2: 2, 3: 3}
    assert E.bonus_permils({"1": 10, "2": 20, "3": 30}) == {1: 10, 2: 20, 3: 30}
    assert E.bonus_permils({1: 7, 2: 8, 3: 9}) == {1: 7, 2: 8, 3: 9}


def test_parse_deck_entry_accepts_gkms_card_dict():
    """bench 模板展开后的牌（初 / N.I.A 题）：{produce_card_id, current_upgrade_count, customizes}。"""
    card = {
        "produce_card_id": "p_card-x",
        "current_upgrade_count": 1,
        "customizes": [{"id": "c1", "customize_count": 2}],
    }
    assert parse_deck_entry(card) == ("p_card-x", 1, {"c1": 2})
    assert parse_deck_entry({"produce_card_id": "p_card-y", "base_upgrade_count": 1}) == ("p_card-y", 1, {})
    assert parse_deck_entry({"id": "p_card-z", "upgrade_count": 1}) == ("p_card-z", 1, {})
    assert parse_deck_entry("p_card-a+1/c1*2") == ("p_card-a", 1, {"c1": 2})


def test_support_number_is_permil(monkeypatch):
    """支援卡 "id:数字" 的数字是考试内强化概率（千分比），不是等级（gkbench 展开时叫 permil）。"""
    monkeypatch.setattr("gakumas_exam.loadout._support", lambda m, sid: {"produceCardUpgradeLessonParameterType": "T", "upgradeProduceCardSearchId": "S"},
                        raising=False)  # fmt: skip
    for s in ("s_card-3-0010:64", {"support_card_id": "s_card-3-0010", "permil": 64}):
        e = E._support(None, s)
        assert e["_supportCardId"] == "s_card-3-0010" and e["_produceCardUpgradePermil"] == 64


def test_lesson_loadout_from_master(monkeypatch):
    """初的课程题 → 课程写法（同 GKMS build_lesson_session）：回合 = limitTurn + exam_extra_turn，满分线 = resultTargetValueLimit +
    lesson_limit_up_score，合格线 = successThreshold；属性按 step_type（32 = レジェンド Da SP）。stage_objective 给 clear_score / score_cap。"""
    level = {"limitTurn": 8, "successThreshold": 85, "resultTargetValueLimit": 160}
    monkeypatch.setattr(E, "lesson_level", lambda sid: (level, "レジェンドレッスン"))
    doc = {"round": "lesson", "produce_id": "produce-006", "idol_card_id": "i", "deck_cards": ["p_card-a"], "max_stamina": 30,
           "step_type": 32, "step_id": "p_step_lesson-x", "exam_extra_turn": -1, "lesson_limit_up_score": 90,
           "lesson_start_enchant_ids": ["enc"], "drink_ids": ["d"]}  # fmt: skip
    lo = E.from_hif_loadout(doc)
    assert lo["lesson"] == {"parameter_type": 2, "kind": "SP", "name": "レジェンドレッスン", "step_id": "p_step_lesson-x",
                            "limit_turn": 7, "clear_score": 85, "perfect_score": 250}  # fmt: skip
    assert lo["start_enchant_ids"] == ["enc"] and lo["starting_stamina"] == 30 and "exam" not in lo
    assert E.stage_objective(lo) == {"clear_score": 85, "score_cap": 250}
    with pytest.raises(E.ExamUnsupported, match="step_type"):
        E.from_hif_loadout({**doc, "step_type": 16})


def test_audition_row_picks_by_number(monkeypatch):
    rows = {("d", "produce-005", "ProduceStepType_AuditionMid2"): [{"number": 1, "id": "d"}, {"number": 3, "id": "d"}]}
    monkeypatch.setattr(E, "_difficulty_rows", lambda: rows)
    monkeypatch.setattr(E, "_idol_card", lambda cid: {"produceStepAuditionDifficultyId": "d"})
    assert E.audition_row("i", "produce-005", "mid2", 3)["number"] == 3
    with pytest.raises(E.ExamUnsupported, match="2 行"):
        E.audition_row("i", "produce-005", "mid2")  # 有几个可选、又没给编号：不猜
    with pytest.raises(E.ExamUnsupported, match="不是试镜"):
        E.audition_row("i", "produce-005", "lesson")


def test_objective_prefers_given_rivals():
    assert E.objective({}, [100, 300, 200])["rivals"] == [300, 200, 100]


# ---------------------------------------------------------------- 按场次的目标（3940a9，设计稿第五节）


def test_stage_objective_by_scenario():
    from hiro.search.exam_env import stage_objective

    assert stage_objective({"audition": {"rankThreshold": 1, "forceEndScore": 0}}) == {
        "pass_rank": 1
    }  # N.I.A：必须第 1
    assert stage_objective({"audition": {"rankThreshold": 3, "forceEndScore": 900}}) == {
        "pass_rank": 3,
        "score_cap": 900,
    }  # 初 REGULAR 中間
    assert stage_objective({"lesson": {"clear_score": 30, "perfect_score": 90}}) == {
        "clear_score": 30,
        "score_cap": 90,
    }  # 课程
    assert stage_objective({}) == {}  # 偶像之路等：不加键


def test_gnrpa_drops_newer_objective_on_old_rust(monkeypatch):
    """旧 Rust 包的 search 不认 pass_rank / score_cap：这一局去掉这些参数照常搜，不整步报错。"""
    import types

    from hiro.search import tower_algo as ta

    calls = []

    def search(rx, prefix, free, **kw):
        calls.append(sorted(kw))
        if "pass_rank" in kw:
            raise TypeError("search() got an unexpected keyword argument 'pass_rank'")
        return {"best": ("end",), "votes": [], "worlds": []}

    fake_rs = types.SimpleNamespace(search=search, plan_many=None)
    monkeypatch.setitem(__import__("sys").modules, "gakumas_exam_rs", fake_rs)
    algo = ta.TowerGnrpa(warm=0)
    algo.obj = {"rivals": [1], "lost_ssr_max": None, "debuffs_min": None, "pass_rank": 1}
    algo.prefix, algo.policy, algo.prev, algo.stats = 0, {}, None, __import__("collections").Counter()

    class Rx:
        current_turn, limit_turn, ended = 1, 2, False

        def clone(self):
            return self

        def step(self, a):
            pass

        def advance(self):
            pass

    algo.rx = Rx()
    monkeypatch.setattr(algo, "_align", lambda obs: None)
    d = algo.decide(types.SimpleNamespace(obs={"exam": {"turn": 1, "types": [1, 1]}, "legal": [["end"]]}, budget=0.5))
    assert d.action == ("end",) and "pass_rank" in calls[0] and "pass_rank" not in calls[1]
    assert "pass_rank" not in algo.obj and algo.stats["no_newer_kwargs"] == 1


def test_scale_npc_range_client_formula():
    """客户端 CalculateAuditionNpc：f = 1 + permil/1000（float32），下限 / 区间各自 × f 后五取偶。"""
    assert E.scale_npc_range(1000, 2000, 0) == (1000, 2000)
    assert E.scale_npc_range(49880, 66453, -800) == (
        9976,
        9976 + 3315,
    )  # N.I.Aキー −80%：49880×0.2、16573×0.2=3314.6→3315
    lo, hi = E.scale_npc_range(18481, 20567, 400)
    assert lo == round(18481 * 1.4) and hi - lo == round((20567 - 18481) * 1.4)
    assert E.scale_npc_range(5, 7, 100) == (6, 6 + 2)  # 5.5 → 6（五取偶）、2.2 → 2


def test_npc_enhance_permil_value_times_fire_count(monkeypatch):
    """服务器的 AuditionNpcEnhancePermil = Σ 道具值 × 已发动次数（GKMS 抓包 7 组全对，如 80×5 + 150×3 + 80×5 = 1250）。"""
    monkeypatch.setattr(E, "_npc_item_values", lambda: {"key": -800, "pitem_a-challenge": 80, "pitem_b-challenge": 150})
    items = [{"produce_item_id": "pitem_a-challenge", "fire_count": 5}, {"produce_item_id": "pitem_b-challenge", "fire_count": 3},
             {"produce_item_id": "pitem_c", "fire_count": 9}]  # fmt: skip
    assert E.npc_enhance_permil(items) == (80 * 5 + 150 * 3, [])
    assert E.npc_enhance_permil([{"id": "pitem_b-challenge"}]) == (
        150,
        ["pitem_b-challenge"],
    )  # 没给次数：按 1 次、记不确定
    assert E.npc_enhance_permil(["key"]) == (-800, [])
    assert E.npc_enhance_permil([{"produce_item_id": "pitem_a-challenge", "fire_count": None}]) == (0, [])  # 没发动过
    assert E.loadout_npc_permil({"npc_enhance_permil": 1010, "produce_item_ids": ["key"]}) == 1010  # 显式给的优先
    assert E.loadout_npc_permil({"produce_item_ids": ["key"]}) == -800


def test_objective_scales_master_rivals(monkeypatch):
    rows = [{"id": "g", "scoreMin": 1000, "scoreMax": 1500}, {"id": "g", "scoreMin": 500, "scoreMax": 600}]
    monkeypatch.setattr("hiro.bench_bridge.tower.master", lambda: SimpleNamespace(rows=lambda t: rows))
    lo = {"exam": {"produce_exam_battle_npc_group_id": "g"}, "npc_enhance_permil": -800}
    assert E.objective(lo)["rivals"] == [300, 120]  # 1000×0.2 + 500×0.2、500×0.2 + 100×0.2
    assert E.objective({**lo, "npc_enhance_permil": 0})["rivals"] == [1500, 600]


def test_score_points_cover_hajime_and_nia():
    """Master._score_points 以前只收 -tower_ 的行：初 / N.I.A 的 battle score config 查不到，实机按三属性粗算倍率会失败（协调 11-18）。"""
    from hiro.bench_bridge import tower as tw

    try:
        pts = tw.master().score_points("p_exam_battle_score_config-voda-03-produce-001-1")
    except Exception as e:  # noqa: BLE001  没有主数据
        pytest.skip(f"没有主数据：{e}")
    assert pts and pts == sorted(pts, key=lambda r: int(r.get("parameter") or 0))
