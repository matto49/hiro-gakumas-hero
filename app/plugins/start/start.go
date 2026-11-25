// Package start 是内置的「开局」插件：开局面板、确认卡解读、最近几局的上下文（设计稿 §5.3）。
package start

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/common"
)

type mode struct {
	id, name, desc string
	diffs          []string // 难度（空 = 没有难度）
}

var modes = []mode{
	{"hif", "H.I.F", "选拔打完拿回忆，本战冲名次。开本战会用掉一份选拔回忆。", nil},
	{"hajime", "初", "定期公演「初」：REGULAR / PRO / MASTER。", []string{"regular", "pro", "master"}},
	{"nia", "N.I.A", "PRO / MASTER。认不出的页面会停下来等你处理。", []string{"pro", "master"}},
	{"tower", "偶像之路", "按偶像路线往上爬，三星优先。", nil},
	{"daily", "日常", "活動費、礼物、工作、竞赛、任务奖励、社团，不花ジュエル。", nil},
}

func modeName(kind string) string {
	for _, m := range modes {
		if m.id == kind {
			return m.name
		}
	}
	return labels.Kind(kind)
}

// 默认难度（和 agent 提示词的固定流程一致：初 regular，N.I.A pro）。
func defaultDiff(kind string) string {
	switch kind {
	case "hajime":
		return "regular"
	case "nia":
		return "pro"
	}
	return ""
}

type idol struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Rarity string `json:"rarity"`
}

type state struct {
	mode    int
	resume  bool
	hifMain int // 0 选拔 1 本战
	diff    int
	query   string
	idol    idol
	idols   []idol
	loaded  bool
	plan    *planBrief
	planAt  time.Time

	recent        []plugin.Job
	recentAt      time.Time
	recentLoading bool
}

type planBrief struct {
	Idol struct {
		Name string `json:"name"`
	} `json:"idol"`
	Support struct {
		Set *int `json:"set"`
		Own []struct {
			ID    string `json:"id"`
			Name  string `json:"name"`
			Level *int   `json:"level"`
		} `json:"own"`
		Rental *struct {
			ID        string `json:"id"`
			Name      string `json:"name"`
			Estimated bool   `json:"estimated"`
		} `json:"rental"`
	} `json:"support"`
	Memory struct {
		Set    *int `json:"set"`
		Chosen []struct {
			Name    string `json:"name"`
			Timing  string `json:"timing"`
			Upgrade int    `json:"upgrade"`
		} `json:"chosen"`
	} `json:"memory"`
}

// groups 是方案里的支援卡（自有 + 租借，共 6 格）和回忆，确认卡里逐项列出（11-25 review：只列两张、没有回忆）。
func (pl *planBrief) groups(h plugin.Host, cat *common.Catalog) []plugin.ExplainGroup {
	name := func(id, ja string) string {
		if id != "" {
			if en := cat.Get(h, id); en != nil && en.Name != "" {
				return en.Name
			}
		}
		if ja != "" {
			return ja
		}
		return "?"
	}
	sup := plugin.ExplainGroup{Title: "支援卡"}
	for _, o := range pl.Support.Own {
		it := plugin.ExplainItem{Thumb: cat.Sprite(h, "support-"+o.ID), Name: name(o.ID, o.Name)}
		if o.Level != nil {
			it.Note = fmt.Sprintf("Lv%d", *o.Level)
		}
		sup.Items = append(sup.Items, it)
	}
	if r := pl.Support.Rental; r != nil {
		it := plugin.ExplainItem{Tag: "租借", Name: name(r.ID, r.Name)}
		if r.ID != "" {
			it.Thumb = cat.Sprite(h, "support-"+r.ID)
		}
		if r.ID == "" && r.Name == "" {
			it.Name = "开局时按实际列表挑"
		}
		if r.Estimated {
			it.Note = "估算"
		}
		sup.Items = append(sup.Items, it)
	}
	mem := plugin.ExplainGroup{Title: "回忆"}
	for _, m := range pl.Memory.Chosen {
		mem.Items = append(mem.Items, plugin.ExplainItem{Tag: labels.Timing(m.Timing), Name: m.Name + strings.Repeat("+", m.Upgrade)})
	}
	var out []plugin.ExplainGroup
	for _, g := range []plugin.ExplainGroup{sup, mem} {
		if len(g.Items) > 0 {
			out = append(out, g)
		}
	}
	return out
}

// New 建开局插件。
func New() *plugin.Plugin {
	s := &state{mode: 1}
	cat := common.Shared
	return &plugin.Plugin{
		ID:    "start",
		Name:  "开局",
		Icon:  kit.IcPlay,
		Tools: []string{"run start", "pending list"},
		Commands: []plugin.Command{
			{Name: "开局", Hint: "选模式、偶像、难度，开一局"},
		},
		Chips: []plugin.Chip{
			{Label: "/开局", Say: "/开局"},
			{Label: "接着打进行中的培育", Run: func(h plugin.Host) {
				h.RunArgs("接着打进行中的培育", []string{"run", "start", "hif", "--command", "resume"})
			}},
			{Label: "领日常奖励", Run: func(h plugin.Host) {
				h.RunArgs("帮我领日常奖励", []string{"run", "start", "daily"})
			}},
		},
		Panel: func(c *ui.Context, h plugin.Host, _ string) { s.panel(c, h) },
		Explain: func(h plugin.Host, p hiroapi.Pending) (plugin.Explained, bool) {
			return s.explain(h, cat, p)
		},
		Starters:     func(h plugin.Host) []plugin.Starter { return s.starters(h, cat) },
		ContextTitle: "最近几局",
		Context:      func(ctx context.Context, h plugin.Host) string { return recent(ctx, h, cat) },
	}
}

// ---- 确认卡解读 ----

func (s *state) planFor(h plugin.Host) *planBrief {
	if s.plan != nil && h.Now().Sub(s.planAt) < time.Minute {
		return s.plan
	}
	s.planAt = h.Now()
	h.Go(func() func() {
		var p planBrief
		if h.Call(context.Background(), []string{"plan", "show"}).Decode(&p) != nil {
			return nil
		}
		return func() { s.plan = &p }
	})
	return s.plan
}

// 代价行：会操作游戏，再加一句这件事会动到什么（11-25 review：非培育任务也写成「用掉 1 次培育」）。
const (
	costProduce  = "会操作游戏 · 用掉 1 次培育"
	costReadOnly = "会操作游戏 · 只看画面，不改东西"
)

func (s *state) explain(h plugin.Host, cat *common.Catalog, p hiroapi.Pending) (plugin.Explained, bool) {
	r, ok := common.RunArgsOf(p)
	if !ok {
		return plugin.Explained{}, false
	}
	name := modeName(r.Kind)
	dry := r.Flags["dry-run"] == "true"
	e := plugin.Explained{Title: name, Cost: "会操作游戏"}
	produce := false
	switch {
	case r.Flags["command"] == "resume":
		e.Title, e.Cost = "接着打 "+name, "会操作游戏 · 接着游戏里开着的那局"
	case r.Kind == "hajime" || r.Kind == "nia" || r.Kind == "tower":
		e.Title, e.Cost, produce = "开一局 "+name, costProduce, true
	case r.Kind == "hif":
		produce = true
		if r.Flags["mode"] == "main" {
			e.Title, e.Cost = "开一局 H.I.F 本战", "会操作游戏 · 用掉一份选拔回忆"
		} else {
			e.Title, e.Cost = "开一局 H.I.F 选拔", costProduce
		}
	case r.Kind == "daily":
		e.Title, e.Cost = "领日常奖励", "会操作游戏 · 不花ジュエル"
	case strings.HasPrefix(r.Kind, "scan-") || r.Kind == "check-settings" || r.Kind == "selftest":
		e.Cost = costReadOnly
	case r.Kind == "write-formation":
		if dry {
			e.Title, e.Cost = "对照编成方案", costReadOnly
		} else {
			e.Title, e.Cost = "把编成方案写进游戏", "会操作游戏 · 会改游戏里的编成"
		}
		if sc := r.Flags["scenario"]; sc != "" {
			e.Subtitle = modeName(sc) + " 的编成"
		}
		if pl := s.planFor(h); pl != nil {
			e.Groups = pl.groups(h, cat)
		}
		return e, true
	case r.Kind == "main-task":
		e.Title = "做初星課題"
	case r.Kind == "story":
		e.Title = "看初星コミュ"
		if ch := r.Flags["chapter"]; ch != "" {
			e.Subtitle = "第 " + ch + " 章"
		}
	case r.Kind == "contest-unit":
		if dry {
			e.Title, e.Cost = "看竞赛编队总合力", costReadOnly
		} else {
			e.Title, e.Cost = "竞赛编队", "会操作游戏 · 会改竞赛编队（おまかせ重排）"
		}
	}
	var sub []string
	if id := r.Flags["idol"]; id != "" {
		if en := cat.Get(h, id); en != nil {
			sub = append(sub, en.Name)
			e.Attr = en.Attr
			e.Art = cat.Sprite(h, en.Asset)
		} else {
			sub = append(sub, id)
		}
	}
	if d := r.Flags["difficulty"]; d != "" {
		sub = append(sub, strings.ToUpper(d))
	} else if d := defaultDiff(r.Kind); d != "" && r.Flags["command"] != "resume" {
		sub = append(sub, strings.ToUpper(d))
	}
	if len(sub) > 0 {
		e.Subtitle = strings.Join(sub, " · ")
	}
	if produce && r.Flags["idol"] != "" && r.Flags["command"] != "resume" {
		switch {
		case r.Flags["support-set"] != "" || r.Flags["memory-set"] != "":
			e.Rows = append(e.Rows, [2]string{"编成", fmt.Sprintf("游戏里的支援卡 %s 号、回忆 %s 号", or(r.Flags["support-set"], "当前"), or(r.Flags["memory-set"], "当前"))})
		default:
			if pl := s.planFor(h); pl != nil && pl.Support.Set != nil {
				e.Rows = append(e.Rows, [2]string{"编成", fmt.Sprintf("沿用编成方案（支援卡 %d 号、回忆 %s 号）", *pl.Support.Set, intOr(pl.Memory.Set))})
				e.Groups = pl.groups(h, cat)
			} else {
				e.Rows = append(e.Rows, [2]string{"编成", "沿用游戏里当前选中的编成"})
			}
		}
		if a := r.Flags["main-attr"]; a != "" {
			e.Rows = append(e.Rows, [2]string{"主属性", labels.Attr(a)})
		}
	}
	return e, true
}

func or(s, d string) string {
	if s == "" {
		return d
	}
	return s
}

func intOr(p *int) string {
	if p == nil {
		return "?"
	}
	return fmt.Sprint(*p)
}

// ---- 面板 ----

func (s *state) loadIdols(h plugin.Host) {
	if s.loaded {
		return
	}
	s.loaded = true
	h.Go(func() func() {
		var d struct {
			Items []idol `json:"items"`
		}
		_ = h.Call(context.Background(), []string{"info", "idols"}).Decode(&d)
		return func() { s.idols = d.Items }
	})
}

func (s *state) panel(c *ui.Context, h plugin.Host) {
	s.loadIdols(h)
	m := modes[s.mode]
	kit.Section(c, "模式")
	ui.Row(c).Gap(6).Wrap().Children(func() {
		for i, x := range modes {
			b := ui.Button(c, x.name).Key("mode-"+x.id).FontSize(kit.FSmall).Padding(6, 12).Radius(99)
			if i == s.mode {
				b = b.Background(kit.BrandT).TextColor(kit.WarnInk).Border(kit.Hair, kit.Warn)
			} else {
				b = b.Background(kit.CardBg).TextColor(kit.Ink2).Border(kit.Hair, kit.Line)
			}
			if b.Clicked() && s.mode != i {
				s.mode, s.diff = i, 0
			}
		}
	})
	kit.Note(c, m.desc)
	produce := m.id == "hif" || m.id == "hajime" || m.id == "nia"
	if produce {
		ui.Checkbox(c, &s.resume, "接着打游戏里开着的那局")
	}
	if m.id == "hif" {
		kit.Section(c, "打哪一段")
		ui.Segmented(c, &s.hifMain, "选拔", "本战")
	}
	if len(m.diffs) > 0 && !s.resume {
		kit.Section(c, "难度")
		names := make([]string, len(m.diffs))
		for i, d := range m.diffs {
			names[i] = strings.ToUpper(d)
		}
		s.diff = min(s.diff, len(names)-1)
		ui.Segmented(c, &s.diff, names...)
	}
	needIdol := (produce && !s.resume) || m.id == "tower"
	if needIdol {
		s.idolPicker(c, h)
	}
	args, label := s.args()
	ui.Row(c).Gap(8).AlignItems(ui.Center).Padding(8, 0, 0, 0).Children(func() {
		if label != "" {
			kit.Note(c, label).Shrink(1)
		}
		ui.Spacer(c)
		if kit.Primary(c, "发起").Disabled(args == nil).Clicked() && args != nil {
			h.RunArgs(label, args)
		}
	})
	kit.Note(c, "点「发起」后对话里会出一张确认卡，再点「开始」才动游戏。")
}

func (s *state) idolPicker(c *ui.Context, h plugin.Host) {
	kit.Section(c, "偶像卡")
	ui.TextInput(c, &s.query).Placeholder("输入偶像名或卡名筛选，例如 篠澤広").FontSize(kit.FSmall)
	if s.idol.ID != "" {
		ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
			ui.Icon(c, kit.IcCheck).FontSize(13).TextColor(kit.Good)
			ui.Text(c, "已选："+s.idol.Name).FontSize(kit.FSmall).TextColor(kit.Ink)
		})
	}
	if len(s.idols) == 0 {
		kit.Note(c, "还没扫描过偶像卡：可以在资料库里扫描，或者直接在对话里说「用某某打一把」。")
		return
	}
	q := strings.TrimSpace(s.query)
	list := make([]idol, 0, len(s.idols))
	for _, it := range s.idols {
		if q == "" || strings.Contains(it.Name, q) || strings.Contains(it.ID, q) {
			list = append(list, it)
		}
	}
	sort.SliceStable(list, func(i, j int) bool { return rank(list[i].Rarity) < rank(list[j].Rarity) })
	ui.Column(c).Gap(2).Children(func() {
		for i, it := range list {
			if i >= 12 {
				kit.Note(c, fmt.Sprintf("还有 %d 张，输入名字筛选", len(list)-12))
				break
			}
			en := common.Shared.Get(h, it.ID)
			art := ""
			if en != nil {
				art = common.Shared.Sprite(h, en.Asset)
			}
			if kit.RowButton(c, "idol-"+it.ID, it.Name, it.ID == s.idol.ID).Children(func() {
				kit.Thumb(c, art, 28, 28, 6)
				ui.Text(c, it.Name).FontSize(kit.FSmall).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
				ui.Spacer(c)
				ui.Text(c, it.Rarity).FontSize(10.5).FontWeight(700).TextColor(kit.Brand)
			}).Clicked() {
				s.idol = it
			}
		}
	})
}

func rank(r string) int {
	switch r {
	case "SSR":
		return 0
	case "SR":
		return 1
	case "R":
		return 2
	}
	return 3
}

// args 是面板现在选的东西对应的 run start 参数和给人看的一句话；缺东西时 args 为 nil。
func (s *state) args() ([]string, string) {
	m := modes[s.mode]
	a := []string{"run", "start", m.id}
	switch m.id {
	case "daily":
		return a, "帮我领日常奖励"
	case "tower":
		if s.idol.ID == "" {
			return nil, ""
		}
		return append(a, "--idol", s.idol.ID), "开一局偶像之路（" + s.idol.Name + "）"
	}
	if s.resume {
		a = append(a, "--command", "resume")
	} else {
		a = append(a, "--command", "new")
	}
	if m.id == "hif" {
		a = append(a, "--mode", []string{"qualify", "main"}[s.hifMain])
	}
	if s.resume {
		return a, "接着打 " + m.name
	}
	if s.idol.ID == "" {
		return nil, ""
	}
	a = append(a, "--idol", s.idol.ID)
	if len(m.diffs) > 0 {
		a = append(a, "--difficulty", m.diffs[min(s.diff, len(m.diffs)-1)])
	}
	return a, "开一局 " + m.name + "（" + s.idol.Name + "）"
}

// ---- 上下文 ----

func recent(ctx context.Context, h plugin.Host, cat *common.Catalog) string {
	var st struct {
		Current *plugin.Job  `json:"current"`
		Recent  []plugin.Job `json:"recent"`
	}
	if h.Call(ctx, []string{"run", "status"}).Decode(&st) != nil {
		return ""
	}
	jobs := st.Recent
	if len(jobs) == 0 {
		return ""
	}
	sort.SliceStable(jobs, func(i, j int) bool { return jobs[i].StartedAt > jobs[j].StartedAt })
	var b strings.Builder
	idolCount, modeCount := map[string]int{}, map[string]int{}
	var ids []string
	for _, j := range jobs {
		if id, _ := j.Params["idol"].(string); id != "" {
			if idolCount[id] == 0 {
				ids = append(ids, id)
			}
			idolCount[id]++
		}
		if modes := modeName(j.Kind); j.Kind == "hif" || j.Kind == "hajime" || j.Kind == "nia" || j.Kind == "tower" {
			modeCount[modes]++
		}
	}
	names := cat.Lookup(h, ids...)
	idolName := func(id string) string {
		if e := names[id]; e != nil {
			n := e.Name
			if i := strings.LastIndex(n, "】"); i >= 0 {
				n = n[i+len("】"):]
			}
			return strings.TrimSpace(n)
		}
		return id
	}
	for i, j := range jobs {
		if i >= 5 {
			break
		}
		line := time.Unix(int64(j.StartedAt), 0).Format("01-02 15:04") + " " + modeName(j.Kind)
		if id, _ := j.Params["idol"].(string); id != "" {
			line += " · " + idolName(id)
		}
		line += " · " + labels.State(j.State)
		b.WriteString(line + "\n")
	}
	b.WriteString("常用偶像：" + top(idolCount, idolName) + "\n")
	b.WriteString("常用模式：" + top(modeCount, func(s string) string { return s }))
	return b.String()
}

func top(m map[string]int, name func(string) string) string {
	type kv struct {
		k string
		n int
	}
	var l []kv
	for k, n := range m {
		l = append(l, kv{k, n})
	}
	sort.Slice(l, func(i, j int) bool { return l[i].n > l[j].n || (l[i].n == l[j].n && l[i].k < l[j].k) })
	var out []string
	for i, x := range l {
		if i >= 3 {
			break
		}
		out = append(out, fmt.Sprintf("%s（%d 局）", name(x.k), x.n))
	}
	if len(out) == 0 {
		return "—"
	}
	return strings.Join(out, "、")
}

// ---- 新对话页的引导卡 ----

func shortIdol(name string) string {
	if i := strings.LastIndex(name, "】"); i >= 0 {
		name = name[i+len("】"):]
	}
	return strings.TrimSpace(name)
}

func (s *state) loadRecent(h plugin.Host) {
	if s.recentLoading || (!s.recentAt.IsZero() && h.Now().Sub(s.recentAt) < time.Minute) {
		return
	}
	s.recentLoading = true
	h.Go(func() func() {
		var st struct {
			Recent []plugin.Job `json:"recent"`
		}
		_ = h.Call(context.Background(), []string{"run", "status"}).Decode(&st)
		return func() {
			s.recentLoading, s.recentAt = false, h.Now()
			sort.SliceStable(st.Recent, func(i, j int) bool { return st.Recent[i].StartedAt > st.Recent[j].StartedAt })
			s.recent = st.Recent
		}
	})
}

// starters 是「用某某再打一把某模式」（最近两局里不同的偶像 + 模式）和「领日常奖励」；没有最近几局时引导去开局面板。
func (s *state) starters(h plugin.Host, cat *common.Catalog) []plugin.Starter {
	s.loadRecent(h)
	var out []plugin.Starter
	seen := map[string]bool{}
	for _, j := range s.recent {
		if len(out) == 2 {
			break
		}
		id, _ := j.Params["idol"].(string)
		if id == "" || (j.Kind != "hajime" && j.Kind != "nia" && j.Kind != "hif" && j.Kind != "tower") || seen[id+j.Kind] {
			continue
		}
		seen[id+j.Kind] = true
		e := cat.Get(h, id)
		if e == nil {
			continue
		}
		name, mode := shortIdol(e.Name), modeName(j.Kind)
		out = append(out, plugin.Starter{
			Label: "用" + name + "再打一把" + mode,
			Sub:   "上次 " + time.Unix(int64(j.StartedAt), 0).Format("01-02") + " · " + labels.State(j.State),
			Say:   "帮我用" + name + "打一把" + mode,
			Art:   cat.Sprite(h, e.Asset),
			Attr:  e.Attr,
		})
	}
	if len(out) == 0 {
		out = append(out, plugin.Starter{Label: "选个偶像开一局", Sub: "也可以直接说「帮我用某某打一把初」", Say: "/开局", Icon: kit.IcPlay})
	}
	out = append(out, plugin.Starter{Label: "领日常奖励", Sub: "活動費、礼物、工作、任务，不花ジュエル", Icon: kit.IcSparkles,
		Run: func(h plugin.Host) { h.RunArgs("帮我领日常奖励", []string{"run", "start", "daily"}) }})
	return out
}
