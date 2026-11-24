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
