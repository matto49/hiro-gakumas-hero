package pending

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

func setup(t *testing.T) string {
	t.Helper()
	h := t.TempDir()
	t.Setenv("HIRO_HOME", h)
	return h
}

func code(err error) string {
	var e *clierr.Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ""
}

func TestCreateTakeOnce(t *testing.T) {
	setup(t)
	args := jsonx.NewObject().Set("times", 2)
	rec, err := Create("test poke", args, "戳 2 下")
	if err != nil {
		t.Fatal(err)
	}
	if len(rec.ID) != 12 || rec.Command != "test poke" || rec.Summary != "戳 2 下" || rec.ExpiresAt-rec.CreatedAt != TTL.Seconds() {
		t.Fatalf("rec = %+v", rec)
	}
	got, err := Take(rec.ID)
	if err != nil {
		t.Fatal(err)
	}
	if n, _ := jsonx.Int(got.Args, "times"); n != 2 {
		t.Fatalf("args = %v", got.Args)
	}
	if _, err := Take(rec.ID); code(err) != "pending_unknown" {
		t.Fatalf("second take: %v", err)
	}
}

func TestExpiredIsRefusedAndRemoved(t *testing.T) {
	setup(t)
	rec, _ := Create("x", jsonx.NewObject(), "s")
	now = func() time.Time { return time.Now().Add(time.Hour) }
	defer func() { now = time.Now }()
	if _, err := Take(rec.ID); code(err) != "pending_expired" {
		t.Fatalf("got %v", err)
	}
	if _, err := Take(rec.ID); code(err) != "pending_unknown" {
		t.Fatalf("expired record should be gone: %v", err)
	}
}

func TestBadIDIsRejected(t *testing.T) {
	setup(t)
	for _, id := range []string{"../../etc/passwd", "", "ABCDEF123456", "abc"} {
		if _, err := Take(id); code(err) != "pending_unknown" {
			t.Fatalf("%q: %v", id, err)
		}
		if Cancel(id) {
			t.Fatalf("cancel %q should be false", id)
		}
	}
}

func TestCancelAndListing(t *testing.T) {
	setup(t)
	a, _ := Create("a", jsonx.NewObject(), "A")
	b, _ := Create("b", jsonx.NewObject(), "B")
	list, err := Listing()
	if err != nil || len(list) != 2 {
		t.Fatalf("list = %v %v", list, err)
	}
	if !Cancel(a.ID) || Cancel(a.ID) {
		t.Fatal("cancel once")
	}
	list, _ = Listing()
	if len(list) != 1 || list[0].ID != b.ID {
		t.Fatalf("after cancel %v", list)
	}
}

// Python 版写的待确认，Go 版要能确认（迁移期间两边共用 HIRO_HOME/pending）。
func TestReadsPythonRecord(t *testing.T) {
	h := setup(t)
	dir := filepath.Join(h, "pending")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	exp := float64(time.Now().Unix() + 600)
	raw := `{"id": "0123456789ab", "command": "run start", "args": {"kind": "hif", "idol": null, "support_set": 4}, "summary": "开一局 H.I.F 选拔", "created_at": 1.5, "expires_at": ` +
		json.Number(jsonNum(exp)).String() + `}`
	if err := os.WriteFile(filepath.Join(dir, "0123456789ab.json"), []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	rec, err := Take("0123456789ab")
	if err != nil {
		t.Fatal(err)
	}
	if rec.Command != "run start" || jsonx.Str(rec.Args, "kind") != "hif" || !jsonx.IsNull(rec.Args, "idol") {
		t.Fatalf("rec = %+v", rec)
	}
	out, _ := jsonx.Marshal(rec.Args)
	if string(out) != `{"kind":"hif","idol":null,"support_set":4}` {
		t.Fatalf("args order/number lost: %s", out)
	}
}

func jsonNum(f float64) string { b, _ := json.Marshal(f); return string(b) }
