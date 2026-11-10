# 来源：digital-hiro tests/test_observe.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""识别结果 → 决策层数据结构的对接（ProduceState 字段名、BattleState 观测参数）。"""

import json
from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.brain.state import BattleState, ExamMemory, profile_for
from hiro.vision.action import read_action_select
from hiro.vision.hif import read_hif_exam
from hiro.vision.observe import battle_observation, produce_observation
from hiro.vision.offline import load

FIX = Path(__file__).with_name("fixtures")
# 选拔①之前的日程（gakumas_produce produce-007），与画面「選抜試験1まで N日」推出的 step 对应
SCHEDULE = {1: {"shop", "gift", "special_guidance"}, 2: {"training"}, 3: {"class"}, 4: {"training"}, 5: {"outing", "shop"}}


def test_produce_observation_matches_schedule():
    for name in json.loads((FIX / "action" / "truth.json").read_text()):
        obs = produce_observation(read_action_select(cv2.imread(str(FIX / "action" / f"{name}.jpg"))).to_dict())
        assert obs["mode"] == "produce-007"
        assert set(obs["params"]) == {"Vo", "Da", "Vi"} and obs["param_cap"] == 3000
        assert obs["growth_permil"] == {"Vo": 1413, "Da": 1564, "Vi": 1272}
        options = {o["option"] for o in obs["options"]} - {"rest"}  # 休息不在日程表里
        assert options == SCHEDULE[obs["step"]], name


@pytest.mark.user_assets("status")
def test_battle_observation_builds_state():
    exam = read_hif_exam(load(cv2.imread(str(FIX / "hif_hud" / "01782.jpg"))))
    state = BattleState.build(ExamMemory("qualify-1"), profile_for("i_card-shro-3-007"), **battle_observation(exam))
    assert (state.motivation, state.impression, state.score) == (18, 18, 3819)
    assert [c.name for c in state.hand] == ["成長痛+", "眠気", "えいえいおー+"]
    assert [d.name for d in state.drinks] == ["特製ハツボシエキス"]
    assert state.buffs["デイドリーミング"] == 4


def test_hif_result_observation():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("HIF_EXAM_RESULT", load(cv2.imread(str(FIX / "hif_hud" / "result-02416.jpg"))))
    obs = observation(1, 0.0, "HIF_EXAM_RESULT", raw)
    assert obs.problems(strict=True) == []
    f = obs.fields
    assert (f["score"], f["rank"], f["passed"], f["exam_no"], f["rank_line"], f["participants"]) == (8217, 1, True, 1, 64, 128)
    assert [(e["score"], e["passed"], e["me"]) for e in f["entries"]] == [
        (8217, True, True), (5962, True, False), (2477, False, False), (2409, False, False), (2362, False, False)]
    assert obs.field_meta["rank"].method == "derived" and obs.field_meta["score"].method == "ocr"


def test_hif_result_round2_and_split_pt(monkeypatch):
    """第 2 回结算（玩家敗退、第 2 名）；Vision 把「Pt」拆成单独的框时也要读出分数。"""
    from hiro.vision import ocr as ocr_mod
    from hiro.vision.hif import read_hif_result

    img = cv2.imread(str(FIX / "hif_hud" / "result-r10-round2.jpg"))
    r = read_hif_result(img)
    assert (r["exam_no"], r["my_score"], r["rank"], r["passed"], r["rank_line"], r["participants"]) == (2, 48603, 2, False, 32, 64)

    real = ocr_mod.ocr_boxes

    def split_pt(crop, **kw):
        out = []
        for t, c, (x1, y1, x2, y2) in real(crop, **kw):
            if t.endswith("Pt") and t[:-2]:
                w = x2 - x1
                out += [(t[:-2], c, (x1, y1, x2 - w // 5, y2)), ("Pt", c, (x2 - w // 5, y1, x2, y2))]
            else:
                out.append((t, c, (x1, y1, x2, y2)))
        return out

    monkeypatch.setattr(ocr_mod, "ocr_boxes", split_pt)
    assert read_hif_result(img)["my_score"] == 48603


# 三选一：推荐角标（可同时挂两张）与底部「あと N回」次数
SELECT_CARD_TRUTH = {"00283": ([True, True, False], 0), "00309": ([False, True, False], 1), "00338": ([False, False, False], None)}


def test_select_card_observation():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    for name, (flags, recommended) in SELECT_CARD_TRUTH.items():
        raw, _ = extract("SELECT_CARD", load(cv2.imread(str(FIX / "select_card" / f"{name}.jpg"))))
        obs = observation(1, 0.0, "SELECT_CARD", raw)
        assert obs.problems(strict=True) == [], name
        assert [c["recommended"] for c in obs.fields["candidates"]] == flags, name
        assert obs.fields["recommended"] == recommended, name
        assert (obs.fields["excludes_left"], obs.fields["rerolls_left"]) == (3, 4), name
        assert all(c["card_id"] for c in obs.fields["candidates"]), name


@pytest.mark.user_assets("status")
def test_hif_exam_field_meta():
    from hiro.vision.observe import observation

    raw = read_hif_exam(load(cv2.imread(str(FIX / "hif_hud" / "01782.jpg"))))
    obs = observation(1, 0.0, "HIF_EXAM", raw)
    meta = obs.field_meta
    assert meta["hp"].method == "digits" and meta["attr"].method == "ocr" and meta["hand"].method == "template"
    assert meta["motivation"].confidence and meta["motivation"].confidence >= 0.6
    assert set(meta) == set(obs.known())


def test_status_absent_is_zero_only_when_column_fully_read():
    seen = {"statuses": [{"kind": "status", "key": "examcardplayaggressive", "value": 5, "score": 0.9}],
            "status": {"examcardplayaggressive": 5}}  # fmt: skip
    assert (battle_observation(seen)["motivation"], battle_observation(seen)["impression"]) == (5, 0)
    unknown_slot = {**seen, "statuses": seen["statuses"] + [{"kind": "status", "key": None, "score": 0.4}]}
    assert battle_observation(unknown_slot)["impression"] is None
    assert battle_observation({"statuses": [], "status": {}})["motivation"] is None
    legacy = {"status": {"motivation": 8, "impression": 4}}  # 早期数据集格式
    assert (battle_observation(legacy)["motivation"], battle_observation(legacy)["impression"]) == (8, 4)


@pytest.mark.user_assets("status")
def test_hif_exam_contract_fields():
    """契约 d1e05bc：attr 用 Vo/Da/Vi、千分比倍率、对手分数与名次、statuses{type,name,value,unit}。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("HIF_EXAM", load(cv2.imread(str(FIX / "hif_hud" / "01782.jpg"))))
    obs = observation(1, 0.0, "HIF_EXAM", raw, area="produce")
    assert obs.problems(strict=True) == []
    f = obs.fields
    assert (f["attr"], f["multiplier_permil"]) == ("Vi", 10850)
    assert f["npc_scores"] == [3933, 1678, 1637, 1135] and f["my_rank_index"] == 2
    assert [(s["name"], s["value"], s["unit"]) for s in f["statuses"]] == [
        ("やる気", 18, "stack"), ("エウレカ！", None, "none"), ("好印象", 18, "stack"),
        ("パラメータ上昇量増加", 20, "percent"), ("デイドリーミング", 4, "turns"),
    ]  # fmt: skip
    assert f["statuses"][0]["type"] == "ProduceExamEffectType_ExamCardPlayAggressive"


def test_action_select_advice():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("ACTION_SELECT", cv2.imread(str(FIX / "action" / "00051.jpg")))
    obs = observation(1, 0.0, "ACTION_SELECT", raw, area="produce")
    assert obs.missing() == [] and obs.fields["advice"].startswith("Pポイントがありますね")


def test_select_pitem_page():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.pitem_select import is_select_pitem

    img = load(cv2.imread(str(FIX / "select_pitem" / "e2-unselected.jpg")))
    assert is_select_pitem(img)
    for other in ["hif_hud/01782.jpg", "hif_hud/result-02416.jpg", "select_card/00283.jpg", "action/00160.jpg"]:
        assert not is_select_pitem(load(cv2.imread(str(FIX / other)))), other
    img = load(cv2.imread(str(FIX / "select_pitem" / "e2-unselected.jpg")))  # 按钮状态读的是设备当前画面
    raw, _ = extract("SELECT_PITEM", img)
    obs = observation(1, 0.0, "SELECT_PITEM", raw)
    assert obs.problems(strict=True) == []
    assert [p["color"] for p in obs.fields["pitems"]] == ["red", "yellow", "green"]
    assert [p["selected"] for p in obs.fields["pitems"]] == [False, False, False]
    assert obs.fields["selected"] is None and obs.fields["confirm_enabled"] is False


@pytest.mark.user_assets("support")
def test_start_confirm_supports():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.support import is_start_confirm

    img = load(cv2.imread(str(FIX / "start_confirm" / "00025.jpg")))
    assert is_start_confirm(img)
    for other in ["hif_hud/01782.jpg", "select_card/00283.jpg", "action/00160.jpg", "select_pitem/e2-unselected.jpg"]:
        assert not is_start_confirm(load(cv2.imread(str(FIX / other)))), other
    raw, _ = extract("PRODUCE_START_CONFIRM", img)
    obs = observation(1, 0.0, "PRODUCE_START_CONFIRM", raw)
    assert obs.problems(strict=True) == []
    assert [(s["support_card_id"], s["level"]) for s in obs.fields["supports"]] == [
        ("s_card-3-0040", 60), ("s_card-3-0001", 40), ("s_card-3-0010", 55),
        ("s_card-3-0009", 60), ("s_card-2-0007", 50), ("s_card-3-0069", 60)]  # fmt: skip
    assert obs.fields["main_attr"] == "Da"


def test_study_outing_choices():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("STUDY", cv2.imread(str(FIX / "scenes" / "00212-STUDY.jpg")))
    obs = observation(1, 0.0, "STUDY", raw)
    assert obs.problems(strict=True) == []
    assert [(c["text"], c["cost_hp"], c["trouble"]) for c in obs.fields["choices"]] == [
        ("笑顔を忘れない", 5, False), ("物怖じしない", 5, False), ("歌とダンスに合わせる", None, True)]  # fmt: skip
    assert obs.fields["selected"] is None and obs.fields["effect"] is None

    raw, _ = extract("OUTING", cv2.imread(str(FIX / "scenes" / "00257-OUTING.jpg")))
    assert [(c["text"], c["cost_p"], c["trouble"]) for c in raw["choices"]] == [
        ("人の顔でしょうか", 50, False), ("チョウに見えます", None, True), ("ハンバーガー", None, False)]  # fmt: skip

    raw, _ = extract("OUTING", cv2.imread(str(FIX / "event" / "outing-selected.jpg")))
    assert raw["selected"] == 2 and raw["choices"][2]["text"] == "研究以外のもので"
    assert raw["effect"].splitlines()[1:] == ["Pドリンクを選択して獲得", "スキルカードを選択して獲得"]


def test_consult_shelf():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    img = load(cv2.imread(str(FIX / "shop" / "consult-r5.jpg")))
    raw, _ = extract("CONSULT", img)
    obs = observation(1, 0.0, "CONSULT", raw)
    assert obs.problems(strict=True) == []
    shelf = obs.fields["shelf"]
    assert [(s["name"], s["price"], s["sale"]) for s in shelf] == [
        ("励まし+", 49, True), ("イメトレ", 35, False), ("ありがとうの言葉+", 77, False), ("ゆるふわおしゃべり+", 70, False),
        ("ブーストエキス", 52, True), ("センブリソーダ", 75, False), ("ルイボスティー", 50, False), ("リカバリドリンク", 75, False)]  # fmt: skip
    assert all(s["card_id"] for s in shelf[:4]) and all(s["drink_id"] for s in shelf[4:])
    assert not any(s["purchased"] for s in shelf)
    assert obs.fields["services"] == {"upgrade": {"price": 100, "available": True}, "delete": {"price": 100, "available": True}}
    assert obs.fields["rerolls_left"] == 1 and obs.fields["complete"] is True


def test_missing_raw_keys_are_none_not_empty():
    """识别原始结果缺键（没读）给 None；键在但为空（读到了空）才给空容器。"""
    from hiro.vision.observe import observation

    obs = observation(1, 0.0, "HIF_EXAM", {"remaining_turns": 3, "hp": 10})
    assert all(obs.fields[k] is None for k in ("hand", "drinks", "buffs", "statuses", "drink_slots"))
    obs = observation(1, 0.0, "HIF_EXAM", {"remaining_turns": 3, "hand": [], "drinks": [], "statuses": [], "drink_slots": []})
    assert obs.fields["hand"] == [] and obs.fields["statuses"] == [] and obs.fields["buffs"] == {}
    assert obs.problems(strict=True) == []


def test_hif_result_during_list_animation():
    """识别-02：名单入场动画中玩家行还没有「敗退」字样（第二局选拔②），仍读出分数；passed 按突破线横幅推出并标 derived；
    左下「あと2回」= 付费再挑戦。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("HIF_EXAM_RESULT", load(cv2.imread(str(FIX / "hif_hud" / "result-anim-140930-00039.jpg"))))
    obs = observation(1, 0.0, "HIF_EXAM_RESULT", raw)
    assert obs.problems(strict=True) == []
    f = obs.fields
    assert (f["score"], f["passed"], f["rank"], f["exam_no"], f["rank_line"], f["participants"]) == (38380, False, 2, 2, 32, 64)
    assert f["retry"] == {"left": 2, "free": False}
    assert obs.field_meta["passed"].method == "derived" and "突破线" in obs.field_meta["passed"].note
    assert [(e["score"], e["passed"], e["me"]) for e in f["entries"]] == [
        (49168, True, False), (38380, None, True), (15232, False, False), (12588, False, False), (11462, False, False)]


def test_hif_result_retry_variants():
    from hiro.vision.hif import read_hif_result

    r = read_hif_result(cv2.imread(str(FIX / "hif_hud" / "result-r10-round2.jpg")))
    assert r["retry"] == {"left": 1, "free": True} and r["passed_derived"] is False  # 「本日あと1回無料」
    r = read_hif_result(cv2.imread(str(FIX / "hif_hud" / "result-pass-retry3-142118-00038.jpg")))
    assert (r["passed"], r["my_score"], r["retry"]) == (True, 14062, {"left": 3, "free": False})


@pytest.mark.parametrize(
    ("name", "me", "scores"),
    [("result-8673-123046-00031", 8673, [8673, 5962, 2536, 2446, 2427]),  # 单次 OCR 曾读成 8073（数据确认 2026-10-31）
     ("result-2438-142118-00037", 14062, [14062, 5962, 2471, 2438, 2406])],  # 名单 OCR 读成 2458
)  # fmt: skip
def test_hif_result_scores_majority_vote(name, me, scores):
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("HIF_EXAM_RESULT", load(cv2.imread(str(FIX / "hif_hud" / f"{name}.jpg"))))
    assert raw["my_score"] == me and [e["score"] for e in raw["entries"]] == scores
    obs = observation(1, 0.0, "HIF_EXAM_RESULT", raw)
    assert obs.problems(strict=True) == [] and obs.field_meta["score"].confidence >= 0.66


@pytest.mark.parametrize(
    ("name", "excludes", "rerolls"),
    [("q6-00040-one-each", 1, 1), ("q6-00043-exclude-gone", 0, 1), ("q6-00078-both-gone", 0, 0)],
)  # fmt: skip
def test_select_card_counters_exhausted(name, excludes, rerolls):
    """次数用完时角标和按钮一起消失：给 0（以前给 None，reduce 保留旧值 1，第六局连刷三次「还剩 1」）。"""
    from hiro.vision.extract import extract

    img = load(cv2.imread(str(FIX / "select_card" / f"{name}.jpg")))
    raw, _ = extract("SELECT_CARD", img)
    assert (raw["excludes_left"], raw["rerolls_left"]) == (excludes, rerolls)
