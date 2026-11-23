import json
from pathlib import Path

DATA = Path(__file__).parent / "data"

CANNED = {
    "version": 1,
    "created": "2026-11-17 16:00:00",
    "idol": {
        "idol_card_id": "i_card-shro-3-007",
        "name": "Campus mode!!",
        "plan": "ProducePlanType_Plan2",
        "profile": "logic",
    },
    "profile": "logic",
    "main_attr": "Da",
    "notes": ["4 张集中在 Da"],
    "support": {
        "set": 4,
        "main_attr": "Da",
        "value": 199,
        "counts": {"Vo": 0, "Da": 4, "Vi": 2},
        "own": [
            {
                "support_card_id": "s_card-3-0067",
                "name": "そろそろ焼けたかな？",
                "attr": "Da",
                "level": 60,
                "value": 20,
                "why": ["事件 Da+20"],
            }
        ],
        "rental": {"support_card_id": "s_card-3-0000", "name": "あなたにも作ってあげる！", "value": 20, "attr": "Da"},
        "rental_estimated": True,
    },
    "memory": {
        "set": 5,
        "cards": ["p_card-01-ido-3_105"],
        "chosen": [
            {
                "id": "p13-16-2",
                "card_id": "p_card-01-ido-3_105",
                "name": "ワクワクが止まらない",
                "upgrade": 1,
                "timing": "start",
                "slot": "A",
                "role": "核心",
            }
        ],
    },
}


def test_inventory_raw_is_kept(run_cli, hiro_home):
    from hiro.cli.store import Store

    run_cli("info", "import", "--kind", "supports", str(DATA / "support-inventory.json"))
    raw = Store(hiro_home / "hiro.db").latest_raw("supports")
    assert raw == json.loads((DATA / "support-inventory.json").read_text(encoding="utf-8"))


def test_plan_without_inventory_says_scan_first(run_cli):
    _code, env = run_cli("plan", "formation", "--idol", "i_card-skin-shro-3-007")
    assert env["ok"] is False and env["error"]["code"] == "no_inventory" and "扫描" in env["error"]["hint"]


def test_plan_uses_latest_inventories_and_writes_the_plan_file(run_cli, hiro_home, monkeypatch):
    import hiro.brain.formation_plan as fp

    seen = {}

    def fake_build(idol, **kw):
        seen.update(kw, idol=idol)
        return CANNED

    monkeypatch.setattr(fp, "build", fake_build)
    monkeypatch.setattr(fp, "format_report", lambda plan: "# 方案\n")
    run_cli("info", "import", "--kind", "supports", str(DATA / "support-inventory.json"))
    run_cli("info", "import", "--kind", "memories", str(DATA / "memory-inventory.json"))
    code, env = run_cli(
        "plan",
        "formation",
        "--idol",
        "i_card-skin-shro-3-007",
        "--support-set",
        "4",
        "--memory-set",
        "5",
        "--main-attr",
        "Da",
    )
    assert code == 0, env
    assert (
        seen["idol"] == "i_card-skin-shro-3-007"
        and seen["support_set"] == 4
        and seen["memory_set"] == 5
        and seen["main_attr"] == "Da"
    )
    assert seen["support_inventory"]["cards"] and seen["memory_inventory"]["entries"]
    assert json.loads((hiro_home / "formation-plan.json").read_text(encoding="utf-8")) == CANNED
    s = env["data"]
    assert s["idol"]["name"] == "Campus mode!!" and s["main_attr"] == "Da"
    assert s["support"]["own"][0] == {
        "id": "s_card-3-0067",
        "name": "そろそろ焼けたかな？",
        "attr": "Da",
        "level": 60,
        "value": 20,
        "why": ["事件 Da+20"],
    }
    assert s["support"]["rental"]["estimated"] is True
    assert s["support"]["rental"]["id"] == "s_card-3-0000"  # 界面按编号配中文名和卡面
    assert s["memory"]["chosen"][0]["name"] == "ワクワクが止まらない" and s["memory"]["chosen"][0]["upgrade"] == 1
    assert s["report"] == "# 方案\n"
    code, env2 = run_cli("plan", "show")
    assert env2["data"]["idol"]["name"] == "Campus mode!!" and env2["data"]["path"].endswith("formation-plan.json")


def test_show_without_plan(run_cli):
    _code, env = run_cli("plan", "show")
    assert env["ok"] is False and env["error"]["code"] == "no_plan"
