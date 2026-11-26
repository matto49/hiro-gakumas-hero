package shell

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// G1 §8 错误文案：对话助手出错时，错误卡要有白话原因、提示和能点的下一步（去设置 / 再试一次）。
func TestAgentErrorCardCarriesHintAndActions(t *testing.T) {
	calls := 0
	ask := func(ctx context.Context, h []Turn, text, extra string, onTool func(string, string)) (Reply, error) {
		calls++
		if calls == 1 {
			return Reply{}, &plugin.UserError{Message: "模型账户余额不足", Hint: "去服务商那里充值", Actions: []string{plugin.ActionRetry, plugin.ActionSettings}}
		}
		return Reply{Text: "好的"}, nil
	}
	reg := &plugin.Registry{}
	reg.Add(&plugin.Plugin{ID: "settings", Name: "设置", Panel: func(c *ui.Context, h plugin.Host, route string) { ui.Text(c, "设置面板") }})
	a, run := newTestApp(newMem(), &fakeCall{}, ask, reg)
	tt := ui.NewTester(a.View, 1280, 800)
	a.Send("帮我领日常奖励")
	run.Drain()
	tt.Frame()
	card := lastCards(a)[0]
	e, _ := card["error"].(map[string]any)
	if card["type"] != "error" || e["message"] != "模型账户余额不足" || e["hint"] != "去服务商那里充值" || card["retry_text"] != "帮我领日常奖励" {
		t.Fatalf("错误卡 %+v", card)
	}
	if got := cardStrings(card["actions"]); len(got) != 2 || got[0] != plugin.ActionRetry || got[1] != plugin.ActionSettings {
		t.Fatalf("按钮 %v", got)
	}
	if !tt.HasText("去服务商那里充值") {
		t.Fatal("提示没画出来")
	}
	// 「去设置」打开设置面板
	if err := tt.Click("去设置"); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	if !a.ui.panelOpen || a.ui.panelPlugin != "settings" {
		t.Fatalf("没打开设置：%v %q", a.ui.panelOpen, a.ui.panelPlugin)
	}
	// 「再试一次」把那句话再发一遍；点过就变成一行字
	if err := tt.Click("再试一次"); err != nil {
		t.Fatal(err)
	}
	run.Drain()
	tt.Frame()
	msgs := a.Current().Messages
	if calls != 2 || msgs[len(msgs)-1].Text != "好的" || msgs[len(msgs)-2].Text != "帮我领日常奖励" {
		t.Fatalf("重发后 %d 次调用，最后两条 %+v", calls, msgs[len(msgs)-2:])
	}
	if !tt.HasText("已重新发送") || tt.Click("再试一次") == nil {
		t.Fatal("点过以后还能再点")
	}
}

func TestPlainErrorStillGetsRetry(t *testing.T) {
	card := errorCardFor(errors.New("出了点别的事"), "扫描回忆")
	e, _ := card["error"].(map[string]any)
	if e["message"] != "出了点别的事" || e["hint"] != nil {
		t.Fatalf("错误卡 %+v", card)
	}
	if got := cardStrings(card["actions"]); len(got) != 1 || got[0] != plugin.ActionRetry {
		t.Fatalf("按钮 %v", got)
	}
}

func TestErrorCardActionsSurviveSave(t *testing.T) {
	card := errorCardFor(&plugin.UserError{Message: "Key 不对", Actions: []string{plugin.ActionSettings}}, "x")
	b, _ := json.Marshal(card)
	var back plugin.Card
	_ = json.Unmarshal(b, &back)
	if got := cardStrings(back["actions"]); len(got) != 1 || got[0] != plugin.ActionSettings {
		t.Fatalf("存盘读回 %v", got)
	}
}

// #121 review：对话助手 panic 时 Send 设的 busy 要清掉、给出能重试的错误卡，之后还能接着发消息。
func TestAskPanicClearsBusy(t *testing.T) {
	calls := 0
	ask := func(ctx context.Context, h []Turn, text, extra string, onTool func(string, string)) (Reply, error) {
		calls++
		if calls == 1 {
			panic("boom")
		}
		return Reply{Text: "好的"}, nil
	}
	a, run := newTestApp(newMem(), &fakeCall{}, ask, nil)
	a.Send("帮我领日常奖励")
	run.Drain()
	if a.busy {
		t.Fatal("panic 后 busy 没清")
	}
	card := lastCards(a)[0]
	if card["type"] != "error" || cardStrings(card["actions"])[0] != plugin.ActionRetry {
		t.Fatalf("错误卡 %+v", card)
	}
	a.Send("再来一次")
	run.Drain()
	if calls != 2 {
		t.Fatalf("panic 之后发不出去了：%d", calls)
	}
}
