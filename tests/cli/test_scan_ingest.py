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


def test_scan_memories_runs_incremental_slices_by_default(hiro_home):
    """设计 5.8：「补扫新回忆」= 12 片都增量补扫；params full=True 时全读（一键全收集用）。"""
    from hiro.cli.jobs import KINDS

    argv = KINDS["scan-memories"].argv({}, hiro_home)
    assert "--slices" in argv and argv[argv.index("--slices") + 1] == "all" and "--incremental" in argv
    full = KINDS["scan-memories"].argv({"full": True}, hiro_home)
    assert "--incremental" not in full and "--slices" in full
