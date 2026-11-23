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
