"""开局前准备的任务：写编成、检查游戏设置、调 DMM 窗口。"""

import sys  # noqa: F401

from hiro.cli.jobs import KINDS


def test_write_formation_argv_and_flags(hiro_home, tmp_path):
    k = KINDS["write-formation"]
    argv = k.argv({"scenario": "hajime", "dry_run": True, "what": "memory"}, tmp_path)
    assert argv[1].endswith("tools/write_formation.py")
    assert argv[2:] == ["--mode", "hajime", "--what", "memory", "--dry-run"]
    assert k.argv({}, tmp_path)[2:] == ["--mode", "hif"]
    assert k.pausable is False
    assert "不改" in k.summary({"dry_run": True}) and "写进游戏" in k.summary({})


def test_run_start_write_formation_is_pending(run_cli, hiro_home):
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "formation-plan.json").write_text("{}", encoding="utf-8")
    code, env = run_cli("run", "start", "write-formation", "--scenario", "nia", "--dry-run")
    assert code == 3 and "不改" in env["data"]["pending"]["summary"]


def test_write_formation_without_plan_is_refused_before_confirm(run_cli):
    _code, env = run_cli("run", "start", "write-formation")
    assert env["ok"] is False and env["error"]["code"] == "no_plan"


def test_check_settings_writes_json_for_ingest(tmp_path):
    k = KINDS["check-settings"]
    argv = k.argv({}, tmp_path)
    assert argv[1].endswith("tools/check_settings.py") and argv[2:] == ["--json", str(tmp_path / "out.json")]
    assert k.output == "game_settings" and k.pausable is False


def test_fit_window_only_on_windows(run_cli, monkeypatch):
    import hiro.cli.jobs as jobs

    k = KINDS["fit-window"]
    assert k.argv({}, None)[1:] == ["-m", "hiro.devices.win_window", "fit"]
    monkeypatch.setattr(jobs.sys, "platform", "darwin")
    _code, env = run_cli("run", "start", "fit-window")
    assert env["ok"] is False and env["error"]["code"] == "windows_only"


def test_scan_account_job_and_ingest(run_cli, tmp_path):
    import json

    k = KINDS["scan-account"]
    from hiro.cli.jobs import HOME_READER

    assert HOME_READER.endswith("home_values")
    assert k.argv({}, tmp_path)[1:] == ["-m", HOME_READER, "--live", "--out", str(tmp_path / "out.json")]
    assert k.output == "account" and k.pausable is False
    p = tmp_path / "a.json"
    p.write_text(
        json.dumps({"scanned_at": "2026-11-17 18:00:00", "plv": 65, "ap": 85, "ap_max": 100, "jewel": 15220}),
        encoding="utf-8",
    )
    run_cli("info", "import", "--kind", "account", str(p))
    _, env = run_cli("info", "summary")
    assert env["data"]["account"] == {"plv": 65, "ap": 85, "ap_max": 100, "jewel": 15220}
    assert env["data"]["kinds"]["account"]["scanned_at"] == "2026-11-17 18:00:00"
