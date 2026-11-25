package shell

import (
	"strconv"
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
		name = v.Job.Summary
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
		if produceKind(kind) {
			text = "「" + name + "」打完了，成绩和复盘在游戏运行现状里。"
		} else {
			text = "「" + name + "」做完了。"
		}
	case "failed":
		// 11-25 review：原因直接写出来（原来只说「在最后几步里」，可对局卡只画步骤，原因在日志里）
		if why := v.FailReason(); why != "" {
			text = "「" + name + "」出错停下了：" + why + "。"
		} else {
			text = "「" + name + "」出错停下了，日志里没写原因；可以到游戏运行现状看最后的画面。"
		}
		// G1-F2（11-26）：扫描这类任务出错后只有一句话，没有下一步；给「再试一次」（照样走权限 / 确认）。培育类不给，免得误开新一局
		if v.Job != nil && kind != "" && !produceKind(kind) {
			cards = []plugin.Card{{"type": "retry", "summary": name, "args": common.StartArgv(kind, v.Job.Params)}}
		}
	case "stopped":
		text = "「" + name + "」已停止。"
	default:
		text = "「" + name + "」" + labels.State(v.State) + "了。"
	}
	t := a.ownerThread(id)
	if t == nil {
		return // 命令行等外部发起的：不进对话（11-25 review），在游戏运行现状的「后台任务」里看
	}
	a.addTo(t, "agent", text, cards)
	a.save(t)
}

// produceKind 是培育类任务（打完有成绩和复盘）。
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
	for _, t := range a.threads {
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

// ChatJob 是这局是不是在某个对话里开的。
func (a *App) ChatJob(id string) bool { return a.ownerThread(id) != nil }

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
		a.OpenPanel("cockpit", "")
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
			title = v.Job.Summary
			if title == "" {
				title = labels.Kind(v.Job.Kind)
			}
		}
		if kit.RowButton(c, "pinned-"+a.live, title, false).Padding(10, 12).Radius(kit.R16-2).Background(kit.CardBg).Border(kit.Hair, kit.LineStr).Children(func() {
			ui.Box(c).Size(7, 7).Radius(4).Background(kit.Brand)
			ui.Text(c, title).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
			ui.Text(c, labels.State(v.State)).FontSize(kit.FNote).TextColor(kit.Ink3)
		}).Clicked() {
			a.OpenPanel("cockpit", "")
		}
	})
}
