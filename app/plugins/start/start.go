// Package start 是内置的「开局」插件：开局面板、确认卡解读、最近几局的上下文（设计稿 §5.3）。
package start

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
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
	{"daily", "日常", "活动费、礼物、工作派遣、竞赛、商店、硬币扭蛋、任务奖励、社团。不花钻石。", nil},
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

type idol = common.IdolItem

type state struct {
	mode    int
	resume  bool
	hifMain int // 0 选拔 1 本战
	diff    int
	pick    common.IdolPicker // 偶像卡（和编成方案页共用的选择器）
	search  searchState       // 考试出牌：按场次选快速 / 搜索（examsearch.go）

	// 日常的选项（11-26）：竞赛挑哪个对手、商店买什么、硬币扭蛋抽哪个
	contestPick int     // 0 综合力最低 1/2/3 第几个
	shopMode    int     // 0 不买 1 推荐商品 2 全部（零值 = 不花钱，#122 review：老用户照常点日常不该第一次就花金币）
	shopItems   string  // 另外要买的商品名，逗号分隔
	shopRefresh bool    // 买完用每日 1 次的免费刷新再买一轮（缺省不勾）
	shopAP      [4]bool // 体力商店买哪几样（apItems 的顺序）
	gachaMode   int     // 0 不抽 1 自动抽 1 次 2 自己定
	gachaN      [4]string
	plan        *planBrief
	planAt      time.Time

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
			CardID  string `json:"card_id"`
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
		nm := name(o.ID, o.Name)
		it := plugin.ExplainItem{Thumb: cat.Sprite(h, "support-"+o.ID), Name: nm}
		lv := ""
		if o.Level != nil {
			lv = fmt.Sprintf("Lv%d", *o.Level)
			it.Note = lv
		}
		it.Tip = common.Tip(cat.Get(h, o.ID), common.Both(nm, o.Name), lv)
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
		note := "租借：开局时从好友的支援卡里借这一张"
		if r.Estimated {
			it.Note = "估算"
			note += "（按常见等级估算）"
		}
		it.Tip = common.Tip(cat.Get(h, r.ID), common.Both(it.Name, r.Name), note)
		sup.Items = append(sup.Items, it)
	}
	mem := plugin.ExplainGroup{Title: "回忆"}
	for _, m := range pl.Memory.Chosen {
		nm := name(m.CardID, m.Name) + strings.Repeat("+", m.Upgrade)
		it := plugin.ExplainItem{Tag: labels.Timing(m.Timing), Name: nm}
		if e := cat.Get(h, m.CardID); e != nil && e.Asset != "" {
			it.Thumb = cat.Sprite(h, e.Asset)
		}
		it.Tip = common.Tip(cat.Get(h, m.CardID), common.Both(nm, m.Name+strings.Repeat("+", m.Upgrade)), "用在："+labels.Timing(m.Timing))
		mem.Items = append(mem.Items, it)
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
				// 带上开局页存的「考试出牌」（选拔、本战两组都带；CodeRabbit #197：原来不带，按启动环境搜索）
				s.search.load(h)
				argv := append([]string{"run", "start", "hif", "--command", "resume"}, s.search.args(examGroups("hif", 0, true))...)
				h.RunArgs("接着打进行中的培育", argv)
			}},
			{Label: "领日常奖励", Run: func(h plugin.Host) {
				h.RunArgs("帮我领日常奖励", s.dailyArgv())
			}},
		},
		// 日常的选项多了（竞赛对手、商店、体力商店、扭蛋），单独一页（11-26 两种界面方案）
		Nav: []plugin.NavItem{
			{Label: "开局", Icon: kit.IcPlay, Route: ""},
			{Label: "日常", Icon: kit.IcCalendar, Route: "daily"},
		},
		Panel: func(c *ui.Context, h plugin.Host, route string) {
			if route == "daily" {
				s.dailyPanel(c, h)
				return
			}
			s.panel(c, h)
		},
		OnJobEnd: func(_ plugin.Host, _ string, v *plugin.JobView) {
			if v != nil && v.Job != nil && v.Job.Kind == "scan-idols" {
				s.pick.Reload() // 扫完偶像卡：下次画面板重读列表（原来只读一次，要重开程序才看得到）
			}
			s.recentAt = time.Time{} // 「最近几局」也重读
		},
		Explain: func(h plugin.Host, p hiroapi.Pending) (plugin.Explained, bool) {
			return s.explain(h, cat, p)
		},
		Starters:     func(h plugin.Host) []plugin.Starter { return s.starters(h, cat) },
		ContextTitle: "最近几局",
		Context:      func(ctx context.Context, h plugin.Host) string { return recent(ctx, h, cat) },
	}
}

// ---- 确认卡解读 ----

// planOf 是待确认里固定的编成方案摘要（CLI 出待确认时存的快照）。确认卡只看这一份：
// 原来每分钟去读一次 plan show，方案期间重新出过，卡上和 confirm 时实际写进游戏的就不一样了（PR #79 CodeRabbit）。
// 没有快照（旧 CLI 出的待确认、或这个任务不读方案）时返回 nil。
func planOf(p hiroapi.Pending) *planBrief {
	if len(p.Plan) == 0 {
		return nil
	}
	raw, err := json.Marshal(p.Plan)
	if err != nil {
		return nil
	}
	var pl planBrief
	if json.Unmarshal(raw, &pl) != nil {
		return nil
	}
	return &pl
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
		e.Title, e.Cost = "领日常奖励", "会操作游戏 · 不花钻石（商店用金币，扭蛋用各池自己的币）"
		if v := r.Flags["contest-pick"]; v != "" && v != "lowest" {
			e.Rows = append(e.Rows, [2]string{"竞赛对手", "第 " + v + " 个"})
		} else {
			e.Rows = append(e.Rows, [2]string{"竞赛对手", "综合力最低"})
		}
		// 商店 / 扭蛋总是写出来：没传就是不买 / 不抽（review：确认卡上看不出会不会花マニー）
		e.Rows = append(e.Rows, [2]string{"商店", map[string]string{"recommended": "买推荐商品", "all": "能买的都买", "off": "不买", "": "不买"}[r.Flags["shop"]]})
		if v := r.Flags["shop-items"]; v != "" && r.Flags["shop"] != "" && r.Flags["shop"] != "off" {
			e.Rows = append(e.Rows, [2]string{"另外买", v})
		}
		if r.Flags["shop-refresh"] == "on" && r.Flags["shop"] != "" && r.Flags["shop"] != "off" {
			e.Rows = append(e.Rows, [2]string{"免费刷新", "买完用每日免费刷新再买一轮（要确认的不点）"})
		}
		if v := r.Flags["shop-ap"]; v != "" {
			var names []string
			for _, k := range strings.Split(v, ",") {
				for _, it := range apItems {
					if it[0] == k {
						names = append(names, it[1])
					}
				}
			}
			e.Rows = append(e.Rows, [2]string{"体力商店", strings.Join(names, "、")})
		}
		e.Rows = append(e.Rows, [2]string{"硬币扭蛋", gachaLabel(r.Flags["coin-gacha"])})
	case r.Kind == "check-settings" && r.Flags["fix"] == "true":
		e.Title, e.Cost = "把游戏设置改好", "会操作游戏 · 只动快进、跳过演出那 5 个开关"
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
		pl := planOf(p)
		if pl != nil {
			e.Groups = pl.groups(h, cat)
		}
		if !dry {
			if row, ok := writeSetsRow(r.Flags, pl, p.Access); ok {
				e.Rows = append(e.Rows, row)
			}
		}
		return e, true
	case r.Kind == "main-task":
		e.Title = "做初星课题"
	case r.Kind == "story":
		e.Title = "看初星剧情"
		if ch := r.Flags["chapter"]; ch != "" {
			e.Subtitle = "第 " + ch + " 章"
		}
	case r.Kind == "contest-unit":
		if dry {
			e.Title, e.Cost = "看竞赛编队总合力", costReadOnly
		} else {
			e.Title, e.Cost = "竞赛编队", "会操作游戏 · 会改竞赛编队（自动编队重排）"
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
			pl := planOf(p)
			switch {
			case pl != nil && pl.Support.Set != nil:
				e.Rows = append(e.Rows, [2]string{"编成", fmt.Sprintf("沿用编成方案（支援卡 %d 号、回忆 %s 号）", *pl.Support.Set, intOr(pl.Memory.Set))})
				e.Groups = pl.groups(h, cat)
			case pl != nil && r.Flags["prepare"] == "true":
				// G1-F3（11-26）：方案还没定编成号，开局前在同一个任务里挑空着的编成位写进去；原来这里落到「沿用当前编成」，和上面那句说反了
				e.Rows = append(e.Rows, [2]string{"编成", "按编成方案（编成号开局前挑空着的）"})
				e.Groups = pl.groups(h, cat)
			default:
				e.Rows = append(e.Rows, [2]string{"编成", "沿用游戏里当前选中的编成"})
			}
		}
		if note := r.Flags["prepare-note"]; note != "" && r.Flags["prepare"] == "true" {
			e.Rows = append(e.Rows, [2]string{"开局前", note})
		}
		if row, ok := overwriteRow(r.Flags); ok {
			e.Rows = append(e.Rows, row)
		}
		if a := r.Flags["main-attr"]; a != "" {
			e.Rows = append(e.Rows, [2]string{"主属性", labels.Attr(a)})
		}
	}
	if row, ok := examSearchRow(r.Kind, r.Flags); ok {
		e.Rows = append(e.Rows, row)
	}
	return e, true
}

// writeSetsRow 是写编成确认卡上「写进第几号、会不会换掉原来的卡」那一行（N35，G1 第 3 轮 11-26：
// 卡上只有方案内容，写进第几号要翻待确认文件才知道；D23 那次就是靠查文件确认回忆写的是空着的 12 号）。
func writeSetsRow(flags map[string]string, pl *planBrief, access string) ([2]string, bool) {
	set := func(flag string, fromPlan *int) string {
		if v := flags[flag]; v != "" {
			return v
		}
		if fromPlan != nil {
			return strconv.Itoa(*fromPlan)
		}
		return ""
	}
	var sup, mem string
	if pl != nil {
		sup, mem = set("support-set", pl.Support.Set), set("memory-set", pl.Memory.Set)
	} else {
		sup, mem = set("support-set", nil), set("memory-set", nil)
	}
	what := flags["what"]
	var parts []string
	if sup != "" && (what == "" || strings.Contains(what, "support")) {
		parts = append(parts, "支援卡编成 "+sup+" 号")
	}
	if mem != "" && (what == "" || strings.Contains(what, "memory")) {
		parts = append(parts, "回忆编成 "+mem+" 号")
	}
	if len(parts) == 0 {
		return [2]string{}, false
	}
	text := strings.Join(parts, "、")
	switch access {
	case "overwrite":
		text += "（原来有卡的位会被换掉）"
	case "blank":
		text += "（都是空着的位）"
	}
	return [2]string{"写进", text}, true
}

// overwriteRow 是开局确认卡上「点名的编成位」那一行（N65，G1 第 5 轮 11-27）：玩家在对话里点名同意覆盖（--overwrite memory=13），
// 卡上要写明会换掉第几号编成里原来的卡，玩家点确认才写。号码只认 --overwrite 里写的，不从别处猜。
func overwriteRow(flags map[string]string) ([2]string, bool) {
	ov := flags["overwrite"]
	if ov == "" || flags["prepare"] != "true" {
		return [2]string{}, false
	}
	label := map[string]string{"support": "支援卡", "memory": "回忆"}
	var parts []string
	for _, item := range strings.Split(ov, ",") {
		w, n, ok := strings.Cut(strings.TrimSpace(item), "=")
		if !ok || label[w] == "" || strings.TrimSpace(n) == "" {
			continue
		}
		parts = append(parts, label[w]+"编成 "+strings.TrimSpace(n)+" 号")
	}
	if len(parts) == 0 {
		return [2]string{}, false
	}
	return [2]string{"点名的编成位", strings.Join(parts, "、") + "（你在对话里点名的；里面原来的卡会被换掉）"}, true
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

func (s *state) panel(c *ui.Context, h plugin.Host) {
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
	if m.id == "daily" {
		s.dailyOptions(c)
	}
	s.search.draw(c, h, examGroups(m.id, s.hifMain, s.resume))
	needIdol := (produce && !s.resume) || m.id == "tower"
	if needIdol {
		s.pick.Draw(c, h, "start")
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
	kit.Note(c, "点「发起」后先给你看要做的事（确认卡），再点「开始」才动游戏。")
}

// dailyArgv 是按日常页现在的选项领日常的整条命令。
func (s *state) dailyArgv() []string {
	return append([]string{"run", "start", "daily"}, s.dailyArgs()...)
}

// dailyPanel 是单独的「日常」页：说明、几项选项、发起。
func (s *state) dailyPanel(c *ui.Context, h plugin.Host) {
	for _, m := range modes {
		if m.id == "daily" {
			kit.Note(c, m.desc)
		}
	}
	s.dailyOptions(c)
	ui.Row(c).Gap(8).AlignItems(ui.Center).Padding(8, 0, 0, 0).Children(func() {
		ui.Spacer(c)
		if kit.Primary(c, "发起").Key("daily-go").Clicked() {
			h.RunArgs("帮我领日常奖励", s.dailyArgv())
		}
	})
	kit.Note(c, "点「发起」后先给你看要做的事（确认卡），再点「开始」才动游戏。")
}

var apItems = [4][2]string{{"pt", "支援强化点数获取提升"}, {"note", "笔记获取数提升"}, {"retry", "再挑战券"}, {"regen", "回忆再生成券"}}

var gachaPools = [4][2]string{{"friend", "友情"}, {"sense", "感性"}, {"logic", "理性"}, {"anomaly", "非凡"}}

// dailyOptions 是日常的几个选项（竞赛对手、商店、硬币扭蛋）。
func (s *state) dailyOptions(c *ui.Context) {
	kit.Section(c, "竞赛挑哪个对手")
	ui.Segmented(c, &s.contestPick, "综合力最低", "第一个", "第二个", "第三个")
	kit.Note(c, "三个对手由上到下难度和奖励不同。每天 5 场，打完就停。")
	kit.Section(c, "商店（每日兑换所，用金币）")
	ui.Segmented(c, &s.shopMode, "不买", "买推荐商品", "能买的都买")
	if s.shopMode != 0 { // 「另外要买的」是推荐 / 全部的补充，不买时不显示也不传（#122 review）
		ui.Row(c).Key("shop-items").Children(func() {
			ui.TextInput(c, &s.shopItems).Grow(1).Placeholder("另外要买的（可不填）：商品名，逗号分隔，例如 仓本千奈的碎片,再挑战券").FontSize(kit.FSmall)
		})
		kit.Note(c, "推荐商品今天还没买过、又一个都没有时，买最便宜的一件（只买 1 个），让「在商店购物」这个日常任务完成。只用金币，要花钻石的一律不买。")
		ui.Row(c).Key("shop-refresh").Children(func() { ui.Checkbox(c, &s.shopRefresh, "买完用每日免费刷新，再买一轮") })
		kit.Note(c, "只在右上角写着「リスト更新:1回無料」时才点；点了要是弹出确认框，一律点空白关掉、不刷新（不花钻石）。")
	}
	kit.Section(c, "体力商店（可选，用体力）")
	ui.Column(c).Gap(4).Children(func() {
		for i, it := range apItems {
			ui.Row(c).Key("ap-" + it[0]).Children(func() { ui.Checkbox(c, &s.shopAP[i], it[1]) })
		}
	})
	kit.Note(c, "每日兑换所的体力页签：每样一天能买 2 次、一次 10 体力，勾了就买满。不勾不买。")
	kit.Section(c, "硬币扭蛋")
	ui.Segmented(c, &s.gachaMode, "不抽", "自动抽 1 次", "自己定")
	if s.gachaMode == 2 {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Wrap().Children(func() {
			for i, p := range gachaPools {
				ui.Text(c, p[1]).FontSize(kit.FNote).TextColor(kit.Ink2)
				ui.Row(c).Key("gacha-" + p[0]).Width(48).Children(func() {
					ui.TextInput(c, &s.gachaN[i]).Grow(1).Placeholder("0").FontSize(kit.FSmall)
				})
			}
		})
		kit.Note(c, fmt.Sprintf("每个池抽几次（最多 %d 次；每次 10 枚这个池自己的币，币不够就抽能抽的）。", MaxGacha))
	}
}

// MaxGacha 是每个池一次最多抽几次（和 hiro.daily.coin_gacha.MAX_TIMES 一样，#122 review）。
const MaxGacha = 10

// gachaLabel 把 --coin-gacha 的值说成人话（「友情 2 次、感性 1 次」）。
func gachaLabel(v string) string {
	switch v {
	case "", "off":
		return "不抽"
	case "auto":
		return "自动抽 1 次"
	}
	var parts []string
	for _, p := range strings.Split(v, ",") {
		k, n, _ := strings.Cut(p, ":")
		for _, g := range gachaPools {
			if g[0] == k {
				parts = append(parts, g[1]+" "+n+" 次")
			}
		}
	}
	return strings.Join(parts, "、")
}

// dailyArgs 是日常选项对应的 run start 参数。
func (s *state) dailyArgs() []string {
	var a []string
	if s.contestPick > 0 {
		a = append(a, "--contest-pick", fmt.Sprint(s.contestPick))
	}
	// 商店、扭蛋在命令行缺省是关的（花マニー / 币的事默认不做，review）：这里总是显式传选的那项
	a = append(a, "--shop", []string{"off", "recommended", "all"}[s.shopMode])
	if s.shopRefresh && s.shopMode != 0 {
		a = append(a, "--shop-refresh", "on")
	}
	if t := strings.TrimSpace(strings.ReplaceAll(s.shopItems, "，", ",")); t != "" && s.shopMode != 0 {
		a = append(a, "--shop-items", t)
	}
	var ap []string
	for i, it := range apItems {
		if s.shopAP[i] {
			ap = append(ap, it[0])
		}
	}
	if len(ap) > 0 {
		a = append(a, "--shop-ap", strings.Join(ap, ","))
	}
	switch s.gachaMode {
	case 0:
		a = append(a, "--coin-gacha", "off")
	case 1:
		a = append(a, "--coin-gacha", "auto")
	case 2:
		var parts []string
		for i, p := range gachaPools {
			n := 0
			if _, err := fmt.Sscanf(strings.TrimSpace(s.gachaN[i]), "%d", &n); err == nil && n > 0 {
				n = min(n, MaxGacha)
				parts = append(parts, fmt.Sprintf("%s:%d", p[0], n))
			}
		}
		if len(parts) > 0 {
			a = append(a, "--coin-gacha", strings.Join(parts, ","))
		} else {
			a = append(a, "--coin-gacha", "off")
		}
	}
	return a
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
		return append(a, s.dailyArgs()...), "帮我领日常奖励"
	case "tower":
		if s.pick.Chosen.ID == "" {
			return nil, ""
		}
		// 偶像之路按偶像名找路线，传卡编号会在列表页停下（CodeRabbit #122）
		return append(a, "--idol", s.pick.Chosen.BareIdol()), "开一局偶像之路（" + s.pick.Chosen.Name + "）"
	}
	if s.resume {
		a = append(a, "--command", "resume")
	} else {
		a = append(a, "--command", "new")
	}
	if m.id == "hif" {
		a = append(a, "--mode", []string{"qualify", "main"}[s.hifMain])
	}
	a = append(a, s.search.args(examGroups(m.id, s.hifMain, s.resume))...)
	if s.resume {
		return a, "接着打 " + m.name
	}
	if s.pick.Chosen.ID == "" {
		return nil, ""
	}
	a = append(a, "--idol", s.pick.Chosen.ID)
	if len(m.diffs) > 0 {
		a = append(a, "--difficulty", m.diffs[min(s.diff, len(m.diffs)-1)])
	}
	return a, "开一局 " + m.name + "（" + s.pick.Chosen.Name + "）"
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
		if e == nil && j.Kind != "tower" {
			continue
		}
		// 偶像之路的参数是偶像名（不是卡编号），资料库里查不到：直接用名字，不放卡面（CodeRabbit #197）
		name, mode := id, modeName(j.Kind)
		if e != nil {
			name = shortIdol(e.Name)
		}
		st := plugin.Starter{
			Label: "用" + name + "再打一把" + mode,
			Sub:   "上次 " + time.Unix(int64(j.StartedAt), 0).Format("01-02") + " · " + labels.State(j.State),
			Say:   "帮我用" + name + "打一把" + mode,
			Page:  replay(j, name, mode),
		}
		if e != nil {
			st.Art, st.Attr = cat.Sprite(h, e.Asset), e.Attr
		}
		out = append(out, st)
	}
	if len(out) == 0 {
		out = append(out, plugin.Starter{Label: "选个偶像开一局", Sub: "也可以直接说「帮我用某某打一把初」", Say: "/开局", Icon: kit.IcPlay})
	}
	out = append(out, plugin.Starter{Label: "领日常奖励", Sub: "活动费、礼物、工作派遣、任务，不花钻石", Icon: kit.IcSparkles,
		Run: func(h plugin.Host) { h.RunArgs("帮我领日常奖励", s.dailyArgv()) }})
	return out
}

// replay 是首页「用某某再打一把」：按上次那局的参数重新发起（照样出确认卡）。参数拼不出来时 nil（首页不出这一项）。
func replay(j plugin.Job, name, mode string) func(h plugin.Host) {
	params := map[string]any{}
	for k, v := range j.Params {
		params[k] = v
	}
	if params["command"] == "resume" {
		params["command"] = "new" // 上次是接着打的：再打一把是开新的
	}
	// 续打时并进来的记录目录（resume_chain 的 trace）不带：run start 没有 --trace，新局自己建（CodeRabbit #138 / #197）
	delete(params, "trace")
	if idol, _ := params["idol"].(string); j.Kind == "tower" && strings.HasPrefix(idol, "i_card") {
		return nil // 旧的偶像之路记录存的是偶像卡编号：列表页按偶像名找，重放会停在列表页（CodeRabbit #197），首页不出这一项
	}
	argv := common.StartArgv(j.Kind, params)
	if len(argv) == 0 {
		return nil
	}
	return func(h plugin.Host) { h.RunArgs("用"+name+"再打一把"+mode, argv) }
}
