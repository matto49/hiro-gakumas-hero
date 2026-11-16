import pytest


@pytest.fixture(autouse=True)
def hiro_home(tmp_path, monkeypatch):
    home = tmp_path / "hiro-home"
    monkeypatch.setenv("HIRO_HOME", str(home))
    monkeypatch.setenv("HIRO_SECRET_BACKEND", "memory")
    return home


@pytest.fixture
def run_cli(capsys):
    """跑一条 hiro 命令，返回 (退出码, 解析后的 JSON 信封)。"""
    import json

    from hiro.cli.main import main

    def _run(*argv, stdin=None, monkeypatch=None):
        code = main(list(argv))
        out = capsys.readouterr().out
        return code, json.loads(out)

    return _run
