"""本机信息库（HIRO_HOME/hiro.db）：每次扫描存一份快照，查询取最新一份，旧的留着看变化。

表：snapshots（一次扫描：类别、时间、来源、是否扫全）+ rows（这次扫到的每一条，JSON）。
类别：account（PLv / AP / ジュエル / プロデューサーID，一行）、idols、supports、memories、affection。

按账号分开（11-25：Mac 在两个游戏账号之间切换，切号后查到的还是上一个账号的库存）：每份快照记上当时的账号
（プロデューサーID，snapshots.account），查询只看当前账号的。当前账号记在 meta 表，读首页数值读到新的
プロデューサーID 时切过去（switch_account）。还没识别过账号时（current_account 为 None）照旧不分账号；
识别过之后，旧的不带账号的快照不再采用（不知道是谁的，重扫）。
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
CREATE TABLE IF NOT EXISTS memory_slices (
  account TEXT NOT NULL,
  slice TEXT NOT NULL,
  idx INTEGER NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_slices_key ON memory_slices(account, slice);
CREATE TABLE IF NOT EXISTS slice_coverage (
  account TEXT NOT NULL,
  slice TEXT NOT NULL,
  scanned_at TEXT NOT NULL,
  full_at TEXT,
  mode TEXT NOT NULL,
  complete INTEGER,
  context TEXT,
  read INTEGER NOT NULL,
  added INTEGER NOT NULL,
  PRIMARY KEY (account, slice)
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(snapshots)")}
        if "account" not in cols:  # 旧库：补列，原有快照的账号是 NULL（不知道是谁的）
            with self.db:
                self.db.execute("ALTER TABLE snapshots ADD COLUMN account TEXT")

    def current_account(self) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key='account'").fetchone()
        return row[0] if row else None

    def switch_account(self, producer_id: str) -> bool:
        """当前账号切到 producer_id；返回是否真的换了账号。"""
        changed = producer_id != self.current_account()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('account', ?)", (producer_id,))
        return changed

    def _scope(self) -> tuple[str, tuple]:
        """查询条件：识别过账号就只看它的快照；没识别过不限。"""
        acc = self.current_account()
        return ("", ()) if acc is None else (" AND account=?", (acc,))

    def save_snapshot(
        self,
        kind: str,
        rows: list[dict],
        source: str,
        *,
        scanned_at: str | None = None,
        complete: bool | None = None,
        raw: dict | list | None = None,
    ) -> int:
        """raw：扫描工具的原始输出（编成方案要用原格式的库存），原样另存一份。"""
        if kind not in KINDS:
            raise ValueError(f"未知类别 {kind}")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO snapshots(kind, scanned_at, source, complete, count, account) VALUES (?,?,?,?,?,?)",
                (
                    kind,
                    scanned_at or now(),
                    source,
                    None if complete is None else int(complete),
                    len(rows),
                    self.current_account(),
                ),
            )
            sid = cur.lastrowid
            self.db.executemany(
                "INSERT INTO rows(snapshot_id, idx, data) VALUES (?,?,?)",
                [(sid, i, json.dumps(r, ensure_ascii=False)) for i, r in enumerate(rows)],
            )
            if raw is not None:
                self.db.execute(
                    "INSERT INTO raw(snapshot_id, data) VALUES (?,?)", (sid, json.dumps(raw, ensure_ascii=False))
                )
        return sid

    def _meta(self, row) -> dict:
        sid, scanned_at, source, complete, count = row
        return {
            "snapshot": sid,
            "scanned_at": scanned_at,
            "source": source,
            "complete": None if complete is None else bool(complete),
            "count": count,
        }

    def latest(self, kind: str) -> tuple[dict | None, list[dict]]:
        where, args = self._scope()
        row = self.db.execute(
            f"SELECT id, scanned_at, source, complete, count FROM snapshots WHERE kind=?{where} ORDER BY id DESC LIMIT 1",
            (kind, *args),
        ).fetchone()
        if row is None:
            return None, []
        rows = [
            json.loads(d)
            for (d,) in self.db.execute("SELECT data FROM rows WHERE snapshot_id=? ORDER BY idx", (row[0],))
        ]
        return self._meta(row), rows

    # ------------------------------------------------------------ 回忆分片（设计 5.1 / D16 / D18）
    def _acc(self) -> str:
        return self.current_account() or ""

    def slice_entries(self, slice_key: str) -> list[dict]:
        return [
            json.loads(d)
            for (d,) in self.db.execute(
                "SELECT data FROM memory_slices WHERE account=? AND slice=? ORDER BY idx", (self._acc(), slice_key)
            )
        ]

    def save_slice(
        self,
        slice_key: str,
        entries: list[dict],
        *,
        mode: str,
        complete: bool,
        context: dict | None = None,
        scanned_at: str | None = None,
    ) -> dict:
        """存一片的扫描结果（扫描原始条目）。mode=full 且 complete：整片替换成这次读到的（卖掉、删掉的不在了）；
        其余（增量，或全读中途停了）：只追加资料库里还没有的（按 memory_sig.KnownSet 认）。返回 {read, added}。"""
        from hiro.produce.memory_sig import KnownSet

        acc, at = self._acc(), scanned_at or now()
        old = self.slice_entries(slice_key)
        replace = mode == "full" and complete
        if replace:
            known = KnownSet(old)
            added = sum(not known.take(e) for e in entries)
            keep = [dict(e, slice=slice_key) for e in entries]
        else:
            known = KnownSet(old)
            new = [dict(e, slice=slice_key) for e in entries if not known.take(e)]
            added, keep = len(new), old + new
        prev = self.slice_coverage().get(slice_key) or {}
        with self.db:
            self.db.execute("DELETE FROM memory_slices WHERE account=? AND slice=?", (acc, slice_key))
            self.db.executemany(
                "INSERT INTO memory_slices(account, slice, idx, data) VALUES (?,?,?,?)",
                [(acc, slice_key, i, json.dumps(e, ensure_ascii=False)) for i, e in enumerate(keep)],
            )
            self.db.execute(
                "INSERT OR REPLACE INTO slice_coverage(account, slice, scanned_at, full_at, mode, complete, context, read, added)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (acc, slice_key, at, at if replace else prev.get("full_at"), mode, int(complete),
                 json.dumps(context or {}, ensure_ascii=False), len(entries), added),
            )  # fmt: skip
        return {"read": len(entries), "added": added}

    def slice_coverage(self) -> dict[str, dict]:
        """当前账号每一片的最近一次扫描：{片: {scanned_at, full_at（最近一次完整全读）, mode, complete, context, read, added, count}}。"""
        out = {}
        for sl, at, full_at, mode, complete, ctx, read, added in self.db.execute(
            "SELECT slice, scanned_at, full_at, mode, complete, context, read, added FROM slice_coverage WHERE account=?",
            (self._acc(),),
        ):
            count = self.db.execute(
                "SELECT COUNT(*) FROM memory_slices WHERE account=? AND slice=?", (self._acc(), sl)
            ).fetchone()[0]
            out[sl] = {"scanned_at": at, "full_at": full_at, "mode": mode, "complete": bool(complete),
                       "context": json.loads(ctx or "{}"), "read": read, "added": added, "count": count}  # fmt: skip
        return out

    def memories_for_plan(self, plan: str | None) -> dict | None:
        """出编成方案用的回忆库存（扫描原始格式 {entries, complete, scanned_at, source}）。
        D16：这局要的片（slices_for_plan）都完整全读过，就用片数据合并（每条带 slice）；否则整套用旧的整表扫描快照，不混用。"""
        from hiro.state.memory_slices import slices_for_plan

        need = slices_for_plan(plan)
        cov = self.slice_coverage()
        if all((cov.get(k) or {}).get("full_at") for k in need):
            entries = [e for k in need for e in self.slice_entries(k)]
            return {"entries": entries, "complete": True, "source": "slices",
                    "scanned_at": min(cov[k]["scanned_at"] for k in need), "slices": need}  # fmt: skip
        return self.latest_raw("memories")

    def latest_raw(self, kind: str):
        """最新一次带原始输出的快照的原始输出；没有返回 None。"""
        where, args = self._scope()
        row = self.db.execute(
            "SELECT r.data FROM raw r JOIN snapshots s ON s.id = r.snapshot_id"
            f" WHERE s.kind=?{where.replace('account', 's.account')} ORDER BY s.id DESC LIMIT 1",
            (kind, *args),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, kind: str) -> list[dict]:
        return [
            self._meta(r)
            for r in self.db.execute(
                f"SELECT id, scanned_at, source, complete, count FROM snapshots WHERE kind=?{self._scope()[0]} ORDER BY id DESC",
                (kind, *self._scope()[1]),
            )
        ]


def open_store() -> Store:
    from hiro.cli.home import ensure_home

    return Store(ensure_home() / "hiro.db")
