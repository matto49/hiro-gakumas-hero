"""tools.ocr_dataset.scenario_page：场景真值核对 + 系统校验结果页。"""

import json

from tools.ocr_dataset import comments_server as cs
from tools.ocr_dataset import scenario_page as sp
from tools.ocr_dataset import schema

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


def _ds(tmp_path):
    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "dmm-r-00001.jpg"), np.full((1280, 720, 3), 80, np.uint8))
    fr = {"schema": 3, "id": "dmm-r-00001", "image": "dmm-r-00001.jpg", "scenario": "exam", "page": "HIF_EXAM", "platform": {"kind": "dmm"},
          "truth": {"hp": 27, "genki": 0}, "item_status": {"genki": "disputed"},
          "answers": {"claude": {"items": {"hp": 27, "genki": 0}}, "codex": {"items": {"hp": 27, "genki": 3}}},
          "buttons": [{"label": "SKIP", "box": [500, 700, 600, 800]}]}  # fmt: skip
    (ds / "core" / "dmm-r-00001.json").write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    results = {"vision": {"dmm-r-00001": {"page": "HIF_EXAM", "fields": {"hp": 27, "genki": 0}, "texts": [("SKIP", (520, 720, 580, 760))]}},
               "rapidocr": {"dmm-r-00001": {"page": "HIF_EXAM", "fields": {"hp": 21}, "texts": []}}}  # fmt: skip
    return ds, results


def test_page_shows_truth_answers_and_system_results(tmp_path):
    ds, reads = _ds(tmp_path)
    html = sp.build(ds, reads, out=tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert html.count('class="frame"') == 1
    assert 'data-field="hp"' in html and 'data-field="genki"' in html and 'data-field="button:SKIP"' in html
    assert (
        html.count('class="r-错"') == 1 and html.count('class="r-没读"') >= 2
    )  # rapidocr：hp 读错、genki 没读、SKIP 没找到
    assert "定答案" in html and 'data-pick="3"' in html  # 待定项可以一键取 Codex 的读法


def test_server_accepts_final_answer_kind():
    assert cs.clean({"target": "dmm-r-00001", "field": "genki", "kind": "定答案", "text": "0"})["kind"] == "定答案"


def test_scenario_filter_does_not_depend_on_string_replacing_shared_js(tmp_path):
    """场景下拉框的筛选以前靠 .replace("s.dataset.page === p", ...) 改共享 JS：共享 JS 一改写法就不命中、下拉框没反应还不报错（PR #104）。
    现在每个 section 同时带 data-page（共享 filt 用的）和 data-sc。"""
    ds, reads = _ds(tmp_path)
    html = sp.build(ds, reads, out=tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'data-page="exam"' in html and 'data-sc="exam"' in html
    assert "s.dataset.sc === p" not in html and "s.dataset.page === p" in html


def test_pending_marker_follows_index_status_keys_on_keyed_lists(tmp_path):
    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "dmm-r-00002.jpg"), np.full((1280, 720, 3), 90, np.uint8))
    fr = {"schema": 3, "id": "dmm-r-00002", "image": "dmm-r-00002.jpg", "scenario": "action_select", "page": "ACTION_SELECT", "platform": {"kind": "dmm"},
          "truth": {"actions": [{"label": "休む", "hp_change": 11}]}, "item_status": {"actions[0].hp_change": "disputed"},
          "answers": {}, "buttons": []}  # fmt: skip
    (ds / "core" / "dmm-r-00002.json").write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    html = sp.build(ds, {"vision": {}}, out=tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "待定 1 项" in html
