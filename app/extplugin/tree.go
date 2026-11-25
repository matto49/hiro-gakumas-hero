package extplugin

import (
	"fmt"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
)

// Node 是插件用 JSON 描述的一个界面件（§5.4 第一版的件）。字符串一律当纯文字画。
type Node struct {
	Type        string     `json:"type"` // column row text heading tag button card list table image divider progress input select
	ID          string     `json:"id,omitempty"`
	Text        string     `json:"text,omitempty"`
	Variant     string     `json:"variant,omitempty"` // button: primary；text: muted；tag: brand / good / bad
	Children    []Node     `json:"children,omitempty"`
	Items       []string   `json:"items,omitempty"`   // list
	Columns     []string   `json:"columns,omitempty"` // table
	Rows        [][]string `json:"rows,omitempty"`    // table
	Src         string     `json:"src,omitempty"`     // image：插件目录或 HIRO_HOME 下的相对路径
	Width       float32    `json:"width,omitempty"`
	Height      float32    `json:"height,omitempty"`
	Value       string     `json:"value,omitempty"`    // input / select 的初值
	Options     []string   `json:"options,omitempty"`  // select
	Progress    float64    `json:"progress,omitempty"` // 0–1
	Placeholder string     `json:"placeholder,omitempty"`
}

// view 是一棵树的画法和交互：事件交给 onEvent（id，值）。
type view struct {
	dir, home string
	busy      string // 正在等插件回应的件
	inputs    map[string]*string
	onEvent   func(id, value string)
	path      string
}

func (v *view) input(id, init string) *string {
	if v.inputs == nil {
		v.inputs = map[string]*string{}
	}
	p, ok := v.inputs[id]
	if !ok {
		s := init
		p = &s
		v.inputs[id] = p
	}
	return p
}

func (v *view) render(c *ui.Context, n Node, path string) {
	key := fmt.Sprintf("%s/%s", path, n.ID)
	switch n.Type {
	case "column", "card":
		col := ui.Column(c).Gap(8)
		if n.Type == "card" {
			col = kit.Card(c)
		}
		col.Key(key).Children(func() { v.children(c, n, path) })
	case "row":
		ui.Row(c).Key(key).Gap(8).AlignItems(ui.Center).Wrap().Children(func() { v.children(c, n, path) })
	case "heading":
		ui.Text(c, n.Text).FontSize(15).FontWeight(650).TextColor(kit.Ink).Wrap()
	case "text":
		col := kit.Ink
		if n.Variant == "muted" {
			col = kit.Ink3
		}
		ui.Text(c, n.Text).FontSize(kit.FSmall).TextColor(col).Wrap().Selectable()
	case "tag":
		fg, bg := kit.Ink2, kit.Surface2
		switch n.Variant {
		case "brand":
			fg, bg = kit.WarnInk, kit.BrandT
		case "good":
			fg, bg = kit.Good, ui.RGBA(46, 158, 91, 0.12)
		case "bad":
			fg, bg = kit.Bad, kit.BadT
		}
		kit.Tag(c, n.Text, fg, bg)
	case "button":
		var b ui.Element
		if n.Variant == "primary" {
			b = kit.Primary(c, n.Text)
		} else {
			b = kit.Secondary(c, n.Text)
		}
		if b.Key(key).Disabled(v.busy != "").Clicked() && n.ID != "" {
			v.onEvent(n.ID, "")
		}
	case "list":
		ui.Column(c).Key(key).Gap(4).Children(func() {
			for _, it := range n.Items {
				ui.Text(c, "· "+it).FontSize(kit.FSmall).TextColor(kit.Ink2).Wrap()
			}
		})
	case "table":
		ui.Column(c).Key(key).Gap(0).Children(func() {
			row := func(cells []string, head bool) {
				ui.Row(c).Gap(10).Padding(5, 0).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
					for _, cell := range cells {
						t := ui.Text(c, cell).FontSize(kit.FNote).Grow(1).Shrink(1).Wrap()
						if head {
							t.TextColor(kit.Ink3).FontWeight(600)
						} else {
							t.TextColor(kit.Ink)
						}
					}
				})
			}
			if len(n.Columns) > 0 {
				row(n.Columns, true)
			}
			for _, r := range n.Rows {
				row(r, false)
			}
		})
	case "image":
		if p := safePath(v.dir, v.home, n.Src); p != "" {
			w, h := n.Width, n.Height
			if w == 0 {
				w = 64
			}
			if h == 0 {
				h = w
			}
			kit.Thumb(c, p, w, h, 8)
		}
	case "divider":
		ui.Box(c).Height(kit.Hair).Background(kit.Line)
	case "progress":
		ui.Progress(c, n.Progress)
	case "input":
		val := v.input(key, n.Value)
		in := ui.TextInput(c, val).Key(key).Placeholder(n.Placeholder).FontSize(kit.FSmall)
		if in.Submitted() && n.ID != "" {
			v.onEvent(n.ID, *val)
		}
	case "select":
		val := v.input(key, n.Value)
		before := *val
		ui.Select(c, val, n.Options).Key(key)
		if *val != before && n.ID != "" {
			v.onEvent(n.ID, *val)
		}
	default:
		if n.Text != "" {
			ui.Text(c, n.Text).FontSize(kit.FSmall).TextColor(kit.Ink2).Wrap()
		}
	}
}

func (v *view) children(c *ui.Context, n Node, path string) {
	for i, ch := range n.Children {
		v.render(c, ch, fmt.Sprintf("%s/%d", path, i))
	}
}
