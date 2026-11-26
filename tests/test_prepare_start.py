"""开局前准备（hiro.runtime.prepare_start，G0 / D4）：同一个开局任务里先挑空白编成位、写编成，再 exec 开局。"""

import json

from hiro.runtime import prepare_start as ps

import pytest

KOTONE = "i_card-fktn-3-000"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HIRO_HOME", str(h))
    monkeypatch.setenv("HIRO_FORMATION_PLAN", str(h / "formation-plan.json"))
    return h


def _plan(home, sets=(None, None), written=False):
    from hiro.brain import formation_plan as fp

    plan = {"version": fp.PLAN_VERSION, "created": "2026-11-26 10:00:00", "idol": {"idol_card_id": KOTONE, "name": "琴音"},
            "support": {"set": sets[0]}, "memory": {"set": sets[1]}}  # fmt: skip
    if written:
        plan["written"] = {"hajime": {"what": ["memory", "support"]}}
    fp.save_plan(home, plan, "hajime")


@pytest.fixture
def calls(monkeypatch):
    log = []

    def survey(mode):
        log.append(("survey", mode))
        sets = [{"kind": "support", "set": n, "filled": 5 if n < 3 else 0, "cards": []} for n in range(1, 21)]
        sets += [{"kind": "memory", "set": n, "filled": 4 if n < 7 else 0, "cards": []} for n in range(1, 21)]
        return {"scanned_at": "2026-11-26 03:10:00", "mode": mode, "complete": True, "sets": sets}

    def write(mode, what=("support", "memory"), blank_only=()):
        log.append(("write", mode))
        log.append(("write_what", ",".join(what)))
        log.append(("blank_only", ",".join(blank_only)))
        from hiro.brain import formation_plan as fp

        fp.mark_written(ps.plan_path(), mode, tuple(what))
        return 0

    monkeypatch.setattr(ps, "_survey", survey)
    monkeypatch.setattr(ps, "_write", write)
    monkeypatch.setattr(ps, "_exec", lambda argv: log.append(("exec", argv)) or 0)
    monkeypatch.setattr(ps, "_check_memory_freshness", lambda plan_path, home: None)
    return log


def test_no_sets_surveys_picks_blank_writes_then_starts(home, calls, tmp_path):
    _plan(home)
    out = tmp_path / "prepare.json"
    rc = ps.main(["--mode", "hajime", "--out", str(out), "--", "python", "-m", "hiro.runtime.hajime", "new"])
    assert rc == 0
    assert [c[0] for c in calls if c[0] not in ("write_what", "blank_only")] == ["survey", "write", "exec"]
    plan = json.loads((home / "formation-plan.json").read_text(encoding="utf-8"))
    assert plan["support"]["set"] == 3 and plan["memory"]["set"] == 7
    from hiro.cli.store import open_store

    meta, rows = open_store().latest("formations")
    assert meta is not None and any(r["mode"] == "hajime" and r["set"] == 3 and r["filled"] == 0 for r in rows)
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["picked"] == {"support": 3, "memory": 7} and rec["wrote"] is True


def test_sets_known_not_written_only_writes(home, calls):
    _plan(home, sets=(4, 5))
    assert ps.main(["--mode", "hajime", "--", "x"]) == 0
    assert [c[0] for c in calls if c[0] not in ("write_what", "blank_only")] == ["write", "exec"]
    # #134 review：4 / 5 号不是初星助手写过的，写之前要求全空（玩家扫描后自己填了就不覆盖）
    assert ("blank_only", "support,memory") in calls


def test_slot_written_by_us_needs_no_blank_guard(home, calls):
    """初星助手以前写过的编成位（别的方案 written 记着同一个号）可以直接覆盖，不加全空的闸。"""
    import hiro.cli.cmd_run as cr

    _plan(home, sets=(4, 5))
    cr_slot = cr.slot_written_by_us
    try:
        cr.slot_written_by_us = lambda h, kind, what, n: what == "support" and n == 4
        assert ps.main(["--mode", "hajime", "--", "x"]) == 0
    finally:
        cr.slot_written_by_us = cr_slot
    assert ("blank_only", "memory") in calls


def test_nothing_to_do_just_starts(home, calls):
    _plan(home, sets=(4, 5), written=True)
    assert ps.main(["--mode", "hajime", "--", "x"]) == 0
    assert [c[0] for c in calls] == ["exec"]


def test_all_full_stops_without_writing(home, calls, monkeypatch, capsys):
    _plan(home)
    monkeypatch.setattr(ps, "_survey", lambda mode: {"scanned_at": "x", "mode": mode, "complete": True,
                        "sets": [{"kind": k, "set": n, "filled": 3, "cards": []} for k in ("support", "memory") for n in range(1, 21)]})  # fmt: skip
    rc = ps.main(["--mode", "hajime", "--", "x"])
    assert rc != 0 and not any(c[0] in ("write", "exec") for c in calls)
    assert "停下：" in capsys.readouterr().err


def test_write_failure_does_not_start(home, calls, monkeypatch):
    _plan(home, sets=(4, 5))
    monkeypatch.setattr(ps, "_write", lambda mode, what=(), blank_only=(): 3)
    assert ps.main(["--mode", "hajime", "--", "x"]) == 3
    assert not any(c[0] == "exec" for c in calls)


def test_only_what_is_prepared(home, calls):
    """CodeRabbit #95：--what memory 只挑 / 只写回忆；支援卡没定号也不管（开局命令里指定了）。"""
    _plan(home)
    assert ps.main(["--mode", "hajime", "--what", "memory", "--", "x"]) == 0
    plan = json.loads((home / "formation-plan.json").read_text(encoding="utf-8"))
    assert plan["support"]["set"] is None and plan["memory"]["set"] == 7
    assert ("write_what", "memory") in calls


def test_stale_memory_inventory_stops_before_writing(home, calls, monkeypatch, capsys):
    from hiro.cli.registry import CliError

    def stale(plan_path, home_):
        raise CliError("inventory_stale", "回忆库存扫描之后又打过培育，位置变了", "重扫回忆")

    monkeypatch.setattr(ps, "_check_memory_freshness", stale)
    _plan(home, sets=(4, 5))
    assert ps.main(["--mode", "hajime", "--", "x"]) != 0
    assert not any(c[0] in ("write", "exec") for c in calls)
    assert "回忆库存扫描之后又打过培育" in capsys.readouterr().err
