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
  "info memories") echo '{"ok":true,"data":{"kind":"memories","scanned_at":"2026-11-26 05:00:00","items":[{"card":"魅惑の視線"}],"slices":{"done":3,"total":12,"oldest":"2026-11-26 04:00:00","pending":[]}}}' ;;
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

// N64（G1 第 5 轮 11-27）：这一轮查了资料、又发起了要确认的任务：只出确认卡，资料卡不跟着刷屏；只查资料时照常出资料卡。
func TestInfoCardsDroppedWhenTurnEndsWithConfirm(t *testing.T) {
	h, _ := recordingHiro(t)
	srv, _ := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"info_supports","arguments":"{}"}}]}`,
		`{"role":"assistant","content":"好的。","tool_calls":[{"id":"c2","type":"function","function":{"name":"run_start","arguments":"{\"kind\":\"daily\"}"}}]}`)
	r, err := newTestAgent(h, srv.URL).Ask(context.Background(), nil, "帮我领日常")
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "confirm" {
		t.Fatalf("只该有确认卡：%v", r.Cards)
	}
	h3, _ := recordingHiro(t)
	srv3, _ := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"info_supports","arguments":"{}"}}]}`,
		`{"role":"assistant","content":"好的。","tool_calls":[{"id":"c2","type":"function","function":{"name":"run_start","arguments":"{\"kind\":\"daily\"}"}}]}`)
	r3, err := newTestAgent(h3, srv3.URL).Ask(context.Background(), nil, "看看我的支援卡，再帮我领日常")
	if err != nil {
		t.Fatal(err)
	}
	if len(r3.Cards) != 2 {
		t.Fatalf("玩家说了要看，资料卡要留着：%v", r3.Cards)
	}
	h2, _ := recordingHiro(t)
	srv2, _ := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"info_supports","arguments":"{}"}}]}`,
		`{"role":"assistant","content":"你有 1 张。"}`)
	r2, err := newTestAgent(h2, srv2.URL).Ask(context.Background(), nil, "我有哪些支援卡")
	if err != nil {
		t.Fatal(err)
	}
	if len(r2.Cards) != 1 || r2.Cards[0]["type"] != "info" {
		t.Fatalf("只查资料时该有资料卡：%v", r2.Cards)
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

func TestAgentReportsToolsAndUsesContext(t *testing.T) {
	h, _ := recordingHiro(t)
	srv, reqs := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"info_supports","arguments":"{\"rarity\":\"SSR\"}"}}]}`,
		`{"role":"assistant","content":"有 1 张。"}`)
	var tools []string
	a := newTestAgent(h, srv.URL)
	a.Extra = "## 现在的情况（11-24 01:30）\n### 账号\nPLv 52"
	a.OnTool = func(name, summary string) { tools = append(tools, name+"|"+summary) }
	if _, err := a.Ask(context.Background(), nil, "我的 SSR 支援卡"); err != nil {
		t.Fatal(err)
	}
	if len(tools) != 1 || tools[0] != "info_supports|1 条" {
		t.Errorf("工具进度 %q", tools)
	}
	msgs, _ := (*reqs)[0]["messages"].([]any)
	sys, _ := msgs[0].(map[string]any)["content"].(string)
	if !strings.Contains(sys, "PLv 52") || !strings.HasPrefix(sys, SystemPrompt) {
		t.Errorf("系统提示词没接上下文：%q", sys[max(0, len(sys)-80):])
	}
}

func TestCLIAgentPromptUsesContextInsteadOfStatus(t *testing.T) {
	h, calls := recordingHiro(t)
	c := CLIAgent{Kind: "claude", Hiro: h, Extra: "## 现在的情况\n学マス已连上"}
	p := c.fullPrompt(context.Background(), nil, "你好")
	if !strings.Contains(p, "学マス已连上") || !strings.Contains(p, "你好") {
		t.Errorf("提示词 %q", p)
	}
	if got := calls(); len(got) != 0 {
		t.Errorf("有上下文时不再单独查 run status：%v", got)
	}
}

func TestAgentCallsPluginTool(t *testing.T) {
	h, calls := recordingHiro(t)
	srv, _ := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"plugin_demo_echo","arguments":"{\"text\":\"你好\"}"}}]}`,
		`{"role":"assistant","content":"插件说了你好。"}`)
	a := newTestAgent(h, srv.URL)
	var got map[string]any
	a.Ext = []ExtTool{{Spec: ToolSpec{Name: "plugin demo echo", ToolName: "plugin_demo_echo", Help: "回声", Agent: true, Args: []ToolArg{{Name: "text", Type: "string", Required: true}}},
		Run: func(_ context.Context, args map[string]any) Result {
			got = args
			return Result{OK: true, Data: json.RawMessage(`{"said":"你好"}`)}
		}}}
	r, err := a.Ask(context.Background(), nil, "让插件说你好")
	if err != nil || r.Text != "插件说了你好。" {
		t.Fatalf("%v %q", err, r.Text)
	}
	if got["text"] != "你好" {
		t.Errorf("插件工具参数 %v", got)
	}
	if len(calls()) != 0 {
		t.Error("插件工具不该走 hiro 命令行")
	}
}

// 11-25 review：选偶像卡要做成可以点的卡片，不再让玩家打字回答编号
func TestAgentChooseIdolEndsTurnWithChoiceCard(t *testing.T) {
	h, _ := recordingHiro(t)
	srv, reqs := fakeLLM(t,
		`{"role":"assistant","content":"琴音有 2 张 SSR，你要哪一张？","tool_calls":[{"id":"c1","type":"function","function":{"name":"choose_idol","arguments":"{\"question\":\"要用哪张琴音？\",\"idols\":[\"i_card-a\",\"i_card-b\"]}"}}]}`)
	r, err := newTestAgent(h, srv.URL).Ask(context.Background(), nil, "用琴音打一把N.I.A pro")
	if err != nil {
		t.Fatal(err)
	}
	if len(*reqs) != 1 {
		t.Fatalf("出了选择卡就该停下等玩家点，请求了 %d 次", len(*reqs))
	}
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "choice" || r.Cards[0]["kind"] != "idols" {
		t.Fatalf("cards %v", r.Cards)
	}
	opts, _ := r.Cards[0]["options"].([]any)
	if len(opts) != 2 || r.Cards[0]["question"] != "要用哪张琴音？" || r.Cards[0]["status"] != "waiting" {
		t.Fatalf("选择卡 %v", r.Cards[0])
	}
	if !strings.Contains(r.Text, "哪一张") {
		t.Errorf("text %q", r.Text)
	}
	defs, _ := (*reqs)[0]["tools"].([]any)
	found := false
	for _, d := range defs {
		if f, _ := d.(map[string]any)["function"].(map[string]any); f["name"] == "choose_idol" {
			found = true
		}
	}
	if !found {
		t.Error("没把 choose_idol 给模型")
	}
}

func TestCLIChoiceLineBecomesCard(t *testing.T) {
	cards, text := choiceCards("琴音有两张 SSR：\nHIRO_CHOICE: idols i_card-a i_card-b\n")
	if len(cards) != 1 || cards[0]["type"] != "choice" || strings.Contains(text, "HIRO_CHOICE") {
		t.Fatalf("cards %v text %q", cards, text)
	}
	if opts, _ := cards[0]["options"].([]any); len(opts) != 2 {
		t.Errorf("options %v", cards[0]["options"])
	}
}

// 设计 5.8：回忆信息卡带上片覆盖
func TestMemoryInfoCardCarriesSliceCoverage(t *testing.T) {
	h, _ := recordingHiro(t)
	srv, _ := fakeLLM(t,
		`{"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"info_memories","arguments":"{}"}}]}`,
		`{"role":"assistant","content":"你有 1 份"}`)
	a := newTestAgent(h, srv.URL)
	a.Tools = append(append([]ToolSpec{}, testTools...), ToolSpec{Name: "info memories", ToolName: "info_memories", Risk: "read", Help: "回忆", Agent: true})
	r, err := a.Ask(context.Background(), nil, "我有哪些回忆")
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Cards) != 1 {
		t.Fatalf("cards %v", r.Cards)
	}
	sl, _ := r.Cards[0]["slices"].(map[string]any)
	if sl == nil || sl["done"] != 3.0 {
		t.Errorf("没带片覆盖：%v", r.Cards[0])
	}
}
