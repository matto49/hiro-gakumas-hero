package shell

import (
	"context"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func welcomeReg() *plugin.Registry {
	r := viewReg()
	r.Add(&plugin.Plugin{ID: "w", Name: "w",
		Starters: func(plugin.Host) []plugin.Starter {
			return []plugin.Starter{
				{Label: "用月村手毬再打一把初", Sub: "上次 11-23", Say: "帮我用月村手毬打一把初"},
				{Label: "领日常奖励", Sub: "活動費、礼物、任务", Say: "帮我领日常奖励"},
			}
		},
		Greeting: func(plugin.Host) string { return "PLv 52 · AP 120/150" },
	})
	return r
}

func TestWelcomeIsCenteredWithStarters(t *testing.T) {
	asked := ""
	ask := func(_ context.Context, _ []Turn, text, _ string, _ func(string, string)) (Reply, error) {
		asked = text
		return Reply{Text: "好"}, nil
	}
	a, run := newTestApp(newMem(), &fakeCall{}, ask, welcomeReg())
	a.NewThread()
	tt := ui.NewTester(a.View, 1280, 800)
	for _, s := range []string{"制作人，今天也来培育吧", "PLv 52 · AP 120/150", "用月村手毬再打一把初", "领日常奖励"} {
		if !tt.HasText(s) {
			t.Errorf("新对话页没有 %q：%q", s, tt.Texts())
		}
	}
	in, ok := tt.AccessByRole(ui.RoleTextField, "输入框")
	if !ok {
		in, ok = tt.Access("输入框")
	}
	if !ok || in.Bounds.Y < 200 || in.Bounds.Y > 560 {
		t.Errorf("新对话时输入框应在中间：%+v %v", in.Bounds, ok)
	}
	if err := tt.Click("用月村手毬再打一把初"); err != nil {
		t.Fatal(err)
	}
	run.Drain()
	tt.Frame()
	if asked != "帮我用月村手毬打一把初" {
		t.Errorf("点引导卡要把话发出去，问了 %q", asked)
	}
	in2, _ := tt.Access("输入框")
	if in2.Bounds.Y < 650 {
		t.Errorf("有消息后输入框回到底部：%+v", in2.Bounds)
	}
	snap(t, tt, "welcome-after")
}

func TestPreviewWelcome(t *testing.T) {
	a, _ := newTestApp(newMem(), &fakeCall{}, nil, welcomeReg())
	a.NewThread()
	tt := ui.NewTester(a.View, 1280, 800)
	snap(t, tt, "welcome")
}

// 侧栏和新对话页（没有偶像卡面时）都用应用标志，不再是写「初」字的渐变方块。
func TestLogoInSidebarAndWelcome(t *testing.T) {
	a, _ := newTestApp(newMem(), &fakeCall{}, nil, viewReg())
	a.NewThread()
	tt := ui.NewTester(a.View, 1280, 800)
	n := 0
	for _, x := range tt.AccessAll() {
		if x.Role == ui.RoleImage && x.Name == "初星助手" {
			n++
		}
	}
	if n != 2 {
		t.Errorf("标志图出现 %d 次，要 2 次（侧栏 + 新对话页）", n)
	}
	for _, x := range tt.Texts() {
		if x == "初" {
			t.Error("新对话页不该再有写「初」字的方块")
		}
	}
}
