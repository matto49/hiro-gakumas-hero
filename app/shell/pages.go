package shell

import (
	"fmt"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 页面模式（11-26 用户定）：缺省打开就是它。左边导航点各页、按按钮做事，不用配对话模型；
// 侧栏最下面「问 AI」进原来的对话界面（原样保留）。
//
// 页面上按「发起」走的还是同一套：工具层返回待确认 → 确认卡（这里画在弹出的确认框里）→ resolve；
// 权限档允许的照旧自动开始。这些记录挂在一条不显示、不存盘的「页面记录」（a.pages）上，
// 所以自动开始、全部开始、出错重试、任务结束通知都和对话里一样，只是消息变成右下角的提示条。

// PageMaxWidth 是页面主区内容最宽多少（再宽表单就散了）。
const PageMaxWidth float32 = 880

// PageWideMaxWidth 是插件给了宽版 Page 时的最宽（游戏运行现状左右分栏）。
const PageWideMaxWidth float32 = 1180

// Mode 是现在的界面模式（ModePages / ModeChat）。
func (a *App) Mode() string { return a.mode }

// SetMode 切界面：页面 ↔ 问 AI。任务、确认、设置都不受影响。
func (a *App) SetMode(m string) {
	if m != ModeChat {
		m = ModePages
	}
	a.mode = m
}

// actionThread 是「发起」这类动作的记录写到哪：页面模式写页面记录，对话模式写当前对话。
func (a *App) actionThread() *Thread {
	if a.mode != ModeChat {
		return a.pages
	}
	return a.ensureThread()
}

// allThreads 是全部对话加上页面记录（找对局卡、排队项时用）。
func (a *App) allThreads() []*Thread {
	if a.pages == nil {
		return a.threads
	}
	return append(append(make([]*Thread, 0, len(a.threads)+1), a.threads...), a.pages)
}

// threadOf 是消息所在的记录；找不到时退回当前对话。
func (a *App) threadOf(m *Message) *Thread {
	if m != nil {
		for _, t := range a.allThreads() {
			for _, x := range t.Messages {
				if x == m {
					return t
				}
			}
		}
	}
	return a.cur
}

// toastFor 把一条消息变成右下角的提示条（下一帧画出来）：出错卡写原因，重试卡变成「再试一次」按钮。
func (a *App) toastFor(text string, cards []plugin.Card) {
	t := ui.Toast{Title: strings.TrimSpace(text), Timeout: 10 * time.Second}
	for _, c := range cards {
		switch c["type"] {
		case "error":
			e, _ := c["error"].(map[string]any)
			msg, _ := e["message"].(string)
			hint, _ := e["hint"].(string)
			if msg == "" {
				msg = "出错了"
			}
			if t.Title == "" {
				t.Title = msg
			} else {
				t.Description = msg
			}
			if hint != "" {
				t.Description = strings.TrimSpace(t.Description + " " + hint)
			}
			t.Type = "error"
		case "retry":
			summary, _ := c["summary"].(string)
			args := retryArgs(c)
			if len(args) > 0 {
				t.Action = "再试一次"
				t.OnAction = func() { a.runArgs("再试一次「"+summary+"」", args, nil) }
			}
		case "next":
			// 「下一步」按钮（扫描做完：打一把初…）：提示条上只放一个，点了去问 AI 说这句
			label, _ := c["label"].(string)
			say, _ := c["say"].(string)
			if say != "" && t.Action == "" {
				t.Action = label
				t.OnAction = func() { a.Ask(say) }
			}
		}
	}
	if t.Title == "" {
		return
	}
	if t.Type == "" && strings.Contains(t.Title, "出错") {
		t.Type = "error"
	}
	if t.Action != "" {
		t.Timeout = 20 * time.Second
	}
	a.toasts = append(a.toasts, t)
}

func retryArgs(card plugin.Card) []string {
	var args []string
	switch x := card["args"].(type) {
	case []string:
		args = x
	case []any:
		for _, v := range x {
			if s, ok := v.(string); ok {
				args = append(args, s)
			}
		}
	}
	return args
}

// drainToasts 把排着的提示条交给窗口（两种界面都出）。
func (a *App) drainToasts(c *ui.Context) {
	for _, t := range a.toasts {
		c.AddToast(t)
	}
	a.toasts = nil
}

// pagesView 是页面模式的整个窗口。
func (a *App) pagesView(c *ui.Context) {
	ui.Row(c).Fill().Background(kit.Bg).AlignItems(ui.Stretch).Children(func() {
		a.pagesSidebar(c)
		ui.Column(c).Grow(1).FillHeight().Children(func() {
			a.pageLiveBar(c)
			a.pageBody(c)
		})
	})
	a.pageConfirm(c)
}

func (a *App) pagesSidebar(c *ui.Context) {
	ui.Column(c).Width(SidebarWidth).Shrink(0).FillHeight().Background(kit.Side).BorderColor(kit.Line).BorderWidth(0, kit.Hair, 0, 0).Padding(14, 12).Gap(2).Children(func() {
		ui.Row(c).AlignItems(ui.Center).Gap(8).Padding(4, 6, 12, 6).Children(func() {
			kit.Logo(c, 24)
			ui.Text(c, "初星助手").FontSize(14.5).FontWeight(650).TextColor(kit.Ink)
		})
		ui.Scroll(c).Grow(1).Shrink(1).Gap(2).Children(func() {
			a.pageNav(c, "page-home", kit.IcHome, "首页", a.ui.pagePlugin == "", false, func() { a.ui.pagePlugin, a.ui.pageRoute = "", "" })
			for _, p := range a.reg.All() {
				for _, n := range p.Navs() {
					p, n := p, n
					a.pageNav(c, "page-"+p.ID+"-"+n.Route, n.Icon, n.Label, a.pageOn(p, n), p.ID == "cockpit" && a.jobLive(), func() {
						a.ui.pagePlugin, a.ui.pageRoute = p.ID, n.Route
					})
				}
			}
			ui.Box(c).Height(kit.Hair).Background(kit.Line).Margin(8, 6)
			a.pageNav(c, "page-ai", kit.IcChat, "问 AI", false, false, func() { a.SetMode(ModeChat) })
		})
		ui.Column(c).Shrink(0).Gap(6).Padding(10, 8, 2, 8).BorderColor(kit.Line).BorderWidth(kit.Hair, 0, 0, 0).Children(func() {
			for _, p := range a.reg.All() {
				if p.Footer != nil {
					p.Footer(c, a)
				}
			}
			// 操作权限：对话界面里在输入框左下角，页面模式没有输入框，放这里
			ui.Row(c).Key("page-perm").Gap(4).AlignItems(ui.Center).Children(func() {
				ui.Icon(c, kit.IcShield).FontSize(12).TextColor(kit.Ink3)
				a.permMenu(c)
			})
		})
	})
}

// pageOn：这一项是不是当前页（游戏运行现状点进某一局时 route 是 job:…，仍然亮「游戏运行现状」）。
func (a *App) pageOn(p *plugin.Plugin, n plugin.NavItem) bool {
	if a.ui.pagePlugin != p.ID {
		return false
	}
	if a.ui.pageRoute == n.Route {
		return true
	}
	if n.Route != "" {
		return false
	}
	for _, x := range p.Navs() {
		if x.Route == a.ui.pageRoute {
			return false
		}
	}
	return true
}

func (a *App) pageNav(c *ui.Context, key string, icon *ui.SVG, label string, on, live bool, click func()) {
	b := kit.RowButton(c, key, label, on).TextColor(kit.Ink2).Children(func() {
		if icon != nil {
			ui.Icon(c, icon).FontSize(15)
		}
		ui.Text(c, label).FontSize(kit.FSmall).SingleLine()
		ui.Spacer(c)
		if live {
			ui.Box(c).Size(6, 6).Radius(3).Background(kit.Brand)
			ui.Text(c, "运行中").FontSize(11).TextColor(kit.Brand)
		}
	})
	if b.Clicked() {
		click()
	}
}

// pageLiveBar 是有任务在跑时主区顶上那条（不在游戏运行现状页时）：点它去看这一局。
func (a *App) pageLiveBar(c *ui.Context) {
	if a.live == "" || a.ui.pagePlugin == "cockpit" {
		return
	}
	ui.Column(c).Shrink(0).Padding(10, 24).BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
		ui.Column(c).MaxWidth(PageWideMaxWidth).Children(func() {
			if _, v := a.reg.CardView("run-pinned"); v != nil {
				v(c, a, plugin.Card{"type": "run-pinned", "jobId": a.live})
				return
			}
			if kit.Secondary(c, "正在跑一个任务 · 去看").Key("page-live").Clicked() {
				a.OpenPanel("cockpit", "job:"+a.live)
			}
		})
	})
}

// pageBody 是当前那页：插件有宽版 Page 用 Page，没有用 Panel（和对话界面右侧面板同一份）。
func (a *App) pageBody(c *ui.Context) {
	p := a.reg.ByID(a.ui.pagePlugin)
	if p != nil && p.Page == nil && p.Panel == nil {
		p = nil
	}
	title, icon := "首页", kit.IcHome
	width := PageMaxWidth
	if p != nil {
		title, icon = p.Name, p.Icon
		for _, n := range p.Navs() {
			if a.pageOn(p, n) {
				title = n.Label
				if n.Icon != nil {
					icon = n.Icon
				}
			}
		}
		if p.Page != nil {
			width = PageWideMaxWidth
		}
	}
	ui.Scroll(c).Grow(1).Children(func() {
		ui.Column(c).FillWidth().AlignItems(ui.Center).Padding(20, 28, 32, 28).Children(func() {
			ui.Column(c).FillWidth().MaxWidth(width).Gap(12).Children(func() {
				ui.Row(c).Gap(8).AlignItems(ui.Center).Padding(0, 0, 6, 0).Children(func() {
					if icon != nil {
						ui.Icon(c, icon).FontSize(17).TextColor(kit.Ink2)
					}
					ui.Text(c, title).FontSize(18).FontWeight(700).TextColor(kit.Ink)
				})
				switch {
				case p == nil:
					a.pageHome(c)
				case p.Page != nil:
					p.Page(c, a, a.ui.pageRoute)
				default:
					p.Panel(c, a, a.ui.pageRoute)
				}
			})
		})
	})
}

// HomeAction 是首页「常用」里的一个按钮。
type HomeAction struct {
	Label, Sub string
	Art        string
	Icon       *ui.SVG
	Attr       string
	Run        func(h plugin.Host)
}

// HomeActions 是首页的常用按钮：插件的引导卡里能直接做的（再来一局、领日常…），加上快捷按钮里直接执行的（接着打…），按名字去重。
func (a *App) HomeActions() []HomeAction {
	var out []HomeAction
	seen := map[string]bool{}
	for _, s := range a.reg.Starters(a) {
		run := s.Page
		if run == nil {
			run = s.Run
		}
		if run == nil || seen[s.Label] {
			continue
		}
		seen[s.Label] = true
		out = append(out, HomeAction{Label: s.Label, Sub: s.Sub, Art: s.Art, Icon: s.Icon, Attr: s.Attr, Run: run})
	}
	for _, ch := range a.reg.Chips() {
		if ch.Run == nil || seen[ch.Label] {
			continue
		}
		seen[ch.Label] = true
		out = append(out, HomeAction{Label: ch.Label, Icon: kit.IcPlay, Run: ch.Run})
	}
	return out
}

// pageHome 是首页：问候 + 账号数值、正在跑的那局、常用按钮、各页入口。
func (a *App) pageHome(c *ui.Context) {
	greet := a.reg.Greeting(a)
	ui.Column(c).Gap(4).Children(func() {
		ui.Text(c, WelcomeTitle).FontSize(15).FontWeight(650).TextColor(kit.Ink)
		if greet != "" {
			ui.Text(c, greet).FontSize(kit.FSmall).TextColor(kit.Ink3)
		}
	})
	if a.live != "" {
		kit.Section(c, "正在跑")
		if _, v := a.reg.CardView("run-pinned"); v != nil {
			v(c, a, plugin.Card{"type": "run-pinned", "jobId": a.live})
		}
	}
	acts := a.HomeActions()
	if len(acts) > 0 {
		kit.Section(c, "常用")
		ui.Row(c).Gap(10).Wrap().Children(func() {
			for i, x := range acts {
				a.homeAction(c, i, x)
			}
		})
		kit.Note(c, "会动游戏的事，点了都会先弹出确认框，你点「开始」才做（按设置里的操作权限）。")
	}
	kit.Section(c, "去哪一页")
	ui.Row(c).Gap(8).Wrap().Children(func() {
		for _, p := range a.reg.All() {
			for _, n := range p.Navs() {
				p, n := p, n
				if kit.Secondary(c, n.Label).Key("home-go-" + p.ID + "-" + n.Route).Clicked() {
					a.ui.pagePlugin, a.ui.pageRoute = p.ID, n.Route
				}
			}
		}
		if kit.Secondary(c, "问 AI").Key("home-go-ai").Clicked() {
			a.SetMode(ModeChat)
		}
	})
}

func (a *App) homeAction(c *ui.Context, i int, x HomeAction) {
	b := ui.ButtonBase(c).Key(fmt.Sprintf("home-act-%d", i)).Label(x.Label).Justify(ui.Start).AlignItems(ui.Center).Gap(10).
		Width(270).Padding(10, 12).Radius(kit.R16).Background(kit.CardBg).Border(kit.Hair, kit.LineStr)
	if b.Hovered() {
		b = b.Border(1, kit.Brand).Background(ui.Hex("#FFF8F1"))
	}
	if b.Children(func() {
		switch {
		case x.Art != "":
			kit.Thumb(c, x.Art, 40, 40, 10)
		case x.Icon != nil:
			ui.Column(c).Size(40, 40).Radius(10).Background(kit.BrandT).Center().Shrink(0).Children(func() {
				ui.Icon(c, x.Icon).FontSize(18).TextColor(kit.Brand)
			})
		}
		ui.Column(c).Gap(3).Shrink(1).Children(func() {
			ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
				if x.Attr != "" {
					// 颜色不作唯一区分：色点旁边写属性（CodeRabbit #138）
					ui.Box(c).Size(7, 7).Radius(4).Background(kit.AttrColor(x.Attr))
					ui.Text(c, labels.Attr(x.Attr)).FontSize(11).TextColor(kit.Ink3)
				}
				ui.Text(c, x.Label).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
			})
			if x.Sub != "" {
				ui.Text(c, x.Sub).FontSize(kit.FNote).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…")
			}
		})
	}).Clicked() {
		x.Run(a)
	}
}

// pageWaiting 是页面记录里最新一张在等确认的卡（自动开始了、正在发的不算）。
func (a *App) pageWaiting() (*Message, plugin.Card) {
	if a.pages == nil {
		return nil, nil
	}
	for i := len(a.pages.Messages) - 1; i >= 0; i-- {
		m := a.pages.Messages[i]
		for j := len(m.Cards) - 1; j >= 0; j-- {
			card := m.Cards[j]
			if !a.waiting(card) {
				continue
			}
			if auto, _ := card["auto"].(string); auto != "" {
				continue // 按权限档自动开始了，结果还没回来
			}
			return m, card
		}
	}
	return nil, nil
}

// pageConfirm 弹出确认框：同一张确认卡（对话里那张），点「开始」才动游戏；点外面 / Esc = 先不。
// 不接回车（#138 review：缺省焦点在「先不」上，回车其实是取消，和「↵」提示相反；焦点不在按钮上时回车又会确认，
// 而这里没有对话里「卡出现满 1 秒、不是插件塞来的」两道保护，插件刚塞来的卡会被正在打字的回车确认掉）。
func (a *App) pageConfirm(c *ui.Context) {
	m, card := a.pageWaiting()
	if card == nil {
		return
	}
	p, _ := pendingOf(card)
	open := true
	ui.Modal(c, &open, func() {
		ui.Column(c).Width(600).MaxHeight(720).Children(func() {
			a.ui.inModal = true
			ui.Scroll(c).Children(func() { a.confirmCard(c, m, card) })
			a.ui.inModal = false
		})
	})
	if !open && !a.ui.resolving[p.ID] {
		a.resolve(m, card, "cancel")
	}
	if a.ui.resolving[p.ID] {
		a.cancelStalePages(card)
	}
}

// cancelStalePages：页面上一次只弹最新那张确认卡；它点了「开始」/「先不」后，更早还在等的页面卡（弹框出来前连点了几下）一并取消，
// 不再一张张重新弹出来、同一件事问两遍（#138 review）。自动开始了的不动。
func (a *App) cancelStalePages(keep plugin.Card) {
	if a.pages == nil {
		return
	}
	kp, _ := pendingOf(keep)
	for _, m := range a.pages.Messages {
		for _, card := range m.Cards {
			p, ok := pendingOf(card)
			if !ok || p.ID == kp.ID || !a.waiting(card) || a.ui.resolving[p.ID] {
				continue
			}
			if auto, _ := card["auto"].(string); auto != "" {
				continue
			}
			a.resolve(m, card, "cancel")
		}
	}
}

// backToPages 是对话界面侧栏顶上回页面的那一项。
func (a *App) backToPages(c *ui.Context) {
	if kit.RowButton(c, "back-pages", "回到页面", false).Padding(7, 10).TextColor(kit.Ink2).Children(func() {
		ui.Icon(c, kit.IcBack).FontSize(15)
		ui.Text(c, "回到页面").FontSize(kit.FSmall).SingleLine()
	}).Clicked() {
		a.SetMode(ModePages)
	}
}
