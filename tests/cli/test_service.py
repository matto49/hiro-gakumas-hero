"""hiro 服务：真起一个后台进程（127.0.0.1），用内置的 selftest 任务种类（只打印几行，不碰游戏）。"""

import json
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.synthetic


@pytest.fixture
def service(hiro_home):
    yield
    from hiro.cli import client

    client.shutdown()


def start(run_cli, *argv):
    code, env = run_cli("run", "start", *argv)
    assert code == 3, env
    return run_cli("confirm", env["data"]["pending"]["id"])


def wait_state(run_cli, want, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, env = run_cli("run", "status")
        cur = env["data"]["current"] or (env["data"]["recent"] or [None])[0]
        if cur and cur["state"] in want:
            return cur
        time.sleep(0.2)
    raise AssertionError(f"等不到 {want}：{env}")


def test_status_spawns_the_service(run_cli, service, hiro_home):
    code, env = run_cli("run", "status")
    assert code == 0 and env["data"]["current"] is None
    info = json.loads((hiro_home / "service.json").read_text())
    assert info["port"] and info["token"]


def test_selftest_job_streams_events_until_done(run_cli, service):
    code, env = start(run_cli, "selftest", "--seconds", "1")
    assert code == 0 and env["data"]["job"]["kind"] == "selftest"
    job = env["data"]["job"]["id"]
    wait_state(run_cli, {"done"})
    _, env = run_cli("run", "events", "--job", job)
    evs = env["data"]["events"]
    assert any(e["type"] == "log" and "selftest 1" in e["text"] for e in evs)
    assert evs[-1]["type"] == "done"
    assert [e["seq"] for e in evs] == list(range(len(evs)))
    _, env = run_cli("run", "events", "--job", job, "--since", str(len(evs) - 1))
    assert len(env["data"]["events"]) == 1


def test_second_job_is_busy(run_cli, service):
    start(run_cli, "selftest", "--seconds", "5")
    _code, env = start(run_cli, "selftest", "--seconds", "1")
    assert env["ok"] is False and env["error"]["code"] == "busy"
    run_cli("run", "stop")
    wait_state(run_cli, {"stopped"})


def test_pause_takeover_resume_use_the_flag(run_cli, service):
    from pathlib import Path

    _, env = start(run_cli, "selftest", "--seconds", "5")
    flag = Path(env["data"]["job"]["pause_flag"])
    run_cli("run", "pause")
    assert flag.exists() and wait_state(run_cli, {"paused"})
    run_cli("run", "takeover")
    assert flag.exists() and wait_state(run_cli, {"takeover"})
    run_cli("run", "resume")
    assert not flag.exists() and wait_state(run_cli, {"running"})
    run_cli("run", "stop")
    assert wait_state(run_cli, {"stopped"})


def test_requests_without_token_are_refused(run_cli, service, hiro_home):
    run_cli("run", "status")
    info = json.loads((hiro_home / "service.json").read_text())
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/status", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 401


def test_unknown_kind_is_a_usage_error(run_cli, service):
    code, env = run_cli("run", "start", "no-such-kind")
    assert code == 2 and env["error"]["code"] == "bad_args"


def test_trace_steps_become_step_events(run_cli, service):
    _, env = start(run_cli, "selftest", "--seconds", "1")
    job = env["data"]["job"]["id"]
    wait_state(run_cli, {"done"})
    _, env = run_cli("run", "events", "--job", job)
    steps = [e for e in env["data"]["events"] if e["type"] == "step"]
    assert steps, env["data"]["events"]
    s = steps[0]
    assert s["page"] == "SELFTEST" and s["action"] == "Wait" and s["source"] == "rule"
    assert s["text"] == "自检：不碰游戏" and s["frame"].endswith("00001.jpg")
