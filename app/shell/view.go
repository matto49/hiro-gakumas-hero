package shell

import (
	"github.com/egoist/mygo/ui"
	"github.com/egoist/mygo/ui/markdown"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
)

// viewState 是界面上的临时状态（不存盘）。
type viewState struct {
	panelOpen   bool
	panelPlugin string
	panelRoute  string
	panelFrac   float32 // 面板占窗口宽的比例，0 = 默认 45%
	list        ui.ListState
	md          markdown.Options
	resolving   map[string]bool // 正在确认 / 取消的待确认编号（防重复）
	autoOpened  map[string]bool // 已自动打开过游戏运行现状的任务
	chatPct     float32
}

// SidebarWidth 是左侧栏宽度（§4）。
const SidebarWidth float32 = 232

// View 是整个窗口。
func (a *App) View(c *ui.Context) {
	t := kit.Theme(*c.Theme())
	c.SetTheme(&t)
	if a.d.Gate != nil && a.d.Gate(c, a) {
		return
	}
	if c.Shortcut(ui.Cmd, ui.KeyN) {
		a.NewThread()
	}
	if c.Shortcut(ui.Cmd, ui.KeyBackslash) {
		a.TogglePanel()
	}
	a.hidden.Store(c.WindowHidden())
	w, _ := c.Size()
	ui.Row(c).Fill().Background(kit.Bg).AlignItems(ui.Stretch).Children(func() {
		a.sidebar(c)
		chat := func() {
			ui.Column(c).Fill().Children(func() {
				a.header(c)
				a.pinned(c)
				if a.welcomeMode() {
					a.welcome(c)
					return
				}
				a.transcript(c)
				a.composer(c)
			})
		}
		if !a.ui.panelOpen {
			ui.Column(c).Grow(1).FillHeight().Children(chat)
			return
		}
		main := w - SidebarWidth
		if a.ui.chatPct == 0 {
			a.ui.chatPct = DefaultChatPercent(main)
		}
		a.ui.chatPct = ClampChatPercent(a.ui.chatPct, main)
		ui.SplitPercent(c, &a.ui.chatPct, chat, func() { a.panel(c) }).SplitReset(DefaultChatPercent(main)).Grow(1).FillHeight()
	})
}

func (a *App) header(c *ui.Context) {
	title := "新对话"
	if a.cur != nil {
		title = a.cur.Title
	}
	ui.Row(c).Height(48).Shrink(0).AlignItems(ui.Center).Padding(0, 20).Gap(10).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
		ui.Text(c, title).FontSize(13.5).FontWeight(600).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
		ui.Spacer(c)
	})
}
