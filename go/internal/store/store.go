// Package store 是本机信息库 HIRO_HOME/hiro.db：每次扫描存一份快照，查询取最新一份，旧的留着看变化。
// 表结构同 Python 版 hiro.cli.store（两边读写同一个文件）。
//
// 表：snapshots（一次扫描：类别、时间、来源、是否扫全）+ rows（这次扫到的每一条，JSON）+ raw（扫描工具的原始输出）。
package store

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite" // SQLite 驱动（纯 Go）

	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// Kinds 是信息的类别。
var Kinds = []string{"account", "idols", "supports", "memories", "affection", "game_settings"}

// Schema 是建表语句（同 Python _SCHEMA）。
const Schema = `
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
`

// Store 是打开的信息库。用完 Close。
type Store struct{ db *sql.DB }

// Meta 是一次扫描的概况。
type Meta struct {
	Snapshot  int64  `json:"snapshot"`
	ScannedAt string `json:"scanned_at"`
	Source    string `json:"source"`
	Complete  *bool  `json:"complete"`
	Count     int    `json:"count"`
}

// Snapshot 是存一次扫描时的附加信息。ScannedAt 空就用现在；Raw 是扫描工具的原始输出（编成方案要原格式的库存）。
type Snapshot struct {
	Source    string
	ScannedAt string
	Complete  *bool
	Raw       any
}

// Now 是信息库用的时间格式（本地时间）。
func Now() string { return time.Now().Format("2006-01-02 15:04:05") }

// Open 打开（必要时新建）一个信息库文件。
func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(Schema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("打开信息库 %s：%w", path, err)
	}
	return &Store{db: db}, nil
}

// OpenHome 打开 HIRO_HOME/hiro.db。
func OpenHome() (*Store, error) {
	h, err := home.Ensure()
	if err != nil {
		return nil, err
	}
	return Open(filepath.Join(h, "hiro.db"))
}

// Close 关掉信息库。
func (s *Store) Close() error { return s.db.Close() }

func knownKind(kind string) bool {
	for _, k := range Kinds {
		if k == kind {
			return true
		}
	}
	return false
}

// SaveSnapshot 存一次扫描，返回快照编号。
func (s *Store) SaveSnapshot(kind string, rows []any, snap Snapshot) (int64, error) {
	if !knownKind(kind) {
		return 0, fmt.Errorf("未知类别 %s", kind)
	}
	scanned := snap.ScannedAt
	if scanned == "" {
		scanned = Now()
	}
	var complete any
	if snap.Complete != nil {
		complete = 0
		if *snap.Complete {
			complete = 1
		}
	}
	tx, err := s.db.Begin()
	if err != nil {
		return 0, err
	}
	defer func() { _ = tx.Rollback() }()
	res, err := tx.Exec("INSERT INTO snapshots(kind, scanned_at, source, complete, count) VALUES (?,?,?,?,?)",
		kind, scanned, snap.Source, complete, len(rows))
	if err != nil {
		return 0, err
	}
	sid, err := res.LastInsertId()
	if err != nil {
		return 0, err
	}
	for i, r := range rows {
		data, err := jsonx.Marshal(r)
		if err != nil {
			return 0, err
		}
		if _, err := tx.Exec("INSERT INTO rows(snapshot_id, idx, data) VALUES (?,?,?)", sid, i, string(data)); err != nil {
			return 0, err
		}
	}
	if snap.Raw != nil {
		data, err := jsonx.Marshal(snap.Raw)
		if err != nil {
			return 0, err
		}
		if _, err := tx.Exec("INSERT INTO raw(snapshot_id, data) VALUES (?,?)", sid, string(data)); err != nil {
			return 0, err
		}
	}
	return sid, tx.Commit()
}

func scanMeta(sc interface{ Scan(...any) error }) (*Meta, error) {
	var m Meta
	var complete sql.NullInt64
	if err := sc.Scan(&m.Snapshot, &m.ScannedAt, &m.Source, &complete, &m.Count); err != nil {
		return nil, err
	}
	if complete.Valid {
		b := complete.Int64 != 0
		m.Complete = &b
	}
	return &m, nil
}

// Latest 取这一类最新一次扫描：概况和每一条（对象保序）。没扫描过返回 nil、空列表。
func (s *Store) Latest(kind string) (*Meta, []any, error) {
	row := s.db.QueryRow("SELECT id, scanned_at, source, complete, count FROM snapshots WHERE kind=? ORDER BY id DESC LIMIT 1", kind)
	meta, err := scanMeta(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, []any{}, nil
	}
	if err != nil {
		return nil, nil, err
	}
	rs, err := s.db.Query("SELECT data FROM rows WHERE snapshot_id=? ORDER BY idx", meta.Snapshot)
	if err != nil {
		return nil, nil, err
	}
	defer rs.Close()
	out := []any{}
	for rs.Next() {
		var data string
		if err := rs.Scan(&data); err != nil {
			return nil, nil, err
		}
		v, err := jsonx.Decode([]byte(data))
		if err != nil {
			return nil, nil, fmt.Errorf("信息库里的一条记录坏了：%w", err)
		}
		out = append(out, v)
	}
	return meta, out, rs.Err()
}

// LatestRaw 取这一类最新一次带原始输出的快照的原始输出；没有返回 nil。
func (s *Store) LatestRaw(kind string) (any, error) {
	var data string
	err := s.db.QueryRow("SELECT r.data FROM raw r JOIN snapshots s ON s.id = r.snapshot_id WHERE s.kind=? ORDER BY s.id DESC LIMIT 1", kind).Scan(&data)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return jsonx.Decode([]byte(data))
}

// History 这一类的全部扫描（新的在前）。
func (s *Store) History(kind string) ([]Meta, error) {
	rs, err := s.db.Query("SELECT id, scanned_at, source, complete, count FROM snapshots WHERE kind=? ORDER BY id DESC", kind)
	if err != nil {
		return nil, err
	}
	defer rs.Close()
	out := []Meta{}
	for rs.Next() {
		m, err := scanMeta(rs)
		if err != nil {
			return nil, err
		}
		out = append(out, *m)
	}
	return out, rs.Err()
}
