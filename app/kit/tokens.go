// Package kit 是初星助手原生界面的设计 token 和公用件（设计稿 §4，画板 B「初星舞台」）。
package kit

import "github.com/egoist/mygo/ui"

// 颜色（§4 的表）。
var (
	Bg       = ui.Hex("#FFFBF6") // 主区底（奶油白）
	CardBg   = ui.Hex("#FFFFFF") // 卡片底
	Side     = ui.Hex("#FBF0E3") // 侧栏、侧边面板底
	Surface2 = ui.Hex("#FFF4E8") // 代码块、选中行
	Line     = ui.Hex("#EFDFCB")
	LineStr  = ui.Hex("#E2C9AA")
	Ink      = ui.Hex("#2A1C10")
	Ink2     = ui.Hex("#6E5846")
	Ink3     = ui.Hex("#A08B78")
	Primary_ = ui.Hex("#EE6A1F") // 主按钮底
	OnPrim   = ui.Hex("#FFFFFF")
	Brand    = ui.Hex("#EE6A1F") // 运行中、「现在」、算法
	BrandT   = ui.Hex("#FFE8D6") // 也是用户气泡底
	Warn     = ui.Hex("#EE6A1F") // 确认卡描边
	WarnT    = ui.Hex("#FFF1E6")
	WarnInk  = ui.Hex("#9A3F0B")
	Good     = ui.Hex("#2E9E5B")
	GoodT    = ui.RGBA(46, 158, 91, 0.12) // 插件来源标签的浅底
	Bad      = ui.Hex("#D23B3B")
	BadT     = ui.Hex("#FDECEC")
	Blue     = ui.Hex("#3C82F0")
	BlueT    = ui.RGBA(60, 130, 240, 0.12)
	Vo       = ui.Hex("#F0508C")
	Da       = ui.Hex("#3C82F0")
	Vi       = ui.Hex("#E8B400")
)

// 圆角。
const (
	R8  float32 = 8
	R12 float32 = 12
	R16 float32 = 16
	R20 float32 = 20
)

// 字号。
const (
	FBody  float32 = 14
	FSmall float32 = 13
	FNote  float32 = 12
	FTag   float32 = 11
)

// 确认卡头的渐变。
var (
	ConfirmHeadFrom = ui.Hex("#FFE3CC")
	ConfirmHeadTo   = ui.Hex("#FFF6EE")
)

// Hair 是 0.5 px 的线宽。
const Hair float32 = 0.5

// Mono 是等宽字体族（系统等宽）。
const Mono = "monospace"

// Theme 把 mygo 的主题换成初星助手的颜色（控件默认色跟着走）。
func Theme(t ui.Theme) ui.Theme {
	t.Background, t.Surface, t.Border = Bg, Surface2, Line
	t.SurfaceHover, t.SurfacePressed = ui.Hex("#FBEBDA"), ui.Hex("#F6E0C8")
	t.Text, t.TextMuted = Ink, Ink3
	t.Accent, t.AccentHover, t.AccentPressed, t.AccentText = Primary_, ui.Hex("#E05E14"), ui.Hex("#C9520F"), OnPrim
	t.Selection = ui.RGBA(238, 106, 31, 0.22)
	t.Danger, t.Warning, t.Success = Bad, Warn, Good
	return t
}
