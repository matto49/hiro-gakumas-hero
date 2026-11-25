"""DMM 回归清单 → schema 2 字段真值帧（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-fields-design.md §5.1、§9.2）。

tests/dmm_regression.json 里每条的期望值都是逐条看图核过的，直接当真值（truth_source = dmm_regression）。
xfail_on 记成 known_fail（某平台已知读不对），评测照样计分。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from tools.ocr_dataset import schema

import cv2

ROOT = Path(__file__).resolve().parents[2]
LIST = ROOT / "tests" / "dmm_regression.json"
FIX = ROOT / "tests" / "fixtures" / "dmm"
LEGACY = "legacy-v1"  # 旧的框级标注挪到这里（不参与校验和评测），第 3 步按字段重建


def run_seq(filename: str) -> tuple[str, int]:
    """文件名 → (运行名, seq)，规则同 pick 读图片目录。"""
    stem = Path(filename).stem
    m = re.match(r"^(.*)-(\d+)$", stem)
    return (m.group(1), int(m.group(2))) if m else (stem, 0)


def convert(sample: dict, img_path: Path, fid: str, image: str) -> dict:
    run, seq = run_seq(sample["file"])
    h, w = cv2.imread(str(img_path)).shape[:2]
    truth: dict = {}
    for k in ("fields", "quick_fields"):
        if sample.get(k):
            truth[k] = sample[k]
    checks = {k: sample[k] for k in ("quick", "scene", "tap") if k in sample}
    if checks:
        truth["checks"] = checks
    fr = {
        "schema": schema.SCHEMA_FIELDS,
        "id": fid,
        "image": image,
        "sha256": schema.sha256(img_path),
        "source": {"run": run, "seq": seq, "contributor": "matto"},
        "platform": {"kind": "dmm", "native": None, "stored": [w, h]},
        "page": sample.get("page") or "unknown",
        "truth": truth,
        "field_status": {},
        "truth_source": "dmm_regression",
    }
    if sample.get("note"):
        fr["note"] = sample["note"]
    if sample.get("xfail_on"):
        fr["known_fail"] = sample["xfail_on"]
    return fr


def _existing_schema(path: Path):
    """已有标注的 schema 版本；文件不在或读不了返回 None。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d.get("schema") if isinstance(d, dict) else None


def import_all(dataset: str | Path, list_path: str | Path = LIST, fixtures: str | Path = FIX) -> dict[str, int]:
    """回归帧写进 core/（图片已在就复用）；core 里其余 schema 1 的帧连同图片挪到 legacy-v1/；disputed.json 清空（字段版另算）。"""
    root, fixtures = Path(dataset), Path(fixtures)
    core = root / "core"
    core.mkdir(parents=True, exist_ok=True)
    samples = json.loads(Path(list_path).read_text(encoding="utf-8"))
    done, missing, conflicts = set(), 0, set()
    for s in samples:
        src = fixtures / s["file"]
        if not src.is_file():
            missing += 1
            continue
        run, seq = run_seq(s["file"])
        fid = schema.frame_id("dmm", run, seq)
        image = fid + src.suffix.lower()
        if _existing_schema(core / f"{fid}.json") == schema.SCHEMA_SCENARIO:
            conflicts.add(fid)  # 同 ID 已有场景真值（v3）：不能被字段真值覆盖，跳过
            continue
        if not (core / image).is_file():
            shutil.copyfile(src, core / image)
        elif schema.sha256(core / image) != schema.sha256(src):
            conflicts.add(
                fid
            )  # 同名图片内容不同（fixture 换了图）：新真值写到旧截图上，标注里的哈希又是旧图的，校验器发现不了
            continue
        fr = convert(s, core / image, fid, image)
        (core / f"{fid}.json").write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
        done.add(fid)
    legacy = root / LEGACY
    moved = 0
    for js in sorted(core.glob("*.json")):
        if js.stem in done or js.stem in conflicts:
            continue
        if _existing_schema(js) != schema.SCHEMA_VERSION:  # 只挪明确是 v1 框级标注的；v2 / v3 / 读不了的原样留着
            continue
        legacy.mkdir(exist_ok=True)
        for p in core.glob(js.stem + ".*"):
            shutil.move(str(p), legacy / p.name)
        moved += 1
    (root / "disputed.json").write_text("[]\n", encoding="utf-8")
    return {"imported": len(done), "legacy_moved": moved, "missing": missing, "conflicts": len(conflicts)}
