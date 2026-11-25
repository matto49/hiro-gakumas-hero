"""待确认带上操作类别（access），初星助手按用户选的权限档决定要不要出确认卡（11-25 review：参考 dsh 的权限范围）。

read 只看画面；daily 领日常（用户说不用确认）；write 改游戏但不覆盖已有东西（写空白编成位等）；
overwrite 会覆盖已有编成；all 用掉培育次数 / 资源的（开局、续打、初星課題）。
"""

import json

from hiro.cli.cmd_run import run_access

import pytest


@pytest.mark.parametrize(
    ("args", "want"),
    [
        ({"kind": "scan-memories"}, "read"),
        ({"kind": "check-settings"}, "read"),
        ({"kind": "selftest"}, "read"),
        ({"kind": "daily"}, "daily"),
        ({"kind": "hajime", "idol": "i_card-x"}, "all"),
        ({"kind": "hif", "command": "resume"}, "all"),
        ({"kind": "main-task"}, "all"),
        ({"kind": "write-formation", "dry_run": True}, "read"),
        ({"kind": "contest-unit", "dry_run": True}, "read"),
        ({"kind": "contest-unit"}, "write"),
        ({"kind": "story"}, "write"),
    ],
)
def test_access_by_kind(args, want):
    assert run_access(args) == want


def _plan(home, support=2, memory=1):
    home.mkdir(parents=True, exist_ok=True)
    (home / "formation-plan.json").write_text(
        json.dumps({"support": {"set": support}, "memory": {"set": memory}}), encoding="utf-8"
    )


def _formations(mode, filled_support, filled_memory):
    from hiro.cli.store import open_store

    rows = [
        {"mode": mode, "kind": "support", "set": 2, "filled": filled_support, "cards": []},
        {"mode": mode, "kind": "memory", "set": 1, "filled": filled_memory, "cards": []},
    ]
    open_store().save_snapshot("formations", rows, "test")


def test_write_formation_blank_slot_is_write(hiro_home):
    _plan(hiro_home)
    _formations("hajime", 0, 0)
    assert run_access({"kind": "write-formation", "scenario": "hajime"}) == "blank"


def test_write_formation_over_existing_is_overwrite(hiro_home):
    _plan(hiro_home)
    _formations("hajime", 5, 0)
    assert run_access({"kind": "write-formation", "scenario": "hajime"}) == "overwrite"
    # 只写回忆、回忆位是空的：不算覆盖
    assert run_access({"kind": "write-formation", "scenario": "hajime", "what": "memory"}) == "blank"


def test_write_formation_unknown_slots_is_overwrite(hiro_home):
    _plan(hiro_home)
    assert run_access({"kind": "write-formation", "scenario": "hajime"}) == "overwrite"  # 没读过编成位：按覆盖算
    _formations("nia", 0, 0)
    assert run_access({"kind": "write-formation", "scenario": "hajime"}) == "overwrite"  # 读的是别的模式


def test_pending_carries_access(run_cli):
    _code, env = run_cli("run", "start", "scan-memories")
    assert env["data"]["pending"]["access"] == "read"


def test_write_after_scan_makes_slots_stale(hiro_home):
    """CodeRabbit #79：写过编成后，之前读的「空」不能再信（不然下一次写会被当成空白位、不确认就覆盖）。"""
    from hiro.cli.jobs import jobs_dir
    from hiro.cli.store import open_store

    _plan(hiro_home)
    rows = [
        {"mode": "hajime", "kind": "support", "set": 2, "filled": 0, "cards": []},
        {"mode": "hajime", "kind": "memory", "set": 1, "filled": 0, "cards": []},
    ]
    open_store().save_snapshot("formations", rows, "test", scanned_at="2026-11-25 10:00:00")
    assert run_access({"kind": "write-formation", "scenario": "hajime"}) == "blank"
    d = jobs_dir(hiro_home) / "20261125-110000-abcd"
    d.mkdir(parents=True)
    import time

    started = time.mktime(time.strptime("2026-11-25 11:00:00", "%Y-%m-%d %H:%M:%S"))
    (d / "job.json").write_text(
        json.dumps({"kind": "write-formation", "params": {"scenario": "hajime"}, "started_at": started}),
        encoding="utf-8",
    )
    assert run_access({"kind": "write-formation", "scenario": "hajime"}) == "overwrite"
    # 只对照（dry-run）不算写过
    (d / "job.json").write_text(
        json.dumps(
            {"kind": "write-formation", "params": {"scenario": "hajime", "dry_run": True}, "started_at": started}
        ),
        encoding="utf-8",
    )
    assert run_access({"kind": "write-formation", "scenario": "hajime"}) == "blank"
