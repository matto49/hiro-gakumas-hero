"""把复盘页服务器上的标注拉回本地，存成数据集：datasets/annotations/notes.jsonl（全部）和 <run>.md（按局，可读）。

    .venv/bin/python tools/viewer/pull_notes.py
决策会话用 notes.jsonl 生成黄金用例：每条有 run / exam / attempt / seg / seq（定位 trace 步骤）、verdict、should（应该的动作）、tags、text。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "datasets" / "annotations"
EXAM = {"qualify-1": "选拔①", "qualify-2": "选拔②", "qualify-3": "选拔③", "r1": "本战 R1", "r2": "本战 R2"}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    out = Path(ap.parse_args().out)
    # 走 ssh 读服务器文件（不经过本机代理），同一 id 以最后一条为准
    raw = subprocess.run(
        [
            "ssh",
            os.environ["HIRO_VIEW_HOST"],
            "cat " + os.environ.get("HIRO_NOTES_FILE", "/var/lib/hiro-notes/notes.jsonl"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    notes: dict[str, dict] = {}
    for line in raw.splitlines():
        try:
            n = json.loads(line)
        except ValueError:
            continue
        notes[n["id"]] = n
    live = sorted(
        (n for n in notes.values() if not n.get("deleted")),
        key=lambda n: (n.get("run", ""), n.get("exam", ""), n.get("seq", 0)),
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "notes.jsonl").write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in live), encoding="utf-8")
    by_run = defaultdict(list)
    for n in live:
        by_run[n["run"]].append(n)
    for run, ns in by_run.items():
        lines = [f"# 标注：{run}", ""]
        for n in ns:
            where = f"{EXAM.get(n.get('exam'), n.get('exam'))}{'（第' + str(n['attempt']) + '次）' if (n.get('attempt') or 1) > 1 else ''} · 剩{n.get('rem')}（{n.get('attr')}×{n.get('mult')}）· 段{n.get('seg')} #{n.get('seq')}"
            lines.append(f"- {where} · 当时：{n.get('action')}（{n.get('source')}）")
            lines.append(
                f"  判定：{n.get('verdict') or '—'}"
                + (f"；应该：{n['should']}" if n.get("should") else "")
                + (f"；类型：{'、'.join(n['tags'])}" if n.get("tags") else "")
            )
            if n.get("text"):
                lines.append("  " + n["text"].replace("\n", "\n  "))
        (out / f"{run}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(live)} 条标注，{len(by_run)} 局 → {out}")


if __name__ == "__main__":
    main()
