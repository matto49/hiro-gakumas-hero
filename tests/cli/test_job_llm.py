"""对局里的 LLM 跟着控制台的模型设置走：桌面程序在确认开局时把 HIRO_LLM / OPENAI_* 交给工具层，任务子进程拿到；Key 不落盘、不进事件。"""

import json  # noqa: F401
import time

import pytest

pytestmark = pytest.mark.synthetic

KEY = "sk-test-not-a-real-key-123"


@pytest.fixture
def service(hiro_home):
    yield
    from hiro.cli import client

    client.shutdown()


def _run_selftest(run_cli):
    _, env = run_cli("run", "start", "selftest", "--seconds", "1")
    _, env = run_cli("confirm", env["data"]["pending"]["id"])
    job = env["data"]["job"]["id"]
    t0 = time.time()
    while time.time() - t0 < 15:
        _, st = run_cli("run", "events", "--job", job)
        if st["data"]["state"] == "done":
            return job, st["data"]["events"]
        time.sleep(0.2)
    raise AssertionError(st)


def test_model_env_reaches_the_job_but_not_disk(run_cli, service, hiro_home, monkeypatch):
    monkeypatch.setenv("HIRO_LLM", "openai:deepseek-chat")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    _job, events = _run_selftest(run_cli)
    logs = [e["text"] for e in events if e["type"] == "log"]
    assert "llm=openai:deepseek-chat base=https://api.deepseek.com key=set" in logs
    for p in hiro_home.rglob("*"):
        if p.is_file():
            assert KEY.encode() not in p.read_bytes(), p


def test_llm_off_means_rules_only(hiro_home):
    from hiro.cli.jobs import KINDS, Job

    job = Job(hiro_home, KINDS["hif"], {}, llm_env={"HIRO_LLM": "off"})
    argv = job.kind.argv(job.params, job.dir)
    assert "--no-llm" in argv
    assert "HIRO_LLM" not in job.child_env()
    tower = Job(hiro_home, KINDS["tower"], {"idol": "篠澤広"}, llm_env={"HIRO_LLM": "off"})
    assert "--no-llm" in tower.kind.argv(tower.params, tower.dir)


def test_only_model_variables_are_forwarded(hiro_home):
    from hiro.cli.jobs import KINDS, Job

    job = Job(hiro_home, KINDS["selftest"], {}, llm_env={"HIRO_LLM": "codex", "PATH": "/evil", "PYTHONPATH": "/evil"})
    env = job.child_env()
    assert env["HIRO_LLM"] == "codex" and env["PATH"] != "/evil" and "/evil" not in env["PYTHONPATH"]
