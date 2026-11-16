from hiro.cli import registry


def test_tools_lists_itself_as_read(run_cli):
    code, env = run_cli("tools")
    assert code == 0 and env["ok"] is True
    tools = {t["name"]: t for t in env["data"]["tools"]}
    assert tools["tools"]["risk"] == "read"
    assert "help" in tools["tools"] and "args" in tools["tools"]


def test_unknown_command_is_an_error_envelope(run_cli):
    code, env = run_cli("no-such-thing")
    assert code == 2
    assert env["ok"] is False
    assert env["error"]["code"] == "unknown_command"
    assert env["error"]["hint"]


def test_no_forbidden_commands_exist():
    import hiro.cli.main  # noqa: F401  加载全部命令

    for name in registry.COMMANDS:
        assert "jewel" not in name and "discard" not in name and "retire" not in name


def test_home_follows_env(hiro_home):
    from hiro.cli.home import hiro_home as h

    assert h() == hiro_home


def test_registering_a_forbidden_command_is_refused():
    import pytest

    with pytest.raises(ValueError):
        registry.command("produce discard", registry.Risk.ACT, "x")(lambda a: None)
