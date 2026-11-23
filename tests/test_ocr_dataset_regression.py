"""tools.ocr_dataset.regression：DMM 回归清单转成 schema 2 字段真值帧（规格 2026-11-23-recognition-dataset-fields-design.md §4、§5.1、§9.2）。"""

import json

import cv2
import numpy as np
import pytest

from tools.ocr_dataset import regression, schema

pytestmark = pytest.mark.synthetic

SAMPLES = [
    {"file": "maindmm1m-20261110-205150-00051.jpg", "note": "体力读成 2", "page": "ACTION_SELECT", "quick": "ACTION_SELECT", "tap": [360, 1157],
     "scene": "ACTION_SELECT", "fields": {"stamina": 22, "params": {"vo": 1473}}, "xfail_on": {"windows": "RapidOCR 读错"}},
    {"file": "start-confirm-dmm1.png", "page": "PRODUCE_START_CONFIRM", "quick_fields": {"ok": True}},
]  # fmt: skip


def _setup(tmp_path):
    ds = schema.init(tmp_path / "ds")
    fx = tmp_path / "fixtures"
    fx.mkdir()
    for i, s in enumerate(SAMPLES):
        cv2.imwrite(str(fx / s["file"]), np.full((1280, 720, 3), 40 + i, np.uint8))
    lst = tmp_path / "dmm_regression.json"
    lst.write_text(json.dumps(SAMPLES, ensure_ascii=False), encoding="utf-8")
    # 一帧旧的 schema 1 框级标注（不是回归帧）
    cv2.imwrite(str(ds / "core" / "mac-x-00001.jpg"), np.full((1280, 720, 3), 99, np.uint8))
    old = {"schema": 1, "id": "mac-x-00001", "image": "mac-x-00001.jpg", "sha256": schema.sha256(ds / "core" / "mac-x-00001.jpg"),
           "source": {"run": "x", "seq": 1}, "platform": {"kind": "mac", "stored": [720, 1280]}, "page": "HOME", "boxes": []}  # fmt: skip
    (ds / "core" / "mac-x-00001.json").write_text(json.dumps(old), encoding="utf-8")
    return ds, fx, lst


def test_convert_sample_to_field_frame(tmp_path):
    ds, fx, lst = _setup(tmp_path)
    fr = regression.convert(SAMPLES[0], fx / SAMPLES[0]["file"], "dmm-maindmm1m-20261110-205150-00051", "dmm-maindmm1m-20261110-205150-00051.jpg")
    assert fr["schema"] == 2 and fr["page"] == "ACTION_SELECT"
    assert fr["truth"] == {"fields": {"stamina": 22, "params": {"vo": 1473}}, "checks": {"quick": "ACTION_SELECT", "scene": "ACTION_SELECT", "tap": [360, 1157]}}
    assert fr["field_status"] == {} and fr["truth_source"] == "dmm_regression" and fr["note"] == "体力读成 2"
    assert fr["source"] == {"run": "maindmm1m-20261110-205150", "seq": 51, "contributor": "matto"}
    assert fr["platform"] == {"kind": "dmm", "native": None, "stored": [720, 1280]}
    assert fr["known_fail"] == {"windows": "RapidOCR 读错"}


def test_import_writes_v2_frames_and_moves_legacy(tmp_path):
    ds, fx, lst = _setup(tmp_path)
    got = regression.import_all(ds, lst, fx)
    assert got == {"imported": 2, "legacy_moved": 1, "missing": 0}
    names = sorted(p.name for p in (ds / "core").iterdir())
    assert names == ["dmm-maindmm1m-20261110-205150-00051.jpg", "dmm-maindmm1m-20261110-205150-00051.json",
                     "dmm-start-confirm-dmm1-00000.json", "dmm-start-confirm-dmm1-00000.png"]  # fmt: skip
    assert sorted(p.name for p in (ds / "legacy-v1").iterdir()) == ["mac-x-00001.jpg", "mac-x-00001.json"]
    assert schema.validate_dir(ds) == []
    assert json.loads((ds / "disputed.json").read_text(encoding="utf-8")) == []


def test_validate_frame_v2_rules():
    good = {"schema": 2, "id": "a", "image": "a.jpg", "sha256": "x", "page": "HOME", "source": {"run": "r", "seq": 1},
            "platform": {"kind": "dmm", "stored": [720, 1280]}, "truth": {"fields": {"a": 1}}, "field_status": {"a": "disputed"}}  # fmt: skip
    assert schema.validate_frame(good) == []
    assert schema.validate_frame({**good, "truth": {}})
    assert schema.validate_frame({**good, "field_status": {"a": "maybe"}})
