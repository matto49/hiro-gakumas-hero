"""判页来源统计（去 KAA 第 0 步的基线表）：每个页面各由哪一层判出（PageObs.source），KAA 出手了多少次。

    PYTHONPATH=src .venv/bin/python tools/page_sources.py traces/<run> [traces/<run>-r1 ...] [--md out.md]

每个参数是一段 trace 目录（含 steps.jsonl）。旧 trace 没有 source 字段，记成「—」，另外按 notes 里的
recover:page_misjudged 统计补判了几次（那时 KAA 判不出、由我们兜底的次数）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

NO_SOURCE = "—"
MISJUDGED = "recover:page_misjudged:"


def observations(step: dict):
    yield from step.get("observations_before") or []
    if step.get("observation"):
        yield step["observation"]
    yield from step.get("observations_after") or []


def count(seg: Path) -> tuple[Counter, Counter]:
    """返回 (Counter[(页面, 来源)], Counter[补判说明])。同一段里同一 seq 同一页面只算一次（observe_save 与截图共用 seq）。"""
    pages: Counter = Counter()
    misjudged: Counter = Counter()
    seen: set = set()
    with open(seg / "steps.jsonl", encoding="utf-8") as f:
        for line in f:
            step = json.loads(line)
            for obs in observations(step):
                page = obs.get("page") or {}
                key = (obs.get("seq"), obs.get("source"), page.get("name"))
                if key in seen:
                    continue
                seen.add(key)
                pages[(page.get("name") or "unknown", page.get("source") or NO_SOURCE)] += 1
            for note in step.get("notes") or []:
                if isinstance(note, str) and note.startswith(MISJUDGED):
                    misjudged[note[len(MISJUDGED) :].strip()] += 1
    return pages, misjudged


def table(pages: Counter, misjudged: Counter, title: str) -> str:
    sources = sorted({s for _, s in pages}, key=lambda s: (s != "kaa", s))
    by_page: dict[str, Counter] = {}
    for (name, src), n in pages.items():
        by_page.setdefault(name, Counter())[src] += n
    total = Counter()
    for (_, src), n in pages.items():
        total[src] += n
    lines = [
        f"## {title}",
        "",
        "观测总数 " + str(sum(total.values())) + "；按来源：" + "、".join(f"{s} {total[s]}" for s in sources),
        "",
    ]
    lines.append("| 页面 | " + " | ".join(sources) + " | 合计 |")
    lines.append("|---" * (len(sources) + 2) + "|")
    for name, c in sorted(by_page.items(), key=lambda kv: (-kv[1]["kaa"], -sum(kv[1].values()), kv[0])):
        lines.append(f"| {name} | " + " | ".join(str(c[s] or "") for s in sources) + f" | {sum(c.values())} |")
    kaa_miss = Counter({t: n for t, n in misjudged.items() if t.startswith("KAA")})
    other = misjudged - kaa_miss
    for head, c in (("KAA 判不出、我们补判", kaa_miss), ("其他页面不一致（不是判页）", other)):
        if c:
            lines += ["", f"{head}（recover:page_misjudged）{sum(c.values())} 次：", ""]
            lines += [f"- {n} × {text}" for text, n in c.most_common()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("segments", nargs="+", type=Path)
    ap.add_argument("--md", type=Path, help="同时把表写到这个文件")
    args = ap.parse_args(argv)
    pages, misjudged = Counter(), Counter()
    for seg in args.segments:
        if not (seg / "steps.jsonl").exists():
            print(f"跳过 {seg}：没有 steps.jsonl", file=sys.stderr)
            continue
        p, m = count(seg)
        pages += p
        misjudged += m
    out = table(pages, misjudged, " + ".join(s.name for s in args.segments))
    print(out)
    if args.md:
        args.md.write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
