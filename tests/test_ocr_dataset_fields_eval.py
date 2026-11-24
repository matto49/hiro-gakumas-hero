"""tools.ocr_dataset.fields_eval：字段评测打分与报告（规格 2026-11-23-recognition-dataset-fields-design.md §6）。"""

from tools.ocr_dataset import fields_eval as fe

import pytest

pytestmark = pytest.mark.synthetic


def _frame(fid, page, fields, status=None, checks=None, quick_fields=None):
    t = {"fields": fields}
    if checks:
        t["checks"] = checks
    if quick_fields:
        t["quick_fields"] = quick_fields
    return {"id": fid, "page": page, "platform": {"kind": "dmm"}, "truth": t, "field_status": status or {}}


FRAMES = [
    _frame("a", "ACTION_SELECT", {"stamina": 22, "params": {"vo": 1473, "da": 2336}, "options": [{"kind": "rest"}]},
           checks={"quick": "ACTION_SELECT", "scene": "ACTION_SELECT"}),
    _frame("b", "HIF_EXAM", {"my_score": 1200, "hand": [{"card_id": "c1", "upgrade": 1}, {"card_id": "c2", "upgrade": 0}]},
           status={"hand[1].upgrade": "disputed"}),
    _frame("c", "CONSULT", {"shelf": [{"price": 70}]}, quick_fields={"rerolls_left": 1}),
]  # fmt: skip

READS = {
    "a": {"quick": "ACTION_SELECT", "scene": "ACTION_SELECT", "fields": {"stamina": 22, "params": {"vo": 1478, "da": 2336}, "options": [{"kind": "rest"}]}},
    "b": {"quick": "HIF_EXAM", "scene": "HIF_EXAM", "fields": {"my_score": 1200, "hand": [{"card_id": "c1", "upgrade": 1}, {"card_id": "cX", "upgrade": 1}]}},
    "c": {"quick": None, "scene": "CONSULT", "fields": None, "quick_fields": {"rerolls_left": 1}},
}  # fmt: skip


def test_score_counts_by_method_page_and_critical():
    r = fe.score(FRAMES, READS, backend="rapidocr", seconds={"a": 9.0, "b": 1.0, "c": 2.0})
    a = r["groups"]["all"]
    # a: stamina ✓ vo ✗ da ✓ kind ✓ quick ✓ scene ✓；b: score ✓ c1 ✓ up ✓ c2 ✗（hand[1].upgrade 待定不计）；c: price ✗（fields 读不到）、quick rerolls ✓
    assert (a["n"], a["ok"]) == (12, 9)
    assert r["groups"]["method"]["ocr"] == {"n": 6, "ok": 4, "acc": pytest.approx(4 / 6)}
    assert r["groups"]["method"]["icon"]["ok"] == 1 and r["groups"]["method"]["page"]["n"] == 2
    crit = dict(r["groups"]["critical"].items())
    assert crit["HIF_EXAM hand[].card_id"] == {"n": 2, "ok": 1, "acc": 0.5}
    assert crit["CONSULT shelf[].price"]["ok"] == 0
    assert r["timing"]["p50"] == pytest.approx(1.5)  # 第一帧（含加载）不算
    errs = {(e["frame"], e["path"]) for e in r["errors"]}
    assert errs == {("a", "params.vo"), ("b", "hand[1].card_id"), ("c", "shelf[0].price")}
    assert r["disputed"] == 1


def test_report_puts_critical_table_first():
    r1 = fe.score(FRAMES, READS, backend="rapidocr")
    r2 = fe.score(FRAMES, {k: {**v, "fields": None} for k, v in READS.items()}, backend="vision")
    md = fe.report_md([r1, r2])
    assert md.index("高代价字段") < md.index("总表") < md.index("按页面")
    assert "| HIF_EXAM | hand[].card_id |" in md and "params.vo" in md
