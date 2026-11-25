"""开局前自动补扫回忆（设计 5.6 / D4 / D5 / D14）：开关 HIRO_SCAN_BEFORE_START，缺省关。"""

import json

from hiro import memory_refresh as mr
from hiro.cli.jobs import KINDS
from hiro.cli.store import Store
from hiro.produce import memory_sig
from hiro.state import memory_slices as msl

import pytest


def _e(card, vi=64):
    return {"card": {"card_id": card, "upgrade": 0}, "stats": {"Vo": 0, "Da": 52, "Vi": vi, "hp": 0},
            "abilities": [{"text": "初期ビジュアル上昇+15"}]}  # fmt: skip


def test_switch_off_leaves_start_command_alone(monkeypatch, tmp_path):
    monkeypatch.delenv("HIRO_SCAN_BEFORE_START", raising=False)
    argv = KINDS["hajime"].argv({"idol": "i_card-x"}, tmp_path)
    assert "hiro.memory_refresh" not in argv and "开局前会先补扫" not in KINDS["hajime"].summary({"idol": "i_card-x"})


def test_switch_on_wraps_new_game_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("HIRO_SCAN_BEFORE_START", "1")
    monkeypatch.setenv("HIRO_MEMORY_SLICES", "1")
    argv = KINDS["hajime"].argv({"idol": "i_card-x"}, tmp_path)
    assert argv[1:3] == ["-m", "hiro.memory_refresh"] and "--" in argv
    orig = argv[argv.index("--") + 1 :]
    monkeypatch.delenv("HIRO_SCAN_BEFORE_START")
    assert orig == KINDS["hajime"].argv({"idol": "i_card-x"}, tmp_path)
    monkeypatch.setenv("HIRO_SCAN_BEFORE_START", "1")
    assert "开局前会先补扫回忆，最多 3 分钟" in KINDS["hajime"].summary({"idol": "i_card-x"})
    assert "hiro.memory_refresh" not in KINDS["hif"].argv({"command": "resume"}, tmp_path)  # 续打不补扫


@pytest.fixture
def st(tmp_path):
    return Store(tmp_path / "hiro.db")


def test_fresh_slices_skip_scan(st):
    for k in msl.slices_for_plan("sense"):
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    calls = []
    r = mr.ensure_fresh("sense", store=st, run_scan=lambda keys, budget: calls.append((keys, budget)))
    assert calls == [] and r["scanned"] == [] and r["pending"] == []


def test_stale_or_missing_slices_scanned_with_budget(st):
    need = msl.slices_for_plan("logic")
    for k in need[:3]:
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    st.db.execute("UPDATE slice_coverage SET scanned_at='2026-11-20 10:00:00' WHERE slice=?", (need[0],))
    st.db.commit()
    calls = []

    def run_scan(keys, budget):
        calls.append((keys, budget))
        return {"pending": keys[-1:]}

    r = mr.ensure_fresh("logic", store=st, run_scan=run_scan, budget_s=180)
    assert calls == [([need[0], *need[3:]], 180)]
    assert r["pending"] == [need[-1]]


def test_plan_memory_missing_stops(st, tmp_path):
    for k in msl.slices_for_plan("sense"):
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    have = memory_sig.signature({**_e("sense:hif"), "slice": "sense:hif"})
    gone = memory_sig.signature(_e("sold-card"))
    plan = {"idol": {"idol_card_id": "i_card-x"}, "memory": {"chosen": [{"card_id": "sense:hif", "signature": have},
                                                                        {"card_id": "sold-card", "signature": gone}]}}  # fmt: skip
    p = tmp_path / "formation-plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(mr.PlanMemoryMissing) as e:
        mr.check_plan_memories(p, "sense", store=st)
    assert "sold-card" in str(e.value)


def test_switch_read_from_settings_when_env_unset(monkeypatch, hiro_home):
    """初星助手设置里的开关存在 settings.json 的 scan_before_start；环境变量设了以环境变量为准。"""
    monkeypatch.delenv("HIRO_SCAN_BEFORE_START", raising=False)
    assert not mr.enabled()
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "settings.json").write_text(json.dumps({"scan_before_start": True}), encoding="utf-8")
    assert not mr.enabled()  # 补扫是按片扫的：「回忆按片扫描」也要开（#116 审查重要 5）
    (hiro_home / "settings.json").write_text(
        json.dumps({"scan_before_start": True, "memory_slices": True}), encoding="utf-8"
    )
    assert mr.enabled()
    monkeypatch.setenv("HIRO_SCAN_BEFORE_START", "0")
    assert not mr.enabled()


def test_refresh_runs_before_prepare_start(monkeypatch, tmp_path):
    """G0（prepare_start：挑空白编成位、写编成）合入后两层都包在开局命令外面：先补扫回忆（库存新鲜）、再准备编成、再开局。"""
    monkeypatch.setenv("HIRO_SCAN_BEFORE_START", "1")
    monkeypatch.setenv("HIRO_MEMORY_SLICES", "1")
    argv = KINDS["hajime"].argv({"idol": "i_card-x", "prepare": True}, tmp_path)
    mods = [argv[i + 1] for i, a in enumerate(argv) if a == "-m"]
    assert mods[:2] == ["hiro.memory_refresh", "hiro.runtime.prepare_start"]


def test_scan_exit_formation_changed_stops_before_start(st, tmp_path):
    """#116 审查重要 3：补扫时选择页意外关了（回忆扫描以 FORMATION_CHANGED_EXIT 退出）：不开局，退出码照传；普通失败继续开局。"""
    ran = []

    def scan(keys, budget):
        raise mr.FormationMayHaveChanged("选择页在扫描中关了")

    rc = mr.refresh_and_start("sense", ["game"], store=st, scan=scan, run=lambda cmd: ran.append(cmd) or 0,
                              plan_path=tmp_path / "none.json")  # fmt: skip
    assert rc == mr.FORMATION_CHANGED_EXIT and ran == []
    rc = mr.refresh_and_start("sense", ["game"], store=st, scan=lambda k, b: {"pending": k}, run=lambda cmd: ran.append(cmd) or 0,
                              plan_path=tmp_path / "none.json")  # fmt: skip
    assert rc == 0 and ran[0] == ["game"]


def test_scan_subprocess_exit_code_maps_to_formation_changed(monkeypatch, tmp_path):
    import subprocess

    from hiro import memory_scan

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, memory_scan.FORMATION_CHANGED_EXIT)
    )
    with pytest.raises(mr.FormationMayHaveChanged):
        mr._scan(["sense:hif"], 180, tmp_path)


def test_unfinished_slice_is_stale_even_if_recent(st):
    """预算打断的片（最近一次 complete=False）即使不到 1 天也要排补扫。"""
    for k in msl.slices_for_plan("sense"):
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    k0 = msl.slices_for_plan("sense")[0]
    st.save_slice(k0, [], mode="incremental", complete=False)
    calls = []
    mr.ensure_fresh("sense", store=st, run_scan=lambda keys, budget: calls.append(keys) or {})
    assert calls == [[k0]]


def test_refresh_scan_imports_partial_output_on_failure(monkeypatch, tmp_path):
    """子进程非 0 退出时，已写好的逐片进度照样入库；编成可能被改（退出码 5）时入库后再停。"""
    import subprocess

    from hiro import memory_scan
    from hiro.cli import cmd_info

    got = []
    monkeypatch.setattr(
        cmd_info, "import_inventory", lambda kind, inv, source: got.append(inv) or {"count": 1, "added": 0}
    )

    def run(argv, **kw):
        out = argv[argv.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"slices": [{"key": "sense:hif"}], "pending": ["common:hif"]}))
        return subprocess.CompletedProcess(argv, run.code)

    monkeypatch.setattr(subprocess, "run", run)
    run.code = 1
    r = mr._scan(["sense:hif", "common:hif"], 180, tmp_path)
    assert len(got) == 1 and r["pending"] == ["common:hif"]
    run.code = memory_scan.FORMATION_CHANGED_EXIT
    with pytest.raises(mr.FormationMayHaveChanged):
        mr._scan(["sense:hif", "common:hif"], 180, tmp_path)
    assert len(got) == 2
