"""tools.ocr_dataset.truth：字段真值展开、识别方法表、高代价标记、逐字段比对（规格 2026-11-23-recognition-dataset-fields-design.md §2–§3）。"""

import pytest

from tools.ocr_dataset import truth

pytestmark = pytest.mark.synthetic


def test_flatten_paths():
    got = truth.flatten({"stamina": 22, "params": {"vo": 1, "da": 2}, "drinks": ["a", "b"], "hand": [{"card_id": "x", "plus": 1}], "empty": []})
    assert got == {"stamina": 22, "params.vo": 1, "params.da": 2, "drinks[0]": "a", "drinks[1]": "b", "hand[0].card_id": "x", "hand[0].plus": 1, "empty": []}


@pytest.mark.parametrize(
    "page, path, want",
    [
        ("ACTION_SELECT", "params.vo", "ocr"),
        ("ACTION_SELECT", "stamina", "ocr"),
        ("ACTION_SELECT", "growth_pct.da", "ocr"),
        ("ACTION_SELECT", "options[0].kind", "pixel"),
        ("ACTION_SELECT", "options[0].hp_gain", "ocr"),
        ("ACTION_SELECT", "drinks[1]", "icon"),
        ("HIF_EXAM", "hand[2].card_id", "icon"),
        ("HIF_EXAM", "hand[2].available", "pixel"),
        ("HIF_EXAM", "my_score", "ocr"),
        ("HIF_EXAM", "drink_slots[0].drink_id", "icon"),
        ("HIF_EXAM", "status.examreview", "icon"),
        ("CONSULT", "shelf[1].price", "ocr"),
        ("CONSULT", "shelf[1].sale", "pixel"),
        ("CONSULT", "services.delete.price", "ocr"),
        ("HIF_EXAM", "something_new", "other"),
    ],
)
def test_method_of(page, path, want):
    assert truth.method_of(page, path) == want


@pytest.mark.parametrize(
    "page, path, want",
    [
        ("HIF_EXAM", "hand[0].card_id", True),
        ("HIF_EXAM", "hand[0].upgrade", True),
        ("HIF_EXAM", "my_score", True),
        ("CONSULT", "shelf[3].price", True),
        ("CONSULT", "services.upgrade.price", True),
        ("HIF_ROUND_INTERVAL", "interval.cards[0].price", True),
        ("ACTION_SELECT", "params.vo", False),
        ("CONSULT", "shelf[3].card_id", False),
    ],
)
def test_is_critical(page, path, want):
    assert truth.is_critical(page, path) == want


def test_compare_lists_missing_and_extra():
    rows = truth.compare({"a": 1, "b": [1, 2], "c": {"x": 1}}, {"a": 1, "b": [1], "c": {"x": 2}, "extra": 9})
    assert [(r.path, r.want, r.got, r.ok) for r in rows] == [("a", 1, 1, True), ("b[0]", 1, 1, True), ("b[1]", 2, None, False), ("c.x", 1, 2, False)]


def test_compare_got_none_marks_all_wrong():
    rows = truth.compare({"a": 1, "b": 2}, None)
    assert [r.ok for r in rows] == [False, False]
