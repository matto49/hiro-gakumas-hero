"""tools.ocr_dataset.label：出框、三方答案、定正确文字、标签（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.2 / §6.3）。"""

import json

import cv2
import numpy as np
import pytest

from tools.ocr_dataset import fields, label, schema
from tools.ocr_dataset.lexicon import Lexicon

pytestmark = pytest.mark.synthetic

LEX = Lexicon(words={"編成", "スキルカード", "えいえいおー+"}, charset=set(), version="test")


def test_merge_boxes_unions_overlapping_detections():
    vision = [("編成", (300, 1140, 400, 1176)), ("体力", (40, 50, 100, 80))]
    rapid = [("綿成", (305, 1138, 410, 1178)), ("12", (600, 600, 640, 630))]
    got = label.merge_boxes(vision, rapid)
    assert [g["box"] for g in got] == [[40, 50, 100, 80], [600, 600, 640, 630], [300, 1138, 410, 1178]]
    assert got[2]["vision"] == "編成" and got[2]["rapidocr"] == "綿成"
    assert got[0]["rapidocr"] == "" and got[1]["vision"] == ""


def test_merge_boxes_chains_one_long_line_over_two_pieces():
    vision = [("スキルカード 獲得", (100, 500, 500, 540))]
    rapid = [("スキルカード", (100, 500, 330, 540)), ("獲得", (350, 500, 500, 540))]
    got = label.merge_boxes(vision, rapid)
    assert len(got) == 1 and got[0]["box"] == [100, 500, 500, 540] and got[0]["rapidocr"] == "スキルカード獲得"


@pytest.mark.parametrize(
    "vision, llm, want",
    [
        ("編成", "編成", ("編成", "agreed")),  # 三方一致
        ("4,680", "4,680", ("4,680", "agreed")),  # vision = llm、词表没有：也算
        ("編 成", "編成", ("編成", "agreed")),  # 归一后一致
        ("えいえいおー十", "えいえいおー+", ("えいえいおー+", "agreed")),  # 一方正好是词表词、另一方吸附后同词
        ("綿成", "編成", ("編成", "disputed")),  # 两字词不容错：綿成吸附不到
        ("えいえいおー十", "えいえいおー十", ("えいえいおー十", "disputed")),  # 两方一致但词表吸附成别的词：可疑
        ("綿成", "綿成", ("綿成", "agreed")),  # 两方一致、词表里没有相近词
        ("綿成", "絹成", ("綿成", "disputed")),  # 两方都不是词表词
        ("abc", "xyz", ("abc", "disputed")),
        ("", "編成", ("編成", "disputed")),  # 一方没读到
        ("", "", ("", "ignored")),  # 都没字
    ],
)
def test_decide(vision, llm, want):
    assert label.decide({"vision": vision, "llm": llm}, LEX) == want


def test_lexicon_answer_is_recorded():
    assert label.lexicon_answer({"vision": "えいえいおー十", "llm": "abc"}, LEX) == "えいえいおー+"
    assert label.lexicon_answer({"vision": "abc", "llm": "xyz"}, LEX) == ""


def test_type_of_uses_field_table_then_fallbacks(monkeypatch):
    monkeypatch.setattr(fields, "FIELDS", {"CONSULT": [("consult.shelf.name", (40, 470, 700, 1070), "name")]})
    assert label.type_of("えいえいおー+", [100, 600, 300, 640], "CONSULT") == {"field": "consult.shelf.name", "type": "name"}
    assert label.type_of("1,234", [100, 100, 160, 130], "CONSULT") == {"field": None, "type": "number"}
    assert label.type_of("+12", [100, 100, 160, 130], "HOME") == {"field": None, "type": "number"}
    assert label.type_of("確認", [100, 205, 200, 235], "DIALOG", bands=[(200, 240)]) == {"field": None, "type": "title"}
    assert label.type_of("受け取る", [100, 900, 200, 940], "DIALOG", bands=[(200, 240)]) == {"field": None, "type": "other"}


def test_field_table_is_well_formed():
    for page, rows in fields.FIELDS.items():
        for name, (x1, y1, x2, y2), typ in rows:
            assert typ is None or typ in schema.TYPES, (page, name)
            assert 0 <= x1 < x2 <= 720 and 0 <= y1 < y2 <= 1280, (page, name)


class FakeReaders:
    def __init__(self):
        self.llm_calls = 0

    def detect_vision(self, img):
        return [("編成", (300, 1140, 400, 1176)), ("4,680", (540, 120, 700, 170))]

    def detect_rapid(self, img):
        return [("綿成", (305, 1138, 410, 1178)), ("ロゴ", (10, 10, 60, 40))]

    def read_vision(self, crop):
        return {(40, 110): "編成", (50, 160): "4,680"}.get(crop.shape[:2], "")

    def read_llm(self, img, boxes):
        self.llm_calls += 1
        return ["", "4,680", "編成"]


def _meta():
    return {"id": "dmm540-r-00001", "image": "dmm540-r-00001.jpg", "sha256": "x", "page": None, "note": None,
            "source": {"run": "r", "seq": 1, "contributor": "matto", "captured": "2026-11-23"},
            "platform": {"kind": "dmm540", "native": [540, 960], "stored": [720, 1280], "device": None, "ocr": "rapidocr"}}  # fmt: skip


def test_label_frame_builds_valid_frame_json():
    img = np.zeros((1280, 720, 3), np.uint8)
    rd = FakeReaders()
    fr = label.label_frame(img, _meta(), rd, LEX, pad=0)
    assert schema.validate_frame(fr) == []
    assert rd.llm_calls == 1  # 每帧问一次
    assert fr["page"] == "unknown"  # 没判页的帧
    by_text = {b["answers"]["vision"]: b for b in fr["boxes"]}
    b = by_text["編成"]
    assert b["text"] == "編成" and b["status"] == "agreed" and b["answers"]["rapidocr"] == "綿成" and b["answers"]["lexicon"] == "編成"
    assert by_text["4,680"]["status"] == "agreed" and by_text["4,680"]["tags"]["type"] == "number"
    logo = by_text[""]
    assert logo["status"] == "ignored" and logo["ignore"] is True


def _dataset(tmp_path, n=1):
    ds = schema.init(tmp_path / "ds")
    inbox = ds / "inbox" / "matto" / "2026-11-23"
    inbox.mkdir(parents=True)
    frames = []
    for i in range(1, n + 1):
        m = _meta()
        m["id"] = f"dmm540-r-{i:05d}"
        m["image"] = m["id"] + ".jpg"
        m["source"]["seq"] = i
        img = np.full((1280, 720, 3), 30 + i, np.uint8)
        cv2.imwrite(str(inbox / m["image"]), img)
        m["sha256"] = schema.sha256(inbox / m["image"])
        frames.append(m)
    cv2.imwrite(str(inbox / "dmm540-r-00001.native.png"), np.zeros((960, 540, 3), np.uint8))
    frames[0]["native_image"] = "dmm540-r-00001.native.png"
    (inbox / "meta.json").write_text(json.dumps({"schema": 1, "contributor": "matto", "created": "2026-11-23", "frames": frames}))
    return ds, inbox


def test_run_moves_inbox_into_core_and_writes_disputed(tmp_path):
    ds, inbox = _dataset(tmp_path, n=2)
    done = label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    assert done == 2
    assert not inbox.exists()  # 投稿区标完就清掉
    assert sorted(p.name for p in (ds / "core").iterdir()) == [
        "dmm540-r-00001.jpg", "dmm540-r-00001.json", "dmm540-r-00001.native.png", "dmm540-r-00002.jpg", "dmm540-r-00002.json"]  # fmt: skip
    assert schema.validate_dir(ds) == []
    disputed = json.loads((ds / "disputed.json").read_text(encoding="utf-8"))
    assert disputed == []  # 假读者两框都 agreed，ロゴ 框 ignored


def test_rebuild_disputed_lists_disputed_boxes(tmp_path):
    ds, inbox = _dataset(tmp_path)

    class Split(FakeReaders):
        def read_llm(self, img, boxes):
            return ["", "4,680", "絹成"]

    label.run(ds, inbox, readers=Split(), lex=LEX, pad=0)
    disputed = json.loads((ds / "disputed.json").read_text(encoding="utf-8"))
    assert [(d["frame"], d["set"], d["answers"]["llm"]) for d in disputed] == [("dmm540-r-00001", "core", "絹成")]


def test_validate_dir_accepts_native_companion_but_not_orphans(tmp_path):
    ds, inbox = _dataset(tmp_path)
    label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    assert schema.validate_dir(ds) == []
    cv2.imwrite(str(ds / "core" / "dmm540-x-00009.native.png"), np.zeros((4, 4, 3), np.uint8))
    assert any("dmm540-x-00009.native.png" in e for e in schema.validate_dir(ds))


def test_spot_check_samples_agreed_boxes(tmp_path):
    ds, inbox = _dataset(tmp_path, n=2)
    label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    page = label.spot_check(ds, n=3, seed=1)
    html = page.read_text(encoding="utf-8")
    assert page.parent == ds / ".work"
    assert html.count('class="item"') == 3  # 2 帧 × 2 个 agreed 框，抽 3 个
    assert "data:image/png;base64," in html


def test_counter_counts_types_for_pick(monkeypatch):
    monkeypatch.setattr(fields, "FIELDS", {})
    count = label.type_counter(FakeReaders())
    from tools.ocr_dataset.pick import Candidate

    c = Candidate(path=None, run="r", seq=1, platform="dmm540", page="HOME", sha="s")
    c.image = np.zeros((1280, 720, 3), np.uint8)
    assert count(c) == {"other": 2, "number": 1}


def test_type_of_long_text_falls_back_to_body():
    assert label.type_of("★ランダムな特定のPドリンクを獲得", [180, 463, 450, 484], "ACQUIRE")["type"] == "body"
    assert label.type_of("ブーストエキス", [264, 853, 464, 890], "ACQUIRE")["type"] == "other"
