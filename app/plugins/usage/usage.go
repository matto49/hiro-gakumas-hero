// Package usage 是内置的「用量」插件：每局花了多少钱的卡片、用量面板（今天 / 本月 / 按模型 / 最近每局 / 一局明细）、
// 侧栏底部的本局 / 今天花费、给对话助手的用量上下文。数据一律经工具层 hiro usage …（设计稿 2026-11-25 §3）。
package usage

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// Group 是一组调用的汇总（hiro usage 各命令的一行 / 合计 / 一局）。
type Group struct {
	Key          string             `json:"key"`
	Provider     string             `json:"provider"`
	Model        string             `json:"model"`
	Calls        int                `json:"calls"`
	Total        int                `json:"total"`
	Cost         map[string]float64 `json:"cost"`
	Subscription float64            `json:"subscription_usd"`
	Unpriced     int                `json:"unpriced"`
	NoUsage      int                `json:"no_usage"`
	Failed       int                `json:"failed"`
	Models       []Group            `json:"models"`
}

// Item 是一局里的一次调用。
type Item struct {
	TS       string   `json:"ts"`
	Source   string   `json:"source"`
	Provider string   `json:"provider"`
	Model    string   `json:"model"`
	Tokens   *int     `json:"tokens"`
	Cost     *float64 `json:"cost"`
	Currency string   `json:"currency"`
	Kind     string   `json:"kind"`
	OK       *bool    `json:"ok"`
}

// JobUsage 是 usage job 的结果。
type JobUsage struct {
	Group
	Job      string           `json:"job"`
	BySource map[string]Group `json:"by_source"`
	ByModel  []Group          `json:"by_model"`
	Items    []Item           `json:"items"`
	Checked  string           `json:"prices_checked"`
}

// Table 是 usage daily / monthly / chat 的结果。
type Table struct {
	Rows    []Group `json:"rows"`
	Totals  Group   `json:"totals"`
	Checked string  `json:"prices_checked"`
}

// Recent 是 usage recent 的结果。
type Recent struct {
	Jobs []struct {
		Group
		Job     string `json:"job"`
		Kind    string `json:"kind"`
		Summary string `json:"summary"`
	} `json:"jobs"`
	Checked string `json:"prices_checked"`
}

func decode(b []byte, v any) error { return json.Unmarshal(b, v) }

// Refresh 是后台刷新的间隔（Footer、面板）。
const Refresh = 15 * time.Second

// playKinds 是会调模型的对局；别的任务（领日常、扫描）没调用时不发卡。
var playKinds = map[string]bool{"hajime": true, "nia": true, "hif": true, "tower": true}

var sourceNames = map[string]string{"decision": "决策", "rescue": "兜底", "chat": "对话"}
var sourceOrder = []string{"decision", "rescue", "chat"}

var symbols = map[string]string{"CNY": "¥", "USD": "$"}

// Tokens 写成「800」「5,000」「3.4 万」「1.2 亿」。
func Tokens(n int) string {
	switch {
	case n >= 1e8:
		return trim(float64(n)/1e8) + " 亿"
	case n >= 1e4:
		return trim(float64(n)/1e4) + " 万"
	case n >= 1000:
		return fmt.Sprintf("%d,%03d", n/1000, n%1000)
	}
	return fmt.Sprint(n)
}

func trim(v float64) string {
	s := fmt.Sprintf("%.1f", v)
	return strings.TrimSuffix(s, ".0")
}

// money 写一个币种的金额：≥ 0.01 两位小数，更小的保留两位有效数字（不写成 0.00）。
func money(cur string, v float64) string {
	sym := symbols[cur]
	if sym == "" {
		sym = cur + " "
	}
	if v >= 0.01 || v == 0 {
		return sym + fmt.Sprintf("%.2f", v)
	}
	d := int(math.Ceil(-math.Log10(v))) + 1 // 两位有效数字
	return sym + strings.TrimRight(fmt.Sprintf("%.*f", d, v), "0")
}

// Cost 是花费那一段：「约 ¥0.42」「订阅（…）」「花费 —」；没有花费信息返回 ""。
func Cost(g Group) string {
	curs := make([]string, 0, len(g.Cost))
	for c := range g.Cost {
		curs = append(curs, c)
	}
	sort.Slice(curs, func(i, j int) bool { // 人民币在前
		if (curs[i] == "CNY") != (curs[j] == "CNY") {
			return curs[i] == "CNY"
		}
		return curs[i] < curs[j]
	})
	var parts []string
	for _, c := range curs {
		parts = append(parts, money(c, g.Cost[c]))
	}
	out := ""
	if len(parts) > 0 {
		out = "约 " + strings.Join(parts, " + ")
	}
	if g.Subscription > 0 {
		sub := "折合 API 约 " + money("USD", g.Subscription)
		if out == "" {
			out = "订阅（不另收费，" + sub + "）"
		} else {
			out += "，订阅" + sub
		}
	}
	switch {
	case g.Unpriced > 0 && out == "":
		out = "花费 —"
	case g.Unpriced > 0:
		out += fmt.Sprintf("（%d 次未定价）", g.Unpriced)
	}
	return out
}

// Line 是一组调用的一行主数：「12 次 · 3.4 万 token · 约 ¥0.42」。
func Line(g Group) string {
	if g.Calls == 0 {
		return "没有调用模型"
	}
	parts := []string{fmt.Sprintf("%d 次", g.Calls)}
	if g.Total == 0 && g.NoUsage > 0 {
		parts = append(parts, "用量不详")
	} else {
		parts = append(parts, Tokens(g.Total)+" token")
	}
	if c := Cost(g); c != "" {
		parts = append(parts, c)
	}
	return strings.Join(parts, " · ")
}

func short(g Group) string {
	if c := Cost(g); c != "" {
		return c
	}
	return fmt.Sprintf("%d 次", g.Calls)
}

func bySource(m map[string]Group) []string {
	var keys []string
	for _, k := range sourceOrder {
		if _, ok := m[k]; ok {
			keys = append(keys, k)
		}
	}
	var rest []string
	for k := range m {
		if sourceNames[k] == "" {
			rest = append(rest, k)
		}
	}
	sort.Strings(rest)
	return append(keys, rest...)
}

func sourceName(k string) string {
	if n := sourceNames[k]; n != "" {
		return n
	}
	return k
}

// sourceText 是「决策 10 次 约 ¥0.40 · 兜底 2 次 约 ¥0.02」。
func sourceText(m map[string]Group) string {
	var parts []string
	for _, k := range bySource(m) {
		g := m[k]
		s := fmt.Sprintf("%s %d 次", sourceName(k), g.Calls)
		if c := Cost(g); c != "" {
			s += " " + c
		}
		parts = append(parts, s)
	}
	return strings.Join(parts, " · ")
}

func modelName(g Group) string {
	if g.Model == "" {
		return g.Provider
	}
	return g.Model
}

// jobTime 由任务编号（20261124-010000-aaaa）写「11-24 01:00」。
func jobTime(id string) string {
	if len(id) < 13 || id[8] != '-' {
		return ""
	}
	return id[4:6] + "-" + id[6:8] + " " + id[9:11] + ":" + id[11:13]
}

func pricesNote(c *ui.Context, checked string) {
	if checked != "" {
		kit.Note(c, "花费按 "+checked+" 价格表估算，以服务商账单为准")
	}
}

type home struct {
	today, month Table
	recent       Recent
	err          string
}

type jobPage struct {
	u   *JobUsage
	err string
	at  time.Time
	sig string
}

type state struct {
	// 侧栏底部（界面线程读写；后台只算结果）
	footAt      time.Time
	footSig     string
	footLoading bool
	today       *Group
	month       *Group
	liveID      string
	live        *Group

	home        *home
	homeAt      time.Time
	homeSig     string
	homeLoading bool

	jobs       map[string]*jobPage
	jobLoading map[string]bool
}

// New 建用量插件。
func New() *plugin.Plugin {
	s := &state{jobs: map[string]*jobPage{}, jobLoading: map[string]bool{}}
	return &plugin.Plugin{
		ID:       "usage",
		Name:     "用量",
		Icon:     kit.IcCoins,
		Tools:    []string{"usage daily", "usage monthly", "usage job", "usage chat", "usage recent"},
		Commands: []plugin.Command{{Name: "用量", Hint: "看模型调用次数、token 和花费"}},
		Cards: map[string]plugin.CardView{
			"usage-job":   jobCard,
			"usage-table": tableCard,
		},
		Panel: func(c *ui.Context, h plugin.Host, route string) {
			if id, ok := strings.CutPrefix(route, "job/"); ok {
				s.jobPanel(c, h, id)
				return
			}
			s.homePanel(c, h)
		},
		OnJobEnd: func(h plugin.Host, id string, v *plugin.JobView) {
			s.homeSig = "" // 这局的 usage.json 刚写好：面板「最近每局」下次重读
			onJobEnd(h, id, v)
		},
		Footer: func(c *ui.Context, h plugin.Host) { s.footer(c, h) },
		Greeting: func(h plugin.Host) string {
			s.refreshFoot(h)
			if s.month == nil || s.month.Calls == 0 {
				return ""
			}
			if c := Cost(*s.month); c != "" {
				return "本月 " + c
			}
			return ""
		},
		ContextTitle: "模型用量",
		Context:      func(ctx context.Context, h plugin.Host) string { return contextText(ctx, h) },
	}
}

// ---- 任务结束发卡 ----

func onJobEnd(h plugin.Host, id string, v *plugin.JobView) {
	kind := ""
	if v != nil && v.Job != nil {
		kind = v.Job.Kind
	}
	h.Go(func() func() {
		r := h.Call(context.Background(), []string{"usage", "job", id})
		var u map[string]any
		if !r.OK || json.Unmarshal(r.Data, &u) != nil {
			return nil
		}
		calls, _ := u["calls"].(float64)
		if calls == 0 && !playKinds[kind] {
			return nil
		}
		return func() { h.Post("", plugin.Card{"type": "usage-job", "jobId": id, "usage": u}) }
	})
}

func cardData(card plugin.Card, key string, v any) bool {
	b, err := json.Marshal(card[key])
	return err == nil && json.Unmarshal(b, v) == nil
}

func jobCard(c *ui.Context, h plugin.Host, card plugin.Card) {
	var u JobUsage
	if !cardData(card, "usage", &u) {
		return
	}
	id, _ := card["jobId"].(string)
	kit.Card(c).MaxWidth(560).Children(func() {
		ui.Text(c, "本局花费").FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
		main := Line(u.Group)
		if u.Calls == 0 {
			main = "本局没有调用模型"
		}
		ui.Text(c, main).FontSize(kit.FBody).TextColor(kit.Ink).Wrap()
		if st := sourceText(u.BySource); st != "" && u.Calls > 0 {
			ui.Text(c, st).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
		}
		if id != "" && u.Calls > 0 && kit.Ghost(c, "看明细").Key("usage-open-"+id).Clicked() {
			h.OpenPanel("usage", "job/"+id)
		}
	})
}

// tableCard 画对话助手调 usage 工具的结果。
func tableCard(c *ui.Context, h plugin.Host, card plugin.Card) {
	tool, _ := card["tool"].(string)
	kit.Card(c).MaxWidth(620).Children(func() {
		switch tool {
		case "usage job":
			var u JobUsage
			if !cardData(card, "data", &u) {
				return
			}
			ui.Text(c, "一局的模型用量").FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
			ui.Text(c, Line(u.Group)).FontSize(kit.FBody).TextColor(kit.Ink).Wrap()
			if st := sourceText(u.BySource); st != "" {
				ui.Text(c, st).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
			}
			if u.Job != "" && u.Calls > 0 && kit.Ghost(c, "看明细").Key("usage-tool-open-"+u.Job).Clicked() {
				h.OpenPanel("usage", "job/"+u.Job)
			}
			pricesNote(c, u.Checked)
		case "usage recent":
			var r Recent
			if !cardData(card, "data", &r) {
				return
			}
			ui.Text(c, "最近每局的模型用量").FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
			for i, j := range r.Jobs {
				row(c, fmt.Sprintf("r-%d", i), jobTime(j.Job)+" "+j.Summary, Line(j.Group))
			}
			pricesNote(c, r.Checked)
		default:
			var t Table
			if !cardData(card, "data", &t) {
				return
			}
			title := map[string]string{"usage daily": "按天的模型用量", "usage monthly": "按月的模型用量", "usage chat": "对话助手的模型用量"}[tool]
			if title == "" {
				title = "模型用量"
			}
			ui.Text(c, title).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
			if len(t.Rows) == 0 {
				kit.Note(c, "这段时间没有调用模型。")
			}
			for i, g := range t.Rows {
				row(c, fmt.Sprintf("t-%d", i), g.Key, Line(g))
			}
			if len(t.Rows) > 1 {
				row(c, "t-total", "合计", Line(t.Totals))
			}
			pricesNote(c, t.Checked)
		}
	})
}

func row(c *ui.Context, key, label, value string) {
	ui.Row(c).Key(key).Gap(10).AlignItems(ui.Center).Children(func() {
		ui.Text(c, label).FontSize(kit.FNote).TextColor(kit.Ink2).MaxLines(1).Ellipsis("…").Width(120).Shrink(0)
		ui.Text(c, value).FontSize(kit.FSmall).TextColor(kit.Ink).Wrap().Shrink(1)
	})
}

// ---- 侧栏底部 ----

func today(h plugin.Host) string      { return h.Now().Format("2006-01-02") }
func monthStart(h plugin.Host) string { return h.Now().Format("2006-01") + "-01" }

// ledgerSig 是这个月流水账文件的大小和修改时间（加上日期：过了零点「今天」要重算）。
// 没变就不刷新：每次刷新要起 Python 进程（fastpath.go 记着 11-18 进程太多把内存吃光的事）。
// 拿不到 HIRO_HOME 时返回 ""，退回只按 Refresh 间隔刷新。
func ledgerSig(h plugin.Host) string {
	if h.Home() == "" {
		return ""
	}
	day := today(h)
	st, err := os.Stat(filepath.Join(h.Home(), "usage", h.Now().Format("2006-01")+".jsonl"))
	if err != nil {
		return day + "|none"
	}
	return fmt.Sprintf("%s|%d|%d", day, st.Size(), st.ModTime().UnixNano())
}

// stale：从没取过；或者过了 Refresh 且流水账变了（拿不到签名时只看时间）。
func stale(h plugin.Host, at time.Time, sig, now string) bool {
	if at.IsZero() {
		return true
	}
	return h.Now().Sub(at) >= Refresh && (now == "" || now != sig)
}

// refreshFoot 在后台取今天 / 本月 / 本局（Footer、Greeting 每帧调用，只读缓存）：至少隔 Refresh，且流水账变了或活任务换了才取。
func (s *state) refreshFoot(h plugin.Host) {
	live := h.LiveJob()
	sig := ledgerSig(h)
	if s.footLoading || (!stale(h, s.footAt, s.footSig, sig) && live == s.liveID) {
		return
	}
	s.footLoading, s.footSig = true, sig
	day, month := today(h), monthStart(h)
	h.Go(func() func() {
		ctx := context.Background()
		var d, m Table
		var j *Group
		dErr := h.Call(ctx, []string{"usage", "daily", "--since", day}).Decode(&d)
		mErr := h.Call(ctx, []string{"usage", "monthly", "--since", month}).Decode(&m)
		if live != "" {
			var u JobUsage
			if h.Call(ctx, []string{"usage", "job", live}).Decode(&u) == nil {
				j = &u.Group
			}
		}
		return func() {
			s.footLoading, s.footAt, s.liveID, s.live = false, h.Now(), live, j
			if dErr == nil {
				s.today = &d.Totals
			}
			if mErr == nil {
				s.month = &m.Totals
			}
		}
	})
}

func (s *state) footer(c *ui.Context, h plugin.Host) {
	s.refreshFoot(h)
	text := ""
	switch {
	case h.LiveJob() != "" && s.live != nil && s.liveID == h.LiveJob():
		text = "本局 " + short(*s.live)
		if s.live.Calls > 0 && Cost(*s.live) != "" {
			text += fmt.Sprintf(" · %d 次", s.live.Calls)
		}
	case s.today != nil && s.today.Calls > 0:
		text = "今天 " + short(*s.today)
	default:
		return // 今天还没调用过模型：不占位置
	}
	if kit.RowButton(c, "usage", text, false).Padding(3, 0).Children(func() {
		ui.Icon(c, kit.IcCoins).FontSize(12).TextColor(kit.Ink3)
		ui.Text(c, text).FontSize(kit.FNote).TextColor(kit.Ink2).MaxLines(1).Ellipsis("…")
	}).Clicked() {
		h.OpenPanel("usage", "")
	}
}

// ---- 面板 ----

func (s *state) loadHome(h plugin.Host) {
	sig := ledgerSig(h)
	if s.homeLoading || (s.home != nil && !stale(h, s.homeAt, s.homeSig, sig)) {
		return
	}
	s.homeLoading, s.homeSig = true, sig
	day, month := today(h), monthStart(h)
	h.Go(func() func() {
		ctx := context.Background()
		var x home
		errs := []error{
			h.Call(ctx, []string{"usage", "daily", "--since", day}).Decode(&x.today),
			h.Call(ctx, []string{"usage", "monthly", "--since", month, "--breakdown"}).Decode(&x.month),
			h.Call(ctx, []string{"usage", "recent", "--limit", "20"}).Decode(&x.recent),
		}
		for _, e := range errs {
			if e != nil {
				x.err = e.Error()
				break
			}
		}
		return func() { s.homeLoading, s.home, s.homeAt = false, &x, h.Now() }
	})
}

func (s *state) homePanel(c *ui.Context, h plugin.Host) {
	s.loadHome(h)
	ui.Text(c, "模型用量").FontSize(17).FontWeight(650).TextColor(kit.Ink)
	x := s.home
	if x == nil {
		kit.Note(c, "正在读用量…")
		return
	}
	if x.err != "" {
		ui.Text(c, x.err).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
	}
	row(c, "today", "今天", Line(x.today.Totals))
	row(c, "month", "本月", Line(x.month.Totals))
	if len(x.month.Rows) > 0 && len(x.month.Rows[0].Models) > 0 {
		kit.Section(c, "本月按模型")
		for i, m := range x.month.Rows[0].Models {
			row(c, fmt.Sprintf("m-%d", i), modelName(m), Line(m))
		}
	}
	kit.Section(c, "最近每局")
	if len(x.recent.Jobs) == 0 {
		kit.Note(c, "还没有打完的局。打完一局，这里会列出它花了多少。")
	}
	for _, a := range averages(x.recent) {
		kit.Note(c, a)
	}
	for i, j := range x.recent.Jobs {
		label := jobTime(j.Job) + " " + j.Summary
		id := j.Job
		if kit.RowButton(c, fmt.Sprintf("job-%d", i), label, false).Padding(4, 0).Children(func() {
			ui.Column(c).Gap(2).Shrink(1).Children(func() {
				ui.Text(c, label).FontSize(kit.FNote).TextColor(kit.Ink2).MaxLines(1).Ellipsis("…")
				ui.Text(c, Line(j.Group)).FontSize(kit.FSmall).TextColor(kit.Ink).Wrap()
			})
		}).Clicked() {
			h.OpenPanel("usage", "job/"+id)
		}
	}
	checked := x.month.Checked
	if checked == "" {
		checked = x.recent.Checked
	}
	pricesNote(c, checked)
}

// averages 是「初 平均每局 约 ¥0.36（2 局）」：按模式分组，只算有花费的局，按币种平均。
func averages(r Recent) []string {
	type acc struct {
		n   int
		sum map[string]float64
	}
	groups := map[string]*acc{}
	var order []string
	for _, j := range r.Jobs {
		if len(j.Cost) == 0 || j.Kind == "" {
			continue
		}
		a := groups[j.Kind]
		if a == nil {
			a = &acc{sum: map[string]float64{}}
			groups[j.Kind] = a
			order = append(order, j.Kind)
		}
		a.n++
		for cur, v := range j.Cost {
			a.sum[cur] += v
		}
	}
	var out []string
	for _, k := range order {
		a := groups[k]
		if a.n < 2 {
			continue
		}
		avg := Group{Cost: map[string]float64{}}
		for cur, v := range a.sum {
			avg.Cost[cur] = v / float64(a.n)
		}
		name := labels.Kind(k)
		if name == "" {
			name = k
		}
		out = append(out, fmt.Sprintf("%s 平均每局 %s（%d 局）", name, Cost(avg), a.n))
	}
	return out
}

func (s *state) loadJob(h plugin.Host, id string) {
	p := s.jobs[id]
	// 打完的局用量定了，读过就不再读；活着的局按流水账变化刷新
	if s.jobLoading[id] || (p != nil && (h.LiveJob() != id || !stale(h, p.at, p.sig, ledgerSig(h)))) {
		return
	}
	sig := ledgerSig(h)
	s.jobLoading[id] = true
	h.Go(func() func() {
		var u JobUsage
		err := h.Call(context.Background(), []string{"usage", "job", id}).Decode(&u)
		return func() {
			s.jobLoading[id] = false
			np := &jobPage{at: h.Now(), sig: sig}
			if err != nil {
				np.err = err.Error()
			} else {
				np.u = &u
			}
			s.jobs[id] = np
		}
	})
}

const maxItems = 200

func (s *state) jobPanel(c *ui.Context, h plugin.Host, id string) {
	s.loadJob(h, id)
	if kit.Ghost(c, "‹ 用量").Key("usage-back").Clicked() {
		h.OpenPanel("usage", "")
	}
	title := s.jobTitle(h, id)
	ui.Text(c, title).FontSize(17).FontWeight(650).TextColor(kit.Ink).Wrap()
	p := s.jobs[id]
	if p == nil {
		kit.Note(c, "正在读这局的用量…")
		return
	}
	if p.err != "" {
		ui.Text(c, p.err).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
		return
	}
	u := p.u
	ui.Text(c, Line(u.Group)).FontSize(kit.FBody).TextColor(kit.Ink).Wrap()
	if len(u.BySource) > 0 {
		kit.Section(c, "按来源")
		for _, k := range bySource(u.BySource) {
			row(c, "src-"+k, sourceName(k), Line(u.BySource[k]))
		}
	}
	if len(u.ByModel) > 0 {
		kit.Section(c, "按模型")
		for i, m := range u.ByModel {
			row(c, fmt.Sprintf("bm-%d", i), modelName(m), Line(m))
		}
	}
	if len(u.Items) > 0 {
		kit.Section(c, "每次调用")
		for i, it := range u.Items {
			if i >= maxItems {
				kit.Note(c, fmt.Sprintf("还有 %d 次没列出。", len(u.Items)-maxItems))
				break
			}
			row(c, fmt.Sprintf("it-%d", i), itemLabel(it), itemValue(it))
		}
	}
	pricesNote(c, u.Checked)
}

// jobTitle 是明细页标题：「11-24 01:00 初 · 篠澤広」；不知道摘要时只写时间。
func (s *state) jobTitle(h plugin.Host, id string) string {
	summary := ""
	if s.home != nil {
		for _, j := range s.home.recent.Jobs {
			if j.Job == id {
				summary = j.Summary
			}
		}
	}
	if summary == "" {
		if v := h.Job(id); v != nil && v.Job != nil {
			summary = v.Job.Summary
		}
	}
	t := jobTime(id)
	switch {
	case t != "" && summary != "":
		return t + " " + summary
	case summary != "":
		return summary
	case t != "":
		return t + " 的任务"
	}
	return id
}

func itemLabel(it Item) string {
	t := it.TS
	if len(t) >= 19 {
		t = t[11:19]
	}
	return t + " " + sourceName(it.Source)
}

func itemValue(it Item) string {
	model := it.Model
	if model == "" {
		model = it.Provider
	}
	parts := []string{model}
	if it.Tokens != nil {
		parts = append(parts, Tokens(*it.Tokens)+" token")
	} else {
		parts = append(parts, "用量不详")
	}
	switch {
	case it.Kind == "subscription" && it.Cost != nil:
		parts = append(parts, "订阅（折合 "+money("USD", *it.Cost)+"）")
	case it.Cost != nil:
		parts = append(parts, money(it.Currency, *it.Cost))
	case it.Kind == "unpriced":
		parts = append(parts, "—")
	}
	if it.OK != nil && !*it.OK {
		parts = append(parts, "失败")
	}
	return strings.Join(parts, " · ")
}

// ---- 给对话助手的上下文 ----

func contextText(ctx context.Context, h plugin.Host) string {
	var d, m Table
	if h.Call(ctx, []string{"usage", "daily", "--since", today(h)}).Decode(&d) != nil ||
		h.Call(ctx, []string{"usage", "monthly", "--since", monthStart(h), "--breakdown"}).Decode(&m) != nil {
		return ""
	}
	head := "今天是 " + today(h) + "（查用量时日期写成 YYYY-MM-DD）。"
	if m.Totals.Calls == 0 {
		return head + "这个月还没调用过模型。"
	}
	day := Cost(d.Totals)
	if d.Totals.Calls == 0 {
		day = "没调用"
	} else if day == "" {
		day = fmt.Sprintf("%d 次（没有花费信息）", d.Totals.Calls)
	}
	mon := Cost(m.Totals)
	if mon == "" {
		mon = fmt.Sprintf("%d 次（没有花费信息）", m.Totals.Calls)
	}
	out := head + "今天模型花费 " + day + "，本月 " + mon
	if len(m.Rows) > 0 {
		var names []string
		for _, g := range m.Rows[0].Models {
			names = append(names, modelName(g))
		}
		if len(names) > 0 {
			out += "（" + strings.Join(names, "、") + "）"
		}
	}
	return out + "。要细看用 hiro usage daily / monthly / job / recent。"
}
