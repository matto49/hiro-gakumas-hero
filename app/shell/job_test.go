package shell

import (
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func TestClampChatPercent(t *testing.T) {
	for _, c := range []struct{ p, w, want float32 }{
		{55, 1048, 55},                   // 默认：面板 45%
		{10, 1048, 400.0 / 1048 * 100},   // 对话至少 400 px
		{20, 2000, 30},                   // 面板最多 70%
		{95, 1048, 100 - 300.0/1048*100}, // 面板最窄 300 px
	} {
		if got := ClampChatPercent(c.p, c.w); got < c.want-0.01 || got > c.want+0.01 {
			t.Errorf("ClampChatPercent(%v, %v) = %v，要 %v", c.p, c.w, got, c.want)
		}
	}
}

func liveCall(state string) *fakeCall {
	return &fakeCall{resp: map[string]hiroapi.Result{
		"run status": ok(`{"current":{"id":"j7","kind":"hajime","state":"` + state + `","summary":"初 · 月村手毬","started_at":1}, "recent":[]}`),
		"run events": ok(`{"state":"` + state + `","events":[{"seq":0,"t":2,"type":"step","text":"选了上课","step":1,"page":"ACTION_SELECT","source":"gnrpa"}]}`),
	}}
}

func TestLiveJobPinnedAndOpensPanel(t *testing.T) {
	reg := viewReg()
	reg.Add(&plugin.Plugin{ID: "cockpit", Name: "游戏运行现状", Panel: func(c *ui.Context, _ plugin.Host, _ string) { ui.Text(c, "实时画面") }})
	a, run := newTestApp(newMem(), liveCall("running"), nil, reg)
	a.PollJobs()
	run.Drain()
	tt := ui.NewTester(a.View, 1280, 800)
	if !tt.HasText("初 · 月村手毬") {
		t.Fatalf("没有钉住的对局卡：%q", tt.Texts())
	}
	if v := a.Job("j7"); v == nil || len(v.Steps()) != 1 || !v.Live() {
		t.Fatalf("任务数据 %+v", v)
	}
	if err := tt.Click("初 · 月村手毬"); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	if !a.ui.panelOpen || a.ui.panelPlugin != "cockpit" || !tt.HasText("实时画面") {
		t.Errorf("没打开游戏运行现状：%q", tt.Texts())
	}
}

func TestPollingStopsWhenJobEnds(t *testing.T) {
	call := liveCall("done")
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j7","kind":"hajime","state":"done","started_at":1}]}`)
	a, run := newTestApp(newMem(), call, nil, nil)
	a.Job("j7") // 对话里的对局卡要看这局
	a.PollJobs()
	run.Drain()
	if v := a.Job("j7"); v == nil || v.State != "done" || len(v.Events) != 1 {
		t.Fatalf("结束的任务也读一次：%+v", v)
	}
	n := len(call.calls)
	a.PollJobs()
	run.Drain()
	for _, c := range call.calls[n:] {
		if c[0] == "run" && c[1] == "events" {
			t.Errorf("结束了还在读事件：%v", c)
		}
	}
	if a.LiveJob() != "" {
		t.Error("没有活着的任务")
	}
}

func TestConfirmStartOpensPanelOnce(t *testing.T) {
	a, run, _, tt := confirmApp(t, 4102444800)
	a.reg.Add(&plugin.Plugin{ID: "cockpit", Name: "游戏运行现状", Panel: func(c *ui.Context, _ plugin.Host, _ string) {}})
	tt.Click("开始")
	run.Drain()
	if !a.ui.panelOpen || a.ui.panelPlugin != "cockpit" {
		t.Error("开局后自动打开游戏运行现状")
	}
	a.ClosePanel()
	a.onJobStarted(plugin.Card{"type": "run", "jobId": "j7"})
	if a.ui.panelOpen {
		t.Error("同一局关掉后不再自动打开")
	}
}

// I4：结束了的任务读到一次之后就不再查事件，即使一条事件都没有。
func TestEndedJobWithoutEventsStopsPolling(t *testing.T) {
	call := &fakeCall{resp: map[string]hiroapi.Result{
		"run status": ok(`{"current":null,"recent":[{"id":"j9","kind":"daily","state":"done","started_at":1}]}`),
		"run events": ok(`{"state":"done","events":[]}`),
	}}
	a, run := newTestApp(newMem(), call, nil, nil)
	a.Job("j9")
	a.PollJobs()
	run.Drain()
	n := len(call.calls)
	for i := 0; i < 3; i++ {
		a.PollJobs()
		run.Drain()
	}
	for _, c := range call.calls[n:] {
		if c[0] == "run" && c[1] == "events" {
			t.Fatalf("结束的任务还在被查：%v", c)
		}
	}
}

// M13：任务停下要用户管时（接手、出错、结束），在对话里冒一条消息，只冒一次。
// ownJob 让这局属于当前对话（对话里有它的对局卡，像在对话里确认开的局）。
func ownJob(a *App, id string) {
	a.add("agent", "", []plugin.Card{{"type": "run", "jobId": id}})
}

func TestJobStateChangePostsOnce(t *testing.T) {
	call := liveCall("running")
	a, run := newTestApp(newMem(), call, nil, nil)
	a.NewThread()
	ownJob(a, "j7")
	a.PollJobs()
	run.Drain()
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j7","kind":"hajime","state":"failed","summary":"初 · 月村手毬","started_at":1}]}`)
	call.resp["run events"] = ok(`{"state":"failed","events":[]}`)
	for i := 0; i < 3; i++ {
		a.PollJobs()
		run.Drain()
	}
	n := 0
	for _, m := range a.Current().Messages {
		if strings.Contains(m.Text, "出错停下") {
			n++
		}
	}
	if n != 1 {
		t.Errorf("出错消息 %d 条", n)
	}
}

// I1：算上下文用的是界面线程上拍下的快照，后台读不到之后的修改。
func TestContextHostIsSnapshot(t *testing.T) {
	a, run := newTestApp(newMem(), liveCall("running"), nil, nil)
	a.PollJobs()
	run.Drain()
	h := a.snapshotHost()
	a.jobs["j7"].Events = nil
	a.live = ""
	if h.LiveJob() != "j7" || len(h.Job("j7").Steps()) != 1 {
		t.Error("快照跟着变了")
	}
}

// 面板默认约 380 px 宽（不是窗口的一半）。
func TestDefaultPanelIsNarrow(t *testing.T) {
	for _, w := range []float32{1048, 1500} {
		p := DefaultChatPercent(w)
		panel := w * (100 - p) / 100
		if panel < 320 || panel > 360 {
			t.Errorf("主区 %v 时面板 %v px", w, panel)
		}
	}
	if got := ClampChatPercent(90, 1048); 1048*(100-got)/100 < 299 {
		t.Errorf("面板最窄 300 px，得到 %v%%", got)
	}
}

// 当前对话里已经有这局的对局卡时，不再在顶上钉一张（不重复、不压住对话）。
func TestNoPinnedCardWhenRunCardInThread(t *testing.T) {
	reg := viewReg()
	a, run := newTestApp(newMem(), liveCall("running"), nil, reg)
	a.NewThread()
	a.PollJobs()
	run.Drain()
	tt := ui.NewTester(a.View, 1280, 800)
	if !tt.HasText("初 · 月村手毬") {
		t.Fatal("别的对话里要钉顶")
	}
	a.Post("", plugin.Card{"type": "run", "jobId": "j7"})
	tt.Frame()
	if a.showPinned() {
		t.Error("对话里有这局的对局卡时不钉顶")
	}
}

// CR#12：任务结束那一轮读事件失败了，下一轮还要再读（最后几步、成绩不能丢）。
func TestFinalEventsRetriedAfterFailure(t *testing.T) {
	call := liveCall("running")
	a, run := newTestApp(newMem(), call, nil, nil)
	a.PollJobs()
	run.Drain()
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j7","kind":"hajime","state":"done","started_at":1}]}`)
	call.resp["run events"] = hiroapi.Result{OK: false, Error: &hiroapi.CliError{Code: "x", Message: "暂时读不到"}}
	a.PollJobs()
	run.Drain()
	call.resp["run events"] = ok(`{"state":"done","events":[{"seq":1,"t":3,"type":"report","exams":[{"label":"最终","score":100}]}]}`)
	a.PollJobs()
	run.Drain()
	if len(a.Job("j7").Report()) == 0 {
		t.Error("结束后的成绩没再读到")
	}
}

func TestOnJobEndCalledOnceWhenJobFinishes(t *testing.T) {
	var ended []string
	reg := &plugin.Registry{}
	reg.Add(&plugin.Plugin{ID: "u", Name: "用量", OnJobEnd: func(h plugin.Host, id string, v *plugin.JobView) {
		ended = append(ended, id+"|"+v.State)
		h.Post("", plugin.Card{"type": "usage-job", "jobId": id})
	}})
	call := liveCall("running")
	a, run := newTestApp(newMem(), call, nil, reg)
	jobThread := a.NewThread()
	a.Post("", plugin.Card{"type": "run", "jobId": "j7"})
	a.PollJobs()
	run.Drain()
	call.resp["run status"] = ok(`{"current":{"id":"j7","kind":"hajime","state":"paused","summary":"初","started_at":1},"recent":[]}`)
	call.resp["run events"] = ok(`{"state":"paused","events":[]}`)
	a.PollJobs()
	run.Drain()
	if len(ended) != 0 {
		t.Fatalf("暂停不算结束：%v", ended)
	}
	other := a.NewThread() // 用户切到别的对话
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j7","kind":"hajime","state":"done","summary":"初","started_at":1}]}`)
	call.resp["run events"] = ok(`{"state":"done","events":[]}`)
	for i := 0; i < 3; i++ {
		a.PollJobs()
		run.Drain()
	}
	if len(ended) != 1 || ended[0] != "j7|done" {
		t.Fatalf("结束钩子 %v", ended)
	}
	has := func(th *Thread) bool {
		for _, m := range th.Messages {
			for _, c := range m.Cards {
				if c["type"] == "usage-job" {
					return true
				}
			}
		}
		return false
	}
	if !has(jobThread) || has(other) {
		t.Fatal("用量卡要发到有这局对局卡的对话里")
	}
}

// 11-25 review：出错原因要写进通知；非培育任务完成不说「成绩和复盘」，用户停下的不说「原因在最后几步里」
func TestJobNoticeTexts(t *testing.T) {
	cases := []struct {
		kind, state, status, events, want, not string
	}{
		{"hajime", "failed", `"reason":"没打开支援卡选择列表"`, `[]`, "出错停下了：没打开支援卡选择列表", "最后几步"},
		{"hajime", "failed", ``, `[{"seq":1,"t":1,"type":"log","text":"[2026-11-24 01:31:04,918] [ERROR] [hiro.runtime.play] new 开局要编成号"},{"seq":2,"t":2,"type":"failed","text":"任务出错退出（代码 1），看上面的日志"}]`, "出错停下了：new 开局要编成号", "最后几步"},
		{"hajime", "stopped", ``, `[]`, "已停止", "原因"},
		{"scan-memories", "done", ``, `[]`, "做完了", "成绩和复盘"},
		{"hajime", "done", ``, `[]`, "成绩和复盘", ""},
	}
	for _, c := range cases {
		call := liveCall("running")
		a, run := newTestApp(newMem(), call, nil, nil)
		a.NewThread()
		ownJob(a, "j7")
		a.PollJobs()
		run.Drain()
		extra := ""
		if c.status != "" {
			extra = "," + c.status
		}
		call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j7","kind":"` + c.kind + `","state":"` + c.state + `","summary":"某任务","started_at":1` + extra + `}]}`)
		call.resp["run events"] = ok(`{"state":"` + c.state + `","events":` + c.events + `}`)
		for i := 0; i < 3; i++ {
			a.PollJobs()
			run.Drain()
		}
		got := ""
		for _, m := range a.Current().Messages {
			if strings.Contains(m.Text, "某任务") {
				got = m.Text
			}
		}
		if !strings.Contains(got, c.want) || (c.not != "" && strings.Contains(got, c.not)) {
			t.Errorf("%s/%s：%q", c.kind, c.state, got)
		}
	}
}

// 11-25 review：命令行等外部发起的任务不进对话（原来 26 条通知全塞进一个「新对话」），只在「后台任务」里看
func TestExternalJobNotPostedToChat(t *testing.T) {
	call := liveCall("running")
	a, run := newTestApp(newMem(), call, nil, nil)
	a.PollJobs()
	run.Drain()
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j7","kind":"hajime","state":"failed","summary":"初 · 月村手毬","started_at":1,"reason":"掉线"}]}`)
	call.resp["run events"] = ok(`{"state":"failed","events":[]}`)
	for i := 0; i < 3; i++ {
		a.PollJobs()
		run.Drain()
	}
	if len(a.threads) != 0 {
		t.Fatalf("外部任务不该建对话：%d 个", len(a.threads))
	}
	if a.ChatJob("j7") {
		t.Error("外部任务不该算对话里开的")
	}
}

// 在对话里确认开的局：通知回到那个对话，哪怕用户已经切到别的对话（「agent 发起的对局直接统计进当前会话」）
func TestChatJobNoticeGoesToItsThread(t *testing.T) {
	a, run, call, tt := confirmApp(t, 4102444800)
	first := a.Current()
	if err := tt.Click("开始"); err != nil {
		t.Fatal(err)
	}
	run.Drain()
	if !a.ChatJob("j7") {
		t.Fatal("确认卡开的局应算这个对话的")
	}
	call.resp["run status"] = ok(`{"current":{"id":"j7","kind":"hajime","state":"running","summary":"初","started_at":1},"recent":[]}`)
	call.resp["run events"] = ok(`{"state":"running","events":[]}`)
	a.PollJobs()
	run.Drain()
	a.NewThread()
	other := a.Current()
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j7","kind":"hajime","state":"done","summary":"初","started_at":1}]}`)
	call.resp["run events"] = ok(`{"state":"done","events":[]}`)
	a.PollJobs()
	run.Drain()
	if len(other.Messages) != 0 {
		t.Errorf("通知跑到了别的对话：%+v", other.Messages)
	}
	last := first.Messages[len(first.Messages)-1]
	if !strings.Contains(last.Text, "打完了") {
		t.Errorf("通知没回到开局的对话：%q", last.Text)
	}
}
