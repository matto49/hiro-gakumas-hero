"""tools.ocr_dataset.eval / charset_rapid：评测指标与字表屏蔽（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.5）。"""

from tools.ocr_dataset import charset_rapid
from tools.ocr_dataset import eval as ev
from tools.ocr_dataset.lexicon import Lexicon

import numpy as np
import pytest

pytestmark = pytest.mark.synthetic

LEX = Lexicon(words={"編成", "スキルカード", "えいえいおー+"}, charset=set(), version="t")


def _b(i, box, text, status="agreed", typ="button"):
    return {
        "id": i,
        "box": box,
        "text": text,
        "status": status,
        "answers": {},
        "tags": {"field": None, "type": typ},
        "ignore": status == "ignored",
    }


def _frame(fid="f1", kind="dmm540", page="HOME", boxes=None):
    return {"id": fid, "platform": {"kind": kind}, "page": page, "boxes": boxes or []}


def test_match_pairs_by_iou_or_center_and_joins_pieces():
    truth = [
        _b(0, [100, 100, 300, 140], "スキルカード獲得"),
        _b(1, [100, 500, 200, 540], "編成"),
        _b(2, [400, 900, 500, 940], "x"),
    ]
    pred = [
        ("スキルカード", (100, 102, 220, 140)),
        ("獲得", (230, 100, 300, 140)),
        ("綿成", (102, 498, 205, 541)),
        ("ゴミ", (600, 10, 700, 40)),
    ]
    got, extra = ev.match(pred, truth)
    assert got == {0: "スキルカード獲得", 1: "綿成", 2: None}
    assert extra == 1


def test_score_cer_exact_keywords_and_missed():
    frames = [_frame(boxes=[_b(0, [100, 500, 200, 540], "編成"), _b(1, [100, 100, 400, 140], "スキルカード強化", typ="body"),
                            _b(2, [400, 900, 500, 940], "ロゴ")])]  # fmt: skip
    preds = {
        "f1": {"texts": [("綿成", (100, 500, 200, 540)), ("スキルカード強化", (100, 100, 400, 140))], "seconds": 0.5}
    }
    r = ev.score(frames, preds, LEX)
    a = r["groups"]["all"]
    assert a["boxes"] == 3 and a["chars"] == 2 + 8 + 2
    assert a["edits"] == 1 + 0 + 2  # 編→綿 1 个字；ロゴ 整段漏读 2 个字
    assert a["cer"] == pytest.approx(3 / 12)
    assert a["line_exact"] == pytest.approx(1 / 3)
    assert a["missed"] == 1
    # 关键词：編成（没读对）、スキルカード（读对了）
    assert (a["keywords"], a["keyword_hits"]) == (2, 1)
    assert r["groups"]["type"]["body"]["cer"] == 0
    assert r["groups"]["platform"]["dmm540"]["boxes"] == 3


def test_disputed_and_ignored_do_not_score_but_share_is_reported():
    frames = [_frame(boxes=[_b(0, [0, 0, 50, 30], "編成"), _b(1, [0, 100, 50, 130], "?", status="disputed"),
                            _b(2, [0, 200, 50, 230], "", status="ignored"), _b(3, [0, 300, 50, 330], "体力", status="resolved")])]  # fmt: skip
    preds = {
        "f1": {
            "texts": [("編成", (0, 0, 50, 30)), ("ナニカ", (0, 100, 50, 130)), ("飾り", (0, 200, 50, 230))],
            "seconds": 0.1,
        }
    }
    r = ev.score(frames, preds, LEX)
    assert r["groups"]["all"]["boxes"] == 2 and r["groups"]["all"]["edits"] == 2
    assert r["groups"]["all"]["extra"] == 0  # 读到待定 / 忽略框上的字不算多读
    assert r["disputed_share"] == pytest.approx(1 / 3)  # 待定 / 不含忽略的框
    assert r["disputed"] == [{"frame": "f1", "box": 1, "pred": "ナニカ", "answers": {}}]


def test_timing_skips_first_frame_and_errors_are_ranked():
    frames = [_frame(fid=f"f{i}", boxes=[_b(0, [0, 0, 50, 30], "編成")]) for i in range(4)]
    preds = {
        f"f{i}": {"texts": [("綿成" if i == 2 else "編成", (0, 0, 50, 30))], "seconds": s}
        for i, s in enumerate([9.0, 1.0, 2.0, 3.0])
    }
    r = ev.score(frames, preds, LEX)
    assert r["timing"]["p50"] == pytest.approx(2.0) and r["timing"]["p90"] == pytest.approx(2.8)
    assert r["errors"][0]["frame"] == "f2" and r["errors"][0]["pred"] == "綿成" and len(r["errors"]) == 1


def test_filter_by_tags():
    frames = [_frame(fid="a", kind="dmm540"), _frame(fid="b", kind="mac", page="SHOP")]
    assert [f["id"] for f in ev.filter_frames(frames, {"platform": "dmm540"})] == ["a"]
    assert [f["id"] for f in ev.filter_frames(frames, {"page": "SHOP"})] == ["b"]


def test_report_md_lists_backends_and_compare_delta():
    frames = [_frame(boxes=[_b(0, [0, 0, 50, 30], "編成")])]
    good = ev.score(frames, {"f1": {"texts": [("編成", (0, 0, 50, 30))], "seconds": 0.1}}, LEX, backend="vision")
    bad = ev.score(frames, {"f1": {"texts": [("綿成", (0, 0, 50, 30))], "seconds": 0.1}}, LEX, backend="rapidocr")
    md = ev.report_md([good, bad])
    assert "| vision |" in md and "| rapidocr |" in md and "50.0%" in md
    base = {"results": [dict(bad, groups={**bad["groups"], "all": {**bad["groups"]["all"], "cer": 1.0}})]}
    md2 = ev.report_md([bad], compare=base)
    assert "−50.0" in md2  # 字错率比基线降了 50 个百分点


def test_formal_gate():
    assert ev.formal({"disputed_share": 0.04}) and not ev.formal({"disputed_share": 0.06})


def test_charset_mask_picks_best_allowed_character():
    pytest.importorskip("rapidocr_onnxruntime")  # KAA 带的 RapidOCR；CI 只装 .[dev]
    from rapidocr_onnxruntime.ch_ppocr_rec.utils import CTCLabelDecode

    dec = charset_rapid.MaskedDecode(CTCLabelDecode(character=["編", "綿", "成"]), {"編", "成"})
    # 列：blank, 編, 綿, 成, 空格；第 0 步綿最高、編次高；第 2 步成
    p = np.zeros((1, 4, 5), np.float32)
    p[0, 0] = [0.05, 0.3, 0.6, 0.05, 0.0]
    p[0, 1] = [0.9, 0.05, 0.05, 0.0, 0.0]
    p[0, 2] = [0.1, 0.0, 0.1, 0.8, 0.0]
    p[0, 3] = [0.9, 0.0, 0.1, 0.0, 0.0]
    assert dec(p)[0][0] == "編成"
    plain = CTCLabelDecode(character=["編", "綿", "成"])
    assert plain(p)[0][0] == "綿成"


def test_charset_mask_keeps_punctuation_and_fullwidth_variants():
    m = charset_rapid.allowed_mask(["blank", "Ａ", "％", "綿", "a", " "], {"A", "a"})
    assert m.tolist() == [1, 1, 1, 0, 1, 1]


def test_report_shows_ignored_share_and_matching_caveat():
    frames = [_frame(boxes=[_b(0, [0, 0, 50, 30], "編成"), _b(1, [0, 100, 50, 130], "", status="ignored"),
                            _b(2, [0, 200, 50, 230], "x", status="disputed")])]  # fmt: skip
    r = ev.score(frames, {"f1": {"texts": [("編成", (0, 0, 50, 30))], "seconds": 0.1}}, LEX, backend="vision")
    assert r["ignored_share"] == pytest.approx(1 / 3)
    md = ev.report_md([r])
    assert "忽略占比 33.3%" in md and "一段识别只归一个标注框" in md


def test_load_frames_keeps_only_box_level_frames(tmp_path):
    """core 里有场景帧（schema 3）、字段帧（schema 2）时，框级评测不能把它们喂给 score（fr["boxes"] 会 KeyError，PR #104）。"""
    import json

    (tmp_path / "core").mkdir()
    for fid, sc in (("a", 1), ("b", 2), ("c", 3)):
        fr = {"schema": sc, "id": fid, "image": fid + ".jpg"}
        (tmp_path / "core" / f"{fid}.json").write_text(json.dumps(fr), encoding="utf-8")
    assert [f["id"] for f in ev.load_frames(tmp_path, "core")] == ["a"]


def test_main_without_box_level_frames_stops_with_a_message(tmp_path):
    import json

    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "c.json").write_text(json.dumps({"schema": 3, "id": "c"}), encoding="utf-8")
    (tmp_path / "lexicon").mkdir()
    with pytest.raises(SystemExit) as e:
        ev.main(tmp_path, ["rapidocr"], "core", log=lambda *_: None)
    assert "没有符合条件的帧" in str(e.value)
