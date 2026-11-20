package store

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

func obj(kv ...any) *jsonx.Object {
	o := jsonx.NewObject()
	for i := 0; i+1 < len(kv); i += 2 {
		o.Set(kv[i].(string), kv[i+1])
	}
	return o
}

func TestLatestSnapshotWins(t *testing.T) {
	s, err := Open(filepath.Join(t.TempDir(), "hiro.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if _, err := s.SaveSnapshot("supports", []any{obj("id", "a")}, Snapshot{Source: "list"}); err != nil {
		t.Fatal(err)
	}
	yes := true
	if _, err := s.SaveSnapshot("supports", []any{obj("id", "b"), obj("id", "c", "n", 1)}, Snapshot{Source: "list", Complete: &yes, Raw: obj("x", 1)}); err != nil {
		t.Fatal(err)
	}
	meta, rows, err := s.Latest("supports")
	if err != nil {
		t.Fatal(err)
	}
	if meta == nil || meta.Count != 2 || meta.ScannedAt == "" || meta.Complete == nil || !*meta.Complete || meta.Source != "list" {
		t.Fatalf("meta %+v", meta)
	}
	if got, _ := jsonx.Marshal(rows); string(got) != `[{"id":"b"},{"id":"c","n":1}]` {
		t.Fatalf("rows %s", got)
	}
	hist, err := s.History("supports")
	if err != nil || len(hist) != 2 || hist[0].Snapshot <= hist[1].Snapshot {
		t.Fatalf("history %+v %v", hist, err)
	}
	raw, err := s.LatestRaw("supports")
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := jsonx.Marshal(raw); string(got) != `{"x":1}` {
		t.Fatalf("raw %s", got)
	}
	if m, rows, _ := s.Latest("idols"); m != nil || len(rows) != 0 {
		t.Fatal("no idols yet")
	}
	if r, _ := s.LatestRaw("memories"); r != nil {
		t.Fatal("no raw memories")
	}
	if _, err := s.SaveSnapshot("nope", nil, Snapshot{Source: "x"}); err == nil {
		t.Fatal("unknown kind should be refused")
	}
	if got, _ := jsonx.Marshal(meta); string(got) != `{"snapshot":2,"scanned_at":"`+meta.ScannedAt+`","source":"list","complete":true,"count":2}` {
		t.Fatalf("meta json %s", got)
	}
}

// Python 版写的 hiro.db，Go 版要能读（迁移期间两边共用一个信息库）。
func TestReadsPythonDatabase(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("没有 python3")
	}
	db := filepath.Join(t.TempDir(), "hiro.db")
	script := `
import sqlite3, json, sys
db = sqlite3.connect(sys.argv[1])
db.executescript(open(sys.argv[2]).read())
cur = db.execute("INSERT INTO snapshots(kind, scanned_at, source, complete, count) VALUES ('memories','2026-11-16 18:44:59','scan:x',1,1)")
db.execute("INSERT INTO rows(snapshot_id, idx, data) VALUES (?,0,?)", (cur.lastrowid, json.dumps({"card": "ワクワク", "stats": {"Vo": 30}}, ensure_ascii=False)))
db.commit()
`
	schema := filepath.Join(t.TempDir(), "schema.sql")
	if err := writeFile(schema, Schema); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(py, "-c", script, db, schema).CombinedOutput(); err != nil {
		t.Fatalf("python: %v %s", err, out)
	}
	s, err := Open(db)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	meta, rows, err := s.Latest("memories")
	if err != nil || meta == nil || meta.ScannedAt != "2026-11-16 18:44:59" || len(rows) != 1 {
		t.Fatalf("meta %+v rows %v err %v", meta, rows, err)
	}
	if got, _ := jsonx.Marshal(rows[0]); string(got) != `{"card":"ワクワク","stats":{"Vo":30}}` {
		t.Fatalf("row %s", got)
	}
}

func writeFile(path, s string) error { return os.WriteFile(path, []byte(s), 0o600) }
