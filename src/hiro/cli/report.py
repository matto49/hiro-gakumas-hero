"""局后：成绩摘要和复盘页。直接读 trace 的 steps.jsonl 原始 JSON（不建契约对象：不同版本的 trace 字段会多出来，
建对象会报未知字段），考试分数和复盘页用 tools/viewer/build_exam_view.py。"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from hiro.cli.home import ensure_home
from hiro.cli.registry import CliError

_JOB = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def check_job(jid: str) -> str:
    if not _JOB.match(jid or ""):
        raise CliError("bad_args", f"任务编号不对：{jid}", "用 hiro run status 看任务编号", exit_code=2)
    return jid


def traces_dir() -> Path:
    return ensure_home() / "work" / "traces"


def segments(traces: Path, base: str) -> list[Path]:
    """一个任务的 trace：<编号> 和续跑段 <编号>-rN（按 N 排）。"""
    segs = [traces / base] + sorted(
        traces.glob(base + "-r*"),
        key=lambda p: int(p.name.rsplit("-r", 1)[1]) if p.name.rsplit("-r", 1)[1].isdigit() else 0,
    )
    return [s for s in segs if (s / "steps.jsonl").is_file()]


def uses_hidden(traces: Path, base: str) -> bool:
    """这个任务的任何一段用过数据来源插件给的隐藏信息（plugins.json 的 uses_hidden）。"""
    import json

    for seg in segments(traces, base):
        try:
            if json.loads((seg / "plugins.json").read_text(encoding="utf-8")).get("uses_hidden"):
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def _viewer():
    from hiro.cli.paths import REPO

    p = str(REPO / "tools" / "viewer")
    if p not in sys.path:
        sys.path.insert(0, p)
    import build_exam_view

    return build_exam_view


def exam_scores(base: str, traces: Path) -> list[dict]:
    """每场考试的最终分（复盘页的考试切分）。读不了就空。"""
    try:
        data = _viewer().build(base, traces)
    except Exception:  # noqa: BLE001 复盘工具对老 trace 读不了不影响摘要
        return []
    return [{"label": e.get("label"), "score": e.get("final_score")} for e in data.get("exams") or []]


def build_review_html(base: str, traces: Path) -> str:
    v = _viewer()
    data = v.build(base, traces)
    if not data["exams"]:
        raise CliError("no_exam", "这一局没有考试步骤，没有复盘页", "复盘页只给有考试的对局")
    from deck_view import attach_decks

    attach_decks(data, v._segments(traces, base))
    return v.TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))


def summarize(base: str) -> dict:
    traces = traces_dir()
    segs = segments(traces, base)
    if not segs:
        raise CliError("no_trace", f"任务 {base} 没有对局记录", "只有对局（hif / hajime / nia / tower）才有记录")
    steps = decisions = llm = recov = 0
    by_source: Counter = Counter()
    final_page = None
    for seg in segs:
        for line in (seg / "steps.jsonl").open(encoding="utf-8"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            steps += 1
            page = ((rec.get("observation") or {}).get("page") or {}).get("name")
            final_page = page or final_page
            dec = rec.get("decision")
            if dec:
                decisions += 1
                by_source[dec.get("source") or "?"] += 1
                llm += len(dec.get("llm") or [])
            recov += sum(1 for n in rec.get("notes") or [] if str(n).startswith("recover:"))
    return {
        "job": base,
        "segments": [s.name for s in segs],
        "steps": steps,
        "decisions": decisions,
        "by_source": dict(by_source),
        "llm_calls": llm,
        "recoveries": recov,
        "final_page": final_page,
        "exams": exam_scores(base, traces),
    }


def score_line(exams: list[dict]) -> str:
    scores = [e for e in exams if e.get("score") is not None]
    if not scores:
        return ""
    return f"{len(scores)} 场考试：" + " / ".join(f"{e['score']:,}" for e in scores)
