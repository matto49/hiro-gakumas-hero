package shell

import (
	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
)

// MinChatWidth 是中间对话最少留多宽（§4）。
const MinChatWidth float32 = 400

// DefaultPanelWidth 是面板打开时的宽度（用户 11-24：一半太宽了，收窄；11-25 review：再小一点点。仍可拖宽）。
const DefaultPanelWidth float32 = 340

// MinPanelWidth 是面板最窄多少。
const MinPanelWidth float32 = 300

// DefaultChatPercent 是主区宽 mainWidth 时，面板为 DefaultPanelWidth 的对话占比。
func DefaultChatPercent(mainWidth float32) float32 {
	if mainWidth <= 0 {
		return 65
	}
	return ClampChatPercent(100-DefaultPanelWidth/mainWidth*100, mainWidth)
}

// ClampChatPercent 把对话占比夹在：面板最多 70%（对话 ≥30%）、对话至少 400 px、面板至少 300 px。
func ClampChatPercent(p, mainWidth float32) float32 {
	lo := float32(30)
	hi := float32(80)
	if mainWidth > 0 {
		lo = max(lo, MinChatWidth/mainWidth*100)
		hi = 100 - MinPanelWidth/mainWidth*100
	}
	if p > hi {
		p = hi
	}
	if p < lo {
		p = lo
	}
	return p
}

// ClosePanel 关掉侧边面板。
func (a *App) ClosePanel() { a.ui.panelOpen = false }

// TogglePanel 开关侧边面板（⌘\）：关着时打开上次那页，没有上次就打开游戏运行现状或第一个有面板的插件。
func (a *App) TogglePanel() {
	if a.ui.panelOpen {
		a.ClosePanel()
		return
	}
	if a.ui.panelPlugin == "" {
		for _, p := range a.reg.All() {
			if p.Panel != nil {
				a.ui.panelPlugin = p.ID
				break
			}
		}
	}
	if a.ui.panelPlugin != "" {
		a.ui.panelOpen = true
	}
}

func (a *App) panel(c *ui.Context) {
	p := a.reg.ByID(a.ui.panelPlugin)
	if p == nil || p.Panel == nil {
		a.ui.panelOpen = false
		return
	}
	ui.Column(c).Fill().Background(kit.Side).BorderColor(kit.Line).BorderWidth(0, 0, 0, kit.Hair).Children(func() {
		ui.Row(c).Height(48).Shrink(0).AlignItems(ui.Center).Padding(0, 12, 0, 16).Gap(8).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
			if p.Icon != nil {
				ui.Icon(c, p.Icon).FontSize(15).TextColor(kit.Ink2)
			}
			ui.Text(c, p.Name).FontSize(13.5).FontWeight(600).TextColor(kit.Ink)
			ui.Spacer(c)
			if kit.IconButton(c, kit.IcX, "关闭面板").Border(0, kit.Line).Background(ui.RGBA(0, 0, 0, 0)).Clicked() {
				a.ClosePanel()
			}
		})
		ui.Scroll(c).Grow(1).Padding(14, 16).Gap(12).Children(func() {
			p.Panel(c, a, a.ui.panelRoute)
		})
	})
}
