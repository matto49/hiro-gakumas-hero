"""最终评审提出的问题的回归测试。"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.synthetic

SRC = str(Path(__file__).resolve().parents[2] / "src")


def _service_env(home):
    return {**os.environ, "HIRO_HOME": str(home), "PYTHONPATH": SRC}


def test_second_service_exits_while_first_holds_the_lock(hiro_home):
    from hiro.cli import client

    try:
        client.call("ping")
        first = json.loads((hiro_home / "service.json").read_text())
        p = subprocess.run([sys.executable, "-m", "hiro.cli.service"], env=_service_env(hiro_home), timeout=15,
                           capture_output=True)
        assert p.returncode != 0
        assert json.loads((hiro_home / "service.json").read_text())["pid"] == first["pid"]
    finally:
        client.shutdown()


def test_concurrent_clients_share_one_service(hiro_home):
    from hiro.cli import client

    pids = []

    def go():
        pids.append(client.call("ping")["data"]["pid"])

    try:
        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(set(pids)) == 1, pids
    finally:
        client.shutdown()


def test_orphan_child_of_a_dead_service_is_stopped_on_restart(hiro_home):
    from hiro.cli.jobs import jobs_dir
    from hiro.cli.service import Manager

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    d = jobs_dir(hiro_home) / "20261117-000000-abcd"
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({"id": d.name, "state": "running", "pid": child.pid}), encoding="utf-8")
    Manager(hiro_home)
    assert child.wait(timeout=10) is not None
    assert json.loads((d / "job.json").read_text())["state"] == "lost"


def test_job_record_has_child_pid_and_pausable(hiro_home):
    from hiro.cli.jobs import KINDS, Job

    job = Job(hiro_home, KINDS["selftest"], {"seconds": 1})
    job.start()
    rec = json.loads((job.dir / "job.json").read_text())
    assert rec["pid"] == job.proc.pid and rec["pausable"] is True
    job.proc.wait(10)
    assert KINDS["daily"].pausable is False and KINDS["scan-supports"].pausable is False
    assert KINDS["hif"].pausable is True and KINDS["tower"].pausable is True


def test_pause_on_a_non_pausable_job_is_refused(hiro_home):
    from hiro.cli.jobs import Job, Kind
    from hiro.cli.service import Manager

    mgr = Manager(hiro_home)
    kind = Kind("x", "测试", lambda p, d: [sys.executable, "-c", "import time; time.sleep(5)"], lambda p: "测试", pausable=False)
    job = Job(hiro_home, kind, {})
    mgr.jobs[job.id] = mgr.current = job
    job.start()
    try:
        r = mgr.control("takeover", None)
        assert r["ok"] is False and r["error"]["code"] == "not_pausable"
        assert not job.flag.exists()
    finally:
        job.stop()


def test_windows_children_get_no_console_window(hiro_home, monkeypatch):
    from hiro.cli import jobs

    seen = {}

    class FakeProc:
        pid = 1
        stdout = iter(())

        def wait(self, *a):
            return 0

        def poll(self):
            return 0

    def fake_popen(argv, **kw):
        seen.update(kw)
        return FakeProc()

    monkeypatch.setattr(jobs.sys, "platform", "win32")
    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    job = jobs.Job(hiro_home, jobs.KINDS["selftest"], {})
    job.start()
    assert seen["creationflags"] & 0x08000000  # CREATE_NO_WINDOW


def test_terminal_event_is_written_before_the_state_flips(hiro_home):
    from hiro.cli.jobs import KINDS, Job

    job = Job(hiro_home, KINDS["selftest"], {"seconds": 1})
    states_at_emit = {}
    orig = job.emit

    def spy(type_, text="", **kw):
        states_at_emit[type_] = job.state
        return orig(type_, text, **kw)

    job.emit = spy
    job.start()
    t0 = time.time()
    while job.state == "running" and time.time() - t0 < 10:
        time.sleep(0.05)
    assert states_at_emit["done"] == "running"


def test_follow_is_not_offered_to_agents(run_cli):
    _, env = run_cli("tools")
    ev = next(t for t in env["data"]["tools"] if t["name"] == "run events")
    follow = next(a for a in ev["args"] if a["name"] == "--follow")
    assert follow["agent"] is False


def test_hints_do_not_point_at_scans_that_do_not_exist(run_cli):
    from hiro.cli.jobs import KINDS

    for kind in ("idols", "affection"):
        _, env = run_cli("info", kind)
        hint = env["data"]["hint"]
        for word in hint.split():
            assert not word.startswith("scan-") or word in KINDS, hint
    _, env = run_cli("info", "summary")


def test_truncated_responses_do_not_escape_the_client(hiro_home, monkeypatch):
    import http.client

    from hiro.cli import client

    def boom(*a, **kw):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(client, "_info", lambda: {"port": 1, "token": "x"})
    monkeypatch.setattr(client, "_post", boom)
    assert client._alive({"port": 1, "token": "x"}) is False
    client.shutdown()  # 不抛
