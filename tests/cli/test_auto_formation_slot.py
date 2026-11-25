"""一句话开局不再卡在「编成位填几号」（G0，11-26 真窗口实测：助手反问玩家支援卡 / 回忆编成用第几号）。

开关 HIRO_AUTO_FORMATION（D4）：开着时，方案没定编成号或还没写进游戏，开局不再报 plan_no_set / plan_not_written 让人去问、去写，
而是在同一个开局任务里先做准备：方案没定编成号就挑这个模式里空着的编成位（读过编成位的现在就挑；没读过的进游戏先读），
再把方案写进去（只写空白位，不动玩家已有的编成），然后开局。编成位都满了才停下问。"""

import json

import pytest

KOTONE, KOTONE_SKIN = "i_card-fktn-3-000", "i_card-skin-fktn-3-000"


@pytest.fixture(autouse=True)
def _algo_ok(monkeypatch):
    from hiro.cli import cmd_run

    monkeypatch.setattr(cmd_run, "exam_algo_problem", lambda: None)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("HIRO_AUTO_FORMATION", "1")


def _plan(home, sets=(None, None), written=False):
    from hiro.brain import formation_plan as fp

    plan = {
        "version": fp.PLAN_VERSION,
        "created": "2026-11-26 10:00:00",
        "idol": {"idol_card_id": KOTONE, "name": "世界一可愛い私"},
        "support": {"set": sets[0], "own": [{"support_card_id": "s_card-3-0001"}]},
        "memory": {"set": sets[1], "cards": ["p_card-01-act-2_001"]},
    }
    if written:
        plan["written"] = {"hajime": {"what": ["memory", "support"]}}
    home.mkdir(parents=True, exist_ok=True)
    fp.save_plan(home, plan, "hajime")


def _formations(mode, support, memory):
    """support / memory：{编成号: 有几格}。"""
    from hiro.cli.store import open_store

    rows = [{"mode": mode, "kind": "support", "set": n, "filled": f, "cards": []} for n, f in support.items()]
    rows += [{"mode": mode, "kind": "memory", "set": n, "filled": f, "cards": []} for n, f in memory.items()]
    open_store().save_snapshot("formations", rows, "test")


def test_off_by_default_still_asks(run_cli, hiro_home):
    _plan(hiro_home)
    _formations("hajime", {1: 5, 2: 0}, {1: 4, 2: 0})
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "plan_no_set"


def test_picks_blank_slots_from_last_scan_and_prepares_in_same_job(run_cli, hiro_home, on):
    _plan(hiro_home)
    _formations("hajime", {1: 5, 2: 0, 3: 0}, {1: 4, 2: 4, 3: 0})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3, env
    pend = env["data"]["pending"]
    assert pend["args"]["prepare"] is True
    assert "支援卡编成 2 号" in pend["summary"] and "回忆编成 3 号" in pend["summary"] and "空着" in pend["summary"]
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["support"]["set"] == 2 and per["memory"]["set"] == 3


def test_written_plan_needs_no_prepare(run_cli, hiro_home, on):
    _plan(hiro_home, sets=(4, 5), written=True)
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and not env["data"]["pending"]["args"].get("prepare")


def test_sets_known_but_not_written_prepares_write_only(run_cli, hiro_home, on):
    _plan(hiro_home, sets=(4, 5))
    _formations("hajime", {4: 0}, {5: 0})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and env["data"]["pending"]["args"]["prepare"] is True
    assert "写进" in env["data"]["pending"]["summary"]


def test_never_scanned_prepares_with_scan_in_job(run_cli, hiro_home, on):
    _plan(hiro_home)
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and env["data"]["pending"]["args"]["prepare"] is True
    assert "先读" in env["data"]["pending"]["summary"]


def test_all_full_asks_which_to_overwrite(run_cli, hiro_home, on):
    _plan(hiro_home)
    _formations("hajime", dict.fromkeys(range(1, 21), 5), dict.fromkeys(range(1, 21), 4))
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "formations_full"
    assert "满" in env["error"]["message"] and "--support-set" in env["error"]["hint"]


def test_prepare_wraps_produce_job_argv(tmp_path):
    from hiro.cli import jobs

    argv = jobs.KINDS["hajime"].argv({"idol": KOTONE, "prepare": True}, tmp_path)
    i = argv.index("hiro.runtime.prepare_start")
    assert argv[i + 1 : i + 3] == ["--mode", "hajime"] and "--" in argv and "hiro.runtime.hajime" in " ".join(argv)
    plain = jobs.KINDS["hajime"].argv({"idol": KOTONE}, tmp_path)
    assert "hiro.runtime.prepare_start" not in plain


def _job(home, kind, params, started_at):
    from hiro.cli.jobs import jobs_dir

    d = jobs_dir(home) / f"j-{kind}-{started_at}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(
        json.dumps({"kind": kind, "params": params, "started_at": started_at}), encoding="utf-8"
    )


def test_prepare_job_invalidates_old_scan(run_cli, hiro_home, on):
    """CodeRabbit #95：自动准备的开局任务也会写编成；它开始之后，读编成位前的现状不能再拿来挑空位（两个方案会挑到同一个号）。"""
    import time

    _plan(hiro_home)
    _formations("hajime", {1: 5, 2: 0}, {1: 4, 2: 0})
    _job(hiro_home, "hajime", {"prepare": True, "idol": "i_card-ttmr-3-000"}, time.time() + 5)
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and "先读" in env["data"]["pending"]["summary"], env
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["support"]["set"] is None  # 没拿旧现状挑号，留给任务里重新读


def test_only_unspecified_kind_is_prepared(run_cli, hiro_home, on):
    """CodeRabbit #95：开局给了 --support-set 的，只准备回忆；支援卡位都满了也不影响。"""
    _plan(hiro_home)
    _formations("hajime", dict.fromkeys(range(1, 21), 5), {1: 4, 2: 0})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--support-set", "4")
    assert code == 3, env
    args = env["data"]["pending"]["args"]
    assert args["prepare"] is True and args["prepare_what"] == "memory"
    assert (
        "回忆编成 2 号" in env["data"]["pending"]["summary"] and "支援卡编成" not in env["data"]["pending"]["summary"]
    )


def test_stale_memory_inventory_stops_before_confirm(run_cli, hiro_home, on, monkeypatch):
    """CodeRabbit #95：要写回忆而回忆库存扫描后又打过培育（位置变了），出确认卡之前就说清楚，不进游戏。"""
    from hiro.cli import cmd_run
    from hiro.cli.registry import CliError

    def stale(plan_path, home):
        raise CliError("inventory_stale", "回忆库存是旧的", "重扫回忆")

    monkeypatch.setattr(cmd_run, "_check_memory_freshness", stale)
    _plan(hiro_home)
    _formations("hajime", {2: 0}, {3: 0})
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "inventory_stale"


def test_prepare_what_reaches_job_argv(tmp_path):
    from hiro.cli import jobs

    argv = jobs.KINDS["hajime"].argv({"idol": KOTONE, "prepare": True, "prepare_what": "memory"}, tmp_path)
    i = argv.index("hiro.runtime.prepare_start")
    assert "--what" in argv[i:] and argv[argv.index("--what") + 1] == "memory"


def _other_plan_written(home, sets):
    """别的偶像的方案，初星助手写进过这两个编成位。"""
    from hiro.brain import formation_plan as fp

    plan = {"version": fp.PLAN_VERSION, "created": "2026-11-25 10:00:00", "idol": {"idol_card_id": "i_card-ttmr-3-000", "name": "Luna"},
            "support": {"set": sets[0]}, "memory": {"set": sets[1]},
            "written": {"hajime": {"what": ["memory", "support"], "support_set": sets[0], "memory_set": sets[1]}}}  # fmt: skip
    path = fp.plan_key_path(home, "i_card-ttmr-3-000", "hajime")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def test_known_sets_filled_by_player_fall_back_to_asking(run_cli, hiro_home, on):
    """CodeRabbit #95：沿用的编成号在游戏里有东西、又不是初星助手写的：不自动覆盖，照旧让玩家确认写编成。"""
    _plan(hiro_home, sets=(4, 5))
    _formations("hajime", {4: 5}, {5: 4})
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "plan_not_written"


def test_known_sets_written_by_us_are_reused(run_cli, hiro_home, on):
    _plan(hiro_home, sets=(4, 5))
    _other_plan_written(hiro_home, (4, 5))
    _formations("hajime", {4: 5}, {5: 4})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and env["data"]["pending"]["args"]["prepare"] is True


def test_support_only_left_does_not_check_memory_freshness(run_cli, hiro_home, on, monkeypatch):
    """CodeRabbit #95：只剩支援卡没写时，回忆库存过期不该挡开局。"""
    from hiro.brain import formation_plan as fp
    from hiro.cli import cmd_run
    from hiro.cli.registry import CliError

    def stale(plan_path, home):
        raise CliError("inventory_stale", "回忆库存是旧的", "重扫回忆")

    monkeypatch.setattr(cmd_run, "_check_memory_freshness", stale)
    _plan(hiro_home, sets=(4, 5))
    fp.mark_written(hiro_home / "formation-plan.json", "hajime", ("memory",))
    _formations("hajime", {4: 0}, {5: 4})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and env["data"]["pending"]["args"]["prepare"] is True, env
