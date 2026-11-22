"""本机信息库（HIRO_HOME/hiro.db）：每次扫描存一份快照，查询取最新一份，旧的留着看变化。

表：snapshots（一次扫描：类别、时间、来源、是否扫全）+ rows（这次扫到的每一条，JSON）。
类别：account（PLv / AP / ジュエル，一行）、idols、supports、memories、affection。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

KINDS = ("account", "idols", "supports", "memories", "affection", "game_settings", "formations")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  scanned_at TEXT NOT NULL,
  source TEXT NOT NULL,
  complete INTEGER,
  count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rows (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  idx INTEGER NOT NULL,
  data TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, idx)
);
CREATE INDEX IF NOT EXISTS snapshots_kind ON snapshots(kind, id);
CREATE TABLE IF NOT EXISTS raw (
  snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id),
  data TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)

    def save_snapshot(self, kind: str, rows: list[dict], source: str, *, scanned_at: str | None = None,
                      complete: bool | None = None, raw: dict | list | None = None) -> int:
        """raw：扫描工具的原始输出（编成方案要用原格式的库存），原样另存一份。"""
        if kind not in KINDS:
            raise ValueError(f"未知类别 {kind}")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO snapshots(kind, scanned_at, source, complete, count) VALUES (?,?,?,?,?)",
                (kind, scanned_at or now(), source, None if complete is None else int(complete), len(rows)))
            sid = cur.lastrowid
            self.db.executemany("INSERT INTO rows(snapshot_id, idx, data) VALUES (?,?,?)",
                                [(sid, i, json.dumps(r, ensure_ascii=False)) for i, r in enumerate(rows)])
            if raw is not None:
                self.db.execute("INSERT INTO raw(snapshot_id, data) VALUES (?,?)", (sid, json.dumps(raw, ensure_ascii=False)))
        return sid

    def _meta(self, row) -> dict:
        sid, scanned_at, source, complete, count = row
        return {"snapshot": sid, "scanned_at": scanned_at, "source": source,
                "complete": None if complete is None else bool(complete), "count": count}

    def latest(self, kind: str) -> tuple[dict | None, list[dict]]:
        row = self.db.execute(
            "SELECT id, scanned_at, source, complete, count FROM snapshots WHERE kind=? ORDER BY id DESC LIMIT 1",
            (kind,)).fetchone()
        if row is None:
            return None, []
        rows = [json.loads(d) for (d,) in self.db.execute(
            "SELECT data FROM rows WHERE snapshot_id=? ORDER BY idx", (row[0],))]
        return self._meta(row), rows

    def latest_raw(self, kind: str):
        """最新一次带原始输出的快照的原始输出；没有返回 None。"""
        row = self.db.execute(
            "SELECT r.data FROM raw r JOIN snapshots s ON s.id = r.snapshot_id WHERE s.kind=? ORDER BY s.id DESC LIMIT 1",
            (kind,)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, kind: str) -> list[dict]:
        return [self._meta(r) for r in self.db.execute(
            "SELECT id, scanned_at, source, complete, count FROM snapshots WHERE kind=? ORDER BY id DESC", (kind,))]


def open_store() -> Store:
    from hiro.cli.home import ensure_home

    return Store(ensure_home() / "hiro.db")
