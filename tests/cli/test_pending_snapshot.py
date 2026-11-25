"""确认卡方案快照（PR #79 CodeRabbit：卡上的方案有缓存，确认时写进游戏的可能是另一份）。

待确认生成时把当时的编成方案摘要和内容哈希存进记录；卡按这份摘要画，confirm 时方案文件变了就报错、不执行，
让用户重新出卡，而不是悄悄用新方案。
"""

import json

import pytest


def _plan(home, *, support=2, memory=1, name="A"):
    home.mkdir(parents=True, exist_ok=True)
    plan = {
        "created": "2026-11-26 10:00:00",
        "idol": {"idol_card_id": "i_card-fktn-3-000", "name": "藤田ことね"},
        "support": {"set": support, "own": [{"support_card_id": "s_card-3-0001", "name": name, "level": 60}]},
        "memory": {"set": memory},
    }
    (home / "formation-plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr("hiro.cli.client.data", lambda op, body=None: calls.append((op, body)) or {"job": {"id": "j1"}})
    return calls


def test_write_formation_pending_carries_plan_snapshot(run_cli, hiro_home):
    _plan(hiro_home, name="支援卡甲")
    code, env = run_cli("run", "start", "write-formation", "--scenario", "hif")
    assert code == 3
    plan = env["data"]["pending"]["plan"]
    assert plan["support"]["set"] == 2 and plan["support"]["own"][0]["name"] == "支援卡甲"
    assert plan["idol"]["name"] == "藤田ことね"


def test_new_start_using_plan_carries_snapshot_but_explicit_sets_do_not(run_cli, hiro_home):
    _plan(hiro_home)
    _, env = run_cli("run", "start", "hajime", "--idol", "i_card-fktn-3-000")
    assert env["data"]["pending"]["plan"]["support"]["set"] == 2
    _, env = run_cli("run", "start", "hajime", "--idol", "i_card-fktn-3-000", "--support-set", "3", "--memory-set", "4")
    assert env["data"]["pending"].get("plan") is None
    _, env = run_cli("run", "start", "hif", "--mode", "main")
    assert env["data"]["pending"].get("plan") is None


def test_scan_pending_has_no_snapshot(run_cli, hiro_home):
    _plan(hiro_home)
    _, env = run_cli("run", "start", "scan-memories")
    assert env["data"]["pending"].get("plan") is None


def test_confirm_runs_when_plan_unchanged(run_cli, hiro_home, started):
    _plan(hiro_home)
    _, env = run_cli("run", "start", "write-formation", "--scenario", "hif")
    code, env = run_cli("confirm", env["data"]["pending"]["id"])
    assert code == 0 and env["ok"] is True and started and started[0][0] == "start"


def test_confirm_refuses_when_plan_changed_since_card(run_cli, hiro_home, started):
    _plan(hiro_home, support=2)
    _, env = run_cli("run", "start", "write-formation", "--scenario", "hif")
    pid = env["data"]["pending"]["id"]
    _plan(hiro_home, support=5)  # 卡出来以后方案被重新出过
    _, env = run_cli("confirm", pid)
    assert env["ok"] is False and env["error"]["code"] == "plan_changed"
    assert "重新" in env["error"]["hint"] and started == []
    # 记录已经作废：同一编号不能再确认
    _, env = run_cli("confirm", pid)
    assert env["error"]["code"] == "pending_unknown" and started == []


def test_confirm_refuses_when_plan_deleted(run_cli, hiro_home, started):
    _plan(hiro_home)
    _, env = run_cli("run", "start", "write-formation", "--scenario", "hif")
    (hiro_home / "formation-plan.json").unlink()
    _, env = run_cli("confirm", env["data"]["pending"]["id"])
    assert env["error"]["code"] == "plan_changed" and started == []


def test_pending_list_keeps_snapshot(run_cli, hiro_home):
    _plan(hiro_home)
    run_cli("run", "start", "write-formation", "--scenario", "hif")
    _, env = run_cli("pending", "list")
    assert env["data"]["pending"][0]["plan"]["support"]["set"] == 2


def test_per_idol_plan_activated_at_start_then_confirm_is_not_plan_changed(run_cli, hiro_home, started, monkeypatch):
    """#87 + #93 合流：开局前检查把 plans/<模式>/<偶像卡>.json 设为当前方案（改写 formation-plan.json），
    快照要拍在这之后；确认时开局检查再跑一遍也不能把它当成「方案变了」。"""
    from hiro.brain import formation_plan as fp
    from hiro.cli import cmd_run

    monkeypatch.setattr(cmd_run, "exam_algo_problem", lambda: None)
    _plan(hiro_home, name="别的偶像的")  # 当前方案是旧格式、别的偶像（luna）的
    cur = json.loads((hiro_home / "formation-plan.json").read_text(encoding="utf-8"))
    cur["idol"] = {"idol_card_id": "i_card-ttmr-3-000", "name": "Luna"}
    (hiro_home / "formation-plan.json").write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    mine = {
        "version": fp.PLAN_VERSION,
        "created": "2026-11-26 11:00:00",
        "idol": {"idol_card_id": "i_card-fktn-3-000", "name": "藤田ことね"},
        "support": {"set": 5, "own": [{"support_card_id": "s_card-3-0001", "name": "甲", "level": 60}]},
        "memory": {"set": 6},
        "written": {"hajime": {"what": ["memory", "support"]}},
    }
    fp.save_plan(hiro_home, mine, "hajime")
    fp.activate(hiro_home, cur)  # 当前方案又换回 luna 的：开局时要重新设回琴音的
    _, env = run_cli("run", "start", "hajime", "--idol", "i_card-fktn-3-000")
    pend = env["data"]["pending"]
    assert pend["plan"]["support"]["set"] == 5, env
    code, env = run_cli("confirm", pend["id"])
    assert code == 0 and env["ok"] is True, env
    assert started and started[0][0] == "start"
