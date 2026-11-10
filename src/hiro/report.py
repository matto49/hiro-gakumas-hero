# 来源：digital-hiro src/hiro/report.py（develop ce5085f）
"""识别验证报告：对一批截图重新识别，按场景汇总并生成本地 HTML 画廊，便于人工核对。

    .venv/bin/python -m hiro.report <数据集目录或图片目录>

数据集目录（含 manifest.jsonl）会与采集时的结果对比，标出识别变化；纯图片目录直接识别。
输出 <目录>/report.json 与 <目录>/report.html。
"""

import argparse
import html
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import cv2

import hiro  # noqa: F401
from hiro.vision.extract import extract
from hiro.vision.offline import load
from hiro.vision.scene import classify_current


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    args = parser.parse_args()
    root = Path(args.root)
    previous = {}
    if (root / "manifest.jsonl").exists():
        for line in (root / "manifest.jsonl").open(encoding="utf-8"):
            row = json.loads(line)
            previous[row["file"]] = row["scene"]
    images = sorted(p for p in root.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg") and "report" not in p.name)

    rows = []
    for path in images:
        img = cv2.imread(str(path))
        if img is None:
            continue
        img = load(img)
        result = classify_current()
        fields, extract_ms = extract(result.scene, img)
        rel = str(path.relative_to(root))
        rows.append({
            "file": rel, "area": result.area, "scene": result.scene, "classify_ms": round(result.elapsed_ms, 1),
            "extract_ms": round(extract_ms, 1), "fields": fields, "previous": previous.get(rel),
        })

    counts = Counter(r["scene"] for r in rows)
    changed = [r for r in rows if r["previous"] and r["previous"] != r["scene"]]
    ms = sorted(r["classify_ms"] for r in rows) or [0]
    summary = {
        "frames": len(rows), "scenes": dict(counts.most_common()), "unknown": counts.get("unknown", 0),
        "changed_since_collect": len(changed), "classify_ms_median": ms[len(ms) // 2], "classify_ms_max": ms[-1],
    }
    (root / "report.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1, default=str))

    groups = defaultdict(list)
    for r in rows:
        groups[r["scene"]].append(r)
    parts = [f"<h1>{html.escape(str(root))}</h1><pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=1))}</pre>"]
    for scene in sorted(groups, key=lambda s: (s != "unknown", s)):
        parts.append(f"<h2>{html.escape(scene)} ({len(groups[scene])})</h2><div class=g>")
        for r in groups[scene]:
            note = html.escape(json.dumps(r["fields"], ensure_ascii=False)[:600]) if r["fields"] else ""
            flag = f"<b>采集时：{html.escape(r['previous'])}</b><br>" if r in changed else ""
            src = html.escape(os.path.relpath(root / r["file"], root))
            parts.append(f"<figure><a href='{src}'><img src='{src}'></a><figcaption>{flag}{html.escape(r['file'])} · {r['classify_ms']}ms<br><small>{note}</small></figcaption></figure>")
        parts.append("</div>")
    style = "<style>body{font-family:sans-serif;margin:20px}.g{display:flex;flex-wrap:wrap;gap:10px}figure{width:200px;margin:0}img{width:200px}small{color:#555;word-break:break-all}</style>"
    (root / "report.html").write_text(f"<meta charset=utf-8>{style}" + "".join(parts), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print("report:", root / "report.html")


if __name__ == "__main__":
    main()
