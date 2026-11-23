"""从 trace 产出度量报告：考试内截图到点击的耗时分布、考试判稳（settle_fast 用时、等满次数、决策帧过时次数）、
LLM 调用次数与 token、故障恢复次数、页面切换、每场考试结果。

    python -m hiro.loop.report traces/<run> [traces/<run2> …] [--out reports/runs/<名>.json]
"""

import argparse
import json
import statistics  # noqa: F401
import sys
from collections import Counter
from pathlib import Path

from hiro.contracts import Step, read_steps
from hiro.loop.session import RECOVER_KINDS


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, round((len(xs) - 1) * p)))
    return xs[k]


def llm_summary(llm_calls: list) -> dict:
    """LLM 调用汇总：总输入 = 不含缓存的输入 + 缓存读 + 缓存写；费用为各次 cost_usd 之和（有才计）。"""
    return {
        "calls": len(llm_calls),
        "errors": sum(1 for c in llm_calls if c.error),
        "usage_limit": sum(1 for c in llm_calls if c.error and c.error.startswith("usage_limit")),
        "input_tokens": sum(c.input_tokens or 0 for c in llm_calls),
        "output_tokens": sum(c.output_tokens or 0 for c in llm_calls),
        "cache_read": sum(getattr(c, "cache_read_input_tokens", None) or 0 for c in llm_calls),
        "cache_creation": sum(getattr(c, "cache_creation_input_tokens", None) or 0 for c in llm_calls),
        "total_input": sum(
            (c.input_tokens or 0)
            + (getattr(c, "cache_read_input_tokens", None) or 0)
            + (getattr(c, "cache_creation_input_tokens", None) or 0)
            for c in llm_calls
        ),
        "cost_usd": round(sum(getattr(c, "cost_usd", None) or 0.0 for c in llm_calls), 4),
        "seconds_median": _pct([c.seconds for c in llm_calls], 0.5),
        "adapters": dict(Counter(f"{c.adapter}:{c.model}" for c in llm_calls)),
    }


def exam_settle(steps: list[Step]) -> dict:
    """考试判稳（hiro.exec.exam 每步写进 timings_ms 的 phase_*）：settle_fast 用时分布；等满没稳的次数（unstable_settle_timeout）；
    决策帧过时、不点重新决策的次数（unstable_stale_frame）；重新决策总次数（redo_n，含等存档）。"""
    t = [s.timings_ms for s in steps if "phase_settle_fast" in s.timings_ms]
    fast = [x["phase_settle_fast"] for x in t]

    def total(key: str) -> int:
        return int(sum(s.timings_ms.get(key, 0) for s in steps))

    return {"n": len(fast), "settle_fast_ms": {"median": _pct(fast, 0.5), "p90": _pct(fast, 0.9), "max": max(fast) if fast else None},
            "settle_timeout": total("phase_unstable_settle_timeout"), "stale_frames": total("phase_unstable_stale_frame"),
            "redo": total("phase_redo_n")}  # fmt: skip


def summarize(steps: list[Step], run_meta: dict | None = None) -> dict:
    decisions = [s for s in steps if s.decision is not None]
    exam_lat = [s.timings_ms["shot_to_tap"] for s in steps if "shot_to_tap" in s.timings_ms]
    decide_s = [s.decision.seconds for s in decisions if s.decision.seconds is not None]
    llm_calls = [c for s in decisions for c in s.decision.llm]
    recov = Counter()
    for s in steps:
        for n in s.notes:
            if n.startswith("recover:"):
                recov[n.split(":")[1]] += 1
    page_changes = sum(1 for a, b in zip(steps, steps[1:]) if a.state.page.name != b.state.page.name)  # noqa: RUF007
    exams: dict[str, dict] = {}
    for s in steps:
        label = s.state.exam_session.label
        if s.state.page.name == "HIF_EXAM" and label:
            e = exams.setdefault(label, {"decisions": 0, "turns": 0, "score": None})
            if s.decision is not None:
                e["decisions"] += 1
            e["turns"] = max(e["turns"], s.state.exam_session.turn)
            if s.state.exam and s.state.exam.get("score") is not None:
                e["score"] = s.state.exam["score"]
    # 考试画面最后一帧是打出最后一张牌之前的分数（GKMS 第五 / 六局：458,946 实为 629,767）：有本战结果页就用结果页的本轮得分。
    # 再挑戦时结果页出现多次，取最后一次（最终采用的那次挑战）
    for s in steps:
        f = s.observation.fields if s.observation is not None else {}
        if (
            s.observation is not None
            and s.observation.page.name == "HIF_FINALS_RANKING"
            and f.get("round_score") is not None
        ):
            rnd = f.get("finals_round") or f.get("round_result")
            if rnd in (1, 2):
                exams.setdefault(f"r{rnd}", {"decisions": 0, "turns": 0, "score": None})["score"] = f["round_score"]
    execs = [s.execution for s in steps if s.execution is not None]
    return {
        "run": run_meta or {},
        "steps": len(steps),
        "decisions": len(decisions),
        "decisions_by_source": dict(Counter(s.decision.source for s in decisions)),
        "decisions_by_decider": dict(Counter(s.decision.decider or "?" for s in decisions)),
        # 决策点补全（总控 11-13）：每个决策器各来源几次；兜底（fallback）次数应该逐局下降
        "decisions_by_decider_source": {d: dict(c) for d, c in sorted(_by_decider_source(decisions).items())},
        "fallbacks": sum(1 for s in decisions if s.decision.source == "fallback"),
        "page_changes": page_changes,
        "final_page": steps[-1].state.page.name if steps else None,
        "exam_shot_to_tap_ms": {"n": len(exam_lat), "median": _pct(exam_lat, 0.5), "p90": _pct(exam_lat, 0.9), "max": max(exam_lat) if exam_lat else None},
        "exam_settle": exam_settle(steps),
        "decide_seconds": {"n": len(decide_s), "median": _pct(decide_s, 0.5), "p90": _pct(decide_s, 0.9)},
        "llm": llm_summary(llm_calls),
        "recoveries": {k: recov.get(k, 0) for k in RECOVER_KINDS},
        "execution": {
            "n": len(execs), "ok": sum(1 for e in execs if e.ok), "retries": sum(e.retries for e in execs),
            "unexpected_transition": sum(1 for e in execs if e.expected_transition is False),
        },
        "exams": exams,
    }  # fmt: skip


def _by_decider_source(decisions) -> dict:
    out: dict = {}
    for s in decisions:
        out.setdefault(s.decision.decider or "?", Counter())[s.decision.source] += 1
    return out


def report(trace_dir: Path) -> dict:
    meta = {}
    run_json = trace_dir / "run.json"
    if run_json.exists():
        meta = json.loads(run_json.read_text(encoding="utf-8"))
    out = summarize(list(read_steps(trace_dir)), meta)
    out["trace"] = str(trace_dir)
    return out


def _settle_line(st: dict | None) -> str:
    if not st or not st["n"]:
        return "- 考试判稳：没有考试步"
    f = st["settle_fast_ms"]
    return (
        f"- 考试判稳：n={st['n']}，settle_fast 中位 {f['median']} ms、p90 {f['p90']} ms、最大 {f['max']} ms；"
        f"等满没稳 {st['settle_timeout']} 次，决策帧过时 {st['stale_frames']} 次，重新决策 {st['redo']} 次"
    )


def markdown(r: dict) -> str:
    lat = r["exam_shot_to_tap_ms"]
    llm = r["llm"]
    lines = [
        f"# 运行报告 {r.get('trace', '')}",
        "",
        f"- 步数 {r['steps']}，决策 {r['decisions']}（来源 {r['decisions_by_source']}；决策器 {r['decisions_by_decider']}）",
        f"- 兜底 {r.get('fallbacks', 0)} 次；各决策器来源："
        + "；".join(f"{d} {dict(c)}" for d, c in (r.get("decisions_by_decider_source") or {}).items()),
        f"- 页面切换 {r['page_changes']} 次，终止页面 {r['final_page']}",
        f"- 考试内截图→点击：n={lat['n']}，中位 {lat['median']} ms，p90 {lat['p90']} ms，最大 {lat['max']} ms",
        _settle_line(r.get("exam_settle")),
        f"- LLM：{llm['calls']} 次、总输入 {llm.get('total_input', llm['input_tokens'])}（其中缓存读 {llm.get('cache_read', 0)}、缓存写 {llm.get('cache_creation', 0)}）、"
        f"输出 {llm['output_tokens']}、费用 ${llm.get('cost_usd', 0):.4f}；失败 {llm['errors']}（用量上限 {llm.get('usage_limit', 0)}），单次中位 {llm['seconds_median']} s，适配器 {llm['adapters']}",
        f"- 故障恢复：{r['recoveries']}",
        f"- 执行：{r['execution']}",
        f"- 考试：{r['exams']}",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rows = [report(t) for t in args.traces]
    out = args.out or Path("reports/runs") / (args.traces[0].name + ("-batch" if len(rows) > 1 else "") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows if len(rows) > 1 else rows[0], ensure_ascii=False, indent=1), encoding="utf-8")
    for r in rows:
        print(markdown(r))
        print()
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
