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
	"sync"
	"testing"
)

var no = false

var testTools = []ToolSpec{
	{Name: "info supports", ToolName: "info_supports", Risk: "read", Help: "支援卡", Agent: true,
		Args: []ToolArg{{Name: "--rarity", Help: "稀有度", Type: "string", Choices: []string{"SSR", "SR", "R"}}, {Name: "--follow", Help: "跟到结束", Type: "bool", Agent: &no}}},
	{Name: "run start", ToolName: "run_start", Risk: "act", Help: "开任务", Agent: true,
		Args: []ToolArg{{Name: "kind", Help: "种类", Type: "string", Required: true}, {Name: "--no-llm", Help: "不用模型", Type: "bool"}, {Name: "--layers", Help: "层数", Type: "int"}}},
	{Name: "confirm", ToolName: "confirm", Risk: "read", Help: "确认", Agent: false, Args: []ToolArg{{Name: "id", Type: "string", Required: true}}},
	{Name: "secret copy", ToolName: "secret_copy", Risk: "read", Help: "复制", Agent: true, Args: []ToolArg{{Name: "name", Type: "string", Required: true}}},
}

// recordingHiro 写一个假 hiro：把参数记到文件，按子命令回信封。
func recordingHiro(t *testing.T) (*Hiro, func() []string) {
	t.Helper()
	dir := t.TempDir()
	log := filepath.Join(dir, "calls")
	p := filepath.Join(dir, "hiro")
	script := `#!/bin/sh
echo "$*" >> "` + log + `"
case "$1 $2" in
  "info supports") echo '{"ok":true,"data":{"kind":"supports","scanned_at":"2026-11-16 18:00:00","items":[{"name":"さみだれ","rarity":"SSR","attr":"Vi"}]}}' ;;
  "run start") echo '{"ok":true,"data":{"pending":{"id":"p1","command":"run start","summary":"领日常奖励","expires_at":1}}}'; exit 3 ;;
  *) echo '{"ok":false,"error":{"code":"unknown_command","message":"?"}}'; exit 2 ;;
esac
`
	if err := os.WriteFile(p, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return &Hiro{Command: []string{p}}, func() []string {
		b, _ := os.ReadFile(log)
		s := strings.TrimSpace(string(b))
		if s == "" {
			return nil
		}
		return strings.Split(s, "\n")
	}
}

// fakeLLM 按顺序回事先写好的 assistant 消息，记下每次请求。
func fakeLLM(t *testing.T, replies ...string) (*httptest.Server, *[]map[string]any) {
	t.Helper()
	var mu sync.Mutex
	var reqs []map[string]any
	n := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer sk-test" {
			w.WriteHeader(401)
			return
		}
		b, _ := io.ReadAll(r.Body)
		var req map[string]any
		_ = json.Unmarshal(b, &req)
		mu.Lock()
		reqs = append(reqs, req)
		i := n
		n++
		mu.Unlock()
		if i >= len(replies) {
			t.Errorf("unexpected request %d", i)
			w.WriteHeader(500)
			return
		}
		_, _ = io.WriteString(w, `{"choices":[{"message":`+replies[i]+`}]}`)
	}))
	t.Cleanup(srv.Close)
	return srv, &reqs
}

func newTestAgent(h *Hiro, url string) *Agent {
	return &Agent{Hiro: h, Tools: testTools, Provider: OpenAICompat{BaseURL: url, Model: "deepseek-chat", Key: "sk-test", Client: http.DefaultClient}}
}

func TestAgentCallsToolThenAnswers(t *testing.T) {
	h, calls := recordingHiro(t)
	srv, reqs := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"info_supports","arguments":"{\"rarity\":\"SSR\"}"}}]}`,
		`{"role":"assistant","content":"你有 1 张 SSR 支援卡：さみだれ。"}`)
	r, err := newTestAgent(h, srv.URL).Ask(context.Background(), nil, "我的 SSR 支援卡有哪些")
	if err != nil {
		t.Fatal(err)
	}
	if r.Text != "你有 1 张 SSR 支援卡：さみだれ。" {
		t.Fatalf("text %q", r.Text)
	}
	if got := calls(); len(got) != 1 || got[0] != "info supports --rarity SSR" {
		t.Fatalf("calls %v", got)
	}
	if len(*reqs) != 2 {
		t.Fatalf("requests %d", len(*reqs))
	}
	msgs := (*reqs)[1]["messages"].([]any)
	last := msgs[len(msgs)-1].(map[string]any)
	if last["role"] != "tool" || last["tool_call_id"] != "c1" || !strings.Contains(last["content"].(string), "さみだれ") {
		t.Fatalf("tool message %v", last)
	}
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "info" {
		t.Fatalf("cards %v", r.Cards)
	}
}

func TestAgentStopsAtPendingAndNeverConfirms(t *testing.T) {
	h, calls := recordingHiro(t)
	srv, reqs := fakeLLM(t,
		`{"role":"assistant","content":"好的，我来开。","tool_calls":[{"id":"c1","type":"function","function":{"name":"run_start","arguments":"{\"kind\":\"daily\"}"}}]}`)
	r, err := newTestAgent(h, srv.URL).Ask(context.Background(), nil, "帮我领日常")
	if err != nil {
		t.Fatal(err)
	}
	if len(*reqs) != 1 {
		t.Fatalf("loop should stop at pending, requests %d", len(*reqs))
	}
	if got := calls(); len(got) != 1 || got[0] != "run start daily" {
		t.Fatalf("calls %v", got)
	}
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "confirm" {
		t.Fatalf("cards %v", r.Cards)
	}
	if !strings.Contains(r.Text, "领日常奖励") {
		t.Fatalf("text %q", r.Text)
	}
}

func TestAgentRefusesToolsNotOffered(t *testing.T) {
	h, calls := recordingHiro(t)
	srv, reqs := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"confirm","arguments":"{\"id\":\"p1\"}"}}]}`,
		`{"role":"assistant","content":"这个要你来确认。"}`)
	r, err := newTestAgent(h, srv.URL).Ask(context.Background(), nil, "直接确认吧")
	if err != nil {
		t.Fatal(err)
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("hiro must not be called, got %v", got)
	}
	msgs := (*reqs)[1]["messages"].([]any)
	last := msgs[len(msgs)-1].(map[string]any)
	if !strings.Contains(last["content"].(string), "not_allowed") {
		t.Fatalf("tool message %v", last)
	}
	if r.Text != "这个要你来确认。" {
		t.Fatalf("text %q", r.Text)
	}
}

func TestToolDefinitionsOnlyAgentTools(t *testing.T) {
	defs := toolDefs(testTools)
	names := []string{}
	for _, d := range defs {
		names = append(names, d.Function.Name)
	}
	if strings.Join(names, ",") != "info_supports,run_start,secret_copy" {
		t.Fatalf("names %v", names)
	}
	if _, ok := defs[0].Function.Parameters["properties"].(map[string]any)["follow"]; ok {
		t.Fatal("agent=false 的参数不该给模型")
	}
	rs := defs[1].Function.Parameters
	if req := rs["required"].([]string); len(req) != 1 || req[0] != "kind" {
		t.Fatalf("required %v", req)
	}
}

func TestArgvFromToolArguments(t *testing.T) {
	argv, err := toolArgv(testTools[1], `{"kind":"tower","no_llm":true,"layers":3}`)
	if err != nil || strings.Join(argv, " ") != "run start tower --no-llm --layers 3" {
		t.Fatalf("argv %v err %v", argv, err)
	}
	if _, err := toolArgv(testTools[1], `{}`); err == nil {
		t.Fatal("missing required kind should fail")
	}
}

func TestPromptTellsTheModelToUseCounts(t *testing.T) {
	if !strings.Contains(SystemPrompt, "counts") {
		t.Fatal("system prompt should tell the model to read counts instead of counting items")
	}
}

func TestPlanResultBecomesFormationCard(t *testing.T) {
	dir := t.TempDir()
	p := dir + "/hiro"
	_ = os.WriteFile(p, []byte(`#!/bin/sh
echo '{"ok":true,"data":{"idol":{"name":"Campus mode!!"},"main_attr":"Da","support":{"own":[]},"memory":{"chosen":[]},"notes":[],"report":"# 方案"}}'
`), 0o755)
	tools := []ToolSpec{{Name: "plan formation", ToolName: "plan_formation", Risk: "read", Agent: true,
		Args: []ToolArg{{Name: "--idol", Type: "string", Required: true}}}}
	srv, _ := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"plan_formation","arguments":"{\"idol\":\"i_card-skin-shro-3-007\"}"}}]}`,
		`{"role":"assistant","content":"方案出好了。"}`)
	a := &Agent{Hiro: &Hiro{Command: []string{p}}, Tools: tools, Provider: OpenAICompat{BaseURL: srv.URL, Model: "m", Key: "sk-test", Client: http.DefaultClient}}
	r, err := a.Ask(context.Background(), nil, "给篠澤広出编成方案")
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "formation" {
		t.Fatalf("cards %v", r.Cards)
	}
	plan := r.Cards[0]["plan"].(map[string]any)
	if plan["main_attr"] != "Da" {
		t.Fatalf("plan %v", plan)
	}
}
