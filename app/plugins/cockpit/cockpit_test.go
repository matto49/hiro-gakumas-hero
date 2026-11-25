package cockpit

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
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

// 11-25 review：没有动作的步骤不进流；一场考试合成一行；理由去掉内部用语
func TestStreamMergesExamAndDropsEmpty(t *testing.T) {
	h, p := liveHost(), New()
	v := h.Job("j7")
	ex := func(seq int, action, text string) plugin.JobEvent {
		e := step(seq, "HIF_EXAM", "bench", text)
		e.Action = action
		return e
	}
	sel := step(2, "SELECT_CARD", "rule", "选卡 オトメゴコロ（攻略 hajime.style-review.card_pick.stack、加好印象）（牌组精简 [g8-PRO]）")
	sel.Action = "ActivateEffect"
	v.Events = []plugin.JobEvent{
		step(1, "ALLOWANCE", "", ""),
		sel,
		ex(3, "PlayCard", "ExamGnrpa（画面读数）：目線の基本；终局分预测中位 92"),
		ex(4, "UseDrink", "ExamGnrpa（画面读数）：饮料；终局分预测中位 900"),
		ex(5, "PlayCard", "ExamGnrpa（画面读数）：手拍子+；终局分预测中位 10025"),
		ex(6, "Skip", "ExamGnrpa（画面读数）：跳过；终局分预测中位 10025"),
		step(7, "ACTION_SELECT", "rule", "上课程 Vi"),
	}
	view := p.Cards["run"]
	tt := ui.NewTester(func(c *ui.Context) { view(c, h, plugin.Card{"type": "run", "jobId": "j7"}) }, 760, 800)
	tt.Frame()
	all := strings.Join(tt.Texts(), "\n")
	for _, s := range []string{"选卡 オトメゴコロ（加好印象）（牌组精简）", "出了 2 张牌、1 瓶饮料", "预计终局 10025 分", "上课程 Vi"} {
		if !strings.Contains(all, s) {
			t.Errorf("流里没有 %q：%q", s, tt.Texts())
		}
	}
	for _, s := range []string{"没写理由", "[g8-PRO]", "攻略 hajime", "ExamGnrpa", "目線の基本"} {
		if strings.Contains(all, s) {
			t.Errorf("流里不该有 %q：%q", s, tt.Texts())
		}
	}
	// 点开考试那一行看逐回合
	if err := tt.Click("展开这场考试"); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	if all := strings.Join(tt.Texts(), "\n"); !strings.Contains(all, "出 目線の基本 · 预计终局 92 分") {
		t.Errorf("展开后没有逐回合：%q", tt.Texts())
	}
}

// 外部发起的任务只在这里的「后台任务」里看（11-25 review）
func TestPanelListsBackgroundJobs(t *testing.T) {
	h := plugintest.New(map[string]string{
		"run status": `{"current":null,"recent":[{"id":"j5","kind":"hajime","state":"done","summary":"对话里开的局","started_at":1763800000},{"id":"j9","kind":"scan-memories","state":"failed","summary":"命令行开的扫描","started_at":1763900000,"reason":"掉线"}]}`,
	})
	h.Chat = map[string]bool{"j5": true}
	p := New()
	tt := ui.NewTester(func(c *ui.Context) { p.Panel(c, h, "") }, 420, 900)
	tt.Frame()
	tt.Frame()
	all := strings.Join(tt.Texts(), "\n")
	if !strings.Contains(all, "后台任务") || !strings.Contains(all, "命令行开的扫描") {
		t.Errorf("没列外部任务：%q", tt.Texts())
	}
	if strings.Contains(all, "对话里开的局") {
		t.Errorf("对话里开的局不算后台任务：%q", tt.Texts())
	}
	if err := tt.Click("命令行开的扫描"); err != nil {
		t.Fatal(err)
	}
	if h.Panel != [2]string{"cockpit", "job:j9"} {
		t.Errorf("点了没打开这局：%v", h.Panel)
	}
}

// CodeRabbit #79：两场考试之间只有空步，也要分成两行
func TestEmptyStepSeparatesExams(t *testing.T) {
	ex := func(seq int) plugin.JobEvent {
		e := step(seq, "HIF_EXAM", "bench", "ExamGnrpa（画面读数）：x；终局分预测中位 1")
		e.Action = "PlayCard"
		return e
	}
	rows := streamRows([]plugin.JobEvent{ex(1), ex(2), step(3, "ALLOWANCE", "", ""), ex(4)})
	if len(rows) != 2 || len(rows[0].exam) != 2 || len(rows[1].exam) != 1 {
		t.Errorf("rows %+v", rows)
	}
}

// 后台任务点进来看详情：出错原因要在面板里（外部任务没有对局卡）
func TestPanelDetailShowsFailReason(t *testing.T) {
	h, p := liveHost(), New()
	v := h.Job("j7")
	v.State, v.Job.State, v.Job.Reason = "failed", "failed", "没打开支援卡选择列表"
	tt := ui.NewTester(func(c *ui.Context) { p.Panel(c, h, "job:j7") }, 420, 1400)
	tt.Frame()
	if !tt.HasText("没打开支援卡选择列表") {
		t.Errorf("面板详情没有出错原因：%q", tt.Texts())
	}
}

// CodeRabbit #79：刷新后台任务失败时保留旧列表，写明原因
func TestBackgroundKeepsListWhenRefreshFails(t *testing.T) {
	h := plugintest.New(map[string]string{
		"run status": `{"current":null,"recent":[{"id":"j9","kind":"scan-memories","state":"done","summary":"命令行开的扫描","started_at":1763900000}]}`,
	})
	p := New()
	tt := ui.NewTester(func(c *ui.Context) { p.Panel(c, h, "") }, 420, 900)
	tt.Frame()
	tt.Frame()
	h.Resp["run status"] = hiroapi.Result{OK: false, Error: &hiroapi.CliError{Code: "service_down", Message: "服务没开"}}
	h.At = h.At.Add(time.Minute)
	tt.Frame()
	tt.Frame()
	all := strings.Join(tt.Texts(), "\n")
	if !strings.Contains(all, "命令行开的扫描") || !strings.Contains(all, "刷新失败") {
		t.Errorf("刷新失败后：%q", tt.Texts())
	}
}

// 11-24 实测清单「结束后面板空」（11-26 真窗口复现：对话里开的局刚出错停下，右侧面板只写「现在没有在跑的对局」）：
// 没有在跑的局时，显示刚结束的那一局（名字、状态、出错原因 / 成绩、复盘入口），对话里开的也算。
func TestIdlePanelShowsLastEndedJob(t *testing.T) {
	h := plugintest.New(map[string]string{
		"run status": `{"current":null,"recent":[{"id":"j5","kind":"write-formation","state":"failed","summary":"对照编成方案看 初 的编成","started_at":1763990000,"reason":"等不到support 页签"},{"id":"j4","kind":"hajime","state":"done","summary":"更早的一局","started_at":1763800000}]}`,
	})
	h.Chat = map[string]bool{"j5": true, "j4": true}
	v := h.Job("j5")
	v.State = "failed"
	v.Job = &plugin.Job{ID: "j5", Kind: "write-formation", State: "failed", Summary: "对照编成方案看 初 的编成", Reason: "等不到support 页签"}
	p := New()
	tt := ui.NewTester(func(c *ui.Context) { p.Panel(c, h, "") }, 420, 1400)
	tt.Frame()
	tt.Frame()
	all := strings.Join(tt.Texts(), "\n")
	for _, s := range []string{"现在没有在跑的对局", "刚结束的一局", "等不到support 页签"} {
		if !strings.Contains(all, s) {
			t.Errorf("空闲面板没有 %q：%q", s, tt.Texts())
		}
	}
	if strings.Contains(all, "更早的一局") {
		t.Errorf("只放最近一局，更早的对话局不进来：%q", tt.Texts())
	}
}

// N6（11-26 G1）：扫描这类任务不说「游玩中」「0 步」「已打」「正在连游戏、开局…」「等第一张画面」，写做到哪了。
func TestScanJobUsesTaskWording(t *testing.T) {
	h, p := liveHost(), New()
	v := h.Job("j7")
	v.Job = &plugin.Job{ID: "j7", Kind: "scan-memories", State: "running", Summary: "扫描回忆库存（只读，不改编成）", StartedAt: float64(h.Now().Unix() - 120)}
	v.Events = []plugin.JobEvent{
		{Seq: 0, Type: "start", Text: "扫描回忆库存"},
		{Seq: 1, Type: "log", Text: "[2026-11-26 04:51:34,433] [INFO] [hiro.produce.memory_picker] 回忆扫描：第 70 页，已读 204 格"},
	}
	card := ui.NewTester(func(c *ui.Context) { p.Cards["run"](c, h, plugin.Card{"type": "run", "jobId": "j7"}) }, 760, 600)
	card.Frame()
	panel := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "job:j7") }), 380, 900)
	panel.Frame()
	for _, tt := range []*ui.Tester{card, panel} {
		if !tt.HasText("回忆扫描：第 70 页，已读 204 格") || !tt.HasText("进行中") {
			t.Errorf("应写做到哪了、状态「进行中」：%q", tt.Texts())
		}
		for _, bad := range []string{"游玩中", "0 步", "正在连游戏、开局…", "等第一张画面", "步数", "已打"} {
			for _, s := range tt.Texts() {
				if strings.Contains(s, bad) {
					t.Errorf("扫描任务不该出现 %q：%q", bad, s)
				}
			}
		}
	}
	v.State, v.Job.State = "done", "done"
	v.Events = append(v.Events, plugin.JobEvent{Seq: 2, Type: "done", Text: "完成"})
	card.Frame()
	if card.HasText("还没有步骤") {
		t.Errorf("做完的扫描不该写「还没有步骤」：%q", card.Texts())
	}
}

// #109 review：没日志的暂停 / 等接手的任务不说「正在连游戏…」，按状态说。
func TestScanProgressFollowsState(t *testing.T) {
	for state, want := range map[string]string{"running": "正在连游戏…", "paused": labels.State("paused"), "takeover": labels.State("takeover")} {
		v := &plugin.JobView{State: state, Job: &plugin.Job{Kind: "scan-idols", State: state}}
		if got := progress(v); got != want || got == "" {
			t.Errorf("%s：%q，要 %q", state, got, want)
		}
	}
}
