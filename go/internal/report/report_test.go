package report

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

func trace(t *testing.T, home, name string, steps ...map[string]any) {
	t.Helper()
	d := filepath.Join(home, "work", "traces", name)
	if err := os.MkdirAll(d, 0o750); err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	for _, s := range steps {
		line, _ := json.Marshal(s)
		b.Write(line)
		b.WriteByte('\n')
	}
	if err := os.WriteFile(filepath.Join(d, "steps.jsonl"), []byte(b.String()), 0o600); err != nil {
		t.Fatal(err)
	}
}

func step(seq int, page, source string, llm int, notes ...string) map[string]any {
	rec := map[string]any{"seq": seq, "observation": map[string]any{"page": map[string]any{"name": page}}, "notes": notes}
	if source != "" {
		calls := make([]any, llm)
		for i := range calls {
			calls[i] = map[string]any{}
		}
		rec["decision"] = map[string]any{"action": map[string]any{"kind": "X"}, "source": source, "llm": calls}
	}
	return rec
}

func TestReportCountsAcrossSegments(t *testing.T) {
	h := t.TempDir()
	t.Setenv("HIRO_HOME", h)
	trace(t, h, "J1", step(1, "HOME", "", 0), step(2, "ACTION_SELECT", "rule", 0), step(3, "HIF_EXAM", "bench", 0))
	trace(t, h, "J1-r1", step(1, "HIF_EXAM", "llm", 2, "recover:page_misjudged: x"), step(2, "RESULT", "rule", 0))
	trace(t, h, "J1-r10", step(1, "HIF_EXAM", "rule", 0))
	trace(t, h, "J10", step(1, "OTHER", "rule", 0)) // 别的任务，不能算进来
	old := examScores
	examScores = func(string) []any { return []any{jsonx.NewObject().Set("label", "final").Set("score", 87121)} }
	defer func() { examScores = old }()
	d, err := Summarize("J1")
	if err != nil {
		t.Fatal(err)
	}
	got, _ := jsonx.Marshal(d)
	want := `{"job":"J1","segments":["J1","J1-r1","J1-r10"],"steps":6,"decisions":5,"by_source":{"rule":3,"bench":1,"llm":1},` +
		`"llm_calls":2,"recoveries":1,"final_page":"HIF_EXAM","exams":[{"label":"final","score":87121}]}`
	if string(got) != want {
		t.Fatalf("\n got %s\nwant %s", got, want)
	}
}

func TestReportWithoutTraceSaysSo(t *testing.T) {
	t.Setenv("HIRO_HOME", t.TempDir())
	if _, err := Summarize("nope"); clierr.As(err).Code != "no_trace" {
		t.Fatalf("%v", err)
	}
	if err := CheckJob("../x"); clierr.As(err).Code != "bad_args" || clierr.As(err).Exit != 2 {
		t.Fatalf("%v", err)
	}
}

func TestScoreLine(t *testing.T) {
	exams := []any{
		jsonx.NewObject().Set("label", "a").Set("score", json.Number("12271")),
		jsonx.NewObject().Set("label", "b").Set("score", nil),
		jsonx.NewObject().Set("label", "c").Set("score", json.Number("368997")),
	}
	if got := ScoreLine(exams); got != "2 场考试：12,271 / 368,997" {
		t.Fatalf("%q", got)
	}
	if ScoreLine(nil) != "" {
		t.Fatal("empty")
	}
}
