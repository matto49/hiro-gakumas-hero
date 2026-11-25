package cockpit

import (
	"context"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

func step(seq int, page, src, text string) plugin.JobEvent {
	n := seq
	return plugin.JobEvent{Seq: seq, T: 1763900000 + float64(seq*60), Type: "step", Step: &n, Page: page, Source: src, Text: text}
}

func liveHost() *plugintest.Host {
	h := plugintest.New(map[string]string{
		"run pause":  `{"ok":true}`,
		"run stop":   `{"ok":true}`,
		"run status": `{"current":{"id":"j7","kind":"hajime","state":"running","summary":"初 · 月村手毬","started_at":1763900000},"recent":[{"id":"j6","kind":"nia","state":"done","summary":"N.I.A · 篠澤広","started_at":1763800000}]}`,
	})
	h.Live = "j7"
	v := h.Job("j7")
	v.Job = &plugin.Job{ID: "j7", Kind: "hajime", State: "running", Summary: "初 · 月村手毬", StartedAt: 1763900000}
	v.State = "running"
	v.Events = []plugin.JobEvent{
		step(1, "EVENT_CHOICE", "rule", "选第 2 个选项（体力 +）"),
		step(2, "SELECT_DRINK", "deepseek", "画面没认出来，问了一下 LLM"),
		step(3, "ACTION_SELECT", "gnrpa", "声乐课「レッスン」：声乐离上限还差 210"),
	}
	return h
}

func TestRunCardStreamsSteps(t *testing.T) {
	h, p := liveHost(), New()
	_, view := p.Cards["run"], p.Cards["run"]
	tt := ui.NewTester(func(c *ui.Context) { view(c, h, plugin.Card{"type": "run", "jobId": "j7"}) }, 760, 600)
	for _, s := range []string{"现在", "选行动", "算法", "LLM", "规则", "声乐课「レッスン」：声乐离上限还差 210", "选第 2 个选项（体力 +）"} {
		if !tt.HasText(s) {
			t.Errorf("对局卡里没有 %q：%q", s, tt.Texts())
		}
	}
	// 最新一步在最下面（流式往下长）
	r1, _ := tt.Find("选第 2 个选项（体力 +）")
	r3, _ := tt.Find("声乐课「レッスン」：声乐离上限还差 210")
	if r3.Y <= r1.Y {
		t.Error("最新的步骤应在下面")
	}
}

func TestPanelHasFrameControlsButNoSteps(t *testing.T) {
	h, p := liveHost(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 520, 760)
	if tt.HasText("声乐课「レッスン」：声乐离上限还差 210") {
		t.Error("右侧面板不放步骤")
	}
	for _, s := range []string{"初 · 月村手毬", "游玩中", "步数"} {
		if !tt.HasText(s) {
			t.Errorf("面板没有 %q：%q", s, tt.Texts())
		}
	}
	if err := tt.Click("暂停"); err != nil {
		t.Fatal(err)
	}
	if !h.Called("run pause --job j7") {
		t.Errorf("暂停没调命令：%v", h.Calls)
	}
}

func TestContextHasLiveJobAndRecentSteps(t *testing.T) {
	h, p := liveHost(), New()
	out := p.Context(context.Background(), h)
	for _, s := range []string{"正在跑：初 · 月村手毬", "3 步", "声乐课", "最近几局", "N.I.A · 篠澤広"} {
		if !strings.Contains(out, s) {
			t.Errorf("上下文没有 %q：\n%s", s, out)
		}
	}
}

// I5：对局结束后，面板还能看到这局的成绩和复盘。
func TestPanelShowsEndedJobScores(t *testing.T) {
	h, p := liveHost(), New()
	score := 4820.0
	v := h.Job("j7")
	v.State = "done"
	v.Job.State = "done"
	v.Events = append(v.Events, plugin.JobEvent{Seq: 9, Type: "report", Exams: []plugin.ExamScore{{Label: "最终试验", Score: &score}}})
	h.Live = ""
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "job:j7") }), 520, 900)
	for _, s := range []string{"初 · 月村手毬", "4,820", "看复盘"} {
		if !tt.HasText(s) {
			t.Errorf("结束后的面板没有 %q：%q", s, tt.Texts())
		}
	}
}

// 面板窄时竖着排：画面在上，按钮在画面下面。
func TestPanelIsVertical(t *testing.T) {
	h, p := liveHost(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 380, 900)
	pause, ok := tt.AccessByRole(ui.RoleButton, "暂停")
	if !ok || pause.Bounds.Y < 380 {
		t.Errorf("暂停按钮应在画面下面：%+v", pause.Bounds)
	}
}

// 11-25 review：出错的对局卡底部写原因（原因原来只在日志事件里，对局卡只画 step）
func TestRunCardShowsFailReason(t *testing.T) {
	h, p := liveHost(), New()
	v := h.Job("j7")
	v.State, v.Job.State, v.Job.Reason = "failed", "failed", "没打开支援卡选择列表"
	view := p.Cards["run"]
	tt := ui.NewTester(func(c *ui.Context) { view(c, h, plugin.Card{"type": "run", "jobId": "j7"}) }, 760, 600)
	tt.Frame()
	if !tt.HasText("没打开支援卡选择列表") {
		t.Errorf("对局卡没有出错原因：%q", tt.Texts())
	}
}
