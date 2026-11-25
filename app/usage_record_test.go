package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/shell"
	"github.com/matto49/hiro-gakumas-hero/app/usagelog"
)

func ledgerLines(t *testing.T, home string) []map[string]any {
	t.Helper()
	files, _ := filepath.Glob(filepath.Join(home, "usage", "*.jsonl"))
	var out []map[string]any
	for _, f := range files {
		b, _ := os.ReadFile(f)
		for _, l := range strings.Split(strings.TrimSpace(string(b)), "\n") {
			var m map[string]any
			if json.Unmarshal([]byte(l), &m) == nil {
				out = append(out, m)
			}
		}
	}
	return out
}

func TestOpenAICompatRecordsUsage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.ReadAll(r.Body)
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"你好"}}],
			"usage":{"prompt_tokens":1200,"completion_tokens":30,"prompt_tokens_details":{"cached_tokens":1000},"prompt_cache_hit_tokens":1000}}`)
	}))
	defer srv.Close()
	home := t.TempDir()
	o := OpenAICompat{BaseURL: srv.URL, Model: "deepseek-chat", Key: "sk-secret", Client: http.DefaultClient, Home: home, Provider: "deepseek"}
	ctx := usagelog.WithThread(context.Background(), "t1")
	if _, err := o.Chat(ctx, []chatMessage{{Role: "user", Content: "hi"}}, nil); err != nil {
		t.Fatal(err)
	}
	rows := ledgerLines(t, home)
	if len(rows) != 1 {
		t.Fatalf("rows %+v", rows)
	}
	r := rows[0]
	if r["source"] != "chat" || r["thread"] != "t1" || r["provider"] != "deepseek" || r["model"] != "deepseek-flash" || r["ok"] != true {
		t.Fatalf("row %+v", r)
	}
	if r["input"].(float64) != 200 || r["cache_read"].(float64) != 1000 || r["output"].(float64) != 30 {
		t.Fatalf("tokens %+v", r)
	}
	b, _ := os.ReadFile(filepath.Join(home, "usage", filepath.Base(mustGlob(t, home))))
	if strings.Contains(string(b), "sk-secret") || strings.Contains(string(b), "你好") || strings.Contains(string(b), "hi\"") {
		t.Fatalf("ledger leaks: %s", b)
	}
}

func TestOpenAICompatDeepSeekCacheHitFallback(t *testing.T) {
	// 只给 prompt_cache_hit_tokens（DeepSeek 自己的字段）也要拆出缓存命中
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"x"}}],
			"usage":{"prompt_tokens":500,"completion_tokens":5,"prompt_cache_hit_tokens":400}}`)
	}))
	defer srv.Close()
	home := t.TempDir()
	o := OpenAICompat{BaseURL: srv.URL, Model: "deepseek-flash", Key: "k", Client: http.DefaultClient, Home: home, Provider: "deepseek"}
	_, _ = o.Chat(context.Background(), nil, nil)
	r := ledgerLines(t, home)[0]
	if r["input"].(float64) != 100 || r["cache_read"].(float64) != 400 || r["thread"] != nil {
		t.Fatalf("row %+v", r)
	}
}

func TestOpenAICompatFailedCallRecorded(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(500) }))
	defer srv.Close()
	home := t.TempDir()
	o := OpenAICompat{BaseURL: srv.URL, Model: "m", Key: "k", Client: http.DefaultClient, Home: home, Provider: "kimi"}
	if _, err := o.Chat(context.Background(), nil, nil); err == nil {
		t.Fatal("want error")
	}
	r := ledgerLines(t, home)[0]
	if r["ok"] != false || r["input"] != nil || r["provider"] != "kimi" {
		t.Fatalf("row %+v", r)
	}
}

func mustGlob(t *testing.T, home string) string {
	files, _ := filepath.Glob(filepath.Join(home, "usage", "*.jsonl"))
	if len(files) == 0 {
		t.Fatal("no ledger file")
	}
	return files[0]
}

func TestClaudeCLIJSONReplyParsed(t *testing.T) {
	dir := t.TempDir()
	argsFile := filepath.Join(dir, "args")
	bin := filepath.Join(dir, "claude")
	reply := `{"type":"result","subtype":"success","is_error":false,"result":"好的。\nHIRO_ACTION: confirm p7","total_cost_usd":0.0123,` +
		`"usage":{"input_tokens":12,"cache_creation_input_tokens":300,"cache_read_input_tokens":4000,"output_tokens":56},` +
		`"modelUsage":{"claude-haiku-5-5":{"costUSD":0.0001},"claude-sonnet-5-5":{"costUSD":0.0122}}}`
	_ = os.WriteFile(bin, []byte("#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done > "+argsFile+"\ncat <<'JSON'\n"+reply+"\nJSON\n"), 0o755)
	c := CLIAgent{Kind: "claude", Bin: bin, Hiro: &Hiro{Command: []string{"true"}}, Home: dir}
	r, err := c.Ask(usagelog.WithThread(context.Background(), "t2"), nil, "hi")
	if err != nil {
		t.Fatal(err)
	}
	if r.Text != "好的。" {
		t.Fatalf("text %q", r.Text)
	}
	a, _ := os.ReadFile(argsFile)
	if !strings.Contains(string(a), "--output-format\njson\n") || !strings.Contains(string(a), "Bash(hiro usage:*)") {
		t.Fatalf("args %q", a)
	}
	row := ledgerLines(t, dir)[0]
	if row["provider"] != "claude-cli" || row["model"] != "claude-sonnet-5-5" || row["thread"] != "t2" || row["cost"].(float64) != 0.0123 ||
		row["cost_source"] != "reported" || row["input"].(float64) != 12 || row["cache_read"].(float64) != 4000 || row["cache_write"].(float64) != 300 {
		t.Fatalf("row %+v", row)
	}
}

func TestClaudeCLIErrorResultIsError(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "claude")
	_ = os.WriteFile(bin, []byte("#!/bin/sh\necho '{\"type\":\"result\",\"is_error\":true,\"result\":\"Claude AI usage limit reached\",\"usage\":{\"input_tokens\":0,\"output_tokens\":0}}'\n"), 0o755)
	c := CLIAgent{Kind: "claude", Bin: bin, Hiro: &Hiro{Command: []string{"true"}}, Home: dir}
	if _, err := c.Ask(context.Background(), nil, "hi"); err == nil || !strings.Contains(err.Error(), "usage limit") {
		t.Fatalf("err %v", err)
	}
	if r := ledgerLines(t, dir)[0]; r["ok"] != false {
		t.Fatalf("row %+v", r)
	}
}

func TestCodexRecordsNoUsageRow(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "codex")
	_ = os.WriteFile(bin, []byte("#!/bin/sh\necho done\n"), 0o755)
	c := CLIAgent{Kind: "codex", Bin: bin, Hiro: &Hiro{Command: []string{"true"}}, Home: dir}
	if _, err := c.Ask(context.Background(), nil, "hi"); err != nil {
		t.Fatal(err)
	}
	r := ledgerLines(t, dir)[0]
	if r["provider"] != "codex-cli" || r["input"] != nil || r["ok"] != true {
		t.Fatalf("row %+v", r)
	}
}

func TestAgentUsageToolBecomesTableCard(t *testing.T) {
	dir := t.TempDir()
	p := dir + "/hiro"
	_ = os.WriteFile(p, []byte(`#!/bin/sh
echo '{"ok":true,"data":{"rows":[{"key":"2026-11-25","calls":3,"total":900,"cost":{"CNY":0.01}}],"totals":{"calls":3},"prices_checked":"2026-11-25"}}'
`), 0o755)
	tools := []ToolSpec{{Name: "usage daily", ToolName: "usage_daily", Risk: "read", Agent: true}}
	srv, _ := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"usage_daily","arguments":"{}"}}]}`,
		`{"role":"assistant","content":"今天花了一分钱。"}`)
	a := &Agent{Hiro: &Hiro{Command: []string{p}}, Tools: tools, Provider: OpenAICompat{BaseURL: srv.URL, Model: "m", Key: "sk-test", Client: http.DefaultClient}}
	r, err := a.Ask(context.Background(), nil, "今天花了多少")
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "usage-table" || r.Cards[0]["tool"] != "usage daily" {
		t.Fatalf("cards %v", r.Cards)
	}
	if rows := r.Cards[0]["data"].(map[string]any)["rows"].([]any); len(rows) != 1 {
		t.Fatalf("data %v", r.Cards[0]["data"])
	}
}

func TestUsagePluginRegistered(t *testing.T) {
	home := t.TempDir()
	call := func(_ context.Context, args []string) hiroapi.Result {
		return hiroapi.Result{OK: true, Data: json.RawMessage(`{}`)}
	}
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "model-keys.json")}}
	app := buildApp(appDeps{Home: home, Call: call, Assistant: a, Bootstrap: &Bootstrap{Home: home, Hiro: &Hiro{}}, Run: &shell.SyncRunner{}})
	if reg := app.Registry(); reg.ByID("usage") == nil || reg.ToolOwner("usage job") != "usage" {
		t.Fatal("用量插件没注册")
	}
}
