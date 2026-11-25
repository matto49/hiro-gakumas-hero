"""开局编成方案按「偶像卡 × 模式」存，开局时没有就按社区攻略自动出（2026-11-26，用户：选好偶像想培育时「没有策略」）。

- hiro plan formation --mode hajime：存进 HIRO_HOME/plans/hajime/<偶像卡>.json，同时设为当前方案（formation-plan.json）。
- hiro run start：找这张卡 × 这个模式的方案；没有就自动出一份（编成位号沿用这个模式上一份方案的），
  先要「写编成」写进游戏再开局（游戏里那个编成位还是上一位偶像的）；写编成成功后方案记 written，之后直接开局。
- 以前的方案文件（没有 version 2）照旧：偶像对得上就放行。"""

import json

import pytest

KOTONE, KOTONE_SKIN = "i_card-fktn-3-000", "i_card-skin-fktn-3-000"
LUNA = "i_card-ttmr-3-000"


@pytest.fixture(autouse=True)
def _algo_ok(monkeypatch):
    from hiro.cli import cmd_run

    monkeypatch.setattr(cmd_run, "exam_algo_problem", lambda: None)


@pytest.fixture
def fake_build(monkeypatch):
    """出方案不真算（要主数据 / 库存）：记下参数，按偶像回一份方案。"""
    import hiro.brain.formation_plan as fp

    calls = []

    def build(idol, **kw):
        calls.append(dict(kw, idol=idol))
        cid = idol.replace("i_card-skin-", "i_card-")
        return {"version": 2, "created": "2026-11-26 10:00:00", "idol": {"idol_card_id": cid, "name": f"卡 {cid}"},
                "mode": kw.get("mode"), "main_attr": "Vi", "notes": [],
                "support": {"set": kw.get("support_set"), "own": [{"support_card_id": "s_card-3-0069"}]},
                "memory": {"set": kw.get("memory_set"), "cards": ["p_card-01-act-2_001"], "chosen": []}}  # fmt: skip

    monkeypatch.setattr(fp, "build", build)
    monkeypatch.setattr(fp, "format_report", lambda plan: "")
    return calls


def _inventory(run_cli):
    from pathlib import Path

    data = Path(__file__).parent / "data"
    run_cli("info", "import", "--kind", "supports", str(data / "support-inventory.json"))
    run_cli("info", "import", "--kind", "memories", str(data / "memory-inventory.json"))


def _legacy(home, idol=LUNA, name="Luna say maybe", sets=(16, 9)):
    home.mkdir(parents=True, exist_ok=True)
    plan = {"idol": {"idol_card_id": idol, "name": name}, "support": {"set": sets[0]}, "memory": {"set": sets[1]}}
    (home / "formation-plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def test_plan_formation_per_mode_file_and_guide_mode(run_cli, hiro_home, fake_build):
    _inventory(run_cli)
    code, env = run_cli("plan", "formation", "--idol", KOTONE_SKIN, "--mode", "hajime", "--difficulty", "pro")
    assert code == 0, env
    assert fake_build[-1]["mode"] == "produce-002"  # 初 PRO：按这个模式查攻略
    per = hiro_home / "plans" / "hajime" / f"{KOTONE}.json"
    assert json.loads(per.read_text(encoding="utf-8"))["idol"]["idol_card_id"] == KOTONE
    assert json.loads((hiro_home / "formation-plan.json").read_text(encoding="utf-8")) == json.loads(per.read_text())
    _code, env = run_cli("plan", "show", "--idol", KOTONE, "--mode", "hajime")
    assert env["ok"] and env["data"]["path"].endswith(f"plans/hajime/{KOTONE}.json")


def test_start_auto_plans_for_new_idol_and_asks_to_write_first(run_cli, hiro_home, fake_build):
    _legacy(hiro_home)  # 现在的方案是给 Luna 的，存在编成 16 / 9
    _inventory(run_cli)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["ok"] is False and env["error"]["code"] == "plan_not_written"
    assert "16" in env["error"]["message"] and "9" in env["error"]["message"]
    assert "write-formation --scenario hajime" in env["error"]["hint"]
    call = fake_build[-1]
    assert call["mode"] == "produce-001" and (call["support_set"], call["memory_set"]) == (16, 9)
    per = hiro_home / "plans" / "hajime" / f"{KOTONE}.json"
    assert json.loads(per.read_text(encoding="utf-8"))["source"] == "auto"
    cur = json.loads((hiro_home / "formation-plan.json").read_text(encoding="utf-8"))
    assert cur["idol"]["idol_card_id"] == KOTONE  # 写编成任务按当前方案写
    # Luna 的旧方案没丢：换回去还能用
    old = hiro_home / "plans" / "_legacy" / f"{LUNA}.json"
    assert json.loads(old.read_text(encoding="utf-8"))["support"]["set"] == 16


def test_written_plan_starts(run_cli, hiro_home, fake_build):
    from hiro.brain import formation_plan as fp

    _legacy(hiro_home)
    _inventory(run_cli)
    run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    fp.mark_written(hiro_home / "formation-plan.json", "hajime", ("support", "memory"))
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert code == 3 and env["data"]["pending"], env
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["written"]["hajime"]["what"] == ["support", "memory"]


def test_auto_plan_without_slot_numbers_says_which_slot(run_cli, hiro_home, fake_build):
    _inventory(run_cli)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "no_slot"
    assert "--support-set" in env["error"]["hint"] and "--mode hajime" in env["error"]["hint"]
    assert (hiro_home / "plans" / "hajime" / f"{KOTONE}.json").is_file()  # 方案已经出好，只差编成位号


def test_no_inventory_still_says_scan_first(run_cli, hiro_home):
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "no_plan" and "扫描" in env["error"]["hint"]


def test_plan_per_mode_does_not_leak_to_other_mode(run_cli, hiro_home, fake_build):
    _inventory(run_cli)
    run_cli("plan", "formation", "--idol", KOTONE, "--mode", "nia", "--support-set", "3", "--memory-set", "4")
    from hiro.brain import formation_plan as fp

    fp.mark_written(hiro_home / "formation-plan.json", "nia", ("support", "memory"))
    code, env = run_cli("run", "start", "nia", "--idol", KOTONE)
    assert code == 3 and env["data"]["pending"]
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE)  # 初没有方案：自动出，编成位号不拿 N.I.A 的
    assert env["error"]["code"] == "no_slot"
