"""tools.ocr_dataset.truth：字段真值展开、识别方法表、高代价标记、逐字段比对（规格 2026-11-23-recognition-dataset-fields-design.md §2–§3）。"""

from tools.ocr_dataset import truth

import pytest

pytestmark = pytest.mark.synthetic


def test_flatten_paths():
    got = truth.flatten(
        {
            "stamina": 22,
            "params": {"vo": 1, "da": 2},
            "drinks": ["a", "b"],
            "hand": [{"card_id": "x", "plus": 1}],
            "empty": [],
        }
    )
    assert got == {
        "stamina": 22,
        "params.vo": 1,
        "params.da": 2,
        "drinks[0]": "a",
        "drinks[1]": "b",
        "hand[0].card_id": "x",
        "hand[0].plus": 1,
        "empty": [],
    }


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
    assert [(r.path, r.want, r.got, r.ok) for r in rows] == [
        ("a", 1, 1, True),
        ("b[0]", 1, 1, True),
        ("b[1]", 2, None, False),
        ("c.x", 1, 2, False),
    ]


def test_compare_got_none_marks_all_wrong():
    rows = truth.compare({"a": 1, "b": 2}, None)
    assert [r.ok for r in rows] == [False, False]


def test_compare_missing_equals_none():
    """读数取稳定项时会丢掉值为 None 的键：真值是 None、读数里没有这个键，算对。"""
    rows = truth.compare({"statuses": [{"card_id": None, "value": 3}]}, {"statuses": [{"value": 3}]})
    assert [(r.path, r.ok) for r in rows] == [("statuses[0].card_id", True), ("statuses[0].value", True)]


def test_compare_does_not_count_bool_and_number_as_same():
    """真值 purchased: true、读到 1：Python 的 == 会算对，但这是两种东西（严格比对，PR #104）。"""
    rows = truth.compare(
        {"purchased": True, "n": 1, "z": 0, "f": False}, {"purchased": 1, "n": True, "z": False, "f": 0}
    )
    assert [r.ok for r in rows] == [False, False, False, False]
    assert all(
        r.ok for r in truth.compare({"purchased": True, "n": 1, "f": False}, {"purchased": True, "n": 1, "f": False})
    )
    assert all(r.ok for r in truth.compare({"x": 1}, {"x": 1.0}))  # JSON 里 1 和 1.0 不区分
    assert truth.compare({"a": None}, {})[0].ok  # 真值 null 允许缺键，照旧
