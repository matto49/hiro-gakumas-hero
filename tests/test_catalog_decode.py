"""主数据行解码：sqlite3.Row 直接迭代得到的是值不是列名，_decode 必须按 row.keys() 取（11-23 ruff SIM118 自动修复改坏过）。"""

import sqlite3

import pytest

pytestmark = pytest.mark.synthetic


def test_by_id_decodes_columns_and_json():
    from hiro.state.catalog import Catalog

    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE "IdolCard" (id TEXT, name TEXT, extra TEXT)')
    db.execute('INSERT INTO "IdolCard" VALUES (?, ?, ?)', ("i_card-shro-3-007", "Campus mode!!", '{"a": 1}'))
    rows = Catalog(db).by_id("IdolCard", "i_card-shro-3-007")
    assert rows == [{"id": "i_card-shro-3-007", "name": "Campus mode!!", "extra": {"a": 1}}]


def test_rows_merged_adds_newer_cards_from_sim_master(monkeypatch):
    """KAA game.db 落后（6 月版 140 张偶像卡）：整表读取要补上模拟器主数据里有、game.db 没有的行，同 id 以 game.db 为准。"""
    from hiro.state import catalog as m

    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE "IdolCard" (id TEXT, name TEXT)')
    db.execute('INSERT INTO "IdolCard" VALUES (?, ?)', ("i_card-fktn-3-000", "世界一可愛い私"))

    class Sim:
        def rows(self, table):
            return iter(
                [
                    {"id": "i_card-fktn-3-000", "name": "（模拟器版）"},
                    {"id": "i_card-fktn-3-019", "name": "GO MY WAY!!"},
                ]
            )

    monkeypatch.setattr(m, "_sim_catalog", lambda: Sim())
    got = m.Catalog(db).rows_merged("IdolCard")
    assert [(r["id"], r["name"]) for r in got] == [
        ("i_card-fktn-3-000", "世界一可愛い私"),
        ("i_card-fktn-3-019", "GO MY WAY!!"),
    ]
