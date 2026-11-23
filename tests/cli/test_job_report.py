"""局后：成绩摘要（hiro run report）和复盘页（hiro run review）。"""

import json

import pytest

pytestmark = pytest.mark.synthetic


def _trace(home, name, steps):
    d = home / "work" / "traces" / name
    d.mkdir(parents=True, exist_ok=True)
    with (d / "steps.jsonl").open("w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return d


def step(seq, page, source=None, llm=0, notes=()):
    rec = {"seq": seq, "observation": {"page": {"name": page}}, "notes": list(notes)}
    if source:
        rec["decision"] = {"action": {"kind": "X"}, "source": source, "llm": [{}] * llm}
    return rec


def test_report_counts_across_segments(run_cli, hiro_home, monkeypatch):
    from hiro.cli import report

    _trace(hiro_home, "J1", [step(1, "HOME"), step(2, "ACTION_SELECT", "rule"), step(3, "HIF_EXAM", "bench")])
    _trace(
        hiro_home,
        "J1-r1",
        [step(1, "HIF_EXAM", "llm", llm=2, notes=["recover:page_misjudged: x"]), step(2, "RESULT", "rule")],
    )
    _trace(hiro_home, "J10", [step(1, "OTHER", "rule")])  # 别的任务，不能算进来
    monkeypatch.setattr(report, "exam_scores", lambda base, traces: [{"label": "final", "score": 87121}])
    _code, env = run_cli("run", "report", "--job", "J1")
    d = env["data"]
    assert d["segments"] == ["J1", "J1-r1"]
    assert d["steps"] == 5 and d["decisions"] == 4
    assert d["by_source"] == {"rule": 2, "bench": 1, "llm": 1}
    assert d["llm_calls"] == 2 and d["recoveries"] == 1 and d["final_page"] == "RESULT"
    assert d["exams"] == [{"label": "final", "score": 87121}]


def test_report_without_trace_says_so(run_cli):
    _code, env = run_cli("run", "report", "--job", "nope")
    assert env["ok"] is False and env["error"]["code"] == "no_trace"


def test_bad_job_id_is_refused(run_cli):
    _code, env = run_cli("run", "report", "--job", "../../etc")
    assert env["ok"] is False and env["error"]["code"] == "bad_args"


def test_review_builds_html_into_job_dir(run_cli, hiro_home, monkeypatch):
    from hiro.cli import report

    _trace(hiro_home, "J2", [step(1, "HIF_EXAM", "bench")])
    monkeypatch.setattr(report, "build_review_html", lambda base, traces: "<html>review</html>")
    _code, env = run_cli("run", "review", "--job", "J2")
    p = hiro_home / "jobs" / "J2" / "review.html"
    assert env["data"]["path"] == str(p) and p.read_text(encoding="utf-8") == "<html>review</html>"
    assert env["data"]["url"] == "review/J2.html"


def test_finished_produce_job_emits_report_event(hiro_home, monkeypatch):
    from hiro.cli import report
    from hiro.cli.jobs import KINDS, Job
    from hiro.cli.service import Manager

    monkeypatch.setattr(
        report, "exam_scores", lambda base, traces: [{"label": "r1", "score": 343538}, {"label": "r2", "score": 644347}]
    )
    mgr = Manager(hiro_home)
    job = Job(hiro_home, KINDS["hif"], {})
    _trace(hiro_home, job.id, [step(1, "HIF_EXAM", "bench")])
    mgr._exited(job)
    ev = [e for e in job.events() if e["type"] == "report"]
    assert ev and "343,538" in ev[0]["text"] and "644,347" in ev[0]["text"]
    assert ev[0]["exams"][1]["score"] == 644347
