"""场景真值落盘：按钮定位、schema 3 帧、旧 schema 2 帧归档（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-scenarios-design.md §3.3、§4）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tools.ocr_dataset import schema
from tools.ocr_dataset.lexicon import levenshtein, norm


def locate_buttons(labels: list[str], detections: list[tuple[str, tuple]]) -> list[dict]:
    """按钮文字（LLM 照画面给的）→ 位置：在 OCR 检测框里找文字最像的那段，只借它的框、不借它的文字。
    完全包含优先，其次编辑距离 ≤ max(1, 字数 // 3)；同样像的取靠下的（按钮多在画面下方）。找不到 box 为 None（核对页让人点）。"""
    out = []
    for label in labels:
        want = norm(label)
        best = None
        for text, box in detections:
            t = norm(text)
            if want and want in t:
                rank = (0, len(t) - len(want))
            else:
                d = levenshtein(t, want, max(1, len(want) // 3))
                if d > max(1, len(want) // 3):
                    continue
                rank = (1, d)
            key = (rank, -box[1])
            if best is None or key < best[0]:
                best = (key, box)
        out.append({"label": label, "box": [int(v) for v in best[1]] if best else None})
    return out


def build_frame(meta: dict, img_path: Path, sid: str, labeled: dict, buttons: list[dict]) -> dict:
    fr = {
        "schema": schema.SCHEMA_SCENARIO,
        "id": meta["id"],
        "image": meta["image"],
        "sha256": schema.sha256(img_path),
        "source": meta.get("source") or {},
        "platform": meta.get("platform") or {},
        "page": meta.get("page") or sid,
        "scenario": sid,
        "truth": labeled["truth"],
        "item_status": labeled.get("item_status") or {},
        "buttons": buttons,
        "answers": labeled.get("answers") or {},
    }
    if meta.get("note"):
        fr["note"] = meta["note"]
    return fr


def archive_schema2(dataset: str | Path) -> int:
    """core/ 里 schema 2（按代码字段出的真值）的帧连同图片挪到 legacy-v2/。"""
    root = Path(dataset)
    n = 0
    for js in sorted((root / "core").glob("*.json")):
        if json.loads(js.read_text(encoding="utf-8")).get("schema") != schema.SCHEMA_FIELDS:
            continue
        (root / "legacy-v2").mkdir(exist_ok=True)
        for p in (root / "core").glob(js.stem + ".*"):
            shutil.move(str(p), root / "legacy-v2" / p.name)
        n += 1
    return n


def _find_frame(root: Path, fid: str) -> tuple[Path, dict] | None:
    """在 core / legacy-v2 / legacy-v1 里找这一帧的元信息（来源、平台）和所在目录。"""
    for sub in ("core", "legacy-v2", "legacy-v1"):
        js = root / sub / f"{fid}.json"
        if js.is_file():
            return root / sub, json.loads(js.read_text(encoding="utf-8"))
    return None


def ingest(dataset: str | Path, sid: str, labeled_dir: str | Path, detect) -> dict[str, int]:
    """labeled_dir 里每帧的双 LLM 标注结果（scenario_label.label_frame 的输出）→ core/<id>.json（schema 3）；
    图片从 legacy 目录挪进 core（同 id 的旧标注留在 legacy 不动）。detect(img) → [(文字, 框)]，只用来给按钮找位置。"""
    import cv2

    root = Path(dataset)
    core = root / "core"
    core.mkdir(exist_ok=True)
    written = missing = 0
    for p in sorted(Path(labeled_dir).glob("*.json")):
        found = _find_frame(root, p.stem)
        if found is None:
            continue
        where, meta = found
        img_src = where / meta["image"]
        img_dst = core / meta["image"]
        if (
            where != core and not img_dst.exists()
        ):  # 上次这一帧图片挪过来了、后面（检测按钮等）失败了：重跑接着处理，不再挪
            shutil.move(str(img_src), img_dst)
        labeled = json.loads(p.read_text(encoding="utf-8"))
        buttons = locate_buttons(labeled.get("button_labels") or [], detect(cv2.imread(str(img_dst))))
        missing += sum(1 for b in buttons if b["box"] is None)
        fr = build_frame(meta, img_dst, sid, labeled, buttons)
        (core / f"{meta['id']}.json").write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1
    return {"written": written, "buttons_missing": missing}


def apply_answers(dataset: str | Path, comments: list[dict]) -> dict[str, int]:
    """核对页上的「定答案」意见（kind = 定答案，field = 信息项路径，text = 值）写回帧的真值：同一项以最后提交的为准；
    值先按 JSON 解析（一键取 Claude / Codex 时是 JSON），不是 JSON 就当手填的文字，再按场景定义的类型归一。写回的项记 resolved。"""
    import time as _time

    from tools.ocr_dataset import scenario

    root = Path(dataset)
    latest: dict[tuple[str, str], dict] = {}
    for c in sorted(
        (c for c in comments if c.get("kind") == "定答案" and c.get("field") and not c.get("deleted")),
        key=lambda c: c.get("created", 0),
    ):
        latest[(c["target"], c["field"])] = c
    applied = missing = 0
    by_frame: dict[str, list[dict]] = {}
    for (fid, _), c in latest.items():
        by_frame.setdefault(fid, []).append(c)
    for fid, cs in by_frame.items():
        js = next(
            (d / f"{fid}.json" for d in sorted(root.iterdir()) if d.is_dir() and (d / f"{fid}.json").is_file()), None
        )
        if js is None:  # 帧在哪个集合（core / hard ……）都行
            missing += len(cs)
            continue
        fr = json.loads(js.read_text(encoding="utf-8"))
        sid = fr["scenario"]
        for c in cs:
            spec = scenario.spec_for(sid, c["field"])
            if spec is None:
                missing += 1
                continue
            try:
                raw = json.loads(c["text"])
            except ValueError:
                raw = c["text"]
            value = scenario._value(spec, raw)
            scenario._set(fr["truth"], c["field"], value)
            fr.setdefault("item_status", {})[c["field"]] = "resolved"
            fr.setdefault("reviewed", {})[c["field"]] = _time.strftime("%Y-%m-%d")
            applied += 1
        js.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"applied": applied, "missing": missing}
