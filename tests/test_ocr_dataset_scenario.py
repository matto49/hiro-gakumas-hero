"""tools.ocr_dataset.scenario：场景定义、标注提示词、答案归一、两方比对（规格 2026-11-23-recognition-dataset-scenarios-design.md §2–§4）。"""

from tools.ocr_dataset import scenario

import pytest

pytestmark = pytest.mark.synthetic


def test_definitions_load_and_map_pages():
    sc = scenario.load()
    assert {"exam", "consult", "round_interval", "action_select"} <= set(sc)
    assert scenario.for_page("HIF_EXAM") == "exam" and scenario.for_page("CONSULT") == "consult"
    assert scenario.for_page("TOWER_LIST") is None
    for sid, d in sc.items():
        for it in d["items"]:
            assert it["type"] in scenario.TYPES, (sid, it["key"])


def test_prompt_lists_every_item_and_forbids_guessing():
    system, user = scenario.prompt("exam")
    for key in ("turns_left", "multiplier_pct", "hand", "cost_type", "statuses"):
        assert key in user
    assert "null" in system and "JSON" in system


def test_normalize_coerces_types():
    raw = {"turns_left": "9", "exam_attr": "Vocal", "multiplier_pct": "2,805%", "top_scores": ["403,729", 303833],
           "hp": 27, "genki": "0", "hand": [{"name": " 気まぐれハート ", "plus": "true", "cost_type": "GREEN_HEART", "cost": "1"}],
           "selected_preview": None, "statuses": [{"kind": "Status", "name": "好印象", "value": "6"}, {"kind": "card_effect", "name": None, "value": "1回", "pct": "50%"}], "unknown_key": 1}  # fmt: skip
    got = scenario.normalize("exam", raw)
    assert got["turns_left"] == 9 and got["exam_attr"] == "vocal" and got["multiplier_pct"] == 2805
    assert got["top_scores"] == [403729, 303833] and got["genki"] == 0
    assert got["hand"] == [{"name": "気まぐれハート", "plus": True, "cost_type": "green_heart", "cost": 1}]
    assert got["selected_preview"] is None
    assert got["statuses"] == [
        {"kind": "status", "name": "好印象", "value": 6, "pct": None},
        {"kind": "card_effect", "name": None, "value": 1, "pct": 50},
    ]
    assert "unknown_key" not in got and "deck_left" not in got  # 没给的项不补


def test_normalize_bad_enum_becomes_raw_text_marker():
    got = scenario.normalize("exam", {"exam_attr": "ボーカル"})
    assert got["exam_attr"] == "?ボーカル"  # 不在取值里：原样留下带 ? 前缀，比对时必然不一致 → 进核对


def test_merge_two_answers():
    a = {"turns_left": 9, "hp": 27, "hand": [{"name": "イメトレ", "plus": False}], "top_scores": [1, 2]}
    b = {
        "turns_left": 9,
        "hp": 21,
        "hand": [{"name": "イメトレ", "plus": False}, {"name": "x", "plus": True}],
        "top_scores": [1, 2],
    }
    truth, status = scenario.merge(a, b)
    assert truth["turns_left"] == 9 and truth["hand"][0]["name"] == "イメトレ"
    assert status == {"hp": "disputed", "hand[1].name": "disputed", "hand[1].plus": "disputed"}
    assert truth["hp"] == 27  # 待定项先填第一方的读法


def test_abs_fields_drop_sign():
    got = scenario.normalize("exam", {"hand": [{"name": "a", "plus": False, "cost_type": "hp", "cost": "-2"}]})
    assert got["hand"][0]["cost"] == 2
