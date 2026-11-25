// Package library 是内置的「资料库」插件：对话里的信息卡（按种类分模板）、支援卡 / 回忆 / 偶像卡 / 资料库搜索页、
// 扫描入口、账号和库存日期的上下文（设计稿 §5.3）。
package library

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/common"
)

var kindName = map[string]string{
	"supports": "支援卡", "memories": "回忆", "idols": "偶像卡", "affection": "好感度", "game_settings": "游戏设置",
	"formations": "编成位", "catalog": "资料库",
}

type listing struct {
	ScannedAt *string          `json:"scanned_at"`
	Items     []map[string]any `json:"items"`
	Hint      string           `json:"hint"`
}

type state struct {
	lists    map[string]*listing
	loading  map[string]bool
	errs     map[string]string
	query    string
	results  []map[string]any
	searched string

	greet        string
	greetAt      time.Time
	greetLoading bool
}

// New 建资料库插件。
func New() *plugin.Plugin {
	s := &state{lists: map[string]*listing{}, loading: map[string]bool{}, errs: map[string]string{}}
	return &plugin.Plugin{
		ID:    "library",
		Name:  "资料库",
		Icon:  kit.IcBook,
		Tools: []string{"info summary", "info supports", "info memories", "info idols", "info affection", "info formations", "info game-settings", "catalog search", "catalog get", "catalog supports", "catalog sync"},
		Commands: []plugin.Command{
			{Name: "资料库", Hint: "看支援卡、回忆、偶像卡"},
			{Name: "扫描", Hint: "扫描支援卡 / 回忆 / 偶像卡 / 首页数值", Route: "scan"},
		},
		Chips: []plugin.Chip{{Label: "/扫描", Say: "/扫描"}, {Label: "/资料库", Say: "/资料库"}},
		Cards: map[string]plugin.CardView{
			"info":   func(c *ui.Context, h plugin.Host, card plugin.Card) { s.infoCard(c, h, card) },
			"choice": func(c *ui.Context, h plugin.Host, card plugin.Card) { choiceCard(c, h, card) },
		},
		Panel:    func(c *ui.Context, h plugin.Host, route string) { s.panel(c, h, route) },
		Greeting: s.greeting,
		Starters: func(plugin.Host) []plugin.Starter {
			return []plugin.Starter{{Label: "看看我的 SSR 支援卡", Sub: "按属性、等级、突破列出来", Say: "我有哪些 SSR 支援卡？", Icon: kit.IcBook}}
		},
		ContextTitle: "账号和库存",
		Context:      func(ctx context.Context, h plugin.Host) string { return summaryContext(ctx, h) },
	}
}

func str(m map[string]any, k string) string {
	switch v := m[k].(type) {
	case string:
		return v
	case float64:
		return fmt.Sprint(int64(v))
	case int:
		return fmt.Sprint(v)
	}
	return ""
}

func shortDate(s string) string {
	if len(s) >= 10 {
		return s[5:10]
	}
	return s
}

// ---- 对话里的信息卡 ----

func (s *state) infoCard(c *ui.Context, h plugin.Host, card plugin.Card) {
	kind, _ := card["kind"].(string)
	title, _ := card["title"].(string)
	if title == "" {
		title = kindName[kind]
	}
	items := toItems(card["items"])
	scanned, _ := card["scannedAt"].(string)
	hint, _ := card["hint"].(string)
	kit.Card(c).MaxWidth(620).Children(func() {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Text(c, title).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
			meta := fmt.Sprintf("共 %d 条", len(items))
			if scanned != "" {
				meta += " · 上次扫描 " + shortDate(scanned)
			}
			ui.Text(c, meta).FontSize(kit.FNote).TextColor(kit.Ink3)
			ui.Spacer(c)
			ui.Text(c, "资料库").FontSize(11).TextColor(kit.Ink3)
		})
		if len(items) == 0 {
			kit.Note(c, or(hint, "没有符合的"))
			return
		}
		shown := items
		if len(shown) > 6 {
			shown = shown[:6]
		}
		for _, it := range shown {
			s.row(c, h, kind, it)
		}
		if len(items) > len(shown) {
			if kit.Ghost(c, fmt.Sprintf("看全部 %d 条 ›", len(items))).Key("all-" + kind).Clicked() {
				h.OpenPanel("library", kind)
			}
		}
	})
}

// choiceCard 是 agent 请玩家挑偶像卡的选择卡（11-25 review）：每项是卡面 + 卡名 + 稀有度 / 流派，点一下就以玩家身份回一句。
func choiceCard(c *ui.Context, h plugin.Host, card plugin.Card) {
	q, _ := card["question"].(string)
	picked, _ := card["picked"].(string)
	done := card["status"] == "picked"
	busy := !done && h.Busy() // 助手还在回复：这时点选会被丢掉（CodeRabbit #79）
	kit.Card(c).MaxWidth(620).Children(func() {
		ui.Text(c, or(q, "选一张偶像卡")).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
		ui.Row(c).Gap(10).Wrap().Children(func() {
			for _, it := range toItems(card["options"]) {
				id := str(it, "id")
				name, title, chara, rarity, plan, art := idolLabel(h, it)
				unknown := name == id // 卡名还没查到：不给看编号、不让选（CodeRabbit #79）
				if unknown {
					name, chara, title = "卡名还在查", "卡名还在查…", ""
				}
				sel := picked == id
				b := ui.ButtonBase(c).Key("pick-"+id).Label("选"+name).Width(132).Padding(10).Radius(kit.R12).
					Background(kit.CardBg).Border(kit.Hair, kit.Line).Disabled(done || busy || unknown)
				switch {
				case sel:
					b = b.Background(kit.BrandT).Border(1.5, kit.Brand)
				case done:
					b = b.Opacity(0.5)
				case b.Hovered():
					b = b.Background(kit.Surface2).Border(1, kit.Warn)
				}
				if b.Children(func() {
					ui.Column(c).Gap(5).AlignItems(ui.Center).Children(func() {
						kit.Thumb(c, art, 72, 72, 12)
						ui.Text(c, chara).FontSize(12.5).FontWeight(600).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
						if title != "" {
							ui.Text(c, "【"+title+"】").FontSize(11).TextColor(kit.Ink2).MaxLines(2).Ellipsis("…")
						}
						ui.Row(c).Gap(6).Children(func() {
							if rarity != "" {
								ui.Text(c, rarity).FontSize(10.5).FontWeight(700).TextColor(rarityColor(rarity))
							}
							if plan != "" {
								ui.Text(c, planName(plan)).FontSize(10.5).TextColor(kit.Ink3)
							}
						})
					})
				}).Clicked() && !done && !busy && !unknown {
					card["status"], card["picked"] = "picked", id
					h.Ask("用" + name) // 只写卡名，不给玩家看编号；agent 按卡名查编号
				}
			}
		})
		if done {
			kit.Note(c, "已选好，接着去开局。")
		} else if busy {
			kit.Note(c, "等这条回复说完再选。")
		}
	})
}

// idolLabel 是偶像卡的显示名：卡里带了就用卡里的，没带（只有编号）就按编号查资料库。
func idolLabel(h plugin.Host, it map[string]any) (name, title, chara, rarity, plan, art string) {
	id := str(it, "id")
	name, title, chara, rarity, plan = str(it, "name"), str(it, "title"), str(it, "idol"), str(it, "rarity"), str(it, "plan")
	if e := common.Shared.Get(h, id); e != nil {
		art = common.Shared.Sprite(h, e.Asset)
		if name == "" {
			name = e.Name
		}
		if rarity == "" {
			rarity = e.Rarity
		}
	}
	if name == "" {
		name = id
	}
	if chara == "" {
		chara = name
	}
	if !strings.HasPrefix(name, "【") && title != "" {
		name = "【" + title + "】" + chara
	}
	return
}

func or(s, d string) string {
	if s == "" {
		return d
	}
	return s
}

func toItems(v any) []map[string]any {
	switch l := v.(type) {
	case []map[string]any:
		return l
	case []any:
		out := make([]map[string]any, 0, len(l))
		for _, x := range l {
			if m, ok := x.(map[string]any); ok {
				out = append(out, m)
			}
		}
		return out
	}
	return nil
}

// row 按种类画一行：偶像卡用偶像模板（不是支援卡的「Lv / 突破」）。
func (s *state) row(c *ui.Context, h plugin.Host, kind string, it map[string]any) {
	switch kind {
	case "idols":
		idolRow(c, h, it)
	case "supports":
		supportRow(c, h, it)
	case "memories":
		memoryRow(c, h, it)
	case "game_settings":
		settingRow(c, it)
	default:
		var parts []string
		for k, v := range it {
			parts = append(parts, fmt.Sprintf("%s：%v", k, v))
		}
		ui.Text(c, strings.Join(parts, "  ")).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
	}
}

func rarityColor(r string) ui.Color {
	switch r {
	case "SSR":
		return kit.Brand
	case "SR":
		return kit.Da
	}
	return kit.Ink3
}

func idolRow(c *ui.Context, h plugin.Host, it map[string]any) {
	id := str(it, "id")
	art := ""
	if e := common.Shared.Get(h, id); e != nil {
		art = common.Shared.Sprite(h, e.Asset)
	}
	chara, title := str(it, "idol"), str(it, "title")
	if chara == "" {
		chara = str(it, "name")
	}
	ui.Row(c).Gap(10).AlignItems(ui.Center).Padding(4, 0).Children(func() {
		kit.Thumb(c, art, 40, 40, 9)
		ui.Column(c).Gap(2).Shrink(1).Children(func() {
			ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
				ui.Text(c, chara).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
				if r := str(it, "rarity"); r != "" {
					ui.Text(c, r).FontSize(10.5).FontWeight(700).TextColor(rarityColor(r))
				}
			})
			if title != "" {
				ui.Text(c, "【"+title+"】").FontSize(kit.FNote).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…")
			}
		})
		ui.Spacer(c)
		if p := str(it, "plan"); p != "" {
			ui.Text(c, planName(p)).FontSize(11.5).TextColor(kit.Ink2).Padding(2, 8).Radius(99).Border(kit.Hair, kit.Line)
		}
	})
}

func planName(p string) string {
	switch strings.ToLower(p) {
	case "sense":
		return "センス"
	case "logic":
		return "ロジック"
	case "anomaly":
		return "アノマリー"
	}
	return p
}

func supportRow(c *ui.Context, h plugin.Host, it map[string]any) {
	id := str(it, "id")
	art := common.Shared.Sprite(h, "support-"+id)
	e := common.Shared.Get(h, id)
	ja := str(it, "name")
	name := or(ja, "（没认出）")
	if e != nil && e.Name != "" {
		name = e.Name // 资料库的中文名（11-22：资源站的信息用上，先加中文）；日文原名跟在后面
	}
	meta := strings.TrimSpace(str(it, "rarity"))
	if lv := str(it, "level"); lv != "" {
		meta += " Lv" + lv
	}
	if b := str(it, "breaks"); b != "" {
		meta += " · 突破 " + b
	}
	row := ui.Row(c).Gap(10).AlignItems(ui.Center).Padding(4, 0)
	if id != "" {
		row = row.Tooltip(common.Tip(e, common.Both(name, ja), strings.TrimSpace(meta)))
	}
	row.Children(func() {
		kit.Thumb(c, art, 36, 36, 8)
		ui.Box(c).Size(7, 7).Radius(4).Background(kit.AttrColor(str(it, "attr"))).Shrink(0)
		ui.Text(c, name).FontSize(kit.FSmall).TextColor(kit.Ink).MaxLines(1).Ellipsis("…").Shrink(1)
		if ja != "" && ja != name {
			ui.Text(c, ja).FontSize(11.5).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…").Shrink(2)
		}
		ui.Spacer(c)
		ui.Text(c, strings.TrimSpace(meta)).FontSize(11.5).TextColor(kit.Ink2).SingleLine()
	})
}

func memoryRow(c *ui.Context, h plugin.Host, it map[string]any) {
	st, _ := it["stats"].(map[string]any)
	num := func(k string) string {
		if v, ok := st[k].(float64); ok {
			return fmt.Sprint(int64(v))
		}
		return "?"
	}
	id := str(it, "card_id")
	ja := str(it, "card")
	name := or(ja, "（没认出）")
	art := ""
	e := common.Shared.Get(h, id)
	if e != nil {
		if e.Name != "" {
			name = e.Name
		}
		art = common.Shared.Sprite(h, e.Asset)
	}
	stats := "Vo " + num("Vo") + " · Da " + num("Da") + " · Vi " + num("Vi")
	timing := or(labels.Timing(str(it, "timing")), "?")
	row := ui.Row(c).Gap(10).AlignItems(ui.Center).Padding(4, 0)
	if id != "" {
		row = row.Tooltip(common.Tip(e, common.Both(name, ja), "用在："+timing, stats))
	}
	row.Children(func() {
		kit.Thumb(c, art, 36, 36, 8)
		ui.Text(c, name).FontSize(kit.FSmall).TextColor(kit.Ink).MaxLines(1).Ellipsis("…").Shrink(1)
		if ja != "" && ja != name {
			ui.Text(c, ja).FontSize(11.5).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…").Shrink(2)
		}
		ui.Spacer(c)
		ui.Text(c, stats).FontSize(11.5).TextColor(kit.Ink2).SingleLine()
		ui.Text(c, timing).FontSize(11).TextColor(kit.Ink2).Padding(1, 6).Radius(5).Background(kit.Surface2).Shrink(0)
	})
}

func settingRow(c *ui.Context, it map[string]any) {
	state := str(it, "state")
	col, text := kit.Ink3, "读不到"
	switch state {
	case "ON":
		col, text = kit.Good, "ON"
	case "OFF":
		col, text = kit.Bad, "OFF"
	}
	ui.Row(c).Padding(4, 0).Children(func() {
		ui.Text(c, str(it, "item")).FontSize(kit.FSmall).TextColor(kit.Ink)
		ui.Spacer(c)
		ui.Text(c, text).FontSize(kit.FNote).FontWeight(700).TextColor(col)
	})
}

// ---- 面板 ----

func (s *state) load(h plugin.Host, kind string, force bool) *listing {
	if l, ok := s.lists[kind]; ok && !force {
		return l
	}
	if s.loading[kind] {
		return s.lists[kind]
	}
	s.loading[kind] = true
	h.Go(func() func() {
		var l listing
		err := h.Call(context.Background(), []string{"info", strings.ReplaceAll(kind, "_", "-")}).Decode(&l)
		return func() {
			delete(s.loading, kind)
			if err != nil {
				s.errs[kind] = err.Error()
				return
			}
			delete(s.errs, kind)
			s.lists[kind] = &l
		}
	})
	return s.lists[kind]
}

func (s *state) tabs(c *ui.Context, h plugin.Host, route string) {
	ui.Row(c).Gap(6).Wrap().Children(func() {
		for _, t := range [][2]string{{"", "概况"}, {"supports", "支援卡"}, {"memories", "回忆"}, {"idols", "偶像卡"}, {"search", "搜资料库"}, {"scan", "扫描"}} {
			b := ui.Button(c, t[1]).Key("tab-"+t[0]).FontSize(kit.FNote).Padding(4, 10).Radius(99)
			if t[0] == route {
				b = b.Background(kit.BrandT).TextColor(kit.WarnInk).Border(kit.Hair, kit.Warn)
			} else {
				b = b.Background(kit.CardBg).TextColor(kit.Ink2).Border(kit.Hair, kit.Line)
			}
			if b.Clicked() {
				h.OpenPanel("library", t[0])
			}
		}
	})
}

func (s *state) panel(c *ui.Context, h plugin.Host, route string) {
	s.tabs(c, h, route)
	switch route {
	case "supports", "memories", "idols", "affection", "game_settings", "formations":
		s.listPanel(c, h, route)
	case "scan":
		s.scanPanel(c, h)
	case "search":
		s.searchPanel(c, h)
	default:
		s.overview(c, h)
	}
}

func (s *state) listPanel(c *ui.Context, h plugin.Host, kind string) {
	l := s.load(h, kind, false)
	ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
		meta := "读取中…"
		if l != nil {
			meta = fmt.Sprintf("共 %d 条", len(l.Items))
			if l.ScannedAt != nil {
				meta += " · 上次扫描 " + shortDate(*l.ScannedAt)
			}
		}
		ui.Text(c, kindName[kind]).FontSize(15).FontWeight(650).TextColor(kit.Ink)
		ui.Text(c, meta).FontSize(kit.FNote).TextColor(kit.Ink3)
		ui.Spacer(c)
		if kit.Ghost(c, "刷新").Key("reload-" + kind).Clicked() {
			s.load(h, kind, true)
		}
	})
	if e := s.errs[kind]; e != "" {
		ui.Text(c, e).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
	}
	if l == nil {
		return
	}
	if len(l.Items) == 0 {
		kit.Note(c, or(l.Hint, "还没扫描过；到「扫描」里扫一次。"))
		return
	}
	ui.Column(c).Gap(0).Children(func() {
		for i, it := range l.Items {
			ui.Column(c).Key(fmt.Sprintf("%s-%d", kind, i)).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Padding(2, 0).Children(func() {
				s.row(c, h, kind, it)
			})
		}
	})
}

var scans = [][3]string{
	{"scan-supports", "扫描支援卡", "翻一遍支援卡列表，记下等级和突破。"},
	{"scan-memories", "扫描回忆", "翻一遍回忆列表，记下技能卡、参数和能力。"},
	{"scan-idols", "扫描偶像卡", "记下持有的偶像卡。"},
	{"scan-account", "读首页数值", "PLv、AP、ジュエル。"},
	{"check-settings", "检查游戏设置", "快进、跳过演出那几项开没开。"},
}

func (s *state) scanPanel(c *ui.Context, h plugin.Host) {
	kit.Note(c, "扫描会操作游戏（只看画面、翻列表，不改东西）。点了会在对话里出确认卡。")
	for _, sc := range scans {
		ui.Row(c).Gap(10).AlignItems(ui.Center).Padding(8, 0).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
			ui.Column(c).Gap(2).Shrink(1).Children(func() {
				ui.Text(c, sc[1]).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
				kit.Note(c, sc[2])
			})
			ui.Spacer(c)
			if kit.Secondary(c, sc[1]).Key("scan-" + sc[0]).Label(sc[1]).Clicked() {
				h.RunArgs(sc[1], []string{"run", "start", sc[0]})
			}
			kit.Note(c, sc[2]).Shrink(1)
		})
	}
}

func (s *state) searchPanel(c *ui.Context, h plugin.Host) {
	in := ui.SearchField(c, &s.query).Placeholder("搜卡名或效果，例如 好調、エウレカ")
	if in.Submitted() && strings.TrimSpace(s.query) != "" {
		q := strings.TrimSpace(s.query)
		s.searched = q
		h.Go(func() func() {
			var d struct {
				Items []map[string]any `json:"items"`
			}
			_ = h.Call(context.Background(), []string{"catalog", "search", q, "--lang", "zh"}).Decode(&d)
			return func() { s.results = d.Items }
		})
	}
	if s.searched != "" && len(s.results) == 0 {
		kit.Note(c, "没搜到「"+s.searched+"」。资料库没同步时先到设置里同步一次。")
	}
	for i, it := range s.results {
		ui.Column(c).Key(fmt.Sprintf("res-%d", i)).Gap(2).Padding(6, 0).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
			ui.Row(c).Gap(6).Children(func() {
				ui.Text(c, str(it, "name")).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
				ui.Text(c, kindName[str(it, "kind")]).FontSize(11).TextColor(kit.Ink3)
			})
			if t := str(it, "text"); t != "" {
				ui.Text(c, t).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
			}
		})
	}
}

type summary struct {
	Account *struct {
		PLv   *int `json:"plv"`
		AP    *int `json:"ap"`
		APMax *int `json:"ap_max"`
		Jewel *int `json:"jewel"`
	} `json:"account"`
	Kinds map[string]struct {
		Count     int     `json:"count"`
		ScannedAt *string `json:"scanned_at"`
	} `json:"kinds"`
}

func (s *state) overview(c *ui.Context, h plugin.Host) {
	if s.lists["_summary"] == nil && !s.loading["_summary"] {
		s.loading["_summary"] = true
		h.Go(func() func() {
			text := summaryContext(context.Background(), h)
			return func() {
				delete(s.loading, "_summary")
				s.lists["_summary"] = &listing{Hint: text}
			}
		})
	}
	if l := s.lists["_summary"]; l != nil {
		for _, line := range strings.Split(l.Hint, "\n") {
			ui.Text(c, line).FontSize(kit.FSmall).TextColor(kit.Ink).Wrap()
		}
	}
}

// summaryContext 是账号数值和每类库存扫到哪天。
func summaryContext(ctx context.Context, h plugin.Host) string {
	var d summary
	if h.Call(ctx, []string{"info", "summary"}).Decode(&d) != nil {
		return ""
	}
	var b strings.Builder
	if a := d.Account; a != nil {
		var parts []string
		if a.PLv != nil {
			parts = append(parts, fmt.Sprintf("PLv %d", *a.PLv))
		}
		if a.AP != nil {
			ap := fmt.Sprintf("AP %d", *a.AP)
			if a.APMax != nil {
				ap += fmt.Sprintf("/%d", *a.APMax)
			}
			parts = append(parts, ap)
		}
		if len(parts) > 0 {
			b.WriteString(strings.Join(parts, " · ") + "\n")
		}
	} else {
		b.WriteString("首页数值没读过。\n")
	}
	var parts []string
	for _, k := range []string{"supports", "memories", "idols"} {
		info, ok := d.Kinds[k]
		switch {
		case !ok || info.ScannedAt == nil:
			parts = append(parts, kindName[k]+"没扫描过")
		default:
			parts = append(parts, fmt.Sprintf("%s %d 张（%s 扫过）", kindName[k], info.Count, shortDate(*info.ScannedAt)))
		}
	}
	b.WriteString(strings.Join(parts, "；"))
	return b.String()
}

// greeting 是新对话页问候语下面的「PLv 52 · AP 120/150」（一分钟读一次首页数值）。
func (s *state) greeting(h plugin.Host) string {
	if !s.greetLoading && (s.greetAt.IsZero() || h.Now().Sub(s.greetAt) > time.Minute) {
		s.greetLoading = true
		h.Go(func() func() {
			var d summary
			err := h.Call(context.Background(), []string{"info", "summary"}).Decode(&d)
			return func() {
				s.greetLoading, s.greetAt = false, h.Now()
				if err != nil || d.Account == nil {
					return
				}
				var parts []string
				if d.Account.PLv != nil {
					parts = append(parts, fmt.Sprintf("PLv %d", *d.Account.PLv))
				}
				if d.Account.AP != nil {
					ap := fmt.Sprintf("AP %d", *d.Account.AP)
					if d.Account.APMax != nil {
						ap += fmt.Sprintf("/%d", *d.Account.APMax)
					}
					parts = append(parts, ap)
				}
				s.greet = strings.Join(parts, " · ")
			}
		})
	}
	return s.greet
}
