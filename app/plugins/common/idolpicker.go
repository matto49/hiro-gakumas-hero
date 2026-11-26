package common

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// IdolItem 是一张扫描到的偶像卡（info idols 的一行）。
type IdolItem struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Rarity string `json:"rarity"`
}

// IdolPicker 是「选一张偶像卡」：读 info idols（扫描到的），筛选、点选。开局页和编成方案页共用。
type IdolPicker struct {
	Query   string
	Items   []IdolItem
	Chosen  IdolItem // Name 是点选时显示的名字（资料库的中文名）
	loaded  bool
	loadErr string // 读取偶像卡失败的原因（和「还没扫描过」分开，#121 review）
}

// Reload 让下次画时重读列表（扫完偶像卡之后）。
func (p *IdolPicker) Reload() { p.loaded = false }

func (p *IdolPicker) load(h plugin.Host) {
	if p.loaded {
		return
	}
	p.loaded = true
	h.Go(func() func() {
		var d struct {
			Items []IdolItem `json:"items"`
		}
		r := h.Call(context.Background(), []string{"info", "idols"})
		why := ""
		if !r.OK {
			why = "读取失败"
			if r.Error != nil && r.Error.Message != "" {
				why = r.Error.Message
			}
		} else if err := r.Decode(&d); err != nil {
			why = "读取结果看不懂：" + err.Error()
		}
		return func() { p.Items, p.loadErr = d.Items, why }
	})
}

// ChosenName 是选中那张现在的显示名（换了语言也跟着变）。
func (p *IdolPicker) ChosenName(h plugin.Host) string {
	return Shared.Name(h, p.Chosen.ID, p.Chosen.Name)
}

// Draw 画选偶像卡：筛选框、已选、最多 12 行（按稀有度）。key 区分同一窗口里的两个选择器。
func (p *IdolPicker) Draw(c *ui.Context, h plugin.Host, key string) {
	p.load(h)
	kit.Section(c, "偶像卡")
	ui.Row(c).Key(key + "-query").Children(func() {
		ui.TextInput(c, &p.Query).Grow(1).Placeholder("输入偶像名或卡名筛选，例如 篠澤広 / 筱泽广").FontSize(kit.FSmall)
	})
	if p.Chosen.ID != "" {
		ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
			ui.Icon(c, kit.IcCheck).FontSize(13).TextColor(kit.Good)
			ui.Text(c, "已选："+p.ChosenName(h)).FontSize(kit.FSmall).TextColor(kit.Ink)
		})
	}
	if p.loadErr != "" {
		kit.Note(c, "偶像卡列表没读出来："+p.loadErr)
		if kit.Secondary(c, "重试").Key(key + "-idol-picker-retry").Label("重新读取偶像卡").Clicked() {
			p.loadErr = ""
			p.Reload()
		}
		return
	}
	if len(p.Items) == 0 {
		kit.Note(c, "还没扫描过偶像卡：在资料库的「扫描」里扫一次偶像卡。")
		return
	}
	// 名字取资料库的中文名（info idols 的 name 是扫描读到的日文原文）；筛选时中日文都认
	ids := make([]string, len(p.Items))
	for i, it := range p.Items {
		ids[i] = it.ID
	}
	cat := Shared.Lookup(h, ids...)
	name := func(it IdolItem) string {
		if e := cat[it.ID]; e != nil && e.Name != "" {
			return e.Name
		}
		return it.Name
	}
	q := strings.TrimSpace(p.Query)
	list := make([]IdolItem, 0, len(p.Items))
	for _, it := range p.Items {
		if q == "" || strings.Contains(it.Name, q) || strings.Contains(name(it), q) || strings.Contains(it.ID, q) {
			list = append(list, it)
		}
	}
	sort.SliceStable(list, func(i, j int) bool { return rarityRank(list[i].Rarity) < rarityRank(list[j].Rarity) })
	ui.Column(c).Gap(2).Children(func() {
		for i, it := range list {
			if i >= 12 {
				kit.Note(c, fmt.Sprintf("还有 %d 张，输入名字筛选", len(list)-12))
				break
			}
			art := ""
			if e := cat[it.ID]; e != nil {
				art = Shared.Sprite(h, e.Asset)
			}
			label := name(it)
			if kit.RowButton(c, key+"-idol-"+it.ID, label, it.ID == p.Chosen.ID).Children(func() {
				kit.Thumb(c, art, 28, 28, 6)
				ui.Text(c, label).FontSize(kit.FSmall).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
				ui.Spacer(c)
				ui.Text(c, it.Rarity).FontSize(10.5).FontWeight(700).TextColor(kit.Brand)
			}).Clicked() {
				p.Chosen = it
				p.Chosen.Name = label
			}
		}
	})
}

func rarityRank(r string) int {
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
