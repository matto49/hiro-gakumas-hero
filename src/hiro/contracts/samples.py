# 来源：digital-hiro src/hiro/contracts/samples.py（develop ce5085f）
"""用数据集（hiro.collect 的 manifest.jsonl）生成 Observation 样本并过 schema 校验。

    python -m hiro.contracts.samples datasets/20261029-224103-hif-qualify [--reread] [--limit N] [--out reports/contracts/x.jsonl]

默认直接用 manifest 里采集时保存的识别字段；--reread 对 ACTION_SELECT / SELECT_CARD 等帧重新跑识别
（采集时未开字段提取的页面只能这样补）。输出每行一个 Observation JSON，并打印校验统计。
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import hiro  # noqa: F401
from hiro.contracts import Observation
from hiro.vision.observe import FIELD_ADAPTERS, observation

REREAD = {"ACTION_SELECT", "SELECT_CARD", "SELECT_DRINK", "HIF_EXAM", "HIF_EXAM_RESULT"}


def _reread(scene: str, path: Path) -> dict | None:
    import cv2

    from hiro.vision.extract import extract
    from hiro.vision.offline import load

    raw = cv2.imread(str(path))
    if raw is None:  # 帧文件已删除：保留 manifest 里的原值
        return None
    img = load(raw)
    fields, _ = extract(scene, img)
    return fields


def _reclassify(path: Path) -> str | None:
    """用当前的 hiro.vision.scene 重判页面（manifest 里是采集时的旧标签）。图不在就返回 None。"""
    import cv2

    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    raw = cv2.imread(str(path))
    if raw is None:
        return None
    load(raw)
    return classify_current().scene


def from_manifest(dataset: Path, *, reread: bool = False, force: bool = False, reclassify: bool = False, limit: int | None = None):
    """reread：只重读 fields 缺失或报错的帧；force：图片还在就全部重读；reclassify：先用当前分类器重判页面。"""
    rows = [json.loads(l) for l in (dataset / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if limit:
        rows = rows[:limit]
    prev = None
    stable = 0
    for r in rows:
        scene = r.get("scene") or "unknown"
        if reclassify:
            scene = _reclassify(dataset / r["file"]) or scene
        stable = stable + 1 if scene == prev else 1
        prev = scene
        raw = r.get("fields") if scene == (r.get("scene") or "unknown") else None
        if force and scene in FIELD_ADAPTERS:
            raw = _reread(scene, dataset / r["file"]) or raw
        elif reread and scene in REREAD and scene in FIELD_ADAPTERS and (raw is None or "error" in raw):
            raw = _reread(scene, dataset / r["file"]) or raw
        yield observation(
            r["seq"], r["t"], scene, raw, area=r.get("area", "unknown"), screenshot=r["file"], source="dataset", stable_frames=stable,
            timings_ms={k[:-3]: r[k] for k in ("capture_ms", "classify_ms", "extract_ms") if r.get(k) is not None},
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--reread", action="store_true", help="只重读字段缺失或报错的帧")
    ap.add_argument("--force-reread", action="store_true", help="图片还在就全部重读（旧格式字段也换成当前 observe 的输出）")
    ap.add_argument("--reclassify", action="store_true", help="用当前分类器重判页面，不信 manifest 的采集时标签")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    out = args.out or Path("reports/contracts") / f"{args.dataset.name}-observations.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = bad = 0
    pages: Counter = Counter()
    with_fields: Counter = Counter()
    missing: Counter = Counter()
    with out.open("w", encoding="utf-8") as f:
        for obs in from_manifest(args.dataset, reread=args.reread, force=args.force_reread, reclassify=args.reclassify, limit=args.limit):
            n += 1
            pages[obs.page.name] += 1
            errs = obs.problems(strict=True)
            if errs:
                bad += 1
                print(f"seq {obs.seq} {obs.page.name}: {errs[:3]}", file=sys.stderr)
            if obs.known():
                with_fields[obs.page.name] += 1
            for m in obs.missing():
                missing[f"{obs.page.name}.{m}"] += 1
            text = obs.to_json()
            Observation.from_json(text)  # 往返
            f.write(text + "\n")
    print(json.dumps({"dataset": str(args.dataset), "out": str(out), "observations": n, "schema_failures": bad,
                      "pages": dict(pages.most_common()), "with_fields": dict(with_fields),
                      "missing_fields": dict(missing.most_common(12))}, ensure_ascii=False, indent=1))  # fmt: skip
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
