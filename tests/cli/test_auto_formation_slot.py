"""一句话开局不再卡在「编成位填几号」（G0，11-26 真窗口实测：助手反问玩家支援卡 / 回忆编成用第几号）。

开关 HIRO_AUTO_FORMATION（D4；D7 用户定缺省打开，=0 关）：开着时，方案没定编成号或还没写进游戏，开局不再报 plan_no_set / plan_not_written 让人去问、去写，
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


def test_switched_off_still_asks(run_cli, hiro_home, monkeypatch):
    """HIRO_AUTO_FORMATION=0：回到报 plan_no_set、让玩家定编成号。"""
    monkeypatch.setenv("HIRO_AUTO_FORMATION", "0")
    _plan(hiro_home)
    _formations("hajime", {1: 5, 2: 0}, {1: 4, 2: 0})
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "plan_no_set"


def test_on_by_default(run_cli, hiro_home, monkeypatch):
    """D7（11-26 用户本人定）：不设开关也会自己挑空白编成位，不报 plan_no_set。"""
    monkeypatch.delenv("HIRO_AUTO_FORMATION", raising=False)
    _plan(hiro_home)
    _formations("hajime", {1: 5, 2: 0}, {1: 4, 2: 0})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3, env  # 出开局确认卡（待确认），不是报错
    assert env["data"]["pending"]["args"]["prepare"] is True


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


def test_all_full_overwrites_sets_we_wrote(run_cli, hiro_home, on):
    """D26（11-26 用户本人定）：都满了、有初星助手写过的号，直接挑来覆盖，开局确认卡里说清楚。"""
    from hiro.brain import formation_plan as fp

    _plan(hiro_home)
    fp.record_slots(hiro_home, "hajime", {"support": 18, "memory": 9})
    fp.record_slots(hiro_home, "nia", {"support": 3})  # 别的模式记的号不算
    _formations("hajime", dict.fromkeys(range(1, 21), 5), dict.fromkeys(range(1, 21), 4))
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3, env
    pend = env["data"]["pending"]
    assert pend["args"]["prepare"] is True
    assert "支援卡编成 18 号（都满了，覆盖初星助手之前写过的）" in pend["summary"]
    assert "回忆编成 9 号（都满了，覆盖初星助手之前写过的）" in pend["summary"]
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["support"]["set"] == 18 and per["memory"]["set"] == 9


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


def test_given_support_slot_with_player_cards_is_not_auto_written(run_cli, hiro_home, on):
    """#134 review：回忆没定编成号、支援卡定了号但没写进去，且那一位是玩家自己的编成（扫描时有卡）：
    不自动准备（不保存挑出的回忆编成号），交给玩家确认覆盖。"""
    from hiro.brain import formation_plan as fp

    _plan(hiro_home, sets=(1, None))
    _formations("hajime", {1: 5, 2: 0}, {1: 4, 2: 0})
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert not ((env.get("data") or {}).get("pending") or {}).get("args", {}).get("prepare"), env
    per = fp.load(hiro_home / "plans" / "hajime" / f"{KOTONE}.json")
    assert per["memory"]["set"] is None  # 没有留下挑好的回忆编成号


def test_unread_kind_in_snapshot_is_surveyed_not_full(run_cli, hiro_home, on):
    """#157 review：开局前准备只读要挑号的那几样（读到空位就停）。快照里只有回忆、没有支援卡的行时，
    支援卡不能当成「都满了」报 formations_full，要在任务里先读再挑；回忆照样从快照里挑空位。"""
    _plan(hiro_home)
    _formations("hajime", {}, {1: 4, 2: 0})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3, env
    pend = env["data"]["pending"]
    assert pend["args"]["prepare"] is True
    assert "回忆编成 2 号" in pend["summary"] and "先读一遍" in pend["summary"] and "支援卡编成位" in pend["summary"]
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["support"]["set"] is None and per["memory"]["set"] == 2


def test_overwrite_reaches_prepare_start_argv(tmp_path):
    """N65：玩家同意覆盖（overwrite）随任务参数传到 prepare_start；没有就不加。"""
    from hiro.cli import cmd_run, jobs

    assert cmd_run._params({"overwrite": "memory=13", "prepare": True})["overwrite"] == "memory=13"
    argv = jobs.KINDS["hajime"].argv({"idol": KOTONE, "prepare": True, "overwrite": "memory=13"}, tmp_path)
    i = argv.index("hiro.runtime.prepare_start")
    assert argv[argv.index("--overwrite", i) + 1] == "memory=13" and argv.index("--overwrite") < argv.index("--")
    plain = jobs.KINDS["hajime"].argv({"idol": KOTONE, "prepare": True}, tmp_path)
    assert "--overwrite" not in plain


# ---------------------------------------------------------------- N65：玩家点名同意覆盖（--overwrite memory=13），走开局检查到确认卡
def test_overwrite_consent_rewrites_plan_into_named_slot(run_cli, hiro_home, on):
    """G1 第 5 轮 11-27：方案写进过回忆 12 号；玩家说「回忆 13 号可以覆盖」（13 号是玩家自己的编成）→ 确认卡按 13 号、写明覆盖，
    参数里带规范写法的 overwrite 给 prepare_start；方案文件这时不改（#192 review：玩家点「先不」时不能留下改过的方案）。"""
    _plan(hiro_home, sets=(4, 12), written=True)
    _formations("hajime", {4: 5}, {12: 4, 13: 4})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--overwrite", "memory=13")
    assert code == 3, env
    pend = env["data"]["pending"]
    args = pend["args"]
    assert args["prepare"] is True and args["overwrite"] == "memory=13" and args["prepare_what"] == "support,memory"
    assert "回忆编成 13 号（你同意覆盖" in args["prepare_note"]
    assert pend["plan"]["memory"]["set"] == 13  # 卡上按同意的号画
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["memory"]["set"] == 12 and "memory" in per["written"]["hajime"]["what"]  # 磁盘上的方案没动


def test_declined_consent_leaves_plain_start_unchanged(run_cli, hiro_home, on):
    """#192 review：出了带 --overwrite 的确认卡、玩家没点，之后不带 --overwrite 开局和原来一样（方案照旧是写好的 12 号）。"""
    _plan(hiro_home, sets=(4, 12), written=True)
    _formations("hajime", {4: 5}, {12: 4, 13: 4})
    run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--overwrite", "memory=13")
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and not env["data"]["pending"]["args"].get("prepare"), env


def test_named_blank_slot_keeps_number_for_job(run_cli, hiro_home, on, tmp_path, monkeypatch):
    """#192 CodeRabbit：玩家点名的 14 号其实是空位（用不上同意）：号码照样传给任务，确认卡和任务都用 14 号；
    卡上说明不写「你同意覆盖」。任务开始时 prepare_start 把方案改成 14 号、写回忆。"""
    from hiro.cli import jobs
    from hiro.runtime import prepare_start as ps

    _plan(hiro_home, sets=(4, 12), written=True)
    _formations("hajime", {4: 5}, {12: 4, 14: 0})
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--overwrite", "memory=14")
    assert code == 3, env
    pend = env["data"]["pending"]
    args = pend["args"]
    assert args["prepare"] is True and args["overwrite"] == "memory=14" and "同意覆盖" not in args["prepare_note"]
    assert "回忆编成 14 号" in args["prepare_note"] and pend["plan"]["memory"]["set"] == 14  # 确认卡用的号
    argv = jobs.KINDS["hajime"].argv(args, tmp_path)
    i = argv.index("hiro.runtime.prepare_start")
    sep = argv.index("--", i)
    calls = []
    monkeypatch.setattr(ps, "_write", lambda mode, what=(), blank_only=(): calls.append(("write", tuple(what))) or 0)
    monkeypatch.setattr(ps, "_exec", lambda argv: 0)
    monkeypatch.setattr(ps, "_check_memory_freshness", lambda plan_path, home: None)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", str(hiro_home / "formation-plan.json"))
    assert ps.main([*argv[i + 1 : sep + 1], "x"]) == 0
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["memory"]["set"] == 14 and ("write", ("memory",)) in calls  # 任务准备的号和卡上一样


def test_player_slot_without_consent_still_refused(run_cli, hiro_home, on):
    """没说同意：方案指向玩家自己的编成位（13 号有卡、不是初星助手写的），照旧不出确认卡（报还没写进游戏）。"""
    _plan(hiro_home, sets=(4, 13))
    _formations("hajime", {4: 0}, {13: 4})
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "plan_not_written"


@pytest.mark.parametrize(
    "bad",
    [
        ["--overwrite", "memory"],
        ["--overwrite", "memory=99"],
        ["--overwrite", "memory=13", "--memory-set", "13"],
        ["--overwrite", "memory=13", "--support-set", "4", "--memory-set", "13"],  # 两样都给了号也要拦（不能提前放行）
        ["--overwrite", "memory=\u00b2"],  # Unicode 数字（isdigit 认、int 不认）
    ],
)
def test_overwrite_bad_usage_is_rejected(run_cli, hiro_home, on, bad):
    """没写号、号超出 1–20、和 --memory-set 给了同一样：报 bad_overwrite，不出确认卡。"""
    _plan(hiro_home, sets=(4, 12), written=True)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, *bad)
    assert env["error"]["code"] == "bad_overwrite"


def test_overwrite_with_explicit_plan_file_is_rejected(run_cli, hiro_home, on, monkeypatch, tmp_path):
    """#192 review：HIRO_FORMATION_PLAN 指向别的方案文件时 --overwrite 用不上，要明说，不能悄悄忽略玩家的同意。"""
    other = tmp_path / "other-plan.json"
    other.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HIRO_FORMATION_PLAN", str(other))
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--overwrite", "memory=13")
    assert env["error"]["code"] == "bad_overwrite" and "HIRO_FORMATION_PLAN" in env["error"]["message"]


def test_overwrite_without_idol_is_rejected(run_cli, hiro_home, on):
    """#192 CodeRabbit：没给 --idol 时 --overwrite 用不上，不能悄悄忽略、出一张没写编成的确认卡。
    （新开一局本来就要 --idol，参数检查先拦下；_check_formation_plan 里另有一道 bad_overwrite 给直接调用的情况）"""
    from hiro.cli import cmd_run
    from hiro.cli.registry import CliError

    _plan(hiro_home, sets=(4, 12), written=True)
    _code, env = run_cli("run", "start", "hajime", "--overwrite", "memory=13")
    assert not env["ok"] and env["error"]["code"] in ("bad_args", "bad_overwrite")
    with pytest.raises(CliError) as e:
        cmd_run._check_formation_plan({"kind": "hajime", "command": "new", "overwrite": "memory=13"})
    assert e.value.code == "bad_overwrite" and "--idol" in e.value.message
