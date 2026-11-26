"""N10 / N11（11-26 G1 Mac 小白验收）：桌面端「接着打」每次用新任务号当 trace 名、不带偶像卡编号。

- 续打接不上上一段 trace：拿过的卡、考完的场数、局面打底都丢了；
- 偶像退回空，提示词「偶像」一节当成篠澤広写，藤田ことね的局 LLM 理由里出现「和篠澤広的打法契合」。

续打任务要接上同一局上一段的 trace 基名（<基名>-rN）并沿用偶像；偶像不明时提示词不写成任何偶像。
"""

import json
import time

from hiro.cli import jobs

import pytest

FKTN = "i_card-fktn-3-000"


def _job(home, jid, kind, params, state, ended=None):
    d = jobs.jobs_dir(home) / jid
    d.mkdir(parents=True)
    j = {"id": jid, "kind": kind, "params": params, "state": state, "ended_at": ended or time.time()}
    (d / "job.json").write_text(json.dumps(j), encoding="utf-8")


def _trace(home, name):
    (home / "work" / "traces" / name).mkdir(parents=True)


def test_resume_continues_previous_trace_and_idol(tmp_path):
    _job(tmp_path, "20261126-074414-4f81", "hajime", {"idol": FKTN, "difficulty": "regular"}, "stopped")
    _trace(tmp_path, "20261126-074414-4f81")
    job = jobs.Job(tmp_path, jobs.KINDS["hajime"], {"command": "resume"})
    assert job.params["trace"] == "20261126-074414-4f81-r1"
    assert job.params["idol"] == FKTN
    assert job.trace == tmp_path / "work" / "traces" / "20261126-074414-4f81-r1"
    argv = job.kind.argv(job.params, job.dir)
    assert argv[argv.index("--trace") + 1] == "20261126-074414-4f81-r1"
    assert argv[argv.index("--idol") + 1] == FKTN

    # 再续一次：接着编号，偶像还在（上一段续打任务的参数里已经带上了）
    job.state = "stopped"
    job._save()
    _trace(tmp_path, "20261126-074414-4f81-r1")  # 这一段跑过、写了 trace
    job2 = jobs.Job(tmp_path, jobs.KINDS["hajime"], {"command": "resume"})
    assert job2.params["trace"] == "20261126-074414-4f81-r2"
    assert job2.params["idol"] == FKTN


def test_resume_numbering_skips_existing_segments(tmp_path):
    _job(tmp_path, "20261126-092132-1c0b", "hajime", {"command": "resume", "idol": FKTN, "trace": "base-r2"}, "failed")
    for n in ("base", "base-r1", "base-r2", "base-r3"):
        _trace(tmp_path, n)
    out = jobs.resume_chain(jobs.jobs_dir(tmp_path), tmp_path / "work" / "traces", "hajime", {"command": "resume"})
    assert out == {"trace": "base-r4", "idol": FKTN}


@pytest.mark.parametrize(
    ("state", "age", "kind"),
    [("done", 0, "hajime"), ("stopped", 3 * 86400, "hajime"), ("stopped", 0, "nia")],
)
def test_resume_does_not_chain_finished_old_or_other_kind(tmp_path, state, age, kind):
    """上一局打完了 / 停了超过两天 / 是别的模式：这次续的是别处开的局，不接。"""
    _job(tmp_path, "20261126-074414-4f81", kind, {"idol": FKTN}, state, ended=time.time() - age)
    assert (
        jobs.resume_chain(jobs.jobs_dir(tmp_path), tmp_path / "work" / "traces", "hajime", {"command": "resume"}) == {}
    )


def test_resume_keeps_given_idol_and_new_is_untouched(tmp_path):
    _job(tmp_path, "20261126-074414-4f81", "hajime", {"idol": FKTN}, "stopped")
    out = jobs.resume_chain(
        jobs.jobs_dir(tmp_path), tmp_path / "work" / "traces", "hajime", {"command": "resume", "idol": "i_card-x"}
    )
    assert out == {"trace": "20261126-074414-4f81-r1"}
    job = jobs.Job(tmp_path, jobs.KINDS["hajime"], {"command": "new", "idol": FKTN})
    assert "trace" not in job.params and job.trace.name == job.id


def test_idol_section_unknown_idol_is_not_hiro():
    from hiro.brain.deciders import _idol_section

    for idol in (None, ""):
        text = _idol_section(idol, None, "produce-001")
        assert "篠澤広" not in text and "没认出来" in text


@pytest.mark.master
def test_idol_section_hiro_only_for_hiro():
    from hiro.brain.deciders import _idol_section
    from hiro.brain.state import profile_for

    assert "篠澤広" in _idol_section("i_card-skin-shro-3-007", None)
    text = _idol_section(FKTN, profile_for(FKTN), "produce-001")
    assert "篠澤広" not in text and "None" not in text


def test_prior_trace_dirs_numeric_order(tmp_path):
    """#128 review：续打超过 9 段时按编号数值排，-r10 在 -r9 后面，当前段最后。"""
    from types import SimpleNamespace as NS

    from hiro.produce.hif import HifCore

    for n in ["base", *(f"base-r{i}" for i in (1, 2, 9, 10, 11))]:
        (tmp_path / n).mkdir()
    (tmp_path / "base-other").mkdir()  # 别的基名不算
    core = HifCore.__new__(HifCore)
    core.s = NS(trace=NS(dir=tmp_path / "base-r11"))
    assert [d.name for d in core._prior_trace_dirs()] == ["base", "base-r1", "base-r2", "base-r9", "base-r10"]
    (tmp_path / "base-r11" / "steps.jsonl").write_text("{}\n", encoding="utf-8")
    assert core._prior_trace_dirs()[-1].name == "base-r11"


def test_report_reads_whole_chain_for_resume_job(tmp_path, monkeypatch):
    """#128 review：续打任务的 trace 是 <基名>-rN，成绩摘要 / 复盘按整局（基名）读；没接段的任务就是任务编号。"""
    from hiro.cli import report

    monkeypatch.setattr(report, "ensure_home", lambda: tmp_path)
    _job(tmp_path, "20261126-101734-38e8", "hajime", {"command": "resume", "trace": "20261126-074414-4f81-r3"}, "done")
    _job(tmp_path, "20261126-074414-4f81", "hajime", {"idol": FKTN}, "stopped")
    assert report.trace_base("20261126-101734-38e8") == "20261126-074414-4f81"
    assert report.trace_base("20261126-074414-4f81") == "20261126-074414-4f81"
    assert report.trace_base("20261126-000000-dead") == "20261126-000000-dead"
