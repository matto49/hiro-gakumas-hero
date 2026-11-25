package shell

import (
	"context"
	"fmt"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func viewReg() *plugin.Registry {
	r := &plugin.Registry{}
	r.Add(&plugin.Plugin{ID: "start", Name: "开局", Panel: func(c *ui.Context, _ plugin.Host, route string) { ui.Text(c, "开局面板"+route) },
		Commands: []plugin.Command{{Name: "开局", Hint: "开一局"}},
		Chips:    []plugin.Chip{{Label: "/开局"}}})
	r.Add(&plugin.Plugin{ID: "library", Name: "资料库", Panel: func(c *ui.Context, _ plugin.Host, _ string) { ui.Text(c, "资料库面板") },
		Commands: []plugin.Command{{Name: "资料库"}, {Name: "扫描", Route: "scan"}}})
	return r
}

func TestViewShowsSidebarThreadsAndPlugins(t *testing.T) {
	a, _ := newTestApp(newMem(oldThread), &fakeCall{}, nil, viewReg())
	tt := ui.NewTester(a.View, 1280, 800)
	for _, s := range []string{"初星助手", "新对话", "用月村手毬打一把初", "开局", "资料库", "帮我用月村手毬打一把初", "要开始吗？"} {
		if !tt.HasText(s) {
			t.Errorf("没有 %q：%q", s, tt.Texts())
		}
	}
}

func TestTypingEnterSendsToAgent(t *testing.T) {
	asked := ""
	ask := func(_ context.Context, _ []Turn, text, _ string, _ func(string, string)) (Reply, error) {
		asked = text
		return Reply{Text: "好的，马上"}, nil
	}
	a, run := newTestApp(newMem(), &fakeCall{}, ask, viewReg())
	tt := ui.NewTester(a.View, 1280, 800)
	tt.Type("你好")
	tt.Key(0, ui.KeyEnter)
	run.Drain()
	tt.Frame()
	if asked != "你好" || !tt.HasText("好的，马上") {
		t.Errorf("问了 %q，界面 %q", asked, tt.Texts())
	}
}

func TestSlashListsCommandsAndOpensPanel(t *testing.T) {
	a, _ := newTestApp(newMem(), &fakeCall{}, nil, viewReg())
	tt := ui.NewTester(a.View, 1280, 800)
	tt.Type("/")
	tt.Frame()
	if !tt.HasText("/开局") || !tt.HasText("/扫描") {
		t.Fatalf("没列出命令：%q", tt.Texts())
	}
	if err := tt.Click("/扫描"); err != nil {
		t.Fatal(err)
	}
	if !a.ui.panelOpen || a.ui.panelPlugin != "library" || a.ui.panelRoute != "scan" {
		t.Errorf("面板 open=%v plugin=%q route=%q", a.ui.panelOpen, a.ui.panelPlugin, a.ui.panelRoute)
	}
}

func TestGateTakesWholeWindow(t *testing.T) {
	a, _ := newTestApp(newMem(oldThread), &fakeCall{}, nil, viewReg())
	gated := true
	a.d.Gate = func(c *ui.Context, _ plugin.Host) bool {
		if gated {
			ui.Text(c, "第一次使用")
		}
		return gated
	}
	tt := ui.NewTester(a.View, 1280, 800)
	if !tt.HasText("第一次使用") || tt.HasText("新对话") {
		t.Errorf("门没拦住：%q", tt.Texts())
	}
	gated = false
	tt.Frame()
	if !tt.HasText("新对话") {
		t.Error("过了门要显示主界面")
	}
}

func TestHasModelFalseMeansQuickOnly(t *testing.T) {
	asked := false
	ask := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		asked = true
		return Reply{}, nil
	}
	a, run := newTestApp(newMem(), &fakeCall{}, ask, nil)
	a.d.HasModel = func() bool { return false }
	a.Send("你好")
	run.Drain()
	if asked || a.Current().Messages[1].Text != NoModelText {
		t.Error("没配模型时不该问 agent")
	}
}

// I6：对话多了，插件入口还在（不被裁掉）。
func TestSidebarKeepsPluginsWithManyThreads(t *testing.T) {
	var raw []string
	for i := 0; i < 40; i++ {
		raw = append(raw, fmt.Sprintf(`{"id":"t-%d","title":"对话 %d","updatedAt":1,"messages":[{"id":"m","role":"user","text":"x","cards":[],"at":1}]}`, i, i))
	}
	a, _ := newTestApp(newMem(raw...), &fakeCall{}, nil, viewReg())
	tt := ui.NewTester(a.View, 1280, 800)
	info, ok := tt.AccessByRole(ui.RoleButton, "资料库")
	if !ok || info.Bounds.H < 10 || info.Bounds.Y > 800 {
		t.Errorf("插件入口被挤掉了：%+v %v", info.Bounds, ok)
	}
}

// M7：提交时 "/" 后面要精确是命令名，「/ 帮我…」照常发给 agent。
func TestSubmitNeedsExactCommand(t *testing.T) {
	asked := ""
	ask := func(_ context.Context, _ []Turn, text, _ string, _ func(string, string)) (Reply, error) {
		asked = text
		return Reply{}, nil
	}
	a, run := newTestApp(newMem(), &fakeCall{}, ask, viewReg())
	a.submit("/开 帮我打一把")
	run.Drain()
	if a.ui.panelOpen || asked != "/开 帮我打一把" {
		t.Errorf("前缀不该触发命令：panel=%v asked=%q", a.ui.panelOpen, asked)
	}
	a.submit("/开局")
	if !a.ui.panelOpen || a.ui.panelPlugin != "start" {
		t.Error("精确的命令名要打开面板")
	}
}

// 工具行写成玩家看得懂的话，不露内部工具名。
func TestToolLineIsReadable(t *testing.T) {
	a, _ := newTestApp(newMem(), &fakeCall{}, nil, viewReg())
	a.NewThread()
	a.Post("找到了", nil)
	a.Current().Messages[0].Tools = []string{"info_idols · 1 条"}
	tt := ui.NewTester(a.View, 1280, 800)
	if !tt.HasText("查了偶像卡") || tt.HasText("info_idols") {
		t.Errorf("工具行：%q", tt.Texts())
	}
}
