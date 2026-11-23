"""hiro.bench_bridge.tower：倍率公式、层设置（绑定 + 命名规则）、绑定整理、evaluate 接线。全用合成主数据，不读私有数据。"""

import json
from types import SimpleNamespace

from hiro.bench_bridge import tower as T
from hiro.bench_bridge.screen import exam_fields

import pytest

BC = "p_exam_battle_config-tower_001-700_500_300-1500_1100_700-turn_18"
SC = "p_exam_battle_score_config-tower_001-700_500_300-1500_1100_700"
NPC = "p_npc_group-tower_001-shro-exam_review-stage_012"
ROWS = {
    "ProduceExamBattleConfig": [
        {"id": BC, "turn": 18, "vocal": 700, "dance": 500, "visual": 300, "produceExamBattleScoreConfigId": SC}
    ],
    "ProduceExamBattleScoreConfig": [
        {"id": SC, "parameter": 0, "vocalPermil": 1, "dancePermil": 1, "visualPermil": 1},
        {"id": SC, "parameter": 1000, "vocalPermil": 10000, "dancePermil": 8000, "visualPermil": 6000},
        {"id": SC, "parameter": 2000, "vocalPermil": 15000, "dancePermil": 12000, "visualPermil": 9000},
    ],
    "ProduceExamBattleNpcGroup": [
        {"id": NPC, "number": 2, "scoreMin": 50000, "scoreMax": 52000},
        {"id": NPC, "number": 1, "scoreMin": 90000, "scoreMax": 92000},
    ],
    "ProduceItem": [
        {"id": "pitem_tower_001-exam_review-1-stage_012"},
        {"id": "pitem_tower_001-exam_review-2-stage_012"},
        {"id": "pitem_tower_001-exam_review-2-stage_012-1"},
    ],
    "ProduceExamGimmickEffectGroup": [
        {"id": "p_exam_gimmick-tower_001-exam_review-0001-stage_012"},
        {"id": "p_exam_gimmick-tower_001-exam_review-0002-stage_012"},
        {"id": "p_exam_gimmick-tower_001-exam_review-0001-stage_013"},
    ],
}


@pytest.fixture
def m():
    return T.Master(rows=ROWS)


@pytest.fixture
def bindings(tmp_path, monkeypatch):
    p = tmp_path / "b.json"
    monkeypatch.setenv("HIRO_TOWER_BINDINGS", str(p))
    T._bindings.cache_clear()
    yield p
    T._bindings.cache_clear()


# ---------------------------------------------------------------- 倍率


def test_stage_params_main_plus_floored_fifth_of_each_sub():
    assert T.stage_params({"vocal": 1637, "dance": 1119, "visual": 536}, [[1423, 1264, 535]]) == [1921, 1371, 643]
    assert T.stage_params([100, 100, 100], [[4, 9, 10], [4, 9, 10]]) == [100, 102, 104]  # 每份副回忆各自取整
    assert T.stage_params([1, 2, 3]) == [1, 2, 3]


def test_permils_interpolate_cap_and_round_up(m):
    # 1500：Vo 10000 + 0.5 × 5000 = 12500 → +1000 → 13500；超出曲线按最后一点（Vi 9000 → 10000）
    assert T.score_bonus_permils(BC, [1500, 1500, 5000], m) == [13500, 11000, 10000]
    assert T.score_bonus_permils(BC, [1001, 1000, 1000], m) == [
        11010,
        9000,
        7000,
    ]  # 10005 + 1000 = 11005 → 向上取到 11010


def test_permils_shortfall_penalty(m):
    # Vo 350 < 基准 700：扣 0.25 − 0.5 × 0.15 = 0.175，三项都乘 0.825
    raw_vo = 1 + 350 * (10000 - 1) / 1000
    want_vo = -(-((raw_vo * 0.825 + 1000) / 10) // 1) * 10
    got = T.score_bonus_permils(BC, [350, 1000, 1000], m)
    assert got[0] == want_vo and got[1] == -(-((8000 * 0.825 + 1000) / 10) // 1) * 10
    assert T.shortfall(m.battle_config(BC), [700, 500, 300]) == 0.0


# ---------------------------------------------------------------- 层设置


def test_layer_exam_from_binding_with_rivals_and_border(m, bindings):
    bindings.write_text(json.dumps({"exams": [{"tower_id": "tower_001-shro", "layer": 12, "mechanic": "exam_review",
                                               "battle_config": BC, "gimmick": "p_exam_gimmick-tower_001-exam_review-0002-stage_012",
                                               "npc_group": NPC, "exam_effect_type": 31, "source": "bench:AS-X"}]}))  # fmt: skip
    r = T.layer_exam("tower_001-shro", 12, m=m)  # 这层只知道一种机制：不给 mechanic 也行
    assert r["mechanic"] == "exam_review" and r["turns"] == 18 and r["criteria"] == [700, 500, 300]
    assert r["rivals"] == [[90000, 92000], [50000, 52000]] and r["border"] == 92001
    assert r["exam"]["produce_item_ids"] == [
        "pitem_tower_001-exam_review-2-stage_012",
        "pitem_tower_001-exam_review-2-stage_012-1",
    ]
    assert r["exam"]["exam_effect_type"] == 31 and r["source"] == "bench:AS-X"


def test_layer_exam_without_binding_uses_rules(m, bindings):
    r = T.layer_exam("tower_001-shro", 12, "exam_review", m=m)
    assert r["exam"]["produce_exam_battle_npc_group_id"] == NPC and r["border"] == 92001
    assert r["exam"]["produce_exam_battle_config_id"] is None and r["turns"] is None and r["criteria"] is None
    assert (
        r["exam"]["produce_exam_gimmick_effect_group_id"] is None and r["exam"]["produce_item_ids"] is None
    )  # 两个变体：不知道是哪个
    assert len(r["gimmick_variants"]) == 2 and r["source"] == "rule"
    one = T.layer_exam("tower_001-shro", 13, "exam_review", m=m)  # 只有一个变体：就是它（主数据里这层没有道具）
    assert (
        one["exam"]["produce_exam_gimmick_effect_group_id"].endswith("0001-stage_013")
        and one["exam"]["produce_item_ids"] == []
    )
    assert one["border"] is None and one["rivals"] == []
    assert T.layer_exam("tower_001-shro", 12, m=m) is None  # 不给机制、也不知道这层有什么


def test_preservation_shares_full_power_schedule():
    m = T.Master(
        rows={"ProduceExamGimmickEffectGroup": [{"id": "p_exam_gimmick-tower_001-exam_full_power-0001-stage_004"}]}
    )
    assert m.gimmick_variants("tower_001", "exam_preservation", 4) == [
        "p_exam_gimmick-tower_001-exam_full_power-0001-stage_004"
    ]


def test_build_bindings_from_problems_and_captures(tmp_path):
    d = tmp_path / "problems"
    d.mkdir()
    lo = {"produce_id": "tower", "tower_id": "tower_001-shro", "layer": 12, "mechanic": "exam_review",
          "exam": {"exam_effect_type": 31, "produce_exam_battle_config_id": BC, "produce_exam_battle_npc_group_id": NPC,
                   "produce_exam_gimmick_effect_group_id": "p_exam_gimmick-tower_001-exam_review-0001-stage_012", "produce_item_ids": None}}  # fmt: skip
    (d / "AS-A.gksave.json").write_text(json.dumps({"loadout": lo}))
    (d / "AS-B.gksave.json").write_text(json.dumps({"loadout": lo}))  # 同一层两题：合成一组
    (d / "._AS-A.gksave.json").write_text("junk")  # macOS 元数据文件跳过
    (d / "AS-H.gksave.json").write_text(json.dumps({"loadout": {"produce_id": "produce-008"}}))
    cap = tmp_path / "cap.json"
    cap.write_text(json.dumps({"exams": [{"towerId": "tower_001-hmsz", "number": 14, "examEffectType": "PRODUCE_EXAM_EFFECT_TYPE_EXAM_PARAMETER_BUFF",
                                          "produceExamBattleConfigId": BC, "produceExamGimmickEffectGroupId": "g",
                                          "produceExamBattleNpcGroupId": "n"}]}))  # fmt: skip
    rows = T.build_bindings([d], [cap])
    assert [(r["tower_id"], r["layer"], r["mechanic"]) for r in rows] == [
        ("tower_001-hmsz", 14, "exam_parameter_buff"),
        ("tower_001-shro", 12, "exam_review"),
    ]
    assert rows[1]["source"] == "bench:AS-A" and rows[1]["produce_item_ids"] == []
    lo2 = json.loads(json.dumps(lo))
    lo2["exam"]["produce_exam_battle_config_id"] = "other"
    (d / "AS-C.gksave.json").write_text(json.dumps({"loadout": lo2}))
    with pytest.raises(ValueError, match="不一致"):
        T.build_bindings([d])


# ---------------------------------------------------------------- 接线


def test_exam_fields_tower_matches_bench():
    assert exam_fields("tower-24", idol="i_card-shro-3-012", plan=3) == {
        "produce": "tower", "step": None, "plan": 0, "idol": "i_card-shro-3-012", "crit": [0, 0, 0]}  # fmt: skip
    assert exam_fields("r1", idol="x", plan=1)["produce"] == "produce-008"


# ---------------------------------------------------------------- 星

TRIG26 = {
    "id": "e_trigger-exam_end_turn-debuff_count_up-2-remaining_turn-1-for_tower_stage26",
    "phaseTypes": ["ProduceExamPhaseType_ExamEndTurn"],
    "fieldStatusTypes": ["ProduceExamFieldStatusType_DebuffCountUp", "ProduceExamFieldStatusType_RemainingTurn"],
    "fieldStatusValues": [2, 1],
    "fieldStatusCheckTypes": [],
    "fieldStatusProduceCardSearchIds": [],
    "produceDescriptions": [{"text": "クリア時に低下状態が2個以上"}],
}
TRIG27 = {
    "id": "e_trigger-exam_end_turn-not-card_search_count_up-6-p_card_search-ssr-lost-remaining_turn-1-for_tower_stage27",
    "phaseTypes": ["ProduceExamPhaseType_ExamEndTurn"],
    "fieldStatusTypes": ["ProduceExamFieldStatusType_CardSearchCountUp", "ProduceExamFieldStatusType_RemainingTurn"],
    "fieldStatusValues": [6, 1],
    "fieldStatusCheckTypes": ["ProduceExamTriggerCheckType_Not"],
    "fieldStatusProduceCardSearchIds": ["p_card_search-ssr-lost"],
    "produceDescriptions": [{"text": "クリア時に除外にあるスキルカード(SSR)が5枚以下"}],
}
STAR_M = T.Master(
    rows={
        "ProduceExamTrigger": [TRIG26, TRIG27, {"id": "e_trigger-other"}],
        "ProduceCard": [
            {"id": "ssr", "rarity": "ProduceCardRarity_Ssr"},
            {"id": "sr", "rarity": "ProduceCardRarity_Sr"},
        ],
    }
)
RIVALS = [[90000, 92000], [50000, 52000], [20000, 22000], [9000, 9500]]


def test_star_conditions_from_layer_triggers():
    assert T.star_conditions(25, STAR_M) == []
    (c26,) = T.star_conditions(26, STAR_M)
    assert (c26["field"], c26["op"], c26["value"]) == ("debuffs", ">=", 2)
    (c27,) = T.star_conditions(27, STAR_M)
    assert (c27["field"], c27["op"], c27["value"]) == ("lost_ssr", "<=", 5)  # 「6 个以上」取反 = 5 枚以下
    assert T.condition_by_text("クリア時に 除外にあるスキルカード(SSR)が5枚以下", STAR_M)["field"] == "lost_ssr"
    assert T.condition_by_text("クリア時にやる気が10以上", STAR_M) is None
    odd = dict(
        TRIG26, fieldStatusTypes=["ProduceExamFieldStatusType_Unknown", "ProduceExamFieldStatusType_RemainingTurn"]
    )
    assert T.parse_trigger(odd)["field"] is None  # 不认识的条件不判


def test_judge_places_conservative_and_optimistic():
    no = []
    assert T.judge(95000, RIVALS, no, {})["stars"] == 3
    r = T.judge(91000, RIVALS, no, {})  # 落在第 1 名对手区间里：保守第 2、乐观第 1
    assert (r["place"], r["place_optimistic"], r["first"], r["stars"], r["stars_optimistic"]) == (2, 1, False, 2, 3)
    assert T.judge(30000, RIVALS, no, {})["stars"] == 1
    assert T.judge(9200, RIVALS, no, {})["stars"] == 0  # 第 4 名及以后没有星


def test_judge_star3_needs_condition():
    c27 = T.star_conditions(27, STAR_M)
    ok = T.judge(95000, RIVALS, c27, {"lost_ssr": 5})
    assert ok["stars"] == 3 and ok["conditions"][0]["ok"] is True
    bad = T.judge(95000, RIVALS, c27, {"lost_ssr": 6})
    assert bad["first"] and bad["stars"] == 2 and bad["conditions"][0]["got"] == 6
    unknown = T.judge(95000, RIVALS, c27, {})  # 局末读不到：不给★3
    assert unknown["stars"] == 2 and unknown["conditions"][0]["ok"] is None


def test_end_state_counts_ssr_in_lost(monkeypatch):
    monkeypatch.setattr(T, "master", lambda: STAR_M)
    deck = SimpleNamespace(lost=[{"produce_card_id": "ssr"}, {"produce_card_id": "sr"}, {"produce_card_id": "ssr"}])
    ref = SimpleNamespace(battle=SimpleNamespace(session=SimpleNamespace(deck=deck, debuff_count=lambda: 3)))
    assert T._end_state(ref) == {"debuffs": 3, "lost_ssr": 2}
    assert T._end_state(None) == {"debuffs": None, "lost_ssr": None}


def test_evaluate_detail_judges_each_episode(monkeypatch):
    def play(problem, row, bot, step_seconds, keep_obs):
        return {
            "err": None,
            "final": {"score": row["seed"]},
            "meta": {"rival_scores": [{"score_min": 90000, "score_max": 92000}]},
        }

    monkeypatch.setattr(T, "_gkbench", lambda: SimpleNamespace(play_episode=play))
    monkeypatch.setattr("hiro.bench_bridge.advisor.load_algo", lambda spec: object())
    monkeypatch.setattr(T, "star_conditions", lambda layer, m=None: [])
    monkeypatch.setattr(T, "_end_state", lambda ref: {})
    (row,) = T.evaluate([{"layer": 12}], [95000, 91000], detail=True)
    assert [(d["score"], d["place"], d["stars"]) for d in row] == [
        (95000, 1, 3),
        (91000, 2, 2),
    ]  # 落在对手区间里：保守算第 2


def test_evaluate_plays_every_seed_and_respects_budget(monkeypatch):
    calls = []

    def play(problem, row, bot, step_seconds, keep_obs):
        calls.append((problem["loadout"]["k"], row["seed"]))
        return {"err": "boom" if row["seed"] == 3 else None, "final": {"score": row["seed"] * 10}}

    monkeypatch.setattr(T, "_gkbench", lambda: SimpleNamespace(play_episode=play))
    monkeypatch.setattr("hiro.bench_bridge.advisor.load_algo", lambda spec: object())
    assert T.evaluate([{"k": 1}, {"k": 2}], [1, 2, 3]) == [[10, 20, None], [10, 20, None]]
    assert calls == [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
    assert T.evaluate([{"k": 1}], [1, 2], budget_s=-1) == [[None, None]]


def test_evaluate_workers_preflight_explains_native_mismatch(monkeypatch):
    def broken():
        raise AssertionError("SearchParams layout")

    fake_engine = SimpleNamespace(native=broken, GKMS_ROOT="/g", NATIVE_LIB="/old.dylib")
    monkeypatch.setattr(T, "_gkbench", lambda: SimpleNamespace())
    monkeypatch.setitem(__import__("sys").modules, "gkbench", SimpleNamespace(engine=fake_engine))
    monkeypatch.setitem(__import__("sys").modules, "gkbench.engine", fake_engine)
    with pytest.raises(RuntimeError, match="GKMSCLIGPU_LIB"):
        T.evaluate([{}], [1], workers=2)
