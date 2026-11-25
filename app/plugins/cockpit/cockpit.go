// Package cockpit 是内置的「游戏运行现状」插件（原驾驶舱）：对话里的对局卡和流式步骤、钉顶对局卡、
// 右侧面板的实时画面和控制（设计稿 §4、§6.3）。
package cockpit

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// PanelFrameWidth 是右侧面板里实时画面的宽（9:16；面板缺省 340 宽，去掉边距 308）。
const PanelFrameWidth float32 = 260

// MaxStreamSteps 是对话里对局卡最多列多少步（更早的折叠成一行）。
const MaxStreamSteps = 30

type state struct {
	busy    map[string]bool // 正在发控制命令的任务
	review  map[string]string
	reviewE map[string]string
	// openExam 是对局卡里点开了逐回合的那几场考试
	openExam map[string]bool
	// 后台任务：最近的任务（run status），隔一会儿重查
	recent    []plugin.Job
	recentAt  time.Time
	recentGet bool
	recentErr string // 上次刷新失败的原因（列表保留上次成功的）
}

// New 建游戏运行现状插件。
func New() *plugin.Plugin {
	s := &state{busy: map[string]bool{}, review: map[string]string{}, reviewE: map[string]string{}}
	return &plugin.Plugin{
		ID:    "cockpit",
		Name:  "游戏运行现状",
		Icon:  kit.IcGauge,
		Tools: []string{"run events", "run review", "run pause", "run resume", "run stop", "run takeover"},
		Cards: map[string]plugin.CardView{
			"run":        func(c *ui.Context, h plugin.Host, card plugin.Card) { s.runCard(c, h, jobID(card)) },
			"run-pinned": func(c *ui.Context, h plugin.Host, card plugin.Card) { s.pinned(c, h, jobID(card)) },
		},
		Panel:        func(c *ui.Context, h plugin.Host, route string) { s.panel(c, h, route) },
		ContextTitle: "游戏运行现状",
		Context:      func(ctx context.Context, h plugin.Host) string { return s.context(ctx, h) },
	}
}

func jobID(card plugin.Card) string {
	id, _ := card["jobId"].(string)
	return id
}

func title(v *plugin.JobView, id string) string {
	if v != nil && v.Job != nil {
		if v.Job.Summary != "" {
			return v.Job.Summary
		}
		return labels.Kind(v.Job.Kind)
	}
	return "对局 " + id
}

func elapsed(h plugin.Host, v *plugin.JobView) string {
	if v == nil || v.Job == nil || v.Job.StartedAt == 0 {
		return ""
	}
	end := float64(h.Now().Unix())
	if v.Job.EndedAt != nil {
		end = *v.Job.EndedAt
	}
	return labels.Elapsed(v.Job.StartedAt, end)
}

func stats(h plugin.Host, v *plugin.JobView) string {
	parts := []string{fmt.Sprintf("%d 步", len(v.Steps()))}
	if e := elapsed(h, v); e != "" {
		if v.Live() {
			parts = append(parts, "已打 "+e)
		} else {
			parts = append(parts, "用时 "+e)
		}
	}
	return strings.Join(parts, " · ")
}

// ---- 对话里的对局卡：摘要 + 流式步骤 ----

func (s *state) runCard(c *ui.Context, h plugin.Host, id string) {
	v := h.Job(id)
	steps := v.Steps()
	ui.Column(c).MaxWidth(640).Radius(kit.R16).Background(kit.CardBg).Border(kit.Hair, kit.LineStr).Clip().Children(func() {
		head := ui.ButtonBase(c).Key("run-head-"+id).Label(title(v, id)).Justify(ui.Start).AlignItems(ui.Center).Gap(10).Padding(10, 12).
			BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0)
		if head.Hovered() {
			head = head.Background(kit.Surface2)
		}
		if head.Children(func() {
			kit.Thumb(c, v.LastFrame(), 27, 48, 5)
			ui.Column(c).Gap(3).Shrink(1).Children(func() {
				ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
					ui.Text(c, title(v, id)).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
					kit.StatePill(c, v.State)
				})
				ui.Text(c, stats(h, v)).FontSize(kit.FNote).TextColor(kit.Ink3)
			})
			ui.Spacer(c)
			ui.Text(c, "看画面 ›").FontSize(kit.FNote).TextColor(kit.Ink3)
		}).Clicked() {
			h.OpenPanel("cockpit", "job:"+id)
		}
		ui.Column(c).Padding(4, 8, 8, 8).Children(func() {
			rows := streamRows(steps)
			if len(rows) == 0 {
				msg := "还没有步骤"
				if v.Live() {
					msg = "正在连游戏、开局…"
				}
				ui.Text(c, msg).FontSize(kit.FNote).TextColor(kit.Ink3).Padding(8, 6)
				return
			}
			if n := len(rows) - MaxStreamSteps; n > 0 {
				ui.Text(c, fmt.Sprintf("前面还有 %d 条（完整的在复盘里）", n)).FontSize(kit.FNote).TextColor(kit.Ink3).Padding(6, 6)
				rows = rows[n:]
			}
			for i, r := range rows {
				now := i == len(rows)-1 && v.Live()
				if len(r.exam) > 0 {
					s.examRow(c, id, r.exam, now)
				} else {
					stepRow(c, r.step, now)
				}
			}
		})
		failLine(c, v)
	})
}

// failLine 是出错停下的对局卡底部那行红字原因（11-25 review：原因原来只在日志里，对局卡看不到）。
func failLine(c *ui.Context, v *plugin.JobView) {
	why := v.FailReason()
	if why == "" {
		return
	}
	ui.Row(c).Gap(8).Padding(10, 14).Background(kit.BadT).AlignItems(ui.Start).Children(func() {
		ui.Text(c, "出错原因").FontSize(kit.FNote).FontWeight(600).TextColor(kit.Bad).Shrink(0)
		ui.Text(c, why).FontSize(kit.FNote).TextColor(kit.Ink).Wrap().Shrink(1)
	})
}

// streamRow 是对话里对局卡的一行：一步，或者连着的一场考试（合成一行，点开看逐回合）。
type streamRow struct {
	step plugin.JobEvent
	exam []plugin.JobEvent
}

// examPages 是考试中的页面（连着的这些步合成一行）。
var examPages = map[string]bool{"HIF_EXAM": true}

// streamRows（11-25 review）：没有动作、也没写理由的步（ALLOWANCE 等，一局 143 步里有 36 步）不进流；连着的考试步合成一行。
func streamRows(steps []plugin.JobEvent) []streamRow {
	var out []streamRow
	inExam := false // 上一条是考试步（空步也会结束一场考试：两场之间只隔着 ALLOWANCE 时不能合成一场）
	for _, e := range steps {
		if e.Action == "" && strings.TrimSpace(e.Text) == "" && !examPages[e.Page] {
			inExam = false
			continue
		}
		if examPages[e.Page] {
			was := inExam
			inExam = true
			if n := len(out); n > 0 && len(out[n-1].exam) > 0 && was {
				out[n-1].exam = append(out[n-1].exam, e)
				continue
			}
			out = append(out, streamRow{exam: []plugin.JobEvent{e}})
			continue
		}
		inExam = false
		out = append(out, streamRow{step: e})
	}
	return out
}

// examSummary 是一场考试那一行的话：出了几张牌、几瓶饮料，最后一次预测的终局分。
func examSummary(steps []plugin.JobEvent) string {
	cards, drinks := 0, 0
	predict := ""
	for _, e := range steps {
		switch e.Action {
		case "PlayCard":
			cards++
		case "UseDrink":
			drinks++
		}
		if p := labels.Predict(e.Text); p != "" {
			predict = p
		}
	}
	if cards+drinks == 0 { // 步里没写动作（旧记录 / 别的出招插件）：用最后一步自己的话
		return labels.Reason(steps[len(steps)-1].Text)
	}
	parts := []string{fmt.Sprintf("出了 %d 张牌", cards)}
	if drinks > 0 {
		parts[0] += fmt.Sprintf("、%d 瓶饮料", drinks)
	}
	if predict != "" {
		parts = append(parts, "预计终局 "+predict+" 分")
	}
	return strings.Join(parts, " · ")
}

func (s *state) examRow(c *ui.Context, jobID string, steps []plugin.JobEvent, now bool) {
	key := fmt.Sprintf("%s-exam-%d", jobID, steps[0].Seq)
	if s.openExam == nil {
		s.openExam = map[string]bool{}
	}
	open := s.openExam[key]
	b := ui.ButtonBase(c).Key(key).Label("展开这场考试").Justify(ui.Start).Gap(10).Padding(8, 6).Radius(kit.R8)
	if open {
		b = b.Label("收起这场考试")
	}
	if now {
		b = b.Background(kit.BrandT)
	} else if b.Hovered() {
		b = b.Background(kit.Surface2)
	}
	if b.Children(func() {
		t := labels.Clock(steps[0].T)
		if now {
			t = "现在"
		}
		ui.Text(c, t).FontSize(11.5).TextColor(kit.Ink3).Width(36).Shrink(0)
		ui.Column(c).Gap(3).Shrink(1).Children(func() {
			ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
				ui.Text(c, labels.Page(steps[0].Page)).FontSize(12.5).FontWeight(600).TextColor(kit.Ink)
				kit.SourceTag(c, steps[len(steps)-1].Source)
			})
			ui.Text(c, examSummary(steps)).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
		})
		ui.Spacer(c)
		arrow := "›"
		if open {
			arrow = "⌄"
		}
		ui.Text(c, arrow).FontSize(kit.FNote).TextColor(kit.Ink3)
	}).Clicked() {
		s.openExam[key] = !open
	}
	if open {
		ui.Column(c).Padding(0, 0, 0, 28).Children(func() {
			for _, e := range steps {
				stepRow(c, e, false)
			}
		})
	}
}

func stepRow(c *ui.Context, e plugin.JobEvent, now bool) {
	r := ui.Row(c).Key(fmt.Sprintf("step-%d", e.Seq)).Gap(10).Padding(8, 6).Radius(kit.R8)
	if now {
		r = r.Background(kit.BrandT)
	}
	r.Children(func() {
		t := labels.Clock(e.T)
		if now {
			t = "现在"
		}
		ui.Text(c, t).FontSize(11.5).TextColor(kit.Ink3).Width(36).Shrink(0)
		ui.Column(c).Gap(3).Shrink(1).Children(func() {
			ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
				ui.Text(c, labels.Page(e.Page)).FontSize(12.5).FontWeight(600).TextColor(kit.Ink)
				if a := labels.Action(e.Action); a != "" && a != labels.Page(e.Page) {
					ui.Text(c, "· "+a).FontSize(12).TextColor(kit.Ink3)
				}
				kit.SourceTag(c, e.Source)
			})
			if text := labels.Reason(e.Text); text != "" {
				ui.Text(c, text).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
			}
		})
	})
}

// ---- 钉顶对局卡 ----

func (s *state) pinned(c *ui.Context, h plugin.Host, id string) {
	v := h.Job(id)
	steps := v.Steps()
	b := ui.ButtonBase(c).Key("pinned-"+id).Label(title(v, id)).Justify(ui.Start).AlignItems(ui.Center).Gap(12).Padding(10, 12).
		Radius(kit.R16-2).Background(kit.CardBg).Border(kit.Hair, kit.LineStr).Shadow(0, 4, 16, 0, ui.RGBA(120, 60, 10, 0.06))
	if b.Children(func() {
		kit.Thumb(c, v.LastFrame(), 30, 54, 5)
		ui.Column(c).Gap(4).Grow(1).Shrink(1).Children(func() {
			ui.Row(c).AlignItems(ui.Center).Gap(8).Children(func() {
				ui.Box(c).Size(7, 7).Radius(4).Background(kit.Brand)
				ui.Text(c, title(v, id)).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
				ui.Text(c, stats(h, v)).FontSize(kit.FNote).TextColor(kit.Ink3)
			})
			last := "正在连游戏、开局…"
			if n := len(steps); n > 0 {
				last = "刚才：" + labels.Page(steps[n-1].Page) + "，" + steps[n-1].Text
			}
			ui.Text(c, last).FontSize(12.5).TextColor(kit.Ink2).MaxLines(1).Ellipsis("…")
		})
		s.controls(c, h, v, id, true)
	}).Clicked() {
		h.OpenPanel("cockpit", "")
	}
}

// controls 是暂停 / 接手 / 继续 / 停止；compact 时只有图标。
func (s *state) controls(c *ui.Context, h plugin.Host, v *plugin.JobView, id string, compact bool) {
	if !v.Live() {
		return
	}
	pausable := v.Job == nil || v.Job.Pausable == nil || *v.Job.Pausable
	type btn struct {
		action, label string
		icon          *ui.SVG
	}
	var bs []btn
	switch v.State {
	case "running":
		if pausable {
			bs = append(bs, btn{"pause", "暂停", kit.IcPause})
			if !compact {
				bs = append(bs, btn{"takeover", "我来接手", nil})
			}
		}
	case "paused", "takeover":
		bs = append(bs, btn{"resume", "继续自动打", kit.IcPlay})
	}
	bs = append(bs, btn{"stop", "停止", kit.IcStop})
	ui.Row(c).Gap(6).Wrap().Children(func() {
		for _, b := range bs {
			var el ui.Element
			if compact && b.icon != nil {
				el = kit.IconButton(c, b.icon, b.label)
			} else if b.action == "resume" {
				el = kit.Primary(c, b.label)
			} else {
				el = kit.Secondary(c, b.label)
			}
			if el.Key("ctl-" + id + "-" + b.action).Disabled(s.busy[id]).Clicked() {
				s.control(h, id, b.action)
			}
		}
	})
}

func (s *state) control(h plugin.Host, id, action string) {
	s.busy[id] = true
	h.Go(func() func() {
		_ = h.Call(context.Background(), []string{"run", action, "--job", id})
		return func() { delete(s.busy, id) }
	})
}

// ---- 右侧面板：实时画面、控制、几行数、成绩（不放步骤） ----

func (s *state) panel(c *ui.Context, h plugin.Host, route string) {
	if strings.HasPrefix(route, "review:") {
		s.reviewPanel(c, h, strings.TrimPrefix(route, "review:"))
		return
	}
	id := h.LiveJob()
	if strings.HasPrefix(route, "job:") {
		id = strings.TrimPrefix(route, "job:") // 对话里某张对局卡点进来的：结束了也能看成绩和复盘
	}
	if id == "" {
		kit.Note(c, "现在没有在跑的对局。在对话里说「帮我用某某打一把初」就能开一局。")
		// 11-24 实测清单「结束后面板空」：对话里开的局一结束就从面板上消失了（后台任务有自己的列表）。
		// 最近一局是对话里开的、已经结束：照样放在这里，看得到成绩、出错原因和复盘入口
		s.refreshRecent(h)
		if len(s.recent) > 0 && !plugin.LiveStates[s.recent[0].State] && h.ChatJob(s.recent[0].ID) {
			kit.Section(c, "刚结束的一局")
			s.jobView(c, h, s.recent[0].ID)
		}
		s.background(c, h, "")
		return
	}
	s.jobView(c, h, id)
	s.background(c, h, id)
}

// jobView 是一局的详情：标题、状态、画面、几行数、出错原因、控制、成绩和复盘入口（不含后台任务列表）。
func (s *state) jobView(c *ui.Context, h plugin.Host, id string) {
	v := h.Job(id)
	ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
		ui.Text(c, title(v, id)).FontSize(15).FontWeight(650).TextColor(kit.Ink)
		kit.StatePill(c, v.State)
	})
	// 竖着排：画面在上（面板窄），下面几行数和控制
	ui.Column(c).AlignItems(ui.Center).Children(func() { frame(c, v.LastFrame(), v.Live(), PanelFrameWidth) })
	ui.Column(c).Gap(6).Children(func() {
		kv := func(k, val string) {
			ui.Row(c).Children(func() {
				ui.Text(c, k).FontSize(kit.FNote).TextColor(kit.Ink3)
				ui.Spacer(c)
				ui.Text(c, val).FontSize(kit.FNote).FontWeight(600).TextColor(kit.Ink)
			})
		}
		kv("步数", fmt.Sprint(len(v.Steps())))
		if e := elapsed(h, v); e != "" {
			kv("已打", e)
		}
		if st := v.Steps(); len(st) > 0 {
			kv("现在", labels.Page(st[len(st)-1].Page))
		}
	})
	failLine(c, v)
	s.controls(c, h, v, id, false)
	if v.State == "takeover" {
		kit.Note(c, "自动操作已停下。你在游戏里点完，按「继续自动打」，初星助手会从当前画面接着打。")
	}
	kit.Note(c, "每一步做了什么、为什么，在左边对话里那张对局卡下面。")
	if exams := v.Report(); len(exams) > 0 {
		kit.Section(c, "成绩")
		scoreStrip(c, exams)
		s.reviewButton(c, h, id)
	}
}

// refreshRecent 每 10 秒在后台刷一次最近的任务（run status）；刷新失败保留旧列表、记下原因（CodeRabbit #79）。
func (s *state) refreshRecent(h plugin.Host) {
	if !s.recentGet && (s.recentAt.IsZero() || h.Now().Sub(s.recentAt) > 10*time.Second) {
		s.recentGet, s.recentAt = true, h.Now()
		h.Go(func() func() {
			var st struct {
				Current *plugin.Job  `json:"current"`
				Recent  []plugin.Job `json:"recent"`
			}
			err := h.Call(context.Background(), []string{"run", "status"}).Decode(&st)
			return func() {
				s.recentGet = false
				if err != nil { // 刷新失败：保留旧列表，写明原因（CodeRabbit #79）
					s.recentErr = err.Error()
					return
				}
				s.recentErr = ""
				s.recent = st.Recent
				if st.Current != nil {
					s.recent = append([]plugin.Job{*st.Current}, st.Recent...)
				}
			}
		})
	}
}

// background 是「后台任务」：命令行等外部发起的任务（不属于任何对话，11-25 review：它们不再往对话里发通知）。
func (s *state) background(c *ui.Context, h plugin.Host, current string) {
	s.refreshRecent(h)
	var jobs []plugin.Job
	seen := map[string]bool{}
	for _, j := range s.recent {
		if j.ID == current || seen[j.ID] || h.ChatJob(j.ID) {
			continue
		}
		seen[j.ID] = true
		jobs = append(jobs, j)
	}
	if len(jobs) == 0 && s.recentErr == "" {
		return
	}
	if len(jobs) > 8 {
		jobs = jobs[:8]
	}
	kit.Section(c, "后台任务")
	kit.Note(c, "在对话外面（命令行、别的程序）开的任务，不往对话里发消息。")
	if s.recentErr != "" {
		ui.Text(c, "刷新失败："+s.recentErr+"。下面是上次读到的，过几秒会自动再试。").FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
	}
	for _, j := range jobs {
		name := j.Summary
		if name == "" {
			name = labels.Kind(j.Kind)
		}
		b := ui.ButtonBase(c).Key("bg-"+j.ID).Label(name).Justify(ui.Start).AlignItems(ui.Center).Gap(8).Padding(7, 8).Radius(kit.R8)
		if b.Hovered() {
			b = b.Background(kit.Surface2)
		}
		if b.Children(func() {
			ui.Column(c).Gap(2).Shrink(1).Children(func() {
				ui.Text(c, name).FontSize(kit.FSmall).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
				sub := labels.Clock(j.StartedAt)
				if j.Reason != "" {
					sub += " · " + j.Reason
				}
				ui.Text(c, sub).FontSize(11).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…")
			})
			ui.Spacer(c)
			kit.StatePill(c, j.State)
		}).Clicked() {
			h.OpenPanel("cockpit", "job:"+j.ID)
		}
	}
}

func frame(c *ui.Context, path string, live bool, w float32) {
	hgt := w * 16 / 9
	if b := kit.Bitmap(path); b != nil {
		ui.Image(c, b).Size(w, hgt).Fit(ui.Cover).Radius(10).Shrink(0)
		return
	}
	msg := "没有画面"
	if live {
		msg = "等第一张画面"
	}
	ui.Column(c).Size(w, hgt).Radius(10).Background(kit.Surface2).Center().Shrink(0).Children(func() {
		ui.Text(c, msg).FontSize(kit.FNote).TextColor(kit.Ink3)
	})
}

func scoreStrip(c *ui.Context, exams []plugin.ExamScore) {
	ui.Row(c).Gap(24).Wrap().AlignItems(ui.End).Children(func() {
		for i, e := range exams {
			if e.Score == nil {
				continue
			}
			label := e.Label
			if j := strings.Index(label, "#"); j >= 0 {
				label = label[:j]
			}
			if label == "" {
				label = fmt.Sprintf("第 %d 场", i+1)
			}
			ui.Column(c).Gap(2).Children(func() {
				ui.Text(c, label).FontSize(11).TextColor(kit.Ink3)
				size, col := float32(20), kit.Ink
				if i == len(exams)-1 {
					size, col = 28, kit.WarnInk
				}
				ui.Text(c, thousands(int64(*e.Score))).FontSize(size).FontWeight(700).Italic().TextColor(col)
			})
		}
	})
}

func thousands(n int64) string {
	s := fmt.Sprint(n)
	var b strings.Builder
	for i, r := range s {
		if i > 0 && (len(s)-i)%3 == 0 && r != '-' {
			b.WriteByte(',')
		}
		b.WriteRune(r)
	}
	return b.String()
}

func (s *state) reviewButton(c *ui.Context, h plugin.Host, id string) {
	label := "看复盘"
	if s.busy["review-"+id] {
		label = "生成中…"
	}
	if kit.Secondary(c, label).Key("review-" + id).Disabled(s.busy["review-"+id]).Clicked() {
		s.busy["review-"+id] = true
		h.Go(func() func() {
			var d struct {
				Path string `json:"path"`
			}
			err := h.Call(context.Background(), []string{"run", "review", "--job", id}).Decode(&d)
			return func() {
				delete(s.busy, "review-"+id)
				if err != nil {
					s.reviewE[id] = err.Error()
					return
				}
				s.review[id] = d.Path
				h.OpenPanel("cockpit", "review:"+id)
			}
		})
	}
	if e := s.reviewE[id]; e != "" {
		ui.Text(c, e).FontSize(kit.FNote).TextColor(kit.Bad)
	}
}

// reviewPanel 是复盘页；网页视图见 kit.WebPanel（任务 16），这里先给本机路径。
func (s *state) reviewPanel(c *ui.Context, h plugin.Host, id string) {
	if ReviewView != nil {
		ReviewView(c, h, id)
		return
	}
	kit.Note(c, "复盘页在："+s.review[id])
}

// ReviewView 画某局的复盘页（main 里接上网页视图）。
var ReviewView func(c *ui.Context, h plugin.Host, jobID string)

// ---- 上下文 ----

func (s *state) context(ctx context.Context, h plugin.Host) string {
	var b strings.Builder
	if id := h.LiveJob(); id != "" {
		v := h.Job(id)
		b.WriteString("正在跑：" + title(v, id) + "（" + labels.State(v.State) + "，" + stats(h, v) + "）\n")
		st := v.Steps()
		if n := len(st); n > 0 {
			b.WriteString("最近几步：\n")
			for _, e := range st[max(0, n-5):] {
				b.WriteString("- " + labels.Page(e.Page) + "（" + labels.Source(e.Source) + "）：" + e.Text + "\n")
			}
		}
	} else {
		b.WriteString("现在没有在跑的对局。\n")
	}
	var stt struct {
		Recent []plugin.Job `json:"recent"`
	}
	if h.Call(ctx, []string{"run", "status"}).Decode(&stt) == nil && len(stt.Recent) > 0 {
		b.WriteString("最近几局：\n")
		for i, j := range stt.Recent {
			if i >= 3 {
				break
			}
			name := j.Summary
			if name == "" {
				name = labels.Kind(j.Kind)
			}
			b.WriteString("- " + time.Unix(int64(j.StartedAt), 0).Format("01-02 15:04") + " " + name + " · " + labels.State(j.State) + "\n")
		}
	}
	return b.String()
}
