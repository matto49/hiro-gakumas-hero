"""开局前检查（hiro run start 的 precheck / 确认摘要）：问题在待确认之前就说清楚，不开一个注定失败的任务。

Mac 11-24：H.I.F（01:31）和「初」藤田ことね（16:08）的编成方案是给月村手毬的，任务进了游戏流程才报
「开局要编成号」退出；18:27 那局考试出牌算法没加载上，整局退回规则打，只有日志里一行 WARNING。"""

import json

import pytest

KOTONE = "i_card-skin-fktn-3-000"


def _plan(home, idol="i_card-ttmr-3-000", name="Luna say maybe"):
    home.mkdir(parents=True, exist_ok=True)
    plan = {"idol": {"idol_card_id": idol, "name": name}, "support": {"set": 16}, "memory": {"set": 9}}
    (home / "formation-plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def _algo_ok(monkeypatch):
    from hiro.cli import cmd_run

    monkeypatch.setattr(cmd_run, "exam_algo_problem", lambda: None)


# 方案是给别的偶像的、有库存时：自动给这张卡出方案（tests/cli/test_plan_per_idol.py）。这里是没有库存、出不了方案的情况
@pytest.mark.parametrize("kind", ["hajime", "nia"])
def test_plan_for_another_idol_without_inventory_is_refused_before_confirm(run_cli, hiro_home, kind):
    _plan(hiro_home)
    _code, env = run_cli("run", "start", kind, "--idol", KOTONE, "--difficulty", "master")
    assert env["ok"] is False and env["error"]["code"] == "no_plan"
    assert "扫描" in env["error"]["hint"] and "自动出方案" in env["error"]["hint"]


def test_no_plan_is_refused_before_confirm(run_cli, hiro_home):
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE)
    assert env["ok"] is False and env["error"]["code"] == "no_plan"


def test_explicit_plan_file_for_another_idol_is_still_refused(run_cli, hiro_home, tmp_path, monkeypatch):
    other = tmp_path / "my-plan.json"
    other.write_text(
        json.dumps({"idol": {"idol_card_id": "i_card-ttmr-3-000", "name": "Luna say maybe"}}), encoding="utf-8"
    )
    monkeypatch.setenv("HIRO_FORMATION_PLAN", str(other))
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE)
    assert env["error"]["code"] == "plan_other_idol" and "Luna say maybe" in env["error"]["message"]
    assert "plan formation --idol i_card-fktn-3-000 --mode hajime" in env["error"]["hint"]


def test_hif_qualify_checks_plan_but_main_and_resume_do_not(run_cli, hiro_home):
    _plan(hiro_home)
    _code, env = run_cli("run", "start", "hif", "--idol", KOTONE, "--mode", "qualify")
    assert env["error"]["code"] == "no_plan"
    for extra in (("--mode", "main"), ("--command", "resume")):
        code, env = run_cli("run", "start", "hif", "--idol", KOTONE, *extra)
        assert code == 3 and env["data"]["pending"], extra  # 本战不走编成步骤、续跑不重新编成


def test_matching_plan_or_explicit_sets_pass(run_cli, hiro_home):
    _plan(hiro_home, idol="i_card-fktn-3-000", name="世界一可愛い私")
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE)
    assert code == 3 and env["data"]["pending"]
    (hiro_home / "formation-plan.json").unlink()
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE, "--support-set", "3", "--memory-set", "7")
    assert code == 3 and env["data"]["pending"]


def test_confirm_summary_warns_when_exam_algo_is_missing(run_cli, hiro_home, monkeypatch):
    from hiro.cli import cmd_run

    _plan(hiro_home, idol="i_card-fktn-3-000")
    monkeypatch.setattr(cmd_run, "exam_algo_problem", lambda: "缺 Python 模块 gkbench")
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE)
    summary = env["data"]["pending"]["summary"]
    assert code == 3 and "考试出牌只能用规则" in summary and "gkbench" in summary


# ---------------------------------------------------------------- 写编成：库存 / 方案过期
def _write_setup(home, *, mem_scanned, plan_created, produce_ended=None):
    from datetime import datetime

    from hiro.cli.store import Store

    home.mkdir(parents=True, exist_ok=True)
    plan = {
        "created": plan_created,
        "idol": {"idol_card_id": "i_card-fktn-3-000"},
        "support": {"set": 16},
        "memory": {"set": 9, "chosen": [{"cell": [128, 2]}]},
    }
    (home / "formation-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    Store(home / "hiro.db").save_snapshot(
        "memories", [{"id": "m1"}], source="scan:x", scanned_at=mem_scanned, complete=True
    )
    if produce_ended:
        d = home / "jobs" / "20261123-203335-07cd"
        d.mkdir(parents=True)
        ended = datetime.strptime(produce_ended, "%Y-%m-%d %H:%M:%S").timestamp()
        (d / "job.json").write_text(
            json.dumps({"kind": "hajime", "state": "done", "ended_at": ended}), encoding="utf-8"
        )


def test_write_refused_when_a_produce_finished_after_memory_scan(run_cli, hiro_home):
    """Mac 11-24 16:44：回忆库存 11-23 11:22 扫的，11-23 20:33 那局「初」打完又多了一份回忆、列表位置变了，
    写编成翻了 28 格没找到方案里的回忆才停。现在写之前就说清楚：先重扫回忆、重出方案。"""
    _write_setup(hiro_home, mem_scanned="2026-11-23 11:22:48", plan_created="2026-11-24 16:40:00",
                 produce_ended="2026-11-23 21:23:10")  # fmt: skip
    _code, env = run_cli("run", "start", "write-formation", "--scenario", "hajime")
    assert env["ok"] is False and env["error"]["code"] == "inventory_stale"
    assert "scan-memories" in env["error"]["hint"] and "plan formation" in env["error"]["hint"]


def test_write_refused_when_plan_is_older_than_memory_scan(run_cli, hiro_home):
    _write_setup(hiro_home, mem_scanned="2026-11-24 18:09:09", plan_created="2026-11-24 16:40:00")
    _code, env = run_cli("run", "start", "write-formation", "--scenario", "hajime")
    assert env["ok"] is False and env["error"]["code"] == "plan_stale"
    assert "plan formation --idol i_card-fktn-3-000" in env["error"]["hint"]


def test_write_allowed_when_scan_and_plan_are_fresh(run_cli, hiro_home):
    """Mac 11-24 18:09 重扫回忆、18:09:37 出方案、18:10 写编成成功：这种顺序放行。"""
    _write_setup(hiro_home, mem_scanned="2026-11-24 18:09:09", plan_created="2026-11-24 18:09:37",
                 produce_ended="2026-11-23 21:23:10")  # fmt: skip
    code, env = run_cli("run", "start", "write-formation", "--scenario", "hajime")
    assert code == 3 and env["data"]["pending"]


def test_write_support_only_or_dry_run_skip_memory_freshness(run_cli, hiro_home):
    _write_setup(hiro_home, mem_scanned="2026-11-23 11:22:48", plan_created="2026-11-24 16:40:00",
                 produce_ended="2026-11-23 21:23:10")  # fmt: skip
    for extra in (("--what", "support"), ("--dry-run",)):
        code, env = run_cli("run", "start", "write-formation", "--scenario", "hajime", *extra)
        assert code == 3 and env["data"]["pending"], extra


# ---------------------------------------------------------------- 方案没定编成号（11-26 Mac：方案没带编成号，确认后才报「new 开局要编成号」）


def _plan_no_set(home, idol="i_card-fktn-3-000"):
    home.mkdir(parents=True, exist_ok=True)
    plan = {"idol": {"idol_card_id": idol, "name": "世界一可愛い私"}, "support": {"set": None}, "memory": {"set": None}}
    (home / "formation-plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def test_plan_without_set_is_refused_before_confirm(run_cli, hiro_home):
    _plan_no_set(hiro_home)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE)
    assert env["ok"] is False and env["error"]["code"] == "plan_no_set"
    assert "编成位" in env["error"]["message"] and "--support-set" not in env["error"]["message"]
    assert "--support-set" in env["error"]["hint"] and "info formations" in env["error"]["hint"]
    # 开局时直接给了编成号就照常
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE, "--support-set", "3", "--memory-set", "7")
    assert code == 3 and env["data"]["pending"]


def test_write_formation_without_set_is_refused(run_cli, hiro_home):
    _plan_no_set(hiro_home)
    _code, env = run_cli("run", "start", "write-formation", "--scenario", "hajime", "--what", "support")
    assert env["ok"] is False and env["error"]["code"] == "plan_no_set"


def test_only_one_set_given_checks_the_other_in_plan(run_cli, hiro_home):
    """CodeRabbit #88：只给了支援卡编成号，回忆编成号还要看方案；方案里也没有就在确认前说。"""
    _plan_no_set(hiro_home)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE, "--support-set", "3")
    assert env["ok"] is False and env["error"]["code"] == "plan_no_set" and "回忆" in env["error"]["message"]
    assert "支援卡" not in env["error"]["message"]
