// Package plan 是内置的「编成方案」插件：对话里的编成卡、编成方案页（出方案、看差在哪、写进游戏）、方案摘要上下文。
package plan

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/common"
)

// Summary 是 plan formation / plan show 的返回（只取界面用到的）。
type Summary struct {
	Created  string `json:"created"`
	MainAttr string `json:"main_attr"`
	Idol     struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"idol"`
	Support struct {
		Set   *int   `json:"set"`
		Error string `json:"error"`
		Own   []struct {
			ID    string   `json:"id"`
			Name  string   `json:"name"`
			Attr  string   `json:"attr"`
			Level *int     `json:"level"`
			Why   []string `json:"why"`
		} `json:"own"`
		Rental *struct {
			ID        string `json:"id"`
			Name      string `json:"name"`
			Attr      string `json:"attr"`
			Estimated bool   `json:"estimated"`
		} `json:"rental"`
	} `json:"support"`
	Memory struct {
		Set    *int   `json:"set"`
		Error  string `json:"error"`
		Chosen []struct {
			CardID  string `json:"card_id"`
			Name    string `json:"name"`
			Upgrade int    `json:"upgrade"`
			Timing  string `json:"timing"`
		} `json:"chosen"`
	} `json:"memory"`
	Notes []string `json:"notes"`
}

func decode(s string, v any) error { return json.Unmarshal([]byte(s), v) }

var scenarios = [][2]string{{"hif", "H.I.F"}, {"hajime", "初"}, {"nia", "N.I.A"}}

type state struct {
	plan     *Summary
	loading  bool
	err      string
	scenario int
	attr     int // 0 自动 1 Vo 2 Da 3 Vi
	busy     bool
	// 重新出方案时填的编成号（CodeRabbit #88：按钮路径也要能补编成号）；slots 是读过的编成位现状
	supSet, memSet string
	slots          []slotRow
	slotsLoaded    bool
}

type slotRow struct {
	Mode   string `json:"mode"`
	Kind   string `json:"kind"`
	Set    int    `json:"set"`
	Filled *int   `json:"filled"`
}

// emptySlots 是这个模式下空着的编成号（读过编成位时才有）。
func (s *state) emptySlots(mode, kind string) []int {
	var out []int
	for _, r := range s.slots {
		if r.Mode == mode && r.Kind == kind && r.Filled != nil && *r.Filled == 0 {
			out = append(out, r.Set)
		}
	}
	return out
}

// New 建编成方案插件。
func New() *plugin.Plugin {
	s := &state{scenario: 1}
	return &plugin.Plugin{
		ID:    "plan",
		Name:  "编成方案",
		Icon:  kit.IcLayers,
		Tools: []string{"plan formation", "plan show", "write-formation", "scan-formations"},
		Commands: []plugin.Command{
			{Name: "编成", Hint: "看开局编成方案、写进游戏"},
		},
		Chips: []plugin.Chip{{Label: "/编成", Say: "/编成"}},
		Cards: map[string]plugin.CardView{
			"formation": func(c *ui.Context, h plugin.Host, card plugin.Card) {
				var p Summary
				b, _ := json.Marshal(card["plan"])
				if json.Unmarshal(b, &p) != nil {
					return
				}
				if s.plan != nil && p.Created > s.plan.Created {
					s.plan, s.err = nil, "" // 对话里出了比面板上更新的方案：面板下次重新读（旧卡不动缓存）
				}
				kit.Card(c).MaxWidth(620).Children(func() {
					ui.Text(c, "开局编成方案").FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink)
					view(c, h, &p, true)
					if kit.Ghost(c, "打开编成页（看理由、写进游戏）›").Key("open-plan").Clicked() {
						h.OpenPanel("plan", "")
					}
				})
			},
		},
		Panel:        func(c *ui.Context, h plugin.Host, _ string) { s.panel(c, h) },
		ContextTitle: "编成方案",
		Context:      func(ctx context.Context, h plugin.Host) string { return summaryText(ctx, h) },
	}
}

func intStr(p *int) string {
	if p == nil {
		return "?"
	}
	return fmt.Sprint(*p)
}

// view 画一份方案：偶像和主属性、支援卡（+租借）、回忆，非 compact 时带理由和备注。
func view(c *ui.Context, h plugin.Host, p *Summary, compact bool) {
	var ids []string
	for _, o := range p.Support.Own {
		ids = append(ids, o.ID)
	}
	if p.Support.Rental != nil {
		ids = append(ids, p.Support.Rental.ID)
	}
	ids = append(ids, p.Idol.ID)
	names := common.Shared.Lookup(h, ids...)
	zh := func(id, ja string) string {
		if e := names[id]; e != nil && e.Name != "" {
			return e.Name
		}
		if ja != "" {
			return ja
		}
		return "?"
	}
	head := zh(p.Idol.ID, p.Idol.Name)
	if p.MainAttr != "" {
		head += " · 主属性 " + labels.Attr(p.MainAttr)
	}
	if p.Support.Set != nil {
		head += " · 支援卡编成 " + intStr(p.Support.Set)
	}
	if p.Memory.Set != nil {
		head += " · 回忆编成 " + intStr(p.Memory.Set)
	}
	ui.Text(c, head).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
	// 11-26：编成号没定时开局会失败（「new 开局要编成号」），卡上要先说
	if p.Support.Set == nil || p.Memory.Set == nil {
		ui.Row(c).Gap(8).Padding(8, 10).Radius(kit.R8).Background(kit.BadT).Children(func() {
			ui.Text(c, "编成号未定").FontSize(kit.FNote).FontWeight(600).TextColor(kit.Bad).Shrink(0)
			ui.Text(c, "还没定存进游戏里的哪个编成位，这样开不了局。到「编成方案」页的「重新出方案」填上编成号（先点「读编成位」看哪些空着），或者在对话里让初星助手带上编成号重出。").
				FontSize(kit.FNote).TextColor(kit.Ink).Wrap().Shrink(1)
		})
	}
	// 方案自己的备注（「建议手动挑」这类警告）：对话里的卡也显示，原来只在编成页里看得到
	if compact && len(p.Notes) > 0 {
		for i, n := range p.Notes {
			if i == 3 {
				kit.Note(c, fmt.Sprintf("还有 %d 条备注，在编成页里。", len(p.Notes)-3))
				break
			}
			kit.Note(c, "· "+n)
		}
	}
	kit.Section(c, "支援卡")
	if p.Support.Error != "" {
		ui.Text(c, p.Support.Error).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
	}
	for i, o := range p.Support.Own {
		ui.Row(c).Key(fmt.Sprintf("sup-%d", i)).Gap(8).AlignItems(ui.Center).Children(func() {
			kit.Thumb(c, common.Shared.Sprite(h, "support-"+o.ID), 28, 28, 6)
			ui.Box(c).Size(7, 7).Radius(4).Background(kit.AttrColor(o.Attr)).Shrink(0)
			ui.Text(c, zh(o.ID, o.Name)).FontSize(kit.FSmall).TextColor(kit.Ink).MaxLines(1).Ellipsis("…").Shrink(1)
			ui.Text(c, "Lv"+intStr(o.Level)).FontSize(11).TextColor(kit.Ink3)
			if !compact && len(o.Why) > 0 {
				ui.Spacer(c)
				ui.Text(c, strings.Join(o.Why, "；")).FontSize(11).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…").Shrink(1)
			}
		})
	}
	if r := p.Support.Rental; r != nil {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			kit.Thumb(c, common.Shared.Sprite(h, "support-"+r.ID), 28, 28, 6)
			name := zh(r.ID, r.Name)
			if r.ID == "" && r.Name == "" {
				name = "开局时按实际列表挑"
			}
			ui.Text(c, "租借："+name).FontSize(kit.FSmall).TextColor(kit.Ink2)
			if r.Estimated {
				ui.Text(c, "估算").FontSize(11).TextColor(kit.Ink3)
			}
		})
	}
	kit.Section(c, "回忆")
	if p.Memory.Error != "" {
		ui.Text(c, p.Memory.Error).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
	}
	for i, m := range p.Memory.Chosen {
		ui.Row(c).Key(fmt.Sprintf("mem-%d", i)).Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Text(c, labels.Timing(m.Timing)).FontSize(11).TextColor(kit.Ink2).Padding(1, 6).Radius(5).Background(kit.Surface2)
			name := m.Name
			if m.Upgrade > 0 {
				name += strings.Repeat("+", m.Upgrade)
			}
			ui.Text(c, name).FontSize(kit.FSmall).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
		})
	}
	if !compact && len(p.Notes) > 0 {
		kit.Section(c, "备注")
		for _, n := range p.Notes {
			kit.Note(c, "· "+n)
		}
	}
}

func (s *state) load(h plugin.Host) {
	if s.loading {
		return
	}
	s.loading = true
	h.Go(func() func() {
		var p Summary
		err := h.Call(context.Background(), []string{"plan", "show"}).Decode(&p)
		return func() {
			s.loading = false
			if err != nil {
				s.err = err.Error()
				return
			}
			s.err, s.plan = "", &p
		}
	})
}

// setInputs 是「重新出方案」里的两个编成号输入；读过编成位时列出空着的号，点一下就填。
func (s *state) setInputs(c *ui.Context, h plugin.Host) {
	if !s.slotsLoaded {
		s.slotsLoaded = true
		h.Go(func() func() {
			var d struct {
				Items []slotRow `json:"items"`
			}
			_ = h.Call(context.Background(), []string{"info", "formations"}).Decode(&d)
			return func() { s.slots = d.Items }
		})
	}
	if s.plan != nil && s.supSet == "" && s.plan.Support.Set != nil {
		s.supSet = strconv.Itoa(*s.plan.Support.Set)
	}
	if s.plan != nil && s.memSet == "" && s.plan.Memory.Set != nil {
		s.memSet = strconv.Itoa(*s.plan.Memory.Set)
	}
	mode := scenarios[s.scenario][0]
	for _, k := range []struct {
		kind, name string
		v          *string
	}{{"support", "支援卡", &s.supSet}, {"memory", "回忆", &s.memSet}} {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Wrap().Children(func() {
			ui.Text(c, k.name+"编成号").FontSize(kit.FSmall).TextColor(kit.Ink2).Width(84)
			ui.TextInput(c, k.v).Placeholder("不填").FontSize(kit.FSmall).Width(72).Label(k.name + "编成号")
			for _, n := range s.emptySlots(mode, k.kind) {
				label := fmt.Sprintf("用%s编成 %d", k.name, n)
				if ui.Button(c, fmt.Sprintf("%d 空着", n)).Key(label).Label(label).FontSize(11.5).Padding(3, 8).Radius(99).
					TextColor(kit.Ink2).Border(kit.Hair, kit.Line).Clicked() {
					*k.v = strconv.Itoa(n)
				}
			}
		})
	}
	if len(s.slots) == 0 {
		kit.Note(c, "不知道哪些编成位空着：先点下面「写进游戏」里的「读编成位」。")
	}
}

func (s *state) panel(c *ui.Context, h plugin.Host) {
	if s.plan == nil && s.err == "" {
		s.load(h)
	}
	if s.err != "" {
		kit.Note(c, s.err)
	}
	if kit.Ghost(c, "刷新").Key("plan-reload").Clicked() {
		s.err = ""
		s.load(h)
	}
	if s.plan != nil {
		view(c, h, s.plan, false)
		if s.plan.Created != "" {
			kit.Note(c, "出方案时间 "+s.plan.Created)
		}
	}
	kit.Section(c, "重新出方案")
	ui.Segmented(c, &s.attr, "主属性自动", "Vo", "Da", "Vi")
	s.setInputs(c, h)
	ui.Row(c).Gap(8).Children(func() {
		if kit.Secondary(c, "重新出方案").Disabled(s.busy || s.plan == nil || s.plan.Idol.ID == "").Clicked() {
			args := []string{"plan", "formation", "--idol", s.plan.Idol.ID}
			if s.attr > 0 {
				args = append(args, "--main-attr", []string{"", "Vo", "Da", "Vi"}[s.attr])
			}
			for _, x := range [][2]string{{"--support-set", s.supSet}, {"--memory-set", s.memSet}} {
				if n, err := strconv.Atoi(strings.TrimSpace(x[1])); err == nil && n > 0 {
					args = append(args, x[0], strconv.Itoa(n))
				} else if strings.TrimSpace(x[1]) != "" {
					s.err = "编成号要填数字：" + x[1]
					return
				}
			}
			s.busy = true
			h.Go(func() func() {
				r := h.Call(context.Background(), args)
				return func() {
					s.busy = false
					if !r.OK && r.Error != nil {
						s.err = r.Error.Message
						return
					}
					s.load(h)
				}
			})
		}
	})
	kit.Note(c, "换偶像出方案：在对话里说「给篠澤広出一个 H.I.F 的编成」。")
	kit.Section(c, "写进游戏")
	names := make([]string, len(scenarios))
	for i, x := range scenarios {
		names[i] = x[1]
	}
	ui.Segmented(c, &s.scenario, names...)
	sc := scenarios[s.scenario][0]
	ui.Row(c).Gap(8).Wrap().Children(func() {
		if kit.Secondary(c, "读编成位").Clicked() {
			h.RunArgs("读 "+scenarios[s.scenario][1]+" 的编成位", []string{"run", "start", "scan-formations", "--scenario", sc})
		}
		if kit.Secondary(c, "看差在哪（不改）").Clicked() {
			h.RunArgs("看 "+scenarios[s.scenario][1]+" 编成差在哪", []string{"run", "start", "write-formation", "--scenario", sc, "--dry-run"})
		}
		if kit.Primary(c, "写进游戏").Clicked() {
			h.RunArgs("把编成方案写进 "+scenarios[s.scenario][1], []string{"run", "start", "write-formation", "--scenario", sc})
		}
	})
	kit.Note(c, "这三个都会操作游戏，点了会在对话里出确认卡。")
}

func summaryText(ctx context.Context, h plugin.Host) string {
	var p Summary
	if h.Call(ctx, []string{"plan", "show"}).Decode(&p) != nil || (p.Idol.ID == "" && p.Idol.Name == "") {
		return "还没有编成方案。"
	}
	name := p.Idol.Name
	if e := common.Shared.Get(h, p.Idol.ID); e != nil && e.Name != "" {
		name = e.Name
	}
	parts := []string{"偶像 " + name}
	if p.MainAttr != "" {
		parts = append(parts, "主属性 "+labels.Attr(p.MainAttr))
	}
	if p.Support.Set != nil {
		parts = append(parts, "支援卡编成 "+intStr(p.Support.Set))
	}
	if p.Memory.Set != nil {
		parts = append(parts, "回忆编成 "+intStr(p.Memory.Set))
	}
	out := strings.Join(parts, "，")
	if len(p.Created) >= 10 {
		out += "（" + p.Created[5:10] + " 出的）"
	}
	return out
}
