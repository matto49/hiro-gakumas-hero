"""去 KAA 第 4 批：主数据不再用 KAA 公开数据的 game.db，改从模拟器主数据（gakumasu-diff 固定提交，
vendor/gakumas-player 的 catalog.sqlite）生成同结构的 SQLite（每张表一列一个字段；列表 / 对象写 JSON 文本，布尔写 0 / 1，同 game.db）。"""

import json
import sqlite3
import zlib

from hiro.state import master_db

import pytest

pytestmark = [pytest.mark.synthetic, pytest.mark.play_free]


def _sim(path, commit="abc123", rows=None):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO metadata VALUES ('snapshot', ?)", (json.dumps({"commit": commit}),))
    db.execute("CREATE TABLE records(table_name TEXT,row_id TEXT,variant INTEGER,ordinal INTEGER,payload BLOB NOT NULL,"
               "PRIMARY KEY(table_name,ordinal))")  # fmt: skip
    rows = rows or [
        ("ProduceCard", {"id": "p_card-1", "upgradeCount": 0, "name": "アピールの基本", "isInitial": True,
                         "playEffects": [{"produceExamEffectId": "e1"}], "costValue": 4}),
        ("ProduceCard", {"id": "p_card-1", "upgradeCount": 1, "name": "アピールの基本+", "isInitial": False,
                         "playEffects": [], "costValue": 4, "extra": "新字段"}),
        ("Character", {"id": "hski", "lastName": "篠澤", "firstName": "広"}),
    ]  # fmt: skip
    for i, (table, payload) in enumerate(rows):
        db.execute("INSERT INTO records VALUES (?,?,?,?,?)",
                   (table, payload["id"], payload.get("upgradeCount"), i, zlib.compress(json.dumps(payload).encode())))  # fmt: skip
    db.commit()
    db.close()
    return path


def test_build_matches_game_db_layout(tmp_path):
    out = master_db.build(_sim(tmp_path / "catalog.sqlite"), tmp_path / "game.db")
    db = sqlite3.connect(out)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM ProduceCard WHERE id = ? ORDER BY upgradeCount", ("p_card-1",)).fetchall()
    assert [r["name"] for r in rows] == ["アピールの基本", "アピールの基本+"]
    assert rows[0]["isInitial"] == 1 and rows[1]["isInitial"] == 0  # 布尔写 0 / 1
    assert json.loads(rows[0]["playEffects"]) == [{"produceExamEffectId": "e1"}]  # 列表写 JSON 文本
    assert rows[0]["extra"] is None and rows[1]["extra"] == "新字段"  # 列是各行字段的并集
    assert db.execute("SELECT lastName || firstName FROM Character").fetchone()[0] == "篠澤広"
    assert master_db.commit_of(out) == "abc123"


def test_ensure_rebuilds_only_when_commit_changes(tmp_path, monkeypatch):
    sim = _sim(tmp_path / "catalog.sqlite", commit="c1")
    out = tmp_path / "cache" / "game.db"
    monkeypatch.setattr(master_db, "sim_catalog_path", lambda: sim)
    assert master_db.ensure(out) == out and master_db.commit_of(out) == "c1"
    mtime = out.stat().st_mtime_ns
    assert master_db.ensure(out) == out and out.stat().st_mtime_ns == mtime  # 同一提交：不重建
    sim.unlink()
    _sim(sim, commit="c2")
    assert master_db.ensure(out) == out and master_db.commit_of(out) == "c2"


def test_ensure_without_sim_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(master_db, "sim_catalog_path", lambda: None)
    assert master_db.ensure(tmp_path / "game.db") is None


def test_asset_source_reads_generated_master_db(tmp_path, monkeypatch):
    """默认素材源的主数据走生成的库；游戏数据目录里有没有 KAA 的 game.db 都不看。"""
    from hiro.defaults.assets import PublicAssetSource

    built = master_db.build(_sim(tmp_path / "catalog.sqlite"), tmp_path / "master.db")
    monkeypatch.setattr(master_db, "ensure", lambda out=None: built)
    src = PublicAssetSource(kaa_game_data=tmp_path / "game_data", user_assets=tmp_path / "u", bundled_assets="0")
    db = src.master_db()
    assert db is not None and db.execute("SELECT name FROM ProduceCard LIMIT 1").fetchone()[0] == "アピールの基本"

    monkeypatch.setattr(master_db, "ensure", lambda out=None: None)
    assert PublicAssetSource(kaa_game_data=tmp_path / "game_data", bundled_assets="0").master_db() is None


def test_explicit_master_db_is_used_as_is(tmp_path, monkeypatch):
    """HIRO_MASTER_DB 指向一份现成的库（比如旧的 game.db，没有生成信息表）：原样用，不重建、不覆盖。"""
    mine = tmp_path / "mine.db"
    db = sqlite3.connect(mine)
    db.execute("CREATE TABLE ProduceCard (id, name)")
    db.commit()
    db.close()
    before = mine.read_bytes()
    monkeypatch.setenv("HIRO_MASTER_DB", str(mine))
    monkeypatch.setattr(master_db, "sim_catalog_path", lambda: _sim(tmp_path / "catalog.sqlite"))
    assert master_db.ensure() == mine and mine.read_bytes() == before
    monkeypatch.setattr(master_db, "sim_catalog_path", lambda: None)
    assert master_db.ensure() == mine  # 模拟器主数据没构建也能用
