"""过场耗时统计：每个决策步执行完 → 下一个决策步的决策帧，按「页面:动作 → 下一页面」归类。

    .venv/bin/python -m tools.audit.transitions run-a run-b ...

不含 LLM / 决策本身（那段在下一步的决策帧之后）。考试内（HIF_EXAM → HIF_EXAM）单独一类，默认不计入。
间隔中途的观测（observations_before / 页面变化记录）按页面名累计停留时间，用来看时间花在哪种过场画面上。
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _segments(base: str) -> list[Path]:
    d = ROOT / "traces"
    return sorted([p for p in d.glob(f"{base}*") if p.name == base or p.name.startswith(base + "-r")])


def _action(s: dict) -> str | None:
    d = s.get("decision") or {}
    a = d.get("action")
    if isinstance(a, dict):
        return a.get("kind") or a.get("type")
    return a


def gaps(base: str) -> list[dict]:
    out = []
    for seg in _segments(base):
        steps = [json.loads(line) for line in open(seg / "steps.jsonl")]  # noqa: SIM115
        last = None  # 上一个有执行结果的步
        between: list[dict] = []
        for s in steps:
            o = s.get("observation") or {}
            page = (o.get("page") or {}).get("name")
            if s.get("execution") and s.get("decision"):
                if last is not None:
                    src = (last["observation"].get("page") or {}).get("name")
                    gap = o.get("t", s["t"]) - last["t"]
                    if 0 <= gap < 600:
                        dwell: dict[str, float] = defaultdict(float)
                        obs = [
                            x
                            for b in between
                            for x in (b.get("observations_before") or []) + [b.get("observation") or {}]
                        ]
                        obs += list(s.get("observations_before") or [])
                        obs = sorted((x for x in obs if x.get("t")), key=lambda x: x["t"])
                        prev_t = last["t"]
                        for x in obs:
                            if x["t"] < prev_t:
                                continue
                            dwell[(x.get("page") or {}).get("name") or "?"] += x["t"] - prev_t
                            prev_t = x["t"]
                        notes = [n for b in between + [s] for n in (b.get("notes") or [])]
                        out.append(
                            {
                                "segment": seg.name,
                                "seq": s["seq"],
                                "from": src,
                                "action": _action(last),
                                "to": page,
                                "gap": gap,
                                "dwell": dict(dwell),
                                "t": last["t"],
                                "unknown_text": next((n for n in notes if "未识别画面持续" in n), None),
                            }
                        )
                last, between = s, []
            else:
                between.append(s)
    return out


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]  # noqa: RUF046


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("bases", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--include-exam", action="store_true")
    a = ap.parse_args(argv)
    rows = [r for b in a.bases for r in gaps(b)]
    if not a.include_exam:
        rows = [r for r in rows if not (r["from"] == "HIF_EXAM" and r["to"] == "HIF_EXAM")]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["from"], r["action"], r["to"])].append(r)
    total = sum(r["gap"] for r in rows)
    print(f"{len(rows)} 段过场，合计 {total / 60:.1f} min")
    print(f"{'起点页:动作 → 下一页':60s} {'次数':>4s} {'中位':>6s} {'p90':>6s} {'合计min':>7s}  停留最多的画面")
    for key, rs in sorted(groups.items(), key=lambda kv: -sum(r["gap"] for r in kv[1]))[:30]:
        g = [r["gap"] for r in rs]
        dw = defaultdict(float)
        for r in rs:
            for k, v in r["dwell"].items():
                dw[k] += v
        top = ", ".join(f"{k} {v / 60:.1f}m" for k, v in sorted(dw.items(), key=lambda kv: -kv[1])[:3])
        print(
            f"{f'{key[0]}:{key[1]} → {key[2]}':60s} {len(rs):4d} {st.median(g):6.1f} {q(g, 0.9):6.1f} {sum(g) / 60:7.1f}  {top}"
        )
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
