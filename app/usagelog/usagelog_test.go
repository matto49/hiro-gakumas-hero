package usagelog

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func lines(t *testing.T, home string) []map[string]any {
	t.Helper()
	files, _ := filepath.Glob(filepath.Join(home, "usage", "*.jsonl"))
	var out []map[string]any
	for _, f := range files {
		b, _ := os.ReadFile(f)
		for _, l := range strings.Split(strings.TrimSpace(string(b)), "\n") {
			if l == "" {
				continue
			}
			var m map[string]any
			if err := json.Unmarshal([]byte(l), &m); err != nil {
				t.Fatalf("bad line %q: %v", l, err)
			}
			out = append(out, m)
		}
	}
	return out
}

func TestRecordAppendsLine(t *testing.T) {
	home := t.TempDir()
	in, out := 100, 20
	for i := 0; i < 2; i++ {
		if err := Record(home, Row{Source: "chat", Thread: "t1", Provider: "deepseek", Model: "deepseek-chat", Input: &in, Output: &out, OK: true}); err != nil {
			t.Fatal(err)
		}
	}
	rows := lines(t, home)
	if len(rows) != 2 || rows[0]["id"] == rows[1]["id"] || rows[0]["id"] == "" {
		t.Fatalf("rows %+v", rows)
	}
	r := rows[0]
	if r["v"].(float64) != 1 || r["source"] != "chat" || r["thread"] != "t1" || r["job"] != nil {
		t.Fatalf("row %+v", r)
	}
	if r["model"] != "deepseek-flash" { // 别名归一，同 Python
		t.Fatalf("model %v", r["model"])
	}
	if r["input"].(float64) != 100 || r["cache_read"] != nil || r["cache_write"].(float64) != 0 {
		t.Fatalf("tokens %+v", r)
	}
	for _, k := range []string{"ts", "provider", "output", "cost", "currency", "cost_source", "ok"} {
		if _, ok := r[k]; !ok {
			t.Fatalf("missing %s in %+v", k, r)
		}
	}
}

func TestRecordNoUsageKeepsNulls(t *testing.T) {
	home := t.TempDir()
	_ = Record(home, Row{Source: "chat", Provider: "codex-cli", OK: false})
	r := lines(t, home)[0]
	if r["input"] != nil || r["output"] != nil || r["cache_write"] != nil || r["model"] != nil || r["ok"] != false {
		t.Fatalf("row %+v", r)
	}
}

func TestRecordReturnsErrorNotPanic(t *testing.T) {
	f := filepath.Join(t.TempDir(), "file")
	_ = os.WriteFile(f, []byte("x"), 0o644)
	if err := Record(f, Row{Source: "chat"}); err == nil {
		t.Fatal("want error")
	}
	if err := Record("", Row{Source: "chat"}); err != nil { // 没有 HIRO_HOME：不记，不报错
		t.Fatal(err)
	}
}

func TestThreadInContext(t *testing.T) {
	ctx := WithThread(context.Background(), "t9")
	if ThreadOf(ctx) != "t9" || ThreadOf(context.Background()) != "" {
		t.Fatal("thread")
	}
}

func TestProviderFromURL(t *testing.T) {
	cases := map[string]string{"https://api.deepseek.com": "deepseek", "https://api.moonshot.cn/v1": "kimi",
		"https://openrouter.ai/api/v1": "openrouter", "http://127.0.0.1:8000/v1": "openai-compatible"}
	for u, want := range cases {
		if got := ProviderOf(u); got != want {
			t.Fatalf("%s → %s want %s", u, got, want)
		}
	}
}

func TestOldDeepSeekNamesNormalized(t *testing.T) {
	for _, old := range []string{"deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"} {
		home := t.TempDir()
		_ = Record(home, Row{Source: "chat", Provider: "deepseek", Model: old, OK: true})
		if m := lines(t, home)[0]["model"]; m != "deepseek-flash" {
			t.Errorf("%s → %v", old, m)
		}
	}
}
