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
    assert sorted(per["written"]["hajime"]["what"]) == ["memory", "support"]


def test_auto_plan_without_slot_numbers_says_which_slot(run_cli, hiro_home, fake_build):
    _inventory(run_cli)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN)
    assert env["error"]["code"] == "plan_no_set"
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
    assert env["error"]["code"] == "plan_no_set"


def test_partial_writes_merge_and_both_needed(hiro_home):
    """分两次写（只写支援卡、再只写回忆）：written 合并两次写过的部分；两样都写过才算写进游戏（CodeRabbit #87）。"""
    from hiro.brain import formation_plan as fp

    plan = {
        "version": 2,
        "family": "hajime",
        "idol": {"idol_card_id": KOTONE},
        "support": {"set": 3},
        "memory": {"set": 4},
    }
    fp.save_plan(hiro_home, plan, "hajime")
    cur = hiro_home / "formation-plan.json"
    fp.mark_written(cur, "hajime", ("support",))
    assert fp.needs_write(fp.load(cur), "hajime")
    fp.mark_written(cur, "hajime", ("memory",))
    p = fp.load(fp.plan_key_path(hiro_home, KOTONE, "hajime"))
    assert sorted(p["written"]["hajime"]["what"]) == ["memory", "support"] and not fp.needs_write(p, "hajime")


def test_current_plan_without_per_mode_copy_is_kept(hiro_home):
    """当前方案是不带 --mode 出的（version 2、没有按模式的副本）：换别的偶像时先收进 _legacy，不直接覆盖（CodeRabbit #87）。"""
    from hiro.brain import formation_plan as fp

    hiro_home.mkdir(parents=True)
    mine = {"version": 2, "idol": {"idol_card_id": LUNA}, "support": {"set": 1}, "memory": {"set": 2}}
    (hiro_home / "formation-plan.json").write_text(json.dumps(mine), encoding="utf-8")
    fp.save_plan(hiro_home, {"version": 2, "idol": {"idol_card_id": KOTONE}}, "hajime")
    assert fp.load(fp.plan_key_path(hiro_home, LUNA, "_legacy"))["support"]["set"] == 1
    # 有按模式的副本的就不再多收一份
    fp.save_plan(hiro_home, {"version": 2, "idol": {"idol_card_id": LUNA}}, "nia")
    assert not fp.plan_key_path(hiro_home, KOTONE, "_legacy").is_file()


def test_auto_plan_with_one_set_given_only_asks_the_other(run_cli, hiro_home, fake_build):
    """#87 + #88 合流：自动出了方案、开局只给了支援卡编成号，就只问回忆编成位。"""
    _inventory(run_cli)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--support-set", "3")
    assert env["error"]["code"] == "plan_no_set"
    assert "回忆" in env["error"]["message"] and "支援卡" not in env["error"]["message"]
    assert "info formations" in env["error"]["hint"]


def test_explicit_set_beats_inherited_and_only_rest_needs_writing(run_cli, hiro_home, fake_build):
    """CodeRabbit #87：玩家开局给了 --support-set 3（游戏里 3 号编成已经编好），旧方案记的是 16 / 9。
    自动出的方案支援卡用 3、回忆沿用 9；只要求写回忆，写完回忆就能开局，不会去覆盖支援卡 16 号。"""
    from hiro.brain import formation_plan as fp

    _legacy(hiro_home, sets=(16, 9))
    _inventory(run_cli)
    _code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--support-set", "3")
    assert env["error"]["code"] == "plan_not_written", env
    assert fake_build[-1]["support_set"] == 3 and fake_build[-1]["memory_set"] == 9
    msg = env["error"]["message"]
    assert "回忆编成 9 号" in msg and "16" not in msg and "支援卡编成" not in msg
    assert "--what memory" in env["error"]["hint"]
    per = json.loads((hiro_home / "plans" / "hajime" / f"{KOTONE}.json").read_text(encoding="utf-8"))
    assert per["support"]["set"] == 3 and per["memory"]["set"] == 9
    fp.mark_written(hiro_home / "formation-plan.json", "hajime", ("memory",))  # 只写了回忆
    code, env = run_cli("run", "start", "hajime", "--idol", KOTONE_SKIN, "--support-set", "3")
    assert code == 3 and env["data"]["pending"], env
