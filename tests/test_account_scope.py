"""信息库按账号分开（11-25：Mac 在账号 2 / 3 之间切换，切号后信息库还是上一个账号的库存）。

快照记上当时的账号（プロデューサーID）；查询只看当前账号的；读到新的プロデューサーID 就切过去。
"""

import sqlite3
from pathlib import Path

from hiro.cli.store import Store


def _sup(n):
    return [{"support_card_id": f"s{i}"} for i in range(n)]


def test_no_account_keeps_old_behavior(tmp_path):
    st = Store(tmp_path / "hiro.db")
    st.save_snapshot("supports", _sup(2), source="t")
    meta, rows = st.latest("supports")
    assert meta["count"] == 2 and len(rows) == 2
    assert st.current_account() is None


def test_snapshots_are_scoped_to_current_account(tmp_path):
    st = Store(tmp_path / "hiro.db")
    st.save_snapshot("supports", _sup(1), source="legacy")  # 识别账号之前的数据：不知道是谁的
    st.switch_account("VRRL6GVW")
    assert st.latest("supports") == (None, [])  # 切到账号后旧数据不再采用（要重扫）
    st.save_snapshot("supports", _sup(3), source="t")
    st.switch_account("LHLB56ZP")
    assert st.latest("supports") == (None, [])
    st.save_snapshot("supports", _sup(5), source="t")
    assert st.latest("supports")[0]["count"] == 5
    st.switch_account("VRRL6GVW")  # 切回来：还是它自己的那份
    assert st.latest("supports")[0]["count"] == 3
    assert [h["count"] for h in st.history("supports")] == [3]
    assert st.latest_raw("supports") is None


def test_raw_is_scoped(tmp_path):
    st = Store(tmp_path / "hiro.db")
    st.switch_account("A")
    st.save_snapshot("memories", [], source="t", raw={"who": "A"})
    st.switch_account("B")
    assert st.latest_raw("memories") is None
    st.save_snapshot("memories", [], source="t", raw={"who": "B"})
    assert st.latest_raw("memories") == {"who": "B"}


def test_current_account_persists(tmp_path):
    Store(tmp_path / "hiro.db").switch_account("VRRL6GVW")
    assert Store(tmp_path / "hiro.db").current_account() == "VRRL6GVW"


def test_old_database_is_migrated(tmp_path):
    """旧库没有 account 列：打开时补上，原有快照当作「不知道是谁的」。"""
    p: Path = tmp_path / "hiro.db"
    db = sqlite3.connect(p)
    db.executescript(
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, scanned_at TEXT NOT NULL,"
        " source TEXT NOT NULL, complete INTEGER, count INTEGER NOT NULL);"
        "CREATE TABLE rows (snapshot_id INTEGER NOT NULL, idx INTEGER NOT NULL, data TEXT NOT NULL,"
        " PRIMARY KEY (snapshot_id, idx));"
        "INSERT INTO snapshots(kind, scanned_at, source, complete, count) VALUES ('idols', '2026-11-23', 'old', 1, 0);"
    )
    db.commit()
    db.close()
    st = Store(p)
    assert st.latest("idols")[0]["source"] == "old"
    st.switch_account("A")
    assert st.latest("idols") == (None, [])
