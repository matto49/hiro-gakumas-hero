"""tools.ocr_dataset.scenario_eval：真实系统读数 → 场景信息项（适配器），逐项打分：读对 / 读错 / 没读（规格 v3 §5）。"""

from tools.ocr_dataset import scenario_eval as se

import pytest

pytestmark = pytest.mark.synthetic

EXAM_RAW = {
    "remaining_turns": 9, "attr": "vocal", "multiplier_pct": 2805, "finals": True, "my_rank_index": 3, "my_score": 0, "npc_scores": [400729, 303833],
    "rank_line": None, "participants": None, "genki": 0, "hp": 27, "deck_count": None,
    "drink_slots": [{"slot": 0, "state": "drink"}, {"slot": 1, "state": "empty"}],
    "statuses": [{"slot": 1, "kind": "enchant", "card_id": "c1", "value": 6}, {"slot": 0, "kind": "status", "name": "好印象", "value": 5}, {"slot": 2, "kind": "status", "name": "パラメータ上昇量増加", "value": 10, "unit": "%"}],
    "hand": [{"slot": 0, "name": "気まぐれハート+", "plus": 1, "genki_cost": 1, "hp_cost": None, "extra_cost": None, "badge_score": None},
             {"slot": 1, "name": "ありがとうの言葉+", "plus": 1, "genki_cost": None, "hp_cost": 3, "extra_cost": None, "badge_score": 337},
             {"slot": 2, "name": "きらきら紙吹雪+", "plus": 1, "genki_cost": None, "hp_cost": None, "extra_cost": 2, "badge_score": None}],
}  # fmt: skip


def test_adapt_exam():
    got = se.adapt("exam", EXAM_RAW)
    assert got["turns_left"] == 9 and got["exam_attr"] == "vocal" and got["hp"] == 27 and got["genki"] == 0
    assert got["top_scores"] == [400729, 303833, 0]  # 自己按名次插进去
    assert got["hand"] == [{"name": "気まぐれハート", "plus": True, "cost_type": "green_heart", "cost": 1},
                           {"name": "ありがとうの言葉", "plus": True, "cost_type": "red_heart", "cost": 3},
                           {"name": "きらきら紙吹雪", "plus": True, "cost_type": "buff", "cost": 2}]  # fmt: skip
    assert got["selected_preview"] == 337 and got["drinks_count"] == 1
    assert got["statuses"] == [
        {"kind": "status", "name": "好印象", "value": 5, "pct": None},
        {"kind": "card_effect", "name": None, "value": 6, "pct": None},
        {"kind": "status", "name": "パラメータ上昇量増加", "value": None, "pct": 10},
    ]
    assert got["deck_left"] is None and "breakthrough_rank" in got


def test_adapt_consult_and_interval():
    raw = {"stamina": 9, "max_stamina": 33, "produce_point": 280,
           "shelf": [{"index": 1, "kind": "card", "price": 77, "sale": False, "purchased": False}, {"index": 0, "kind": "drink", "price": 53, "sale": True, "purchased": True}],
           "services": {"upgrade": {"price": 150, "used": False}, "delete": {"price": 125, "used": True}}}  # fmt: skip
    got = se.adapt("consult", raw)
    assert got["hp"] == 9 and got["p_points"] == 280 and got["delete_used"] is True
    assert got["shelf"] == [
        {"kind": "drink", "price": 53, "purchased": True},
        {"kind": "card", "price": 77, "purchased": False},
    ]  # 系统不读 SALE 原价：不给这一项 → 没读
    iv = {"stamina": 17, "max_stamina": 33, "produce_point": 428, "interval": {
        "cards": [{"index": 0, "price": 50, "purchased": False}], "changes": [{"index": 2, "price": 30, "purchased": False}],
        "drinks": [{"index": 1, "price": 100, "purchased": True}], "upgrade": {"price": 100, "left": 1}, "customize": {"left": 2}, "refresh": {"price": 10}}}  # fmt: skip
    got = se.adapt("round_interval", iv)
    assert (
        [s["kind"] for s in got["shelf"]] == ["card", "drink", "card"]
        and got["upgrade_left"] == 1
        and got["refresh_price"] == 10
    )


def test_adapt_action_select_keys_actions_by_label():
    raw = {"exam": "選抜試験", "days_left": 4, "stamina": 22, "max_stamina": 33, "p_points": 250, "params": {"vo": 1, "da": 2, "vi": 3},
           "param_caps": {"vo": 9, "da": 9, "vi": 9}, "growth_pct": {"vo": 45.5, "da": 44.0, "vi": 41.3}, "star": 731,
           "options": [{"label": "休む", "hp_gain": 11, "hp_cost": None}, {"label": "授業", "hp_gain": None, "hp_cost": 5}], "drinks": ["a"]}  # fmt: skip
    got = se.adapt("action_select", raw)
    assert got["params"] == [1, 2, 3] and got["growth_pct"] == ["45.5%", "44%", "41.3%"]
    assert got["actions"] == [{"label": "休む", "hp_change": 11}, {"label": "授業", "hp_change": -5}]


def test_score_right_wrong_unread_and_skips():
    truth = {"hp": 27, "genki": 0, "deck_left": None, "selected_preview": None, "top_scores": [403729, 303833],
             "hand": [{"name": "イメトレ", "plus": False}], "multiplier_pct": 2805}  # fmt: skip
    got = {
        "hp": 27,
        "genki": 1,
        "selected_preview": 120,
        "top_scores": [400729, 303833],
        "hand": [{"name": "イメトレ", "plus": False}],
    }
    rows = se.score_frame("exam", truth, got, item_status={"multiplier_pct": "disputed"})
    res = {r["path"]: r["result"] for r in rows}
    assert res == {"hp": "对", "genki": "错", "selected_preview": "错", "top_scores[0]": "错", "top_scores[1]": "对",
                   "hand[0].name": "对", "hand[0].plus": "对"}  # fmt: skip
    # deck_left：真值 null 且系统没读 → 不计；multiplier_pct 待定 → 不计


def test_score_unread_when_system_lacks_item():
    rows = se.score_frame("consult", {"shelf": [{"price": 53, "purchased": True}]}, {"shelf": [{"price": 53}]})
    assert {r["path"]: r["result"] for r in rows} == {"shelf[0].price": "对", "shelf[0].purchased": "没读"}


def test_score_actions_aligned_by_label():
    truth = {"actions": [{"label": "おでかけ", "hp_change": None}, {"label": "休む", "hp_change": 11}]}
    got = {"actions": [{"label": "休む", "hp_change": 11}, {"label": "おでかけ", "hp_change": None}]}
    rows = se.score_frame("action_select", truth, got)
    assert {r["path"]: r["result"] for r in rows} == {
        "actions[おでかけ].label": "对",
        "actions[休む].label": "对",
        "actions[休む].hp_change": "对",
    }


def _fr(fid, sid, truth, buttons=(), status=None):
    return {
        "id": fid,
        "scenario": sid,
        "platform": {"kind": "dmm"},
        "truth": truth,
        "item_status": status or {},
        "buttons": list(buttons),
    }


def test_evaluate_aggregates_items_buttons_and_page():
    frames = [_fr("a", "exam", {"hp": 27, "genki": 0}, buttons=[{"label": "SKIP", "box": [500, 700, 600, 800]}]),
              _fr("b", "consult", {"p_points": 280, "hp": 9})]  # fmt: skip
    reads = {"a": {"page": "HIF_EXAM", "fields": {"hp": 27, "genki": 3}, "texts": [("SKIP", (520, 720, 580, 760))]},
             "b": {"page": "ACTION_SELECT", "fields": {"stamina": 9}, "texts": []}}  # fmt: skip
    r = se.evaluate(frames, reads, backend="vision")
    a = r["groups"]["all"]
    assert (a["对"], a["错"], a["没读"]) == (
        3,
        1,
        1,
    )  # a: hp 对、genki 错、SKIP 对；b: 判成别的页 → 按别的页读不出 → 没读 / 对（hp 碰巧在顶栏）
    assert r["groups"]["page"]["对"] == 1 and r["groups"]["page"]["错"] == 1
    assert {e["path"] for e in r["errors"]} == {"genki", "p_points"}


def test_button_found_outside_box_is_wrong():
    frames = [
        _fr("a", "exam", {}, buttons=[{"label": "SKIP", "box": [500, 700, 600, 800]}, {"label": "決定", "box": None}])
    ]
    r = se.evaluate(
        frames, {"a": {"page": "HIF_EXAM", "fields": {}, "texts": [("SKIP", (10, 10, 50, 30))]}}, backend="x"
    )
    assert r["groups"]["all"]["错"] == 1 and r["groups"]["all"]["n"] == 1  # 決定 没有位置真值：不计


def test_report_md_has_unread_section():
    frames = [_fr("b", "consult", {"shelf": [{"price": 53, "purchased": True}]})]
    r = se.evaluate(
        frames, {"b": {"page": "CONSULT", "fields": {"shelf": [{"price": 53}]}, "texts": []}}, backend="vision"
    )
    md = se.report_md([r])
    assert "没读" in md and "shelf[].purchased" in md


def test_zero_cost_equals_no_cost():
    truth = {"hand": [{"name": "リズミカル", "plus": False, "cost_type": "green_heart", "cost": 0}]}
    got = {"hand": [{"name": "リズミカル", "plus": False, "cost_type": "none", "cost": None}]}
    assert {r["path"]: r["result"] for r in se.score_frame("exam", truth, got)} == {
        "hand[0].name": "对",
        "hand[0].plus": "对",
    }  # 消耗按「没有」算，可由主数据补的项不计


def test_truncated_name_and_hidden_cost_are_not_errors():
    truth = {"hand": [{"name": "ゆるふわおしゃ", "plus": False, "cost_type": "none", "cost": None},
                      {"name": "頭上に注意", "plus": False, "cost_type": "red_heart", "cost": 3}]}  # fmt: skip
    got = {"hand": [{"name": "ゆるふわおしゃべり", "plus": False, "cost_type": "green_heart", "cost": 4},
                    {"name": "頭上に注意!", "plus": False, "cost_type": "red_heart", "cost": 3}]}  # fmt: skip
    res = {r["path"]: r["result"] for r in se.score_frame("exam", truth, got)}
    assert res == {
        "hand[0].name": "对",
        "hand[0].plus": "对",
        "hand[1].name": "对",
        "hand[1].plus": "对",
        "hand[1].cost_type": "对",
        "hand[1].cost": "对",
    }


def test_prefix_rule_needs_two_chars_and_only_for_names():
    res = {
        r["path"]: r["result"]
        for r in se.score_frame("exam", {"hand": [{"name": "ゆ"}]}, {"hand": [{"name": "ゆるふわ"}]})
    }
    assert res["hand[0].name"] == "错"


def test_turns_left_counts_extra_turns():
    truth = {"turns_left": 3, "extra_turns": 1}
    assert {r["path"]: r["result"] for r in se.score_frame("exam", truth, {"turns_left": 4})} == {"turns_left": "对"}
    assert {
        r["path"]: r["result"] for r in se.score_frame("exam", {"turns_left": 3, "extra_turns": 0}, {"turns_left": 3})
    } == {"turns_left": "对"}


def test_selection_exam_inserts_own_score_too():
    got = se.adapt(
        "exam", {**EXAM_RAW, "finals": False, "my_score": 143069, "my_rank_index": 1, "npc_scores": [134524, 39654]}
    )
    assert got["top_scores"] == [143069, 134524, 39654]


def test_button_check_prefers_best_matching_text():
    texts = [("持っているスキルカードの強化や削除ができます。", (115, 884, 595, 912)), ("削除", (443, 954, 508, 984))]
    rows = se.score_buttons([{"label": "削除", "box": [443, 954, 508, 984]}], texts)
    assert rows[0]["result"] == "对" and rows[0]["got"] == "削除"


def test_unscored_items_are_kept_but_not_scored():
    """用户 11-23：NEW 标记、SALE 原价不识别也没事——真值留着，不计分。"""
    truth = {"shelf": [{"kind": "card", "price": 53, "sale_from": 77, "purchased": False}]}
    got = {"shelf": [{"kind": "card", "price": 53, "purchased": False}]}
    assert {r["path"] for r in se.score_frame("consult", truth, got)} == {
        "shelf[0].kind",
        "shelf[0].price",
        "shelf[0].purchased",
    }
    truth = {"shelf": [{"kind": "card", "price": 50, "new": True, "purchased": False}]}
    assert "shelf[0].new" not in {
        r["path"] for r in se.score_frame("round_interval", truth, {"shelf": [{"kind": "card", "price": 50}]})
    }


def test_excluded_frames_are_not_scored():
    frames = [
        {**_fr("a", "consult", {"p_points": 280}), "excluded": "弹窗盖住背景，背景不用读"},
        _fr("b", "consult", {"p_points": 280}),
    ]
    reads = {
        "a": {"page": "DELETE_RESULT", "fields": {}, "texts": []},
        "b": {"page": "CONSULT", "fields": {"produce_point": 280}, "texts": []},
    }
    r = se.evaluate(frames, reads, backend="vision")
    assert r["frames"] == 1 and r["groups"]["all"]["n"] == 1 and r["groups"]["page"]["n"] == 1


def test_plus_hidden_by_stacking_is_not_scored():
    truth = {"hand": [{"name": "あのときの", "plus": None}]}
    got = {"hand": [{"name": "あのときの約束", "plus": False}]}
    assert {r["path"]: r["result"] for r in se.score_frame("exam", truth, got)} == {"hand[0].name": "对"}


def test_hand_cost_is_compared_after_engine_conversion():
    """画面显示的是按状态换算后的消耗：消費体力減少 ×0.5 向上取整、消費体力増加 ×2、再减 消費体力削減（引擎 calculate_damage）。"""
    raw = {**EXAM_RAW, "statuses": [{"slot": 0, "name": "消費体力減少", "value": 3}, {"slot": 1, "name": "消費体力削減", "value": 1}],
           "hand": [{"slot": 0, "name": "成長痛++", "genki_cost": 4}, {"slot": 1, "name": "距離感の基本", "hp_cost": 3},
                    {"slot": 2, "name": "きらきら紙吹雪+", "extra_cost": 2}]}  # fmt: skip
    got = se.adapt("exam", raw)
    assert [h["cost"] for h in got["hand"]] == [1, 1, 2]  # 4→2→1、3→2→1；buff 消耗不受体力状态影响
    up = se.adapt("exam", {**raw, "statuses": [{"slot": 0, "name": "消費体力増加", "value": 1}]})
    assert [h["cost"] for h in up["hand"]] == [8, 6, 2]


def test_status_with_turns_and_percent():
    raw = {
        **EXAM_RAW,
        "statuses": [
            {"slot": 0, "kind": "status", "name": "パラメータ上昇量増加", "value": 20, "unit": "%", "turns": 5}
        ],
    }
    assert se.adapt("exam", raw)["statuses"] == [
        {"kind": "status", "name": "パラメータ上昇量増加", "value": 5, "pct": 20}
    ]


@pytest.mark.parametrize("state", ["disputed", "ignored"])
def test_pending_extra_turns_keeps_the_summed_turns_out_of_scoring(state):
    """turns_left 在打分时 = 圆环大数字 + 额外回合；额外回合待定 / 忽略时，合出来的值不能参与计分（PR #104）。"""
    truth = {"turns_left": 9, "extra_turns": 2}
    rows = se.score_frame("exam", truth, {"turns_left": 9}, item_status={"extra_turns": state})
    assert [r["path"] for r in rows] == []
    rows = se.score_frame("exam", truth, {"turns_left": 11})  # 两项都能计分时照旧相加
    assert [(r["path"], r["result"]) for r in rows] == [("turns_left", "对")]


def test_pending_status_given_by_index_path_skips_keyed_list_items():
    """标注端 item_status 的键是下标路径（actions[0].hp_change），打分端按 label 对齐成 actions[休む].hp_change：
    待定的行动项以前查不到，照样被打分（PR #104）。"""
    truth = {"actions": [{"label": "おでかけ", "hp_change": 3}, {"label": "休む", "hp_change": 11}]}
    got = {"actions": [{"label": "休む", "hp_change": 99}, {"label": "おでかけ", "hp_change": 3}]}
    rows = se.score_frame("action_select", truth, got, item_status={"actions[1].hp_change": "disputed"})
    res = {r["path"]: r["result"] for r in rows}
    assert "actions[休む].hp_change" not in res and res["actions[おでかけ].hp_change"] == "对"
    rows = se.score_frame("action_select", truth, got, item_status={"actions[0].hp_change": "ignored"})
    res = {r["path"]: r["result"] for r in rows}
    assert "actions[おでかけ].hp_change" not in res and res["actions[休む].hp_change"] == "错"
