"""主数据库（hiro.master / hiro.state.catalog / AssetSource.master_db 读的那份 SQLite）：从模拟器主数据生成。

原来用 KAA 公开数据（kaa-game-data）的 game.db；去 KAA 第 4 批改成自己从模拟器主数据生成同结构的库：
模拟器主数据是 gakumasu-diff 固定提交的快照（vendor/gakumas-player/data/master/<提交>/catalog.sqlite，安装脚本第 6 步构建），
每张表的记录是 zlib 压缩的 JSON；这里展开成「每张表一列一个字段」，列表 / 对象写 JSON 文本、布尔写 0 / 1，和 game.db 一样，
hiro.master 里的 SQL 原样能用。两份数据的字段逐表一致（2026-11-25 核对：ProduceCard 43 列、IdolCard 51 列 ……），模拟器的更新。

    python -m hiro.state.master_db            # 按当前模拟器主数据生成 / 更新（安装脚本调；提交没变就不动）

生成到仓库的 cache/master/game.db，记下来源提交；模拟器主数据换了提交就重建。
设了 HIRO_MASTER_DB 且文件存在时直接用那份库（自己准备的 / 旧的 game.db），不重建、不覆盖。
"""

from __future__ import annotations

import json
import os
import sqlite3
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT = ROOT / "cache" / "master" / "game.db"
META = "hiro_master"  # 生成信息表：source_commit


def default_path() -> Path:
    return Path(os.environ.get("HIRO_MASTER_DB") or DEFAULT)


def sim_catalog_path() -> Path | None:
    """当前模拟器主数据的 catalog.sqlite（data/master/current.json 指向的）；没构建返回 None。"""
    try:
        from hiro.sim import ROOT as engine
    except Exception:  # noqa: BLE001  引擎目录不在
        return None
    cur = engine / "data" / "master" / "current.json"
    if not cur.is_file():
        return None
    p = engine / "data" / "master" / json.loads(cur.read_text(encoding="utf-8"))["database"]
    return p if p.is_file() else None


def _source_commit(sim: sqlite3.Connection) -> str:
    return json.loads(sim.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])["commit"]


def _cell(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


def build(sim_path: str | Path, out: str | Path) -> Path:
    """catalog.sqlite → game.db 同结构的库（先写临时文件再换上，生成到一半不留坏库）。返回 out。"""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    sim = sqlite3.connect(f"file:{Path(sim_path).resolve()}?mode=ro", uri=True)
    db = sqlite3.connect(tmp)
    try:
        tables: dict[str, list[dict]] = {}
        for table, payload in sim.execute("SELECT table_name, payload FROM records ORDER BY table_name, ordinal"):
            tables.setdefault(table, []).append(json.loads(zlib.decompress(payload)))
        for table, rows in tables.items():
            cols = list(dict.fromkeys(k for r in rows for k in r))
            q = ", ".join(f'"{c}"' for c in cols)
            db.execute(f'CREATE TABLE "{table}" ({q})')
            if "id" in cols:
                db.execute(f'CREATE INDEX "{table}_id" ON "{table}" (id)')
            db.executemany(f'INSERT INTO "{table}" ({q}) VALUES ({", ".join("?" * len(cols))})',
                           [[_cell(r.get(c)) for c in cols] for r in rows])  # fmt: skip
        db.execute(f"CREATE TABLE {META} (key TEXT PRIMARY KEY, value TEXT)")
        db.execute(f"INSERT INTO {META} VALUES ('source_commit', ?)", (_source_commit(sim),))
        db.commit()
    finally:
        db.close()
        sim.close()
    tmp.replace(out)
    return out


def commit_of(path: str | Path) -> str | None:
    """库是从哪个模拟器主数据提交生成的；不是生成的库（旧 game.db）/ 打不开返回 None。"""
    try:
        db = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        try:
            row = db.execute(f"SELECT value FROM {META} WHERE key='source_commit'").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def ensure(out: str | Path | None = None) -> Path | None:
    """主数据库可用就返回路径：模拟器主数据换了提交（或还没生成）就重建；模拟器主数据没构建返回 None。
    没传 out 且 HIRO_MASTER_DB 指向已有文件：原样返回（使用者自己指定的库不动）。"""
    if out is None and (env := os.environ.get("HIRO_MASTER_DB")) and Path(env).is_file():
        return Path(env)
    out = Path(out) if out else default_path()
    sim = sim_catalog_path()
    if sim is None:
        return None
    s = sqlite3.connect(f"file:{sim.resolve()}?mode=ro", uri=True)
    try:
        want = _source_commit(s)
    finally:
        s.close()
    if out.is_file() and commit_of(out) == want:
        return out
    return build(sim, out)


def main() -> None:
    p = ensure()
    if p is None:
        raise SystemExit("模拟器主数据还没构建：先跑 python vendor/gakumas-player/scripts/fetch_master.py")
    print(f"主数据库：{p}（来源提交 {commit_of(p)}）")


if __name__ == "__main__":
    main()
