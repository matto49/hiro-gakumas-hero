"""tools.ocr_dataset.review：待定框核对页与写回（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.4）。"""

import json

from tools.ocr_dataset import label, review, schema

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


def _box(i, status, text, answers=None):
    return {"id": i, "box": [10 + i * 100, 10, 90 + i * 100, 40], "text": text, "status": status,
            "answers": answers or {"vision": text, "llm": text, "lexicon": "", "rapidocr": ""},
            "tags": {"field": None, "type": "other"}, "ignore": status == "ignored"}  # fmt: skip


def _dataset(tmp_path):
    ds = schema.init(tmp_path / "ds")
    img = np.full((1280, 720, 3), 200, np.uint8)
    cv2.imwrite(str(ds / "core" / "dmm540-r-00001.jpg"), img)
    fr = {"schema": 1, "id": "dmm540-r-00001", "image": "dmm540-r-00001.jpg", "sha256": schema.sha256(ds / "core" / "dmm540-r-00001.jpg"),
          "source": {"run": "r", "seq": 1}, "platform": {"kind": "dmm540", "native": None, "stored": [720, 1280]}, "page": "HOME",
          "boxes": [_box(0, "agreed", "編成"),
                    _box(1, "disputed", "綿成", {"vision": "綿成", "llm": "編成", "lexicon": "", "rapidocr": "綿戍"}),
                    _box(2, "disputed", "Jslaoli", {"vision": "Jslaoli", "llm": "uboshi", "lexicon": "", "rapidocr": ""})]}  # fmt: skip
    (ds / "core" / "dmm540-r-00001.json").write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    label.rebuild_disputed(ds)
    return ds


def _frame(ds):
    return json.loads((ds / "core" / "dmm540-r-00001.json").read_text(encoding="utf-8"))


def test_build_lists_every_disputed_box_with_answers(tmp_path):
    ds = _dataset(tmp_path)
    page = review.build(ds)
    html = page.read_text(encoding="utf-8")
    assert page.parent == ds / ".work"
    assert html.count('class="item"') == 2
    assert 'data-frame="dmm540-r-00001"' in html and 'data-box="1"' in html and 'data-box="2"' in html
    for ans in ("綿成", "編成", "綿戍", "uboshi"):
        assert ans in html
    assert html.count("data:image/png;base64,") >= 4  # 每框裁图 + 上下文


def test_apply_writes_resolved_and_ignored_and_refreshes_disputed(tmp_path):
    ds = _dataset(tmp_path)
    answers = [{"frame": "dmm540-r-00001", "set": "core", "box": 1, "text": "編成"},
               {"frame": "dmm540-r-00001", "set": "core", "box": 2, "ignore": True}]  # fmt: skip
    got = review.apply(ds, answers)
    assert got == {"resolved": 1, "ignored": 1, "disputed": 0, "missing": 0}
    boxes = {b["id"]: b for b in _frame(ds)["boxes"]}
    assert boxes[1]["status"] == "resolved" and boxes[1]["text"] == "編成" and boxes[1]["ignore"] is False
    assert boxes[1]["answers"]["vision"] == "綿成"  # 原始答案保留
    assert boxes[2]["status"] == "ignored" and boxes[2]["ignore"] is True
    assert json.loads((ds / "disputed.json").read_text(encoding="utf-8")) == []
    assert schema.validate_dir(ds) == []


def test_apply_spot_check_result_can_send_agreed_back_to_disputed(tmp_path):
    ds = _dataset(tmp_path)
    review.apply(ds, [{"frame": "dmm540-r-00001", "set": "core", "box": 0, "disputed": True}])
    assert _frame(ds)["boxes"][0]["status"] == "disputed"
    assert len(json.loads((ds / "disputed.json").read_text(encoding="utf-8"))) == 3


def test_apply_reports_missing_entries(tmp_path):
    ds = _dataset(tmp_path)
    got = review.apply(
        ds,
        [
            {"frame": "nope", "set": "core", "box": 1, "text": "x"},
            {"frame": "dmm540-r-00001", "set": "core", "box": 9, "text": "x"},
        ],
    )
    assert got["missing"] == 2


def test_apply_reads_answers_file(tmp_path):
    ds = _dataset(tmp_path)
    f = tmp_path / "review-answers.json"
    f.write_text(
        json.dumps([{"frame": "dmm540-r-00001", "set": "core", "box": 1, "text": "編成"}], ensure_ascii=False),
        encoding="utf-8",
    )
    assert review.apply(ds, f)["resolved"] == 1


def _evil_dataset(tmp_path):
    """投稿者能控制 answers 的键、box 的 tags.type：这些原样进复核页 / 抽查页的 HTML 就是 XSS（PR #104）。"""
    ds = _dataset(tmp_path)
    p = ds / "core" / "dmm540-r-00001.json"
    fr = json.loads(p.read_text(encoding="utf-8"))
    for b in fr["boxes"]:
        b["answers"]["<img src=x onerror=alert(1)>"] = "綿成" if b["id"] == 1 else b["text"]
        b["tags"]["type"] = "<script>alert(2)</script>"
    p.write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    label.rebuild_disputed(ds)
    return ds


def test_review_page_escapes_answer_keys_and_tags(tmp_path):
    html = review.build(_evil_dataset(tmp_path)).read_text(encoding="utf-8")
    assert "<img src=x onerror" not in html and "<script>alert(2)" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_spot_check_page_escapes_answer_keys_and_tags(tmp_path):
    html = label.spot_check(_evil_dataset(tmp_path), n=5, seed=1).read_text(encoding="utf-8")
    assert "<img src=x onerror" not in html and "<script>alert(2)" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_apply_blank_text_is_not_a_resolved_answer(tmp_path):
    """复核页点了候选又把输入框清空：导出 text 是空串，不能写成 resolved + 真值空串（要「忽略」按钮才表示没字，PR #104）。"""
    ds = _dataset(tmp_path)
    got = review.apply(ds, [{"frame": "dmm540-r-00001", "set": "core", "box": 1, "text": "  "},
                            {"frame": "dmm540-r-00001", "set": "core", "box": 2, "text": ""}])  # fmt: skip
    assert got == {"resolved": 0, "ignored": 0, "disputed": 0, "missing": 2}
    boxes = {b["id"]: b for b in _frame(ds)["boxes"]}
    assert boxes[1]["status"] == "disputed" and boxes[1]["text"] == "綿成"
