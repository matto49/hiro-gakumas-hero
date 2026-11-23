from hiro.cli import registry

import pytest

CALLS = []


@pytest.fixture
def act_cmd():
    import hiro.cli.main  # noqa: F401

    @registry.command(
        "test poke",
        registry.Risk.ACT,
        "测试用：会动游戏的命令",
        args=(registry.Arg("--times", "次数", type="int", default=1),),
        confirm_summary=lambda a: f"戳 {a['times']} 下",
    )
    def _poke(a):
        CALLS.append(a["times"])
        return {"poked": a["times"]}

    CALLS.clear()
    yield
    registry.COMMANDS.pop("test poke")


def test_act_command_returns_pending_without_running(run_cli, act_cmd):
    code, env = run_cli("test", "poke", "--times", "3")
    assert code == 3 and env["ok"] is True
    p = env["data"]["pending"]
    assert p["id"] and p["summary"] == "戳 3 下" and p["command"] == "test poke" and p["expires_at"]
    assert CALLS == []


def test_confirm_runs_once(run_cli, act_cmd):
    _, env = run_cli("test", "poke", "--times", "2")
    pid = env["data"]["pending"]["id"]
    code, env = run_cli("confirm", pid)
    assert code == 0 and env["data"] == {"poked": 2} and CALLS == [2]
    code, env = run_cli("confirm", pid)
    assert env["ok"] is False and env["error"]["code"] == "pending_unknown"
    assert CALLS == [2]


def test_expired_pending_is_refused(run_cli, act_cmd, monkeypatch):
    from hiro.cli import pending

    _, env = run_cli("test", "poke")
    pid = env["data"]["pending"]["id"]
    monkeypatch.setattr(pending, "_now", lambda: 10**12)
    _code, env = run_cli("confirm", pid)
    assert env["error"]["code"] == "pending_expired" and CALLS == []


def test_cancel_and_list(run_cli, act_cmd):
    _, env = run_cli("test", "poke")
    pid = env["data"]["pending"]["id"]
    _, env = run_cli("pending", "list")
    assert [p["id"] for p in env["data"]["pending"]] == [pid]
    run_cli("cancel", pid)
    _, env = run_cli("pending", "list")
    assert env["data"]["pending"] == []


def test_confirm_is_not_an_agent_tool(run_cli):
    _, env = run_cli("tools")
    tools = {t["name"]: t for t in env["data"]["tools"]}
    assert tools["confirm"]["agent"] is False


def test_bad_id_is_rejected(run_cli):
    _code, env = run_cli("confirm", "../../etc/passwd")
    assert env["error"]["code"] == "pending_unknown"


def test_agent_sandbox_env_refuses_confirm_secrets_and_act(run_cli, act_cmd, monkeypatch):
    _, env = run_cli("test", "poke")
    pid = env["data"]["pending"]["id"]
    monkeypatch.setenv("HIRO_AGENT_SANDBOX", "1")
    for argv in (("confirm", pid), ("test", "poke"), ("secret", "copy", "game-password"), ("cancel", pid)):
        code, env = run_cli(*argv)  # noqa: RUF059
        assert env["ok"] is False and env["error"]["code"] == "agent_readonly", argv
        assert "HIRO_ACTION" in env["error"]["hint"]
    _code, env = run_cli("pending", "list")
    assert env["ok"] is True
    assert CALLS == []
