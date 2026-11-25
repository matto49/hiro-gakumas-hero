"""写编成（tools/write_formation.py）写完、回读核对通过后，在方案上记 written[模式]：开局检查据此放行（2026-11-26）。
只看差异（--dry-run）时两样都不用改 = 游戏里已经是这套编成，也记上。设备和菜单操作用替身。"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.synthetic, pytest.mark.play_free]
ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location("write_formation_tool", ROOT / "tools/write_formation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import hiro.produce.formation_menu as fm
    import hiro.tool_device as td

    plan = {"version": 2, "family": "hajime", "idol": {"idol_card_id": "i_card-fktn-3-000"},
            "support": {"set": 16}, "memory": {"set": 9}}  # fmt: skip
    cur = tmp_path / "formation-plan.json"
    cur.write_text(json.dumps(plan), encoding="utf-8")
    per = tmp_path / "plans" / "hajime" / "i_card-fktn-3-000.json"
    per.parent.mkdir(parents=True)
    per.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("HIRO_FORMATION_PLAN", str(cur))
    monkeypatch.setattr(
        td, "open_tool_device", lambda: type("D", (), {"screenshot": None, "tap": None, "swipe": None})()
    )
    result = {}
    monkeypatch.setattr(fm, "run", lambda *a, **k: result["out"])
    return cur, per, result


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["write_formation.py", *argv])
    return _tool().main()


def test_write_marks_plan_written(setup, monkeypatch):
    cur, per, result = setup
    result["out"] = {"support": ["第 1 格"], "memory": []}
    assert _run(["--mode", "hajime"], monkeypatch) == 0
    for p in (cur, per):
        w = json.loads(p.read_text(encoding="utf-8"))["written"]["hajime"]
        assert sorted(w["what"]) == ["memory", "support"] and w["support_set"] == 16


def test_dry_run_marks_only_when_nothing_to_change(setup, monkeypatch):
    cur, _per, result = setup
    result["out"] = {"support": ["第 1 格"], "memory": []}
    assert _run(["--mode", "hajime", "--dry-run"], monkeypatch) == 0
    assert "written" not in json.loads(cur.read_text(encoding="utf-8"))
    result["out"] = {"support": [], "memory": []}
    assert _run(["--mode", "hajime", "--dry-run"], monkeypatch) == 0
    assert "hajime" in json.loads(cur.read_text(encoding="utf-8"))["written"]
