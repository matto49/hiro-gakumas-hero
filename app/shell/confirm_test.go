package shell

import (
	"context"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func confirmApp(t *testing.T, expires float64) (*App, *SyncRunner, *fakeCall, *ui.Tester) {
	t.Helper()
	call := &fakeCall{resp: map[string]hiroapi.Result{
		"confirm p1": ok(`{"job":{"id":"j7","kind":"hajime"}}`),
		"cancel p1":  ok(`{"cancelled":true}`),
	}}
	ask := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		return Reply{Text: "要开这一局吗？", Cards: []plugin.Card{ConfirmCard(hiroapi.Pending{ID: "p1", Command: "hiro run start hajime --idol i_card-x", Summary: "初", ExpiresAt: expires})}}, nil
	}
	reg := viewReg()
	reg.Add(&plugin.Plugin{ID: "explain", Explain: func(_ plugin.Host, p hiroapi.Pending) (plugin.Explained, bool) {
		return plugin.Explained{Title: "开一局 初", Subtitle: "月村手毬 · 普通", Rows: [][2]string{{"编成", "沿用编成方案"}}, Cost: "会操作游戏 · 用掉 1 次培育"}, true
	}})
	a, run := newTestApp(newMem(), call, ask, reg)
	tt := ui.NewTester(a.View, 1280, 800)
	a.Send("帮我用月村手毬打一把初")
	run.Drain()
	tt.Frame()
	return a, run, call, tt
}

func lastCards(a *App) []plugin.Card {
	ms := a.Current().Messages
	return ms[len(ms)-1].Cards
}

func TestConfirmCardShowsExplanationAndStarts(t *testing.T) {
	a, run, call, tt := confirmApp(t, 4102444800)
	for _, s := range []string{"开一局 初", "月村手毬 · 普通", "沿用编成方案", "会操作游戏 · 用掉 1 次培育", "等你确认", "先不"} {
		if !tt.HasText(s) {
			t.Errorf("确认卡没有 %q：%q", s, tt.Texts())
		}
	}
	snap(t, tt, "confirm-waiting")
	if err := tt.Click("开始"); err != nil {
		t.Fatal(err)
	}
	run.Drain()
	tt.Frame()
	if !call.called("confirm p1") {
		t.Fatal("没调 confirm")
	}
	card := a.Current().Messages[1].Cards[0]
	if card["status"] != "confirmed" {
		t.Errorf("卡状态 %v", card["status"])
	}
	cards := lastCards(a)
	if cards[len(cards)-1]["type"] != "run" || cards[len(cards)-1]["jobId"] != "j7" {
		t.Errorf("没加对局卡：%+v", cards)
	}
	if tt.HasText("先不") {
		t.Error("确认过的卡不该还有按钮")
	}
	// 再按 Enter 不会再确认一次
	n := len(call.calls)
	tt.Key(0, ui.KeyEnter)
	run.Drain()
	if len(call.calls) != n {
		t.Errorf("重复确认：%v", call.calls[n:])
	}
}

func TestEnterOnEmptyInputConfirmsLatest(t *testing.T) {
	a, run, call, tt := confirmApp(t, 4102444800)
	a.d.Now = func() time.Time { return fixedNow.Add(2 * time.Second) } // 卡出现 1 秒以后
	tt.Key(0, ui.KeyEnter)
	run.Drain()
	if !call.called("confirm p1") {
		t.Error("输入框空时 Enter 应确认最新那张")
	}
}

func TestEscCancels(t *testing.T) {
	a, run, call, tt := confirmApp(t, 4102444800)
	a.d.Now = func() time.Time { return fixedNow.Add(2 * time.Second) }
	tt.Key(0, ui.KeyEscape)
	run.Drain()
	tt.Frame()
	if !call.called("cancel p1") || a.Current().Messages[1].Cards[0]["status"] != "cancelled" {
		t.Errorf("Esc 没取消：%v", call.calls)
	}
}

func TestEnterWithTextSendsInsteadOfConfirming(t *testing.T) {
	_, run, call, tt := confirmApp(t, 4102444800)
	tt.Type("等一下")
	tt.Key(0, ui.KeyEnter)
	run.Drain()
	if call.called("confirm") {
		t.Error("输入框有字时 Enter 不该确认")
	}
}

func TestExpiredConfirmShowsExpired(t *testing.T) {
	_, run, call, tt := confirmApp(t, float64(fixedNow.Unix()-10))
	if !tt.HasText("「初」的确认过期了，再说一次就行") || tt.HasText("先不") {
		t.Errorf("过期显示：%q", tt.Texts())
	}
	tt.Key(0, ui.KeyEnter)
	run.Drain()
	if call.called("confirm") {
		t.Error("过期的不该确认")
	}
}

func TestConfirmCardSaysWhichPlugin(t *testing.T) {
	call := &fakeCall{}
	a, run := newTestApp(newMem(), call, nil, viewReg())
	card := ConfirmCard(hiroapi.Pending{ID: "p5", Command: "hiro run start daily", Summary: "领日常", ExpiresAt: 4102444800})
	card["from"] = "示例插件"
	a.Post("插件「示例插件」想要：领日常", card)
	run.Drain()
	tt := ui.NewTester(a.View, 1280, 800)
	if !tt.HasText("来自插件「示例插件」") {
		t.Errorf("确认卡没写来源：%q", tt.Texts())
	}
}

// I3：按键只认初星助手自己回的、出现超过 1 秒的那张；插件塞进来的卡只能点按钮。
func TestKeysIgnorePluginCardsAndFreshCards(t *testing.T) {
	call := &fakeCall{}
	a, run := newTestApp(newMem(), call, nil, viewReg())
	card := ConfirmCard(hiroapi.Pending{ID: "p5", Command: "hiro run start daily", Summary: "领日常", ExpiresAt: 4102444800})
	card["from"] = "示例插件"
	a.Post("", card)
	run.Drain()
	if a.keyOnEmptyInput(ui.KeyEnter) {
		t.Error("插件的卡不该被 Enter 确认")
	}
	own := ConfirmCard(hiroapi.Pending{ID: "p6", Command: "hiro run start daily", Summary: "领日常", ExpiresAt: 4102444800})
	a.Post("", own)
	if a.keyOnEmptyInput(ui.KeyEnter) {
		t.Error("刚出现的卡不该被 Enter 确认")
	}
	a.d.Now = func() time.Time { return fixedNow.Add(2 * time.Second) }
	if !a.keyOnEmptyInput(ui.KeyEnter) {
		t.Error("出现超过 1 秒的自家卡可以 Enter")
	}
	run.Drain()
	if !call.called("confirm p6") || call.called("confirm p5") {
		t.Errorf("确认了 %v", call.calls)
	}
}

// 解读不了的待确认：兜底样式也不显示命令名（11-25 review：「不要只展示 command run start，甚至指令名都不要显示」）
func TestConfirmFallbackHidesCommand(t *testing.T) {
	call := &fakeCall{resp: map[string]hiroapi.Result{}}
	ask := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		return Reply{Text: "好", Cards: []plugin.Card{ConfirmCard(hiroapi.Pending{ID: "p2", Command: "run start", Summary: "做点什么", ExpiresAt: 4102444800})}}, nil
	}
	a, run := newTestApp(newMem(), call, ask, viewReg())
	tt := ui.NewTester(a.View, 1280, 800)
	a.Send("随便")
	run.Drain()
	tt.Frame()
	if !tt.HasText("做点什么") {
		t.Fatalf("兜底标题：%q", tt.Texts())
	}
	for _, s := range tt.Texts() {
		if s == "命令" || s == "run start" {
			t.Errorf("兜底样式不该出现命令：%q", tt.Texts())
		}
	}
}

func TestPendingArgsSurviveCard(t *testing.T) {
	card := ConfirmCard(hiroapi.Pending{ID: "p3", Command: "run start", Args: map[string]any{"kind": "daily"}})
	p, ok := pendingOf(card)
	if !ok || p.Args["kind"] != "daily" {
		t.Errorf("卡里读回的待确认 %+v", p)
	}
}
