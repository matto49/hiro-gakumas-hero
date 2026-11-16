import io
import json

import pytest

from hiro.cli import secrets as sec

VALUE = "p@ss-WORD-1234"


@pytest.fixture
def clip(monkeypatch):
    calls = []
    monkeypatch.setattr(sec, "write_clipboard", lambda value, concealed, clear_after: calls.append((value, concealed, clear_after)))
    return calls


@pytest.fixture
def stdin(monkeypatch):
    def feed(text):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return feed


def test_set_reads_stdin_and_list_shows_names_only(run_cli, stdin, capsys):
    stdin(VALUE + "\n")
    code, env = run_cli("secret", "set", "game-password")
    assert code == 0 and VALUE not in json.dumps(env)
    code, env = run_cli("secret", "list")
    assert env["data"]["secrets"] == [{"name": "game-password", "saved": True}]
    assert VALUE not in json.dumps(env)


def test_copy_puts_value_on_clipboard_concealed_without_printing_it(run_cli, stdin, clip):
    stdin(VALUE)
    run_cli("secret", "set", "game-password")
    code, env = run_cli("secret", "copy", "game-password")
    assert code == 0 and VALUE not in json.dumps(env)
    assert env["data"] == {"copied": "game-password", "clears_in": 45}
    assert clip == [(VALUE, True, 45)]


def test_copy_missing_secret_is_explained(run_cli, clip):
    code, env = run_cli("secret", "copy", "game-password")
    assert env["error"]["code"] == "secret_missing" and "设置" in env["error"]["hint"]
    assert clip == []


def test_no_command_can_print_a_secret(run_cli, stdin, capsys):
    import hiro.cli.main  # noqa: F401
    from hiro.cli import registry

    stdin(VALUE)
    run_cli("secret", "set", "game-password")
    names = [c for c in registry.COMMANDS if c.startswith("secret")]
    assert sorted(names) == ["secret copy", "secret delete", "secret list", "secret set"]
    assert registry.COMMANDS["secret set"].agent is False and registry.COMMANDS["secret delete"].agent is False


def test_empty_value_and_bad_name_are_refused(run_cli, stdin):
    stdin("\n")
    code, env = run_cli("secret", "set", "game-password")
    assert env["error"]["code"] == "empty_secret"
    stdin(VALUE)
    code, env = run_cli("secret", "set", "../x")
    assert env["error"]["code"] == "bad_args"


def test_delete_removes_it(run_cli, stdin):
    stdin(VALUE)
    run_cli("secret", "set", "game-account")
    run_cli("secret", "delete", "game-account")
    _, env = run_cli("secret", "list")
    assert env["data"]["secrets"] == []
