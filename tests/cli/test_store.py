from pathlib import Path

from hiro.cli.store import Store

DATA = Path(__file__).parent / "data"


def test_latest_snapshot_wins(hiro_home):
    s = Store(hiro_home / "hiro.db")
    s.save_snapshot("supports", [{"id": "a"}], source="list")
    s.save_snapshot("supports", [{"id": "b"}, {"id": "c"}], source="list")
    meta, rows = s.latest("supports")
    assert [r["id"] for r in rows] == ["b", "c"]
    assert meta["count"] == 2 and meta["scanned_at"]
    assert len(s.history("supports")) == 2


def test_info_without_scan_says_so(run_cli):
    code, env = run_cli("info", "supports")
    assert code == 0
    assert env["data"]["scanned_at"] is None
    assert env["data"]["items"] == []
    assert "扫描" in env["data"]["hint"]


def test_import_supports_then_filter_by_rarity(run_cli):
    code, env = run_cli("info", "import", "--kind", "supports", str(DATA / "support-inventory.json"))
    assert code == 0 and env["data"]["count"] == 4
    code, env = run_cli("info", "supports", "--rarity", "SSR")
    names = [c["name"] for c in env["data"]["items"]]
    assert len(names) == 3 and "テストSR" not in names
    first = env["data"]["items"][0]
    assert first["rarity"] == "SSR" and first["level"] == 60 and first["attr"] == "Vi"


def test_import_memories_and_filter_by_timing(run_cli):
    run_cli("info", "import", "--kind", "memories", str(DATA / "memory-inventory.json"))
    code, env = run_cli("info", "memories", "--timing", "start")
    assert len(env["data"]["items"]) == 1
    assert env["data"]["complete"] is True


def test_summary_counts_each_kind(run_cli):
    run_cli("info", "import", "--kind", "supports", str(DATA / "support-inventory.json"))
    code, env = run_cli("info", "summary")
    kinds = env["data"]["kinds"]
    assert kinds["supports"]["count"] == 4
    assert kinds["idols"]["count"] == 0 and kinds["idols"]["scanned_at"] is None


def test_import_keeps_the_scan_time_from_generated(run_cli):
    # support_inventory 的输出把扫描时间记在 generated（epoch 秒），不是 scanned_at
    run_cli("info", "import", "--kind", "supports", str(DATA / "support-inventory.json"))
    _, env = run_cli("info", "supports")
    from datetime import datetime

    assert env["data"]["scanned_at"] == datetime.fromtimestamp(1763300000.0).strftime("%Y-%m-%d %H:%M:%S")


def test_listing_carries_counts_before_items(run_cli, capsys):
    import json as _json

    from hiro.cli.main import main

    run_cli("info", "import", "--kind", "supports", str(DATA / "support-inventory.json"))
    _, env = run_cli("info", "supports", "--rarity", "SSR")
    c = env["data"]["counts"]
    assert c["total"] == 3
    attrs = {}
    for it in env["data"]["items"]:
        attrs[it["attr"]] = attrs.get(it["attr"], 0) + 1
    assert c["by_attr"] == attrs and c["by_rarity"] == {"SSR": 3}
    main(["info", "supports"])
    raw = capsys.readouterr().out
    assert raw.index('"counts"') < raw.index('"items"')  # 交给模型的结果太长被截断时，统计数还在


def test_memory_counts_by_timing(run_cli):
    run_cli("info", "import", "--kind", "memories", str(DATA / "memory-inventory.json"))
    _, env = run_cli("info", "memories")
    assert env["data"]["counts"]["by_timing"] == {"mid": 2, "start": 1}
