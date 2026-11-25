import io
import json

from hiro.cli import secrets as sec

import pytest

VALUE = "p@ss-WORD-1234"


@pytest.fixture
def clip(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sec, "write_clipboard", lambda value, concealed, clear_after: calls.append((value, concealed, clear_after))
    )
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
    _code, env = run_cli("secret", "copy", "game-password")
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
    code, env = run_cli("secret", "set", "game-password")  # noqa: RUF059
    assert env["error"]["code"] == "empty_secret"
    stdin(VALUE)
    _code, env = run_cli("secret", "set", "../x")
    assert env["error"]["code"] == "bad_args"


def test_delete_removes_it(run_cli, stdin):
    stdin(VALUE)
    run_cli("secret", "set", "game-account")
    run_cli("secret", "delete", "game-account")
    _, env = run_cli("secret", "list")
    assert env["data"]["secrets"] == []


class _Fn:
    def __init__(self, ret, log, name):
        self.ret, self.log, self.name = ret, log, name
        self.argtypes = self.restype = None

    def __call__(self, *a):
        self.log.append((self.name, a))
        return self.ret(*a) if callable(self.ret) else self.ret


class _Lib:
    def __init__(self, log, **rets):
        for name, ret in rets.items():
            setattr(self, name, _Fn(ret, log, name))


def _fake_win(log, *, lock=0x7FF000001000, set_data=1):
    u = _Lib(
        log,
        CreateWindowExW=0x1234,
        DestroyWindow=1,
        OpenClipboard=1,
        EmptyClipboard=1,
        SetClipboardData=set_data,
        CloseClipboard=1,
        RegisterClipboardFormatW=0xC100,
        GetClipboardSequenceNumber=7,
    )
    k = _Lib(log, GlobalAlloc=0x7FF000000F00, GlobalLock=lock, GlobalUnlock=1, GlobalFree=0)
    return u, k


def test_windows_clipboard_checks_every_step(monkeypatch):
    from hiro.cli import secrets as sec
    from hiro.cli.registry import CliError

    import pytest

    monkeypatch.setattr(sec, "_detach", lambda argv: None)
    monkeypatch.setattr(sec.ctypes, "memmove", lambda *a: None)
    log = []
    u, k = _fake_win(log, lock=0)  # GlobalLock 失败
    with pytest.raises(CliError):
        sec._win_write("pw", 45, u=u, k=k)
    assert ("CloseClipboard", ()) in log
    log.clear()
    u, k = _fake_win(log, set_data=0)  # SetClipboardData 失败
    with pytest.raises(CliError):
        sec._win_write("pw", 45, u=u, k=k)
    log.clear()
    u, k = _fake_win(log)
    sec._win_write("pw", 45, u=u, k=k)
    opened = [a for n, a in log if n == "OpenClipboard"]
    assert opened and opened[0][0] == 0x1234  # 有所有者窗口，不是 None
    assert k.GlobalLock.argtypes and k.GlobalLock.restype is not None  # 64 位句柄不被截断


# ---------------------------------------------------------------- 不用系统钥匙串（用户 11-23：「这个也不要」，和模型 Key 一样存文件）


def test_default_backend_is_file_on_every_platform(monkeypatch):
    monkeypatch.delenv("HIRO_SECRET_BACKEND", raising=False)
    for plat in ("darwin", "win32", "linux"):
        monkeypatch.setattr(sec.sys, "platform", plat)
        assert isinstance(sec.backend(), sec.FileBackend)


def test_file_backend_keeps_values_in_0600_file_apart_from_name_list(run_cli, stdin, monkeypatch, hiro_home):
    import os
    import stat

    monkeypatch.setenv("HIRO_SECRET_BACKEND", "file")
    stdin(VALUE)
    run_cli("secret", "set", "game-password")
    store = hiro_home / "game-secrets.json"
    assert json.loads(store.read_text(encoding="utf-8")) == {"game-password": VALUE}
    if os.name != "nt":
        assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert VALUE not in (hiro_home / "secrets.json").read_text(encoding="utf-8")  # 名字清单里只有名字
    assert sec.backend().get("game-password") == VALUE
    run_cli("secret", "delete", "game-password")
    assert json.loads(store.read_text(encoding="utf-8")) == {}


def test_corrupt_store_says_how_to_recover_and_keeps_file(run_cli, stdin, monkeypatch, hiro_home):
    """CodeRabbit #89：文件坏了时提示先改名备份再重填；不悄悄覆盖（里面可能还有能救回来的值）。"""
    monkeypatch.setenv("HIRO_SECRET_BACKEND", "file")
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "game-secrets.json").write_text('{"game-account": "abc', encoding="utf-8")
    stdin(VALUE)
    _code, env = run_cli("secret", "set", "game-password")
    assert env["ok"] is False and env["error"]["code"] == "secret_store"
    assert "game-secrets.json" in env["error"]["hint"] and "备份" in env["error"]["hint"]
    assert (hiro_home / "game-secrets.json").read_text(encoding="utf-8") == '{"game-account": "abc'


def test_concurrent_writes_keep_every_value(monkeypatch, hiro_home):
    """CodeRabbit #89：几个进程同时存，读改写要串行，谁的值都不丢。"""
    import subprocess
    import sys

    monkeypatch.setenv("HIRO_SECRET_BACKEND", "file")
    hiro_home.mkdir(parents=True, exist_ok=True)
    code = "import sys; from hiro.cli import secrets as s; [s.backend().set(f'k{sys.argv[1]}-{i}', 'v') for i in range(60)]"
    procs = [subprocess.Popen([sys.executable, "-c", code, str(n)]) for n in range(8)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    data = json.loads((hiro_home / "game-secrets.json").read_text(encoding="utf-8"))
    assert len(data) == 480
