package shell

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/common"
)

// 任务轮询（§6.3）：每秒 run status + 活着的 / 被看的任务 run events --since；窗口隐藏时 5 秒；任务结束就不再读事件。

// Job 是盯着的某个任务（第一次问就开始盯）。
func (a *App) Job(id string) *plugin.JobView {
	if id == "" {
		return nil
	}
	if a.jobs == nil {
		a.jobs = map[string]*plugin.JobView{}
	}
	v, ok := a.jobs[id]
	if !ok {
		v = &plugin.JobView{}
		a.jobs[id] = v
	}
	return v
}

// LiveJob 是现在活着的任务编号。
func (a *App) LiveJob() string { return a.live }

// wasLive 是这局在上次轮询时还活着；在本会话里开的局（onJobStarted 记过）第一次轮询前就结束了也算（CodeRabbit #79：开局即失败时不续做、不通知）。
func (a *App) wasLive(id, before string) bool {
	return plugin.LiveStates[before] || (before == "" && a.started[id])
}

func (a *App) jobLive() bool { return a.live != "" }

type statusData struct {
	Current *plugin.Job  `json:"current"`
	Recent  []plugin.Job `json:"recent"`
}

type eventsData struct {
	State  string            `json:"state"`
	Events []plugin.JobEvent `json:"events"`
}

// PollJobs 读一轮任务状态（后台读，回界面线程改）。上一轮没回来时不重叠。
func (a *App) PollJobs() {
	if a.polling {
		return
	}
	a.polling = true
	// 要读事件的：活着的那个 + 被看但还没读过或还活着的
	type want struct {
		id   string
		next int
	}
	var wants []want
	for id, v := range a.jobs {
		if !v.Final || v.Live() || id == a.live {
			wants = append(wants, want{id, v.Next})
		}
	}
	call := a.d.Call
	a.d.Run.Go(func() func() {
		var st statusData
		_ = call(a.ctx, []string{"run", "status"}).Decode(&st)
		got := map[string]eventsData{}
		seen := map[string]bool{}
		read := func(id string, next int) {
			if seen[id] {
				return
			}
			seen[id] = true
			var ev eventsData
			if call(a.ctx, []string{"run", "events", "--job", id, "--since", strconv.Itoa(next)}).Decode(&ev) == nil {
				got[id] = ev
			}
		}
		for _, w := range wants {
			read(w.id, w.next)
		}
		if st.Current != nil && plugin.LiveStates[st.Current.State] {
			next := 0
			for _, w := range wants {
				if w.id == st.Current.ID {
					next = w.next
				}
			}
			read(st.Current.ID, next)
		}
		return func() {
			a.polling = false
			before := map[string]string{}
			for id, v := range a.jobs {
				before[id] = v.State
			}
			a.live = ""
			jobs := append([]plugin.Job{}, st.Recent...)
			if st.Current != nil {
				jobs = append(jobs, *st.Current)
				if plugin.LiveStates[st.Current.State] {
					a.live = st.Current.ID
				}
			}
			for i := range jobs {
				j := jobs[i]
				if v, ok := a.jobs[j.ID]; ok || j.ID == a.live {
					if !ok {
						v = a.Job(j.ID)
					}
					v.Job = &j
					if v.State == "" || j.State != "" {
						v.State = j.State
					}
				}
			}
			for id, ev := range got {
				v := a.Job(id)
				last := -1
				if n := len(v.Events); n > 0 {
					last = v.Events[n-1].Seq
				}
				for _, e := range ev.Events {
					if e.Seq > last {
						v.Events = append(v.Events, e)
						last = e.Seq
					}
				}
				v.Next = last + 1
				v.Loaded = true
				if ev.State != "" {
					v.State = ev.State
				}
				if ev.State != "" && !plugin.LiveStates[ev.State] {
					v.Final = true
				}
			}
			busy := a.live != ""
			for id, v := range a.jobs {
				a.notify(id, before[id], v)
				a.jobEnded(id, before[id], v)
				if !v.Final {
					busy = true
				}
			}
			a.busyJobs.Store(busy)
		}
	})
}

// notify 在任务从「活着」变成要用户管的状态（接手、出错、结束、中断）时，在对话里冒一条消息（每局每种状态一次）。
func (a *App) notify(id, before string, v *plugin.JobView) {
	if !a.wasLive(id, before) || before == v.State || v.State == "running" || v.State == "stopping" {
		return
	}
	if a.notified == nil {
		a.notified = map[string]bool{}
	}
	key := id + "|" + v.State
	if a.notified[key] {
		return
	}
	a.notified[key] = true
	name := id
	if v.Job != nil {
		name = labels.Title(v.Job.Summary)
		if name == "" {
			name = labels.Kind(v.Job.Kind)
		}
	}
	var text string
	var cards []plugin.Card
	kind := ""
	if v.Job != nil {
		kind = v.Job.Kind
	}
	switch v.State {
	case "takeover":
		text = "「" + name + "」停下来等你接手了：在游戏里处理完，到游戏运行现状点「继续自动打」。"
	case "paused":
		text = "「" + name + "」已暂停。"
	case "done":
		switch {
		case produceKind(kind):
			text = "「" + name + "」打完了，成绩和复盘在游戏运行现状里。"
		case kind == "write-formation" && v.Job != nil && v.Job.Params["dry_run"] != true:
			// N36（G1 第 3 轮 11-26）：编成写好后对话停在「做完了」，不接着开局也不说下一步，小白要自己想到再说一次「开局」
			mode := labels.Kind(fmt.Sprint(v.Job.Params["scenario"]))
			if a.hasModel() {
				text = "「" + name + "」做完了，编成已经写进游戏。点下面的按钮就用这套编成开一局" + mode + "（还会给你确认卡）。"
				say := "用刚写好的编成开一局" + mode
				cards = []plugin.Card{{"type": "next", "label": "用这套编成开局", "say": say}}
			} else {
				text = "「" + name + "」做完了，编成已经写进游戏。要开局点侧栏的「开局」。"
			}
		case v.DoneText() != "":
			// 日常等任务的「结果：」（每项做了什么）
			text = "「" + name + "」做完了：" + v.DoneText() + "。"
		default:
			text = "「" + name + "」做完了。"
			// N31（G1 第 3 轮 11-26）：第一次三项扫描都做完后对话停在「做完了」，没说下一步能做什么。
			// 扫描类做完、后面没有排队的扫描了，补一句（notify 在 continueBatch 之前调，这时还看得到排队）
			// N59（G1 第 5 轮 11-26）：一键全收集（collect-all）做完也要给下一步，原来只认 scan- 开头的
			if (strings.HasPrefix(kind, "scan-") && kind != "scan-formations" || kind == "collect-all") && !a.queuedAfter(id) {
				if a.hasModel() {
					text += "扫描都做完了，现在可以直接说「打一把初」（或者 N.I.A），初星助手会按你的卡出编成方案、给你确认后开局。"
					// N27（G3 第 3 轮 11-26：开局要点输入框、打字、回车 3 下）：给两个按钮，点一下就把话发出去（照样出确认卡）
					cards = []plugin.Card{{"type": "next", "label": "打一把初", "say": "打一把初"},
						{"type": "next", "label": "打一把 N.I.A", "say": "打一把 N.I.A"}}
				} else {
					text += "扫描都做完了，要开局点侧栏的「开局」。"
				}
			}
		}
	case "failed":
		// 11-25 review：原因直接写出来（原来只说「在最后几步里」，可对局卡只画步骤，原因在日志里）
		if why := v.FailReason(); why != "" {
			text = "「" + name + "」出错停下了：" + why + "。"
		} else {
			text = "「" + name + "」出错停下了，日志里没写原因；可以到游戏运行现状看最后的画面。"
		}
		// G1-F2（11-26）：扫描这类任务出错后只有一句话，没有下一步；给「再试一次」（照样走权限 / 确认）。培育类不给，免得误开新一局——
		// 但停在开局前准备（写编成）、游戏还没开局的，重试不会多用一次培育（N61，G1 第 5 轮 11-27：只能在对话里自己打「再试一次」）
		// 只认任务结束后读到的完整事件（v.Final）：读事件失败、手里只有准备阶段的旧日志时不判断，不给重试（#194 CodeRabbit）
		preStart := v.Job != nil && v.Final && produceKind(kind) && failedBeforeStart(v.Job.Params, v.Events) && retryHelps(v.Job.ExitCode)
		if preStart {
			text += "游戏还没开局（停在开局前写编成），可以点「再试一次」。"
		}
		if v.Job != nil && kind != "" && (!produceKind(kind) || preStart) {
			params := v.Job.Params
			if kind == "collect-all" {
				params = resumeCollectAll(params, v.Events)
			}
			cards = []plugin.Card{{"type": "retry", "summary": name, "args": common.StartArgv(kind, params)}}
		}
	case "stopped":
		text = "「" + name + "」已停止。"
	default:
		text = "「" + name + "」" + labels.State(v.State) + "了。"
	}
	t := a.ownerThread(id)
	if t != a.pages {
		// 页面模式里开的局：下面 addTo 到页面记录时就会出提示条。对话里开的、命令行开的也出一条（两边都显示，用户 11-26 定）
		a.toastFor(text, cards)
	}
	if t == nil {
		return // 命令行等外部发起的：不进对话（11-25 review），在游戏运行现状的「后台任务」里看
	}
	a.addTo(t, "agent", text, cards)
	a.save(t)
}

// queuedAfter：这个任务后面还有排队等着做的（「全部开始」的合并确认卡记着 queueJob = 它、queue 还没空）。
func (a *App) queuedAfter(id string) bool {
	for _, t := range a.allThreads() { // 页面上合并确认的批次记在页面记录里（CodeRabbit #138）
		for _, m := range t.Messages {
			for _, card := range m.Cards {
				if card["type"] != "confirm" || card["queueJob"] != id {
					continue
				}
				if q, _ := card["queue"].([]any); len(q) > 0 {
					return true
				}
			}
		}
	}
	return false
}

// produceKind 是培育类任务（打完有成绩和复盘）。
// failedBeforeStart：带开局前准备（prepare）的开局任务停在准备阶段、还没开局——日志里没有 prepare_start 交给开局命令前的
// 「开局前准备做完，开局」。这时重试不会多用一次培育（N61）。
func failedBeforeStart(params map[string]any, events []plugin.JobEvent) bool {
	// 一条事件都没读到（读失败、任务在两次轮询之间就结束）：没法判断，按开了局算，不给重试（#194 review）
	if params["prepare"] != true || len(events) == 0 {
		return false
	}
	for _, e := range events {
		// 交给开局命令了，或者已经有对局步骤：都算开了局（多一道保险，不靠一句日志）
		if strings.Contains(e.Text, "开局前准备做完") || e.Type == "step" {
			return false
		}
	}
	return true
}

// retryHelps：开局前准备这几种停法照原样重试也没用，不提示重试（#194 review）——2 没有方案、4 编成位都满了（要玩家说覆盖哪一号）、
// 5 回忆库存不新鲜（要先重扫）。写编成没成功（3 / 程序出错）这类重试可能就过了。
func retryHelps(code *int) bool {
	if code == nil {
		return true
	}
	switch *code {
	case 2, 4, 5:
		return false
	}
	return true
}

func produceKind(kind string) bool {
	switch kind {
	case "hajime", "nia", "tower", "hif":
		return true
	}
	return false
}

// jobEnded 在任务从活着变成结束时调各插件的 OnJobEnd（每局一次）。
func (a *App) jobEnded(id, before string, v *plugin.JobView) {
	if a.reg == nil || !a.wasLive(id, before) || v.State == "" || plugin.LiveStates[v.State] || a.ended[id] {
		return
	}
	if a.ended == nil {
		a.ended = map[string]bool{}
	}
	a.ended[id] = true
	a.continueBatch(id, v)
	h := &jobHost{App: a, id: id}
	for _, p := range a.reg.All() {
		if p.OnJobEnd != nil {
			p.OnJobEnd(h, id, v)
		}
	}
}

// jobHost 是给 OnJobEnd 的宿主：Post 发到有这局对局卡的对话（用户可能已经切走）。
type jobHost struct {
	*App
	id string
}

func (h *jobHost) Post(text string, card plugin.Card) {
	t := h.ownerThread(h.id)
	if t == nil {
		return
	}
	var cards []plugin.Card
	if card != nil {
		cards = []plugin.Card{card}
	}
	h.addTo(t, "agent", text, cards)
	h.save(t)
}

// ownerThread 是有这局对局卡的对话（在对话里确认开的局）；命令行等外部发起的没有，返回 nil。
func (a *App) ownerThread(id string) *Thread {
	for _, t := range a.allThreads() {
		for _, m := range t.Messages {
			for _, c := range m.Cards {
				if c["type"] == "run" && c["jobId"] == id {
					return t
				}
			}
		}
	}
	return nil
}

// ChatJob 是这局是不是在某个对话里开的（页面上开的不算：对话界面的「后台任务」照样列出来，CodeRabbit #198）。
func (a *App) ChatJob(id string) bool {
	for _, t := range a.threads {
		for _, m := range t.Messages {
			for _, c := range m.Cards {
				if c["type"] == "run" && c["jobId"] == id {
					return true
				}
			}
		}
	}
	return false
}

// StartPolling 在后台一直轮询，直到 stop 关上。
func (a *App) StartPolling(stop <-chan struct{}) {
	go func() {
		for {
			a.d.Run.Post(a.PollJobs)
			d := time.Second
			if a.hidden.Load() || !a.busyJobs.Load() {
				d = 5 * time.Second
			}
			select {
			case <-stop:
				return
			case <-time.After(d):
			}
		}
	}()
}

// onJobStarted 在确认开局后被调用：开始盯这局，第一次自动打开游戏运行现状。
func (a *App) onJobStarted(card map[string]any) {
	id, _ := card["jobId"].(string)
	if id == "" {
		return
	}
	if a.started == nil {
		a.started = map[string]bool{}
	}
	a.started[id] = true
	a.Job(id)
	if a.ui.autoOpened == nil {
		a.ui.autoOpened = map[string]bool{}
	}
	if a.ui.autoOpened[id] {
		return
	}
	a.ui.autoOpened[id] = true
	if a.reg.ByID("cockpit") != nil {
		// 开的是这一局（route "job:<id>"）：结束 / 出错后面板还停在这局，看得到成绩和出错原因（11-25 Windows 实测：
		// 原来开 route ""，任务一结束面板就变成「现在没有在跑的对局」，看不出成没成）
		a.OpenPanel("cockpit", "job:"+id)
	}
}

// showPinned 是要不要钉顶：有活任务，且当前对话里没有这局的对局卡（有的话那张卡就在对话里，不重复、不压住对话）。
func (a *App) showPinned() bool {
	if a.live == "" {
		return false
	}
	if a.cur != nil {
		for _, m := range a.cur.Messages {
			for _, c := range m.Cards {
				if c["type"] == "run" && c["jobId"] == a.live {
					return false
				}
			}
		}
	}
	return true
}

// pinned 是钉在对话顶上的对局卡（有活着的任务时，每个对话都有）。
func (a *App) pinned(c *ui.Context) {
	if !a.showPinned() {
		return
	}
	card := plugin.Card{"type": "run-pinned", "jobId": a.live}
	ui.Column(c).Shrink(0).Padding(14, a.chatPad(), 8, a.chatPad()).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
		if _, v := a.reg.CardView("run-pinned"); v != nil {
			v(c, a, card)
			return
		}
		v := a.Job(a.live)
		title := a.live
		if v.Job != nil {
			title = labels.Title(v.Job.Summary)
			if title == "" {
				title = labels.Kind(v.Job.Kind)
			}
		}
		if kit.RowButton(c, "pinned-"+a.live, title, false).Padding(10, 12).Radius(kit.R16-2).Background(kit.CardBg).Border(kit.Hair, kit.LineStr).Children(func() {
			ui.Box(c).Size(7, 7).Radius(4).Background(kit.Brand)
			ui.Text(c, title).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
			kind := ""
			if v.Job != nil {
				kind = v.Job.Kind
			}
			ui.Text(c, labels.StateOf(kind, v.State)).FontSize(kit.FNote).TextColor(kit.Ink3)
		}).Clicked() {
			a.OpenPanel("cockpit", "job:"+a.live)
		}
	})
}

// collectAllSteps 是一键全收集的步骤顺序（同 hiro.collect_all.STEPS）。
var collectAllSteps = []string{"account", "idols", "supports", "formations", "memories"}

var reCollectStep = regexp.MustCompile(`一键全收集：第 (\d+)/\d+ 步`)

// resumeCollectAll：一键全收集出错后「再试一次」从出错的那一步接着做，前面做完、已入库的几步不再重扫
// （N41，G2 11-26：第 3 步支援卡出错，重试又从首页数值、偶像卡做起，白扫 8 分钟）。读不出第几步时原样返回。
func resumeCollectAll(params map[string]any, events []plugin.JobEvent) map[string]any {
	at := 0
	for _, e := range events {
		if m := reCollectStep.FindStringSubmatch(e.Text); m != nil {
			at, _ = strconv.Atoi(m[1])
		}
	}
	if at < 1 || at > len(collectAllSteps) {
		return params
	}
	want := map[string]bool{}
	if only, _ := params["only"].(string); only != "" {
		for _, k := range strings.Split(only, ",") {
			want[strings.TrimSpace(k)] = true
		}
	}
	var left []string
	for i, k := range collectAllSteps {
		if i+1 >= at && (len(want) == 0 || want[k]) {
			left = append(left, k)
		}
	}
	if len(left) == 0 {
		return params
	}
	out := map[string]any{}
	for k, v := range params {
		out[k] = v
	}
	out["only"] = strings.Join(left, ",")
	return out
}
