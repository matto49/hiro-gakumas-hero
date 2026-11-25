"""tools.ocr_dataset.scenario_store：按钮定位、schema 3 帧写入与校验（规格 v3 §3.3、§4）。"""

import json

from tools.ocr_dataset import scenario_store as st
from tools.ocr_dataset import schema

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


def test_locate_buttons_by_fuzzy_text():
    det = [
        ("綿成", (100, 1100, 200, 1140)),
        ("キャンセル", (60, 1150, 220, 1190)),
        ("決定", (420, 1150, 540, 1190)),
        ("決定", (400, 300, 440, 320)),
    ]
    got = st.locate_buttons(["編成", "決定", "終了"], det)
    assert got == [{"label": "編成", "box": [100, 1100, 200, 1140]},  # 一个字读错也能找回位置（只借位置）
                   {"label": "決定", "box": [420, 1150, 540, 1190]},  # 同名取靠下的（按钮多在下方）
                   {"label": "終了", "box": None}]  # fmt: skip


def _labeled():
    return {"truth": {"turns_left": 9, "hp": 27}, "item_status": {"hp": "disputed"},
            "answers": {"claude": {"items": {"turns_left": 9, "hp": 27}}, "codex": {"items": {"turns_left": 9, "hp": 21}}},
            "button_labels": ["SKIP"]}  # fmt: skip


def test_build_frame_is_valid_schema3(tmp_path):
    img = tmp_path / "a.jpg"
    cv2.imwrite(str(img), np.zeros((1280, 720, 3), np.uint8))
    meta = {
        "id": "dmm-r-00001",
        "image": "dmm-r-00001.jpg",
        "source": {"run": "r", "seq": 1},
        "platform": {"kind": "dmm", "stored": [720, 1280]},
    }
    fr = st.build_frame(meta, img, "exam", _labeled(), [{"label": "SKIP", "box": [560, 760, 640, 820]}])
    assert fr["schema"] == 3 and fr["scenario"] == "exam" and fr["truth"]["turns_left"] == 9
    assert fr["item_status"] == {"hp": "disputed"} and fr["buttons"][0]["label"] == "SKIP"
    assert schema.validate_frame(fr) == []
    assert schema.validate_frame({**fr, "scenario": "nope"})
    assert schema.validate_frame({**fr, "buttons": [{"label": "x", "box": [1, 2]}]})


def test_migrate_moves_schema2_out_of_core(tmp_path):
    ds = schema.init(tmp_path / "ds")
    for fid, sc in (("dmm-a-00001", 2), ("dmm-b-00001", 3)):
        cv2.imwrite(str(ds / "core" / f"{fid}.jpg"), np.full((1280, 720, 3), 10 * sc, np.uint8))
        (ds / "core" / f"{fid}.json").write_text(json.dumps({"schema": sc, "id": fid}), encoding="utf-8")
    assert st.archive_schema2(ds) == 1
    assert sorted(p.name for p in (ds / "legacy-v2").iterdir()) == ["dmm-a-00001.jpg", "dmm-a-00001.json"]
    assert sorted(p.name for p in (ds / "core").iterdir()) == ["dmm-b-00001.jpg", "dmm-b-00001.json"]


def test_ingest_writes_frames_from_labeled_runs(tmp_path):
    ds = schema.init(tmp_path / "ds")
    (ds / "legacy-v1").mkdir()
    img = ds / "legacy-v1" / "mac-x-00001.jpg"
    cv2.imwrite(str(img), np.full((1280, 720, 3), 50, np.uint8))
    (ds / "legacy-v1" / "mac-x-00001.json").write_text(json.dumps({"schema": 1, "id": "mac-x-00001", "image": "mac-x-00001.jpg", "page": "HIF_EXAM",
        "source": {"run": "x", "seq": 1}, "platform": {"kind": "mac", "stored": [720, 1280]}}), encoding="utf-8")  # fmt: skip
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "mac-x-00001.json").write_text(json.dumps({**_labeled(), "image": str(img)}), encoding="utf-8")
    got = st.ingest(ds, "exam", runs, detect=lambda im: [("SKIP", (560, 760, 640, 820))])
    assert got == {"written": 1, "buttons_missing": 0}
    fr = json.loads((ds / "core" / "mac-x-00001.json").read_text(encoding="utf-8"))
    assert fr["scenario"] == "exam" and fr["buttons"] == [{"label": "SKIP", "box": [560, 760, 640, 820]}]
    assert (ds / "core" / "mac-x-00001.jpg").is_file() and not img.exists()  # 图片跟着帧进 core
    assert schema.validate_dir(ds) == []


def test_apply_final_answers(tmp_path):
    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "dmm-r-00001.jpg"), np.zeros((1280, 720, 3), np.uint8))
    fr = {"schema": 3, "id": "dmm-r-00001", "image": "dmm-r-00001.jpg", "sha256": schema.sha256(ds / "core" / "dmm-r-00001.jpg"),
          "source": {"run": "r", "seq": 1}, "platform": {"kind": "dmm", "stored": [720, 1280]}, "page": "HIF_EXAM", "scenario": "exam",
          "truth": {"hand": [{"name": "デイドリーミンク", "cost": None}], "hp": 27},
          "item_status": {"hand[0].name": "disputed", "hand[0].cost": "disputed", "hp": "disputed"}, "buttons": []}  # fmt: skip
    (ds / "core" / "dmm-r-00001.json").write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    comments = [
        {
            "id": "1",
            "target": "dmm-r-00001",
            "field": "hand[0].name",
            "kind": "定答案",
            "text": '"デイドリーミング"',
            "created": 1,
        },
        {
            "id": "2",
            "target": "dmm-r-00001",
            "field": "hand[0].name",
            "kind": "定答案",
            "text": '"デイドリーミンク"',
            "created": 2,
        },  # 后提交的为准
        {"id": "3", "target": "dmm-r-00001", "field": "hand[0].cost", "kind": "定答案", "text": "1", "created": 3},
        {
            "id": "4",
            "target": "dmm-r-00001",
            "field": "hp",
            "kind": "定答案",
            "text": "21 吧",
            "created": 4,
        },  # 手填的非 JSON：按类型取数字
        {
            "id": "5",
            "target": "dmm-r-00001",
            "field": "hp",
            "kind": "其他",
            "text": "随便说说",
            "created": 5,
        },  # 不是定答案：不动
        {"id": "6", "target": "nope", "field": "hp", "kind": "定答案", "text": "1", "created": 6},
    ]
    got = st.apply_answers(ds, comments)
    assert got == {"applied": 3, "missing": 1}
    fr = json.loads((ds / "core" / "dmm-r-00001.json").read_text(encoding="utf-8"))
    assert fr["truth"]["hand"][0] == {"name": "デイドリーミンク", "cost": 1} and fr["truth"]["hp"] == 21
    assert fr["item_status"] == {"hand[0].name": "resolved", "hand[0].cost": "resolved", "hp": "resolved"}
    assert schema.validate_dir(ds) == []


def test_apply_final_answers_finds_frames_in_any_set(tmp_path):
    """进阶集（hard/）核对页上定的答案也要写回，不能只找 core/。"""
    ds = schema.init(tmp_path / "ds")
    (ds / "hard").mkdir(exist_ok=True)
    cv2.imwrite(str(ds / "hard" / "dmm-r-00002.jpg"), np.zeros((1280, 720, 3), np.uint8))
    fr = {"schema": 3, "id": "dmm-r-00002", "image": "dmm-r-00002.jpg", "sha256": schema.sha256(ds / "hard" / "dmm-r-00002.jpg"),
          "source": {"run": "r", "seq": 2}, "platform": {"kind": "dmm", "stored": [720, 1280]}, "page": "HIF_EXAM", "scenario": "exam",
          "truth": {"hp": 27}, "item_status": {"hp": "disputed"}, "buttons": []}  # fmt: skip
    (ds / "hard" / "dmm-r-00002.json").write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    got = st.apply_answers(
        ds, [{"id": "1", "target": "dmm-r-00002", "field": "hp", "kind": "定答案", "text": "21", "created": 1}]
    )
    assert got == {"applied": 1, "missing": 0}
    assert json.loads((ds / "hard" / "dmm-r-00002.json").read_text(encoding="utf-8"))["truth"]["hp"] == 21


def test_ingest_can_be_rerun_after_detect_failed(tmp_path):
    """某帧 detect 抛错：图片已经挪进 core 但标注没写，重跑时 shutil.move 找不到源图、永远卡在这一帧（PR #104）。"""
    ds = schema.init(tmp_path / "ds")
    (ds / "legacy-v1").mkdir()
    img = ds / "legacy-v1" / "mac-x-00001.jpg"
    cv2.imwrite(str(img), np.full((1280, 720, 3), 50, np.uint8))
    (ds / "legacy-v1" / "mac-x-00001.json").write_text(json.dumps({"schema": 1, "id": "mac-x-00001", "image": "mac-x-00001.jpg", "page": "HIF_EXAM",
        "source": {"run": "x", "seq": 1}, "platform": {"kind": "mac", "stored": [720, 1280]}}), encoding="utf-8")  # fmt: skip
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "mac-x-00001.json").write_text(json.dumps({**_labeled(), "image": str(img)}), encoding="utf-8")

    def boom(_im):
        raise RuntimeError("检测挂了")

    with pytest.raises(RuntimeError):
        st.ingest(ds, "exam", runs, detect=boom)
    got = st.ingest(ds, "exam", runs, detect=lambda im: [("SKIP", (560, 760, 640, 820))])  # 重跑接着处理
    assert got == {"written": 1, "buttons_missing": 0}
    assert (ds / "core" / "mac-x-00001.json").is_file()
