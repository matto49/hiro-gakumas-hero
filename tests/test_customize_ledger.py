"""任务 ②：特別指導记下定制了哪一项（CARD_CUSTOMIZE 带 customize_id / levels），牌组记账存下，搜索起点的牌组带上定制次数。"""

from hiro.contracts import Event, GameState
from hiro.loop.events import _apply_deck, events_from_step

import pytest

CZ = "p_card_custom-040_040_070-g_effect-p_card-02-act-1_037-enc_1-g_effect-p_card-02-act-1_037-enc_2-g_effect-p_card-02-act-1_037-enc_3"


def _state(deck):
    return GameState(produce={"deck": [dict(c) for c in deck]})


def test_customize_action_recorded_and_survives_deck_review():
    args = {"card_index": 1, "card_id": "p_card-02-act-1_037", "customize_id": CZ, "item_index": 0, "levels": 3}
    ev = events_from_step("SPECIAL_GUIDANCE", {"kind": "CustomizeCard", "args": args}, True, {}, None, seq=5)
    assert [e.kind for e in ev] == ["CARD_CUSTOMIZE"] and ev[0].args["levels"] == 3
    s = _state([{"card_id": "x", "upgrade": 0}, {"card_id": "p_card-02-act-1_037", "upgrade": 1}])
    _apply_deck(s, ev[0])
    assert s.produce["deck"][1]["customize"] == {CZ: 3}
    review = Event(
        seq=6,
        kind="DECK_REVIEW",
        origin="observed",
        args={"deck": [{"card_id": "p_card-02-act-1_037", "upgrade": 1}, {"card_id": "x"}]},
    )
    _apply_deck(s, review)
    assert s.produce["deck"][0]["customize"] == {CZ: 3} and "customize" not in s.produce["deck"][1]


@pytest.mark.master
def test_customize_counts_follow_master_order():
    from hiro.search.loadout import customize_counts, parse_deck_entry

    cid, up, cz = parse_deck_entry(f"p_card-02-act-1_037+1/{CZ}*3")
    assert (cid, up, cz) == ("p_card-02-act-1_037", 1, {CZ: 3})
    counts = customize_counts(cid, up, cz)
    assert counts is not None and sum(counts) == 3 and customize_counts(cid, up, {}) is None


def test_parse_deck_entry_multiple_customizations():
    from hiro.search.loadout import parse_deck_entry

    s = "p_card-03-act-2_070+1/p_card_custom-070-g_effect-effect_change-e_effect-exam_concentration-0001-e_effect-exam_concentration-0002/p_card_custom-040_070-g_effect-lesson_add-6-g_effect-lesson_add-22*2"
    cid, up, cz = parse_deck_entry(s)
    assert (cid, up) == ("p_card-03-act-2_070", 1)
    assert cz == {
        "p_card_custom-070-g_effect-effect_change-e_effect-exam_concentration-0001-e_effect-exam_concentration-0002": 1,
        "p_card_custom-040_070-g_effect-lesson_add-6-g_effect-lesson_add-22": 2,
    }


def test_parse_deck_entry_public_dict():
    from hiro.search.loadout import parse_deck_entry

    d = {
        "id": "p_card-01-act-2_001",
        "upgrade_count": 1,
        "customizes": [{"id": "p_card_custom-040-x", "customize_count": 1}],
    }
    assert parse_deck_entry(d) == ("p_card-01-act-2_001", 1, {"p_card_custom-040-x": 1})
    assert parse_deck_entry({"id": "p_card-00-men-2_012"}) == ("p_card-00-men-2_012", 0, {})
