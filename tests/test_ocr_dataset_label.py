"""tools.ocr_dataset.label：出框、三方答案、定正确文字、标签（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.2 / §6.3）。"""

import json
from typing import ClassVar

from tools.ocr_dataset import fields, label, schema
from tools.ocr_dataset.lexicon import Lexicon

import cv2
import numpy as np
import pytest

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
        (
            "えいえいおー十",
            "えいえいおー十",
            ("えいえいおー十", "agreed"),
        ),  # 两方一致就算（规格：词表没有这个词也不拦）
        ("→次へ", "次へ", ("次へ", "agreed")),  # 两端的图标符号去掉再比
        (")27", "27", ("27", "agreed")),
        ("-NEW", "NEW", ("-NEW", "disputed")),  # 横线可能是负号（-3），不当图标去掉
        ("+950", "✦950", ("+950", "agreed")),  # 数字前的星号按装饰「+」算
        ("不安です.........", "不安です......", ("不安です.........", "disputed")),  # 点数不同不算一致
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
    assert label.type_of("えいえいおー+", [100, 600, 300, 640], "CONSULT") == {
        "field": "consult.shelf.name",
        "type": "name",
    }
    assert label.type_of("1,234", [100, 100, 160, 130], "CONSULT") == {"field": None, "type": "number"}
    assert label.type_of("+12", [100, 100, 160, 130], "HOME") == {"field": None, "type": "number"}
    assert label.type_of("確認", [100, 205, 200, 235], "DIALOG", bands=[(200, 240)]) == {"field": None, "type": "title"}
    assert label.type_of("受け取る", [100, 900, 200, 940], "DIALOG", bands=[(200, 240)]) == {
        "field": None,
        "type": "other",
    }


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
    assert (
        b["text"] == "編成"
        and b["status"] == "agreed"
        and b["answers"]["rapidocr"] == "綿成"
        and b["answers"]["lexicon"] == "編成"
    )
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
    (inbox / "meta.json").write_text(
        json.dumps({"schema": 1, "contributor": "matto", "created": "2026-11-23", "frames": frames})
    )
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


def test_decide_accepts_either_vision_read():
    assert label.decide({"vision": "", "vision_frame": "9", "llm": "9"}, LEX) == ("9", "agreed")
    assert label.decide({"vision": "ろ", "vision_frame": "3", "llm": "3"}, LEX) == ("3", "agreed")
    assert label.decide({"vision": "", "vision_frame": "", "llm": "P"}, LEX) == ("P", "disputed")


def test_decide_agreed_even_if_lexicon_has_case_variant():
    lex = Lexicon(words={"pアイテム"}, charset=set(), version="t")
    assert label.decide({"vision": "Pアイテム", "llm": "Pアイテム"}, lex) == ("Pアイテム", "agreed")


def test_label_frame_records_full_frame_vision_text():
    fr = label.label_frame(np.zeros((1280, 720, 3), np.uint8), _meta(), FakeReaders(), LEX, pad=0)
    by = {b["answers"]["vision_frame"]: b for b in fr["boxes"]}
    assert set(by) == {"編成", "4,680", ""}


def test_redecide_recomputes_status_from_stored_answers(tmp_path):
    ds, inbox = _dataset(tmp_path)

    class Split(FakeReaders):
        def read_llm(self, img, boxes):
            return ["", "4,680", "→編成"]  # 旧规则下也会 agreed？不：→ 去掉后才一致

    label.run(ds, inbox, readers=Split(), lex=LEX, pad=0)
    p = ds / "core" / "dmm540-r-00001.json"
    fr = json.loads(p.read_text(encoding="utf-8"))
    for b in fr["boxes"]:  # 模拟旧规则写下的 disputed
        if b["answers"]["llm"] == "→編成":
            b["status"] = "disputed"
    p.write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    label.rebuild_disputed(ds)
    got = label.redecide(ds, lex=LEX)
    assert got == {"changed": 1, "disputed": 0}
    b = next(b for b in json.loads(p.read_text(encoding="utf-8"))["boxes"] if b["answers"]["llm"] == "→編成")
    assert (b["status"], b["text"]) == ("agreed", "編成")
    assert json.loads((ds / "disputed.json").read_text(encoding="utf-8")) == []


def test_redecide_keeps_human_decisions(tmp_path):
    ds, inbox = _dataset(tmp_path)
    label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    p = ds / "core" / "dmm540-r-00001.json"
    fr = json.loads(p.read_text(encoding="utf-8"))
    fr["boxes"][0].update(status="resolved", text="手で直した", reviewed="2026-11-23")
    p.write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    label.redecide(ds, lex=LEX)
    assert json.loads(p.read_text(encoding="utf-8"))["boxes"][0]["text"] == "手で直した"


@pytest.mark.parametrize(
    "answers, want",
    [
        ({"vision": "ろ", "vision_frame": "ろ", "llm": "3", "llm2": "3"}, ("3", "agreed")),  # 两个 LLM 一致
        (
            {"vision": "Lst", "vision_frame": "Lst", "llm": "1st", "llm2": "Lst"},
            ("Lst", "agreed"),
        ),  # Vision + 第二个 LLM
        (
            {"vision": "→早送り", "vision_frame": "", "llm": "早送", "llm2": "早送り"},
            ("早送り", "agreed"),
        ),  # 去图标符号后一致
        (
            {"vision": "綿成", "vision_frame": "", "llm": "編成", "llm2": "絹成"},
            ("編成", "disputed"),
        ),  # 三家各不相同（文字先填词表词）
        ({"vision": "", "vision_frame": "", "llm": "P", "llm2": ""}, ("P", "disputed")),
    ],
)
def test_decide_with_second_llm(answers, want):
    assert label.decide(answers, LEX) == want


@pytest.mark.parametrize(
    "answers, want",
    [
        ({"vision": "", "vision_frame": "せせせせ", "llm": ""}, ("", "ignored")),  # LLM 说没字、Vision 两次不一致：图标
        ({"vision": "百", "vision_frame": "旦", "llm": ""}, ("", "ignored")),
        ({"vision": "体力", "vision_frame": "体力", "llm": ""}, ("体力", "disputed")),  # Vision 两次一致：可能真是字
        ({"vision": "", "vision_frame": "三", "llm": "", "llm2": "三"}, ("三", "agreed")),
    ],
)
def test_decide_icon_noise(answers, want):
    assert label.decide(answers, LEX) == want


def test_adjudicate_asks_second_llm_only_for_disputed_boxes(tmp_path):
    ds, inbox = _dataset(tmp_path)

    class Split(FakeReaders):
        def read_llm(self, img, boxes):
            return ["", "4,680", "絹成"]

    label.run(ds, inbox, readers=Split(), lex=LEX, pad=0)

    class Second:
        name = "codex"
        asked: ClassVar[list] = []

        def read_llm(self, img, boxes):
            self.asked.append(list(boxes))
            return ["編成"]

    sec = Second()
    got = label.adjudicate(ds, sec, lex=LEX)
    assert sec.asked == [[[300, 1138, 410, 1178]]]  # 只问待定框
    assert got == {"frames": 1, "changed": 1, "disputed": 0}
    b = next(
        b
        for b in json.loads((ds / "core" / "dmm540-r-00001.json").read_text(encoding="utf-8"))["boxes"]
        if b["answers"]["llm"] == "絹成"
    )
    assert (b["status"], b["text"], b["answers"]["llm2"]) == ("agreed", "編成", "編成")
    assert label.adjudicate(ds, sec, lex=LEX)["frames"] == 0  # 问过的不再问


def test_type_of_master_name_falls_back_to_name():
    names = {"えいえいおー+", "ブーストエキス"}
    assert label.type_of("ブーストエキス", [264, 853, 464, 890], "ACQUIRE", names=names)["type"] == "name"
    assert label.type_of("えいえいおー+", [100, 100, 300, 140], "HOME", names=names)["type"] == "name"
    assert label.type_of("次へ", [100, 100, 300, 140], "HOME", names=names)["type"] == "other"


def test_redecide_retags_master_names(tmp_path):
    ds, inbox = _dataset(tmp_path)
    label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    lex = Lexicon(words=LEX.words, charset=set(), version="t", names={"編成"})
    label.redecide(ds, lex=lex)
    fr = json.loads((ds / "core" / "dmm540-r-00001.json").read_text(encoding="utf-8"))
    assert next(b for b in fr["boxes"] if b["text"] == "編成")["tags"]["type"] == "name"


# ---- 最终评审修复 ----


def _real_readers_with(replies):
    from hiro.brain.llm import MockLLM

    rd = label.RealReaders.__new__(label.RealReaders)
    rd.llm, rd.pad = MockLLM(replies=list(replies)), 0
    return rd


def test_read_llm_numbers_each_sheet_from_one(monkeypatch):
    """C1：分成几张图时，每张图上画的编号和提示词、解析都从 1 开始。"""
    monkeypatch.setattr(label, "SHEET_MAX_H", 100)  # 每段约 68 像素高：一张只放得下一段
    drawn = []
    real = cv2.putText
    monkeypatch.setattr(
        label.cv2, "putText", lambda img, text, *a, **k: (drawn.append(text), real(img, text, *a, **k))[1]
    )
    rd = _real_readers_with([{"texts": {"1": "a"}}, {"texts": {"1": "b"}}, {"texts": {"1": "c"}}])
    img = np.full((1280, 720, 3), 255, np.uint8)
    got = rd.read_llm(img, [[10, 10, 100, 40], [10, 100, 100, 130], [10, 200, 100, 230]])
    assert got == ["a", "b", "c"]
    assert drawn == ["[1]", "[1]", "[1]"]
    assert all("编号 1–1" in user for _, user in rd.llm.calls)


def test_read_llm_missing_or_numeric_answers():
    """I1：漏答的编号记 None（不是「没字」）；数字答案转成字符串。"""
    rd = _real_readers_with([{"texts": {"1": 3, "3": "c"}}])
    img = np.full((1280, 720, 3), 255, np.uint8)
    assert rd.read_llm(img, [[10, 10, 100, 40], [10, 100, 100, 130], [10, 200, 100, 230]]) == ["3", None, "c"]


@pytest.mark.parametrize(
    "answers, want",
    [
        ({"vision": "綿成", "vision_frame": "編成", "llm": None}, "disputed"),  # LLM 漏答 ≠ 说没字
        ({"vision": "", "vision_frame": "", "llm": None}, "disputed"),
        ({"vision": "", "vision_frame": "せせ", "llm": "", "llm2": None}, "disputed"),
        ({"vision": "", "vision_frame": "せせ", "llm": ""}, "ignored"),
    ],
)
def test_decide_missing_llm_answer_is_not_no_text(answers, want):
    assert label.decide(answers, LEX)[1] == want


@pytest.mark.parametrize(
    "answers, want",
    [
        (
            {"vision": "+300", "vision_frame": "+300", "llm": "✦300", "llm2": "✦300"},
            ("+300", "agreed"),
        ),  # I2：装饰「+」
        ({"vision": "+150", "vision_frame": "+150", "llm": "✦150", "llm2": "150"}, ("+150", "agreed")),
        ({"vision": "+300", "vision_frame": "+300", "llm": "300", "llm2": "300"}, ("+300", "agreed")),
    ],
)
def test_decide_keeps_plus_sign_vision_reads_consistently(answers, want):
    assert label.decide(answers, LEX) == want


def test_run_stops_queued_frames_on_keyboard_interrupt(tmp_path):
    """I4：Ctrl-C 后排队的帧不再问 LLM。"""
    ds, inbox = _dataset(tmp_path, n=4)

    class Stop(FakeReaders):
        def read_llm(self, img, boxes):
            self.llm_calls += 1
            raise KeyboardInterrupt

    rd = Stop()
    with pytest.raises(KeyboardInterrupt):
        label.run(ds, inbox, readers=rd, lex=LEX, pad=0, workers=1)
    assert rd.llm_calls <= 2


def test_reask_rereads_llm_for_multi_sheet_frames(tmp_path, monkeypatch):
    ds, inbox = _dataset(tmp_path)

    class Split(FakeReaders):
        def read_llm(self, img, boxes):
            return ["", "4,680", "絹成"]

    label.run(ds, inbox, readers=Split(), lex=LEX, pad=0)
    p = ds / "core" / "dmm540-r-00001.json"
    fr = json.loads(p.read_text(encoding="utf-8"))
    fr["boxes"][2]["answers"]["llm2"] = "x"
    p.write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")

    class Again:
        name = "claude"
        calls = 0

        def read_llm(self, img, boxes):
            Again.calls += 1
            return ["", "4,680", "編成"]

    assert label.reask(ds, Again(), lex=LEX, multi_sheet_only=True)["frames"] == 0  # 一张图放得下：不重问
    got = label.reask(ds, Again(), lex=LEX, multi_sheet_only=False)
    assert got["frames"] == 1 and Again.calls == 1
    b = json.loads(p.read_text(encoding="utf-8"))["boxes"][2]
    assert b["answers"]["llm"] == "編成" and "llm2" not in b["answers"] and b["status"] == "agreed"


def test_cli_reask_runs(tmp_path, monkeypatch):
    from tools.ocr_dataset import __main__ as cli

    ds, inbox = _dataset(tmp_path)
    label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    monkeypatch.setattr(label, "RealReaders", lambda spec=None: FakeReaders())
    monkeypatch.setattr(label, "_load_lexicon", lambda root: LEX)
    assert cli.main(["reask", "--dataset", str(ds), "--workers", "1"]) == 0


def test_adjudicate_retries_boxes_the_second_llm_skipped(tmp_path):
    """第二个 LLM 漏答一个编号时 read_llm 给 None、写成 llm2: null：框还是待定，下次要再问；空串是「明确没有文字」，不再问（PR #104）。"""
    ds, inbox = _dataset(tmp_path)

    class Split(FakeReaders):
        def read_llm(self, img, boxes):
            return ["", "4,680", "絹成"]

    label.run(ds, inbox, readers=Split(), lex=LEX, pad=0)

    class Skips:
        name = "codex"
        calls = 0

        def read_llm(self, img, boxes):
            Skips.calls += 1
            return [None]

    assert label.adjudicate(ds, Skips(), lex=LEX)["frames"] == 1
    assert label.adjudicate(ds, Skips(), lex=LEX)["frames"] == 1  # null 不算答过
    assert Skips.calls == 2

    class Blank:
        name = "codex"

        def read_llm(self, img, boxes):
            return [""]

    label.adjudicate(ds, Blank(), lex=LEX)
    assert label.adjudicate(ds, Blank(), lex=LEX)["frames"] == 0  # 空串是明确回答


def test_run_keeps_inbox_image_until_frame_is_fully_written(tmp_path, monkeypatch):
    """图片挪走后写标注失败 / 被打断：以前 inbox 里的原图没了、标注也没写，下次永远读不了这一帧（PR #104）。"""
    ds, inbox = _dataset(tmp_path)
    real = schema.sha256
    calls = []

    def boom(p):
        calls.append(p)
        if len(calls) == 1:
            raise OSError("磁盘写满")
        return real(p)

    monkeypatch.setattr(label.schema, "sha256", boom)
    with pytest.raises(OSError):
        label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    assert (inbox / "dmm540-r-00001.jpg").is_file() and not (ds / "core" / "dmm540-r-00001.json").exists()
    assert not list((ds / "core").glob("*.tmp"))
    monkeypatch.setattr(label.schema, "sha256", real)
    assert label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0) == 1  # 下次接着标
    assert (ds / "core" / "dmm540-r-00001.json").is_file() and not inbox.exists()
    assert schema.validate_dir(ds) == []


def test_reask_does_not_hold_every_decoded_frame(tmp_path):
    """reask 把所有入选帧的像素数组都留在任务列表里（302 张约 800 MiB）：任务里只放路径，工作线程用的时候再读（PR #104）。"""
    import gc
    import weakref

    ds, inbox = _dataset(tmp_path, n=4)
    label.run(ds, inbox, readers=FakeReaders(), lex=LEX, pad=0)
    seen = []
    real = label.cv2.imread

    def spy(*a, **k):
        img = real(*a, **k)
        if img is not None:
            seen.append(weakref.ref(img))
        return img

    alive = []

    class Again:
        name = "claude"

        def read_llm(self, img, boxes):
            gc.collect()
            alive.append(sum(r() is not None for r in seen))
            return ["", "4,680", "編成"]

    label.cv2.imread = spy
    try:
        label.reask(ds, Again(), lex=LEX, multi_sheet_only=False, pad=0)
    finally:
        label.cv2.imread = real
    assert alive and max(alive) <= 2, alive  # 当前这张（+ 正在读下一张的一张），不是 4 张全在


def test_spot_check_without_agreed_boxes_says_so(tmp_path):
    ds = schema.init(tmp_path / "ds")
    html = label.spot_check(ds, n=5, seed=1).read_text(encoding="utf-8")
    assert "没有可抽查的框" in html


def test_cli_label_refuses_second_llm_from_the_same_source(tmp_path, monkeypatch, capsys):
    """--llm2 和 --llm 解析成同一个来源时，第二轮答案不是独立意见：decide 会把两次相同的答案当一致、写成 agreed。
    要在开跑前拒绝，不能白跑一轮再把同源答案算成第二票（PR #104）。"""
    from tools.ocr_dataset import __main__ as cli

    ds, inbox = _dataset(tmp_path)

    class Named(FakeReaders):
        def __init__(self, spec=None):
            self.name = spec or "claude:opus"

    ran = []
    monkeypatch.setattr(label, "RealReaders", Named)
    monkeypatch.setattr(label, "run", lambda *a, **k: ran.append(1) or 0)
    rc = cli.main(["label", str(inbox), "--dataset", str(ds), "--llm", "claude:opus", "--llm2", "claude:opus"])
    assert rc != 0 and not ran and "同一个来源" in capsys.readouterr().err
    monkeypatch.setattr(label, "adjudicate", lambda *a, **k: {"frames": 0, "changed": 0})
    monkeypatch.setattr(label, "rebuild_disputed", lambda root: [])
    assert (
        cli.main(["label", str(inbox), "--dataset", str(ds), "--llm", "claude:opus", "--llm2", "codex:gpt"]) == 0
        and ran
    )
