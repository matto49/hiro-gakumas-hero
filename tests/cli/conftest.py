import pytest


@pytest.fixture(autouse=True)
def hiro_home(tmp_path, monkeypatch):
    home = tmp_path / "hiro-home"
    monkeypatch.setenv("HIRO_HOME", str(home))
    monkeypatch.setenv("HIRO_SECRET_BACKEND", "memory")
    for k in ("HIRO_KAA_GAME_DATA", "HIRO_USER_ASSETS", "HIRO_FORMATION_PLAN"):  # 命令会往进程环境里补这些：测试后还原
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
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


@pytest.fixture
def auto_formation_off(monkeypatch):
    """D7（11-26 用户定）开局前自动挑编成位、写编成缺省打开；测「没定编成号就报错让玩家选」的老路径时关掉。"""
    monkeypatch.setenv("HIRO_AUTO_FORMATION", "0")
