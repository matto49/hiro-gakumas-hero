import shutil
from pathlib import Path

from hiro.cli.jobs import KINDS, Job

DATA = Path(__file__).parent / "data"


def test_scan_job_output_lands_in_the_store(run_cli, hiro_home):
    from hiro.cli.scan_ingest import ingest

    job = Job(hiro_home, KINDS["scan-supports"], {})
    shutil.copy(DATA / "support-inventory.json", job.dir / "out.json")
    job.state = "done"
    ingest(job)
    _, env = run_cli("info", "supports")
    assert env["data"]["total"] == 4 and env["data"]["source"] == f"scan:{job.id}"
    ev = [e for e in job.events() if e["type"] == "imported"]
    assert ev and "4" in ev[0]["text"]


def test_failed_or_missing_output_is_reported_not_imported(run_cli, hiro_home):
    from hiro.cli.scan_ingest import ingest

    job = Job(hiro_home, KINDS["scan-memories"], {})
    job.state = "done"
    ingest(job)  # 没有 out.json
    _, env = run_cli("info", "memories")
    assert env["data"]["scanned_at"] is None
    assert any(e["type"] == "import_failed" for e in job.events())


def test_import_event_comes_before_done(hiro_home):
    import sys
    import time

    from hiro.cli.jobs import Kind

    src = str(DATA / "support-inventory.json")
    kind = Kind(
        "scan-test",
        "测试",
        lambda p, d: [sys.executable, "-c", f"import shutil; shutil.copy({src!r}, {str(d / 'out.json')!r})"],
        lambda p: "测试扫描",
        output="supports",
    )
    from hiro.cli.service import Manager

    mgr = Manager(hiro_home)
    job = Job(hiro_home, kind, {}, on_exit=mgr._exited)
    job.start()
    t0 = time.time()
    while job.state == "running" and time.time() - t0 < 10:
        time.sleep(0.05)
    types = [e["type"] for e in job.events()]
    assert types.index("imported") < types.index("done"), types


def test_scan_memories_runs_incremental_slices_when_enabled(hiro_home, monkeypatch):
    """设计 5.8：开了「回忆按片扫描」时「扫描回忆」= 12 片都增量补扫；params full=True 时全读（一键全收集用）。"""
    from hiro.cli.jobs import KINDS

    monkeypatch.setenv("HIRO_MEMORY_SLICES", "1")

    argv = KINDS["scan-memories"].argv({}, hiro_home)
    assert "--slices" in argv and argv[argv.index("--slices") + 1] == "all" and "--incremental" in argv
    full = KINDS["scan-memories"].argv({"full": True}, hiro_home)
    assert "--incremental" not in full and "--slices" in full


def test_scan_memories_default_is_old_whole_list_scan(hiro_home, monkeypatch):
    """#116 审查重要 5：「回忆按片扫描」关着时 scan-memories 照旧整表滚动扫描（不开筛选弹窗、不改排序）；开着才按片。
    D10（用户 11-26 18:14 定）起缺省开。"""
    from hiro.cli.jobs import KINDS

    monkeypatch.setenv("HIRO_MEMORY_SLICES", "0")
    argv = KINDS["scan-memories"].argv({}, hiro_home)
    assert "--slices" not in argv and "--incremental" not in argv and "--from-home" in argv
    monkeypatch.delenv("HIRO_MEMORY_SLICES", raising=False)
    assert "--slices" in KINDS["scan-memories"].argv({}, hiro_home)  # 缺省开
