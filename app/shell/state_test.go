package shell

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/usagelog"
)

const oldThread = `{"id":"t-old-1","title":"用月村手毬打一把初","updatedAt":1763900000000,"messages":[
 {"id":"m1","role":"user","text":"帮我用月村手毬打一把初","cards":[],"at":1763900000000},
 {"id":"m2","role":"agent","text":"要开始吗？","cards":[
   {"type":"info","title":"偶像卡","kind":"idols","items":[{"name":"月村手毬"}],"scannedAt":"2026-11-22"},
   {"type":"confirm","pending":{"id":"p1","command":"hiro run start hajime","summary":"初","expires_at":1},"status":"confirmed"},
   {"type":"run","jobId":"j1"}],"at":1763900001000}]}`

func TestLoadsOldThreadFormat(t *testing.T) {
	a, _ := newTestApp(newMem(oldThread), &fakeCall{}, nil, nil)
	if len(a.Threads()) != 1 {
		t.Fatalf("对话 %d 个", len(a.Threads()))
	}
	th := a.Threads()[0]
	if th.Title != "用月村手毬打一把初" || len(th.Messages) != 2 || th.Messages[1].Role != "agent" {
		t.Fatalf("读出 %+v", th)
	}
	cards := th.Messages[1].Cards
	if len(cards) != 3 || cards[2]["type"] != "run" || cards[2]["jobId"] != "j1" {
		t.Errorf("卡片 %+v", cards)
	}
}

func TestSendAsksAgentAndSaves(t *testing.T) {
	mem := newMem()
	var gotHistory []Turn
	var gotExtra string
	ask := func(_ context.Context, history []Turn, text, extra string, onTool func(string, string)) (Reply, error) {
		gotHistory, gotExtra = history, extra
		onTool("info_idols", "找到 1 张")
		return Reply{Text: "找到了：" + text, Cards: []plugin.Card{{"type": "info", "kind": "idols"}}}, nil
	}
	reg := &plugin.Registry{}
	reg.Add(&plugin.Plugin{ID: "acc", Name: "账号", Context: func(context.Context, plugin.Host) string { return "PLv 52" }})
	a, run := newTestApp(mem, &fakeCall{}, ask, reg)
	a.NewThread()
	a.Send("你好")
	if !a.Busy() {
		t.Error("发出后在等回复")
	}
	run.Drain()
	if a.Busy() {
		t.Error("回复到了不该还在等")
	}
	msgs := a.Current().Messages
	if len(msgs) != 2 || msgs[0].Role != "user" || msgs[1].Text != "找到了：你好" || len(msgs[1].Cards) != 1 {
		t.Fatalf("消息 %+v", msgs)
	}
	if len(msgs[1].Tools) != 1 || msgs[1].Tools[0] != "info_idols · 找到 1 张" {
		t.Errorf("工具行 %q", msgs[1].Tools)
	}
	if len(gotHistory) != 0 {
		t.Errorf("第一句没有历史：%+v", gotHistory)
	}
	if !strings.Contains(gotExtra, "PLv 52") {
		t.Errorf("上下文没带上：%q", gotExtra)
	}
	raw := mem.List()
	if len(raw) != 1 {
		t.Fatalf("存了 %d 个对话", len(raw))
	}
	var saved Thread
	if err := json.Unmarshal(raw[0], &saved); err != nil || len(saved.Messages) != 2 || saved.Title != "你好" {
		t.Errorf("存下 %+v %v", saved, err)
	}
	// 第二句带上历史
	a.Send("再来")
	run.Drain()
	if len(gotHistory) != 2 || gotHistory[0].Role != "user" || gotHistory[1].Role != "assistant" {
		t.Errorf("历史 %+v", gotHistory)
	}
}

func TestNoModelExplainsAndRunsChip(t *testing.T) {
	call := &fakeCall{resp: map[string]hiroapiResult{"run start daily": pendingResult("p9", "领日常")}}
	a, run := newTestApp(newMem(), call, nil, nil)
	a.NewThread()
	a.Send("随便说点")
	run.Drain()
	last := a.Current().Messages[len(a.Current().Messages)-1]
	if !strings.Contains(last.Text, "设置") {
		t.Errorf("没配模型时提示去设置：%q", last.Text)
	}
	a.RunArgs("帮我领日常奖励", []string{"run", "start", "daily"})
	run.Drain()
	last = a.Current().Messages[len(a.Current().Messages)-1]
	if len(last.Cards) != 1 || last.Cards[0]["type"] != "confirm" || last.Cards[0]["status"] != "waiting" {
		t.Errorf("快捷指令得到确认卡：%+v", last.Cards)
	}
}

// CR#13：存档里有 null 消息时照样能启动（跳过那条）。
func TestNullMessageDoesNotCrash(t *testing.T) {
	a, _ := newTestApp(newMem(`{"id":"t-n","title":"x","updatedAt":1,"messages":[null,{"id":"m","role":"user","text":"hi","at":1}]}`), &fakeCall{}, nil, nil)
	if len(a.Threads()) != 1 || len(a.Threads()[0].Messages) != 1 {
		t.Errorf("读出 %+v", a.Threads())
	}
}

func TestSendPutsThreadIDInContext(t *testing.T) {
	var got string
	ask := func(ctx context.Context, _ []Turn, _, _ string, _ func(string, string)) (Reply, error) {
		got = usagelog.ThreadOf(ctx)
		return Reply{Text: "ok"}, nil
	}
	a, run := newTestApp(newMem(), &fakeCall{}, ask, &plugin.Registry{})
	a.NewThread()
	a.Send("你好")
	run.Drain()
	if got == "" || got != a.Current().ID {
		t.Fatalf("thread in ctx %q, current %q", got, a.Current().ID)
	}
}
