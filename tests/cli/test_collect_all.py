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


def test_runs_five_steps_in_order_and_imports_each(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_MEMORY_SLICES", "1")  # 开了「回忆按片扫描」：回忆这一步 12 片全读
    calls, imported, runner, importer = _fake()
    assert collect_all.run(tmp_path, None, runner=runner, importer=importer) == 0
    # 编成在回忆前面（终审 I4）：第一次全收集时回忆扫描要靠编成快照挑空编成，才读得到回忆本身的属性
    assert imported == ["account", "idols", "supports", "formations", "memories"]
    mem = calls[4]
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
    pend = env["data"]["pending"]
    assert code == 3 and pend["args"]["only"] == "idols,memories"
    assert pend["summary"] == "一键全收集：偶像卡 → 回忆（12 片全读）（只读，不改编成）"  # N39：标题只列中文步骤


def test_memory_step_is_whole_list_scan_when_slices_off(tmp_path, monkeypatch):
    """开关关着：回忆这一步照旧整表滚动扫（#116 审查重要 5；D10 起缺省开，这里显式关）。"""
    monkeypatch.setenv("HIRO_MEMORY_SLICES", "0")
    calls, _imported, runner, importer = _fake()
    collect_all.run(tmp_path, ["memories"], runner=runner, importer=importer)
    assert "--slices" not in calls[0] and "hiro.memory_scan" in calls[0]


def test_failed_step_still_imports_partial_output(tmp_path):
    """#116 第二轮审查次要：回忆这一步中途出错（退出码非 0），已经逐片写好的结果也要入库（设计 I2），再停下。"""
    imported = []

    def runner(argv, env=None):
        out = argv[argv.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"slices": [{"key": "sense:hif"}]}, f)
        return R(3)

    def importer(kind, inv, source):
        imported.append((kind, inv))
        return {"count": 1}

    assert collect_all.run(tmp_path, ["memories"], runner=runner, importer=importer) == 3
    assert imported == [("memories", {"slices": [{"key": "sense:hif"}]})]


def test_import_failure_fails_the_job(tmp_path, capsys):
    """#116 第三轮：导入抛错不能吞掉、最后还报完成：任务以非 0 结束，写明哪一步没入库。"""
    _calls, _imported, runner, _ = _fake()

    def bad_importer(kind, inv, source):
        raise ValueError("坏数据")

    assert collect_all.run(tmp_path, ["idols"], runner=runner, importer=bad_importer) != 0
    out = capsys.readouterr().out
    assert "没能入库" in out and "完成" not in out.split("没能入库")[-1]


def test_partial_whole_list_scan_not_imported(tmp_path):
    """#116 第三轮：按片关着（缺省）时回忆是整表扫描，出错时的半截结果不能存成最新快照；只有带 slices 的（按片逐片落盘）才导入。"""
    imported = []

    def runner(argv, env=None):
        out = argv[argv.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"entries": [{"x": 1}], "complete": False}, f)
        return R(3)

    collect_all.run(
        tmp_path, ["memories"], runner=runner, importer=lambda kind, inv, source: imported.append(inv) or {"count": 1}
    )
    assert imported == []


def test_summary_lists_steps_in_chinese_not_only_param():
    """N39（G2 11-26）：标题不露出 --only 的英文参数，只列要做的几步。"""
    from hiro.cli.jobs import KINDS

    full = KINDS["collect-all"].summary({})
    assert full == "一键全收集：首页数值 → 偶像卡 → 支援卡 → 编成 → 回忆（12 片全读）（只读，不改编成）"
    assert KINDS["collect-all"].summary({"only": "account,idols,supports,formations,memories"}) == full
    part = KINDS["collect-all"].summary({"only": "memories,idols"})
    assert part == "一键全收集：偶像卡 → 回忆（12 片全读）（只读，不改编成）" and "memories" not in part
