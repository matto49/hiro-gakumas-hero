"""Go 迁移阶段 3 的黄金数据：用 Python 版识别层跑一批截图，记下判页和读数，Go 版对照（go/README.md「验收方法」）。

    PYTHONPATH=src python tools/golden/export_vision.py --out ~/.cache/hiro/golden/vision <截图或目录…> [--limit N]

每张截图写两份到 --out：
- <编号>.png：cv2.imread 解出来的像素，无损存一份。Go 和 Python 读同一份像素（JPEG 两边解码器不一样，会差几个灰度级）。
- <编号>.json：{"src", "w", "h", "ocr", "classify": {name, source, area, fields}, "extract": {"scene", "fields", "ms"}, "error"}。
manifest.jsonl 每行一张的摘要。截图是游戏画面，不进仓库（AGENTS.md）；--out 放仓库外。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np


def jsonable(v):
    """识别结果 → JSON：numpy 数、NamedTuple（Box / Point）、dataclass、集合、bytes 都转成普通值。"""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return {"_ndarray": list(v.shape)}  # 图像之类不记内容
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {k: jsonable(x) for k, x in dataclasses.asdict(v).items()}
    if hasattr(v, "_asdict"):  # NamedTuple
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(jsonable(x) for x in v)
    if isinstance(v, bytes):
        return {"_bytes": len(v)}
    return repr(v)


def images(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.suffix.lower() in (".jpg", ".jpeg", ".png"))
        elif p.is_file():
            out.append(p)
    return out


def export_one(path: Path, out: Path) -> dict:
    from hiro.vision import ocr as ocr_mod
    from hiro.vision.classify import classify
    from hiro.vision.extract import extract

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    rec: dict = {"src": str(path), "ocr": ocr_mod.engine()}
    if img is None:
        rec["error"] = "读不了"
        return rec
    key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]
    rec.update(id=key, w=int(img.shape[1]), h=int(img.shape[0]))
    cv2.imwrite(str(out / f"{key}.png"), img)
    try:
        t0 = time.perf_counter()
        hit = classify(img)
        rec["classify"] = {"name": hit.name, "source": hit.source, "area": hit.area, "fields": jsonable(hit.fields),
                           "ms": round((time.perf_counter() - t0) * 1000, 1)}
        fields, ms = extract(hit.name, img)
        rec["extract"] = {"scene": hit.name, "fields": jsonable(fields), "ms": round(ms, 1)}
    except Exception:  # noqa: BLE001 识别出错也记下来：Go 版要么同样出错、要么给出说法
        rec["error"] = traceback.format_exc(limit=3)
    (out / f"{key}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="最多导出几张（0 = 全部）")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    files = images(a.paths)
    if a.limit:
        files = files[: a.limit]
    pages: dict[str, int] = {}
    with (out / "manifest.jsonl").open("w", encoding="utf-8") as mf:
        for i, f in enumerate(files, 1):
            rec = export_one(f, out)
            page = (rec.get("classify") or {}).get("name", "ERROR" if rec.get("error") else "?")
            pages[page] = pages.get(page, 0) + 1
            mf.write(json.dumps({"id": rec.get("id"), "src": rec["src"], "page": page, "error": bool(rec.get("error"))}, ensure_ascii=False) + "\n")
            if i % 50 == 0:
                print(f"{i}/{len(files)}", file=sys.stderr, flush=True)
    print(json.dumps({"total": len(files), "pages": dict(sorted(pages.items(), key=lambda kv: -kv[1]))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
