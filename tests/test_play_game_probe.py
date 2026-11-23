"""tools/play_game.py：读屏（设置检查 / 读选拔回忆）在短命子进程里做，包装进程不常驻识别层的内存（Windows 第十九局）。"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import play_game  # noqa: E402


class _Out:
    def __init__(self, stdout, code=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, code, stderr


def test_probe_parses_last_json_line(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _Out('KAA 日志…\n{"memory": [1710, 2268, 913]}\n')

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert play_game._probe("memory") == {"memory": [1710, 2268, 913]}
    assert seen["cmd"][-2:] == ["--probe", "memory"]


def test_probe_reports_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _Out("", 1, "Traceback… boom"))
    assert "boom" in play_game._probe("settings")["error"]


def test_wrapper_does_not_import_recognition():
    """包装进程 main 的顶层只用标准库 + hiro.loop.pause_flag；读屏函数在 --probe 子进程里才导入识别层。"""
    src = Path(play_game.__file__).read_text(encoding="utf-8")
    body = src[src.index("def main(") :]
    assert (
        "read_memory()" not in body.split("def _probe_main")[0]
        if "def _probe_main" in body
        else "read_memory()" not in body
    )
    assert "from check_settings import" not in body
    assert (
        "\n    from hiro" not in body and "\n    import hiro" not in body
    )  # pause_flag 也在子进程里等（第二十局包装进程 ~500 MB）
