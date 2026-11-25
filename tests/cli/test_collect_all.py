"""一键全收集（设计 5.7 / D7 / D12）：首页数值 → 偶像卡 → 支援卡 → 回忆（12 片全读）→ 编成，每步跑完就入库，出错停下写明第几步。"""

import json

from hiro import collect_all
from hiro.cli.cmd_run import run_access
from hiro.cli.jobs import KINDS


class R:
    def __init__(self, code):
        self.returncode = code


def _fake(fail_at=None):
    calls, imported = [], []

    def runner(argv, env=None):
        calls.append(argv)
        out = argv[argv.index("--out") + 1] if "--out" in argv else None
        if len(calls) == fail_at:
            return R(2)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"step": len(calls)}, f)
        return R(0)

    def importer(kind, inv, source):
        imported.append(kind)
        return {"count": 1}

    return calls, imported, runner, importer


def test_runs_five_steps_in_order_and_imports_each(tmp_path):
    calls, imported, runner, importer = _fake()
    assert collect_all.run(tmp_path, None, runner=runner, importer=importer) == 0
    assert imported == ["account", "idols", "supports", "memories", "formations"]
    mem = calls[3]
    assert "--slices" in mem and "all" in mem and "--incremental" not in mem  # 回忆 12 片全读


def test_failed_step_stops_and_keeps_earlier_imports(tmp_path, capsys):
    calls, imported, runner, importer = _fake(fail_at=3)
    assert collect_all.run(tmp_path, None, runner=runner, importer=importer) != 0
    assert imported == ["account", "idols"] and len(calls) == 3
    assert "第 3 步（支援卡）" in capsys.readouterr().out


def test_only_runs_listed_steps(tmp_path):
    _calls, imported, runner, importer = _fake()
    collect_all.run(tmp_path, ["memories", "idols"], runner=runner, importer=importer)
    assert imported == ["idols", "memories"]


def test_collect_all_kind_is_read_only_job(tmp_path):
    k = KINDS["collect-all"]
    assert run_access({"kind": "collect-all"}) == "read"
    argv = k.argv({"only": "idols,memories"}, tmp_path)
    assert argv[-2:] == ["--only", "idols,memories"] and "一键全收集" in k.summary({})


def test_run_start_collect_all_passes_only(run_cli, hiro_home):
    code, env = run_cli("run", "start", "collect-all", "--only", "idols,memories")
    assert code == 3 and "只做 idols,memories" in env["data"]["pending"]["summary"]
