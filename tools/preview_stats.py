"""预演可观测汇总（协调 2026-11-02 预演加速一期第 5 项）：trace 里每个考试决策步的预演耗时、计划 / 实际算完的世界数、候选数。

python tools/preview_stats.py traces/my-run-*      # 一个或多个 trace 目录
"""

import json
import statistics
import sys
from pathlib import Path


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def main(argv=None):
    dirs = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    steps = []
    for d in dirs:
        f = d / "steps.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                s = json.loads(line)
            except ValueError:
                continue
            pv = [p for p in s.get("previews") or [] if p.get("engine") == "sim" and p.get("preview_ms") is not None]
            if pv:
                steps.append({"trace": d.name, "seq": s.get("seq"), "ms": pv[0]["preview_ms"], "done": pv[0].get("worlds_done"),
                              "planned": pv[0].get("worlds_planned"), "cands": len(pv)})  # fmt: skip
    if not steps:
        print("没有带 preview_ms 的考试步（旧 trace 没记这些字段）")
        return 0
    ms = [x["ms"] for x in steps]
    done = [x["done"] for x in steps if x["done"] is not None]
    print(f"考试决策步 {len(steps)}：预演耗时 中位 {statistics.median(ms):,.0f} ms / p90 {pct(ms, 0.9):,.0f} ms；"
          f"实际算完的世界数 中位 {statistics.median(done) if done else '—'} / p10 {pct(done, 0.1)} / 最少 {min(done) if done else '—'}"
          f"（计划 {steps[0]['planned']}）；候选数 中位 {statistics.median([x['cands'] for x in steps])}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
