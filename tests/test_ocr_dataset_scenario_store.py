"""tools.ocr_dataset.scenario_store：按钮定位、schema 3 帧写入与校验（规格 v3 §3.3、§4）。"""

import json

import cv2
import numpy as np
import pytest

from tools.ocr_dataset import schema, scenario_store as st

pytestmark = pytest.mark.synthetic


def test_locate_buttons_by_fuzzy_text():
    det = [("綿成", (100, 1100, 200, 1140)), ("キャンセル", (60, 1150, 220, 1190)), ("決定", (420, 1150, 540, 1190)), ("決定", (400, 300, 440, 320))]
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
    meta = {"id": "dmm-r-00001", "image": "dmm-r-00001.jpg", "source": {"run": "r", "seq": 1}, "platform": {"kind": "dmm", "stored": [720, 1280]}}
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
