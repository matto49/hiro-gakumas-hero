package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestHomeFilesServesOnlySpritesAndTraceFrames(t *testing.T) {
	home := t.TempDir()
	must := func(p, s string) {
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		if err := os.WriteFile(p, []byte(s), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	must(filepath.Join(home, "catalog", "sprites", "a.png"), "sprite")
	must(filepath.Join(home, "work", "traces", "j1", "frames", "00001.jpg"), "frame")
	must(filepath.Join(home, "hiro.db"), "db")
	must(filepath.Join(home, "service.json"), "token")
	h := homeFiles(home, nil)
	cases := map[string]int{
		"/sprites/a.png":              200,
		"/traces/j1/frames/00001.jpg": 200,
		"/hiro.db":                    404,
		"/service.json":               404,
		"/sprites/../service.json":    404,
		"/traces/j1/../../../hiro.db": 404,
		"/traces/j1/steps.jsonl":      404,
	}
	for path, want := range cases {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("GET", "hiro://localhost"+path, nil))
		if rec.Code != want {
			t.Errorf("%s: got %d want %d", path, rec.Code, want)
		}
	}
}

func TestHomeFilesFetchesMissingSprite(t *testing.T) {
	home := t.TempDir()
	var asked []string
	fetch := func(ctx context.Context, asset string) error {
		asked = append(asked, asset)
		p := filepath.Join(home, "catalog", "sprites", asset+".png")
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		return os.WriteFile(p, []byte("x"), 0o644)
	}
	rec := httptest.NewRecorder()
	homeFiles(home, fetch).ServeHTTP(rec, httptest.NewRequest("GET", "hiro://localhost/sprites/img_x.png", nil))
	if rec.Code != http.StatusOK || len(asked) != 1 || asked[0] != "img_x" {
		t.Fatalf("code %d asked %v", rec.Code, asked)
	}
}

func TestHomeFilesServesReviewPages(t *testing.T) {
	home := t.TempDir()
	_ = os.MkdirAll(filepath.Join(home, "jobs", "J1"), 0o755)
	_ = os.WriteFile(filepath.Join(home, "jobs", "J1", "review.html"), []byte("<html>r</html>"), 0o644)
	_ = os.WriteFile(filepath.Join(home, "jobs", "J1", "job.json"), []byte("{}"), 0o644)
	h := homeFiles(home, nil)
	for path, want := range map[string]int{"/review/J1.html": 200, "/review/J2.html": 404, "/jobs/J1/job.json": 404, "/review/..%2Fx.html": 404} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("GET", "hiro://localhost"+path, nil))
		if rec.Code != want {
			t.Errorf("%s: %d want %d", path, rec.Code, want)
		}
	}
}
