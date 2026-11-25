package kit

import (
	"os"
	"sync"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/labels"
)

// Primary 是近黑实心主按钮（每屏一个）。
func Primary(c *ui.Context, label string) ui.Element {
	return ui.PrimaryButton(c, label).FontSize(FSmall).FontWeight(600).Padding(7, 14).Radius(9)
}

// Secondary 是描边按钮。
func Secondary(c *ui.Context, label string) ui.Element {
	return ui.Button(c, label).FontSize(FSmall).Padding(7, 12).Radius(9).Background(CardBg).Border(Hair, LineStr).TextColor(Ink)
}

// Ghost 是没底没框的小按钮（面板头、行内操作）。
func Ghost(c *ui.Context, label string) ui.Element {
	b := ui.ButtonBase(c).Label(label).Padding(4, 6).Radius(R8).AlignSelf(ui.Start)
	if b.Hovered() {
		b = b.Background(Surface2)
	}
	return b.Children(func() { ui.Text(c, label).FontSize(FNote).FontWeight(600).TextColor(WarnInk).SingleLine() })
}

// Tag 是一个小标签。
func Tag(c *ui.Context, text string, fg, bg ui.Color) ui.Element {
	return ui.Text(c, text).FontSize(10.5).FontWeight(600).TextColor(fg).Padding(1, 6).Radius(5).Background(bg).MaxLines(1)
}

// SourceTag 是每一步「谁定的」：规则灰、算法橙、LLM 蓝、兜底红、插件绿。
func SourceTag(c *ui.Context, source string) ui.Element {
	l := labels.Source(source)
	if labels.IsPluginSource(source) {
		return Tag(c, l, Good, GoodT)
	}
	switch l {
	case "算法":
		return Tag(c, l, Brand, BrandT)
	case "LLM":
		return Tag(c, l, Blue, BlueT)
	case "兜底":
		return Tag(c, l, Bad, BadT)
	}
	return Tag(c, l, Ink3, Surface2)
}

// AttrColor 是属性的色点颜色（Vo / Da / Vi / vocal…）。
func AttrColor(attr string) ui.Color {
	switch labels.Attr(attr) {
	case "声乐":
		return Vo
	case "舞蹈":
		return Da
	case "视觉":
		return Vi
	}
	return Ink3
}

// AttrTag 是「● 声乐」这样的属性标签。
func AttrTag(c *ui.Context, attr string) {
	name := labels.Attr(attr)
	if name == "" {
		return
	}
	ui.Row(c).AlignItems(ui.Center).Gap(5).Padding(2, 8).Radius(99).Border(Hair, Line).Children(func() {
		ui.Box(c).Size(7, 7).Radius(4).Background(AttrColor(attr))
		ui.Text(c, name).FontSize(11.5).TextColor(Ink2)
	})
}

// StatePill 是任务状态：运行中橙点，出错红，其余灰。
func StatePill(c *ui.Context, state string) { StatePillOf(c, "", state) }

// StatePillOf 同 StatePill，文字按任务种类（扫描等在跑时写「进行中」）。
func StatePillOf(c *ui.Context, kind, state string) {
	fg := Ink2
	switch state {
	case "running", "takeover":
		fg = Brand
	case "failed", "lost":
		fg = Bad
	case "done":
		fg = Good
	}
	ui.Row(c).AlignItems(ui.Center).Gap(5).Children(func() {
		ui.Box(c).Size(7, 7).Radius(4).Background(fg)
		ui.Text(c, labels.StateOf(kind, state)).FontSize(FTag).TextColor(fg)
	})
}

// KV 是「键 值」一行（确认卡、设置）。
func KV(c *ui.Context, k, v string) {
	ui.Row(c).Gap(12).Children(func() {
		ui.Text(c, k).FontSize(12.5).TextColor(Ink3).Width(44).Shrink(0)
		ui.Text(c, v).FontSize(FSmall).TextColor(Ink).Wrap()
	})
}

// Code 是一行等宽命令。
func Code(c *ui.Context, s string) ui.Element {
	return ui.Text(c, s).Font(Mono).FontSize(FNote).TextColor(Ink2).Padding(8, 10).Radius(R8).Background(Surface2).Wrap().Selectable()
}

// Card 是白底细线卡片的外框。
func Card(c *ui.Context) ui.Element {
	return ui.Column(c).Gap(8).Padding(12).Radius(R12).Background(CardBg).Border(Hair, Line)
}

// Section 是面板里的小节标题。
func Section(c *ui.Context, title string) ui.Element {
	return ui.Text(c, title).FontSize(11.5).FontWeight(550).TextColor(Ink3).Padding(6, 0, 4, 0)
}

// Note 是灰色说明文字。
func Note(c *ui.Context, s string) ui.Element {
	return ui.Text(c, s).FontSize(FNote).TextColor(Ink3).Wrap()
}

// MaxBitmaps 是位图缓存最多留几张（超了丢最久没用的）：长局每一步一张截图，不能一直攒。
const MaxBitmaps = 96

var (
	bmMu    sync.Mutex
	bmMap   = map[string]*ui.Bitmap{}
	bmOrder []string // 最近用过的在最后
)

func bitmapCount() int {
	bmMu.Lock()
	defer bmMu.Unlock()
	return len(bmMap)
}

func touch(key string) {
	for i, k := range bmOrder {
		if k == key {
			bmOrder = append(bmOrder[:i], bmOrder[i+1:]...)
			break
		}
	}
	bmOrder = append(bmOrder, key)
	for len(bmOrder) > MaxBitmaps {
		delete(bmMap, bmOrder[0])
		bmOrder = bmOrder[1:]
	}
}

// Bitmap 读一张本机图（png / jpg / webp），按路径和修改时间缓存；读不到返回 nil。
func Bitmap(path string) *ui.Bitmap {
	if path == "" {
		return nil
	}
	st, err := os.Stat(path)
	if err != nil {
		return nil
	}
	key := path + "@" + st.ModTime().String()
	bmMu.Lock()
	defer bmMu.Unlock()
	if b, ok := bmMap[key]; ok {
		touch(key)
		return b
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	b, err := ui.DecodeBitmap(data)
	if err != nil {
		return nil
	}
	bmMap[key] = b
	touch(key)
	return b
}

// Thumb 是圆角小图；没有图时是浅灰占位，不留空白。
func Thumb(c *ui.Context, path string, w, h, r float32) {
	if b := Bitmap(path); b != nil {
		ui.Image(c, b).Size(w, h).Fit(ui.Cover).Radius(r).Shrink(0)
		return
	}
	ui.Box(c).Size(w, h).Radius(r).Background(Surface2).Shrink(0)
}

// IconButton 是方形图标按钮（暂停、停止、关面板）。Label 给测试和读屏用。
func IconButton(c *ui.Context, ic *ui.SVG, label string) ui.Element {
	return ui.Button(c, "").Label(label).Tooltip(label).Size(28, 28).Padding(0).Radius(R8).Background(CardBg).Border(Hair, Line).TextColor(Ink2).
		Children(func() { ui.Icon(c, ic).FontSize(13) })
}

// RowButton 是没外框的整行按钮（侧栏、命令列表）：左对齐，选中或悬停时浅底。label 给测试和读屏。
func RowButton(c *ui.Context, key, label string, on bool) ui.Element {
	b := ui.ButtonBase(c).Key(key).Label(label).Justify(ui.Start).AlignItems(ui.Center).Gap(9).Padding(7, 8).Radius(R8)
	switch {
	case on:
		b = b.Background(Surface2)
	case b.Hovered():
		b = b.Background(ui.RGBA(120, 70, 20, 0.06))
	}
	return b
}
