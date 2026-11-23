"""tools.ocr_dataset.scenario_eval：真实系统读数 → 场景信息项（适配器），逐项打分：读对 / 读错 / 没读（规格 v3 §5）。"""

import pytest

from tools.ocr_dataset import scenario_eval as se

pytestmark = pytest.mark.synthetic

EXAM_RAW = {
    "remaining_turns": 9, "attr": "vocal", "multiplier_pct": 2805, "finals": True, "my_rank_index": 3, "my_score": 0, "npc_scores": [400729, 303833],
    "rank_line": None, "participants": None, "genki": 0, "hp": 27, "deck_count": None,
    "drink_slots": [{"slot": 0, "state": "drink"}, {"slot": 1, "state": "empty"}],
    "statuses": [{"slot": 1, "value": 6}, {"slot": 0, "value": 5}],
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
    assert got["selected_preview"] == 337 and got["status_values"] == [5, 6] and got["drinks_count"] == 1
    assert got["deck_left"] is None and "breakthrough_rank" in got


def test_adapt_consult_and_interval():
    raw = {"stamina": 9, "max_stamina": 33, "produce_point": 280,
           "shelf": [{"index": 1, "kind": "card", "price": 77, "sale": False, "purchased": False}, {"index": 0, "kind": "drink", "price": 53, "sale": True, "purchased": True}],
           "services": {"upgrade": {"price": 150, "used": False}, "delete": {"price": 125, "used": True}}}  # fmt: skip
    got = se.adapt("consult", raw)
    assert got["hp"] == 9 and got["p_points"] == 280 and got["delete_used"] is True
    assert got["shelf"] == [{"kind": "drink", "price": 53, "purchased": True}, {"kind": "card", "price": 77, "purchased": False}]  # 系统不读 SALE 原价：不给这一项 → 没读
    iv = {"stamina": 17, "max_stamina": 33, "produce_point": 428, "interval": {
        "cards": [{"index": 0, "price": 50, "purchased": False}], "changes": [{"index": 2, "price": 30, "purchased": False}],
        "drinks": [{"index": 1, "price": 100, "purchased": True}], "upgrade": {"price": 100, "left": 1}, "customize": {"left": 2}, "refresh": {"price": 10}}}  # fmt: skip
    got = se.adapt("round_interval", iv)
    assert [s["kind"] for s in got["shelf"]] == ["card", "drink", "card"] and got["upgrade_left"] == 1 and got["refresh_price"] == 10


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
    got = {"hp": 27, "genki": 1, "selected_preview": 120, "top_scores": [400729, 303833], "hand": [{"name": "イメトレ", "plus": False}]}
    rows = se.score_frame("exam", truth, got, item_status={"multiplier_pct": "disputed"})
    res = {r["path"]: r["result"] for r in rows}
    assert res == {"hp": "对", "genki": "错", "selected_preview": "错", "top_scores[0]": "错", "top_scores[1]": "对",
                   "hand[0].name": "对", "hand[0].plus": "对"}  # fmt: skip
    # deck_left：真值 null 且系统没读 → 不计；multiplier_pct 待定 → 不计


def test_score_unread_when_system_lacks_item():
    rows = se.score_frame("consult", {"shelf": [{"price": 53, "sale_from": 77}]}, {"shelf": [{"price": 53}]})
    assert {r["path"]: r["result"] for r in rows} == {"shelf[0].price": "对", "shelf[0].sale_from": "没读"}


def test_score_actions_aligned_by_label():
    truth = {"actions": [{"label": "おでかけ", "hp_change": None}, {"label": "休む", "hp_change": 11}]}
    got = {"actions": [{"label": "休む", "hp_change": 11}, {"label": "おでかけ", "hp_change": None}]}
    rows = se.score_frame("action_select", truth, got)
    assert {r["path"]: r["result"] for r in rows} == {"actions[おでかけ].label": "对", "actions[休む].label": "对", "actions[休む].hp_change": "对"}


def _fr(fid, sid, truth, buttons=(), status=None):
    return {"id": fid, "scenario": sid, "platform": {"kind": "dmm"}, "truth": truth, "item_status": status or {}, "buttons": list(buttons)}


def test_evaluate_aggregates_items_buttons_and_page():
    frames = [_fr("a", "exam", {"hp": 27, "genki": 0}, buttons=[{"label": "SKIP", "box": [500, 700, 600, 800]}]),
              _fr("b", "consult", {"p_points": 280, "hp": 9})]  # fmt: skip
    reads = {"a": {"page": "HIF_EXAM", "fields": {"hp": 27, "genki": 3}, "texts": [("SKIP", (520, 720, 580, 760))]},
             "b": {"page": "ACTION_SELECT", "fields": {"stamina": 9}, "texts": []}}  # fmt: skip
    r = se.evaluate(frames, reads, backend="vision")
    a = r["groups"]["all"]
    assert (a["对"], a["错"], a["没读"]) == (3, 1, 1)  # a: hp 对、genki 错、SKIP 对；b: 判成别的页 → 按别的页读不出 → 没读 / 对（hp 碰巧在顶栏）
    assert r["groups"]["page"]["对"] == 1 and r["groups"]["page"]["错"] == 1
    assert {e["path"] for e in r["errors"]} == {"genki", "p_points"}


def test_button_found_outside_box_is_wrong():
    frames = [_fr("a", "exam", {}, buttons=[{"label": "SKIP", "box": [500, 700, 600, 800]}, {"label": "決定", "box": None}])]
    r = se.evaluate(frames, {"a": {"page": "HIF_EXAM", "fields": {}, "texts": [("SKIP", (10, 10, 50, 30))]}}, backend="x")
    assert r["groups"]["all"]["错"] == 1 and r["groups"]["all"]["n"] == 1  # 決定 没有位置真值：不计


def test_report_md_has_unread_section():
    frames = [_fr("b", "consult", {"shelf": [{"price": 53, "sale_from": 77}]})]
    r = se.evaluate(frames, {"b": {"page": "CONSULT", "fields": {"shelf": [{"price": 53}]}, "texts": []}}, backend="vision")
    md = se.report_md([r])
    assert "没读" in md and "shelf[].sale_from" in md
