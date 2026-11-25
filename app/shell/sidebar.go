package shell

import (
	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func (a *App) sidebar(c *ui.Context) {
	ui.Column(c).Width(SidebarWidth).Shrink(0).FillHeight().Background(kit.Side).BorderColor(kit.Line).BorderWidth(0, kit.Hair, 0, 0).Padding(14, 12).Gap(2).Children(func() {
		ui.Row(c).AlignItems(ui.Center).Gap(8).Padding(4, 6, 12, 6).Children(func() {
			kit.Logo(c, 24)
			ui.Text(c, "初星助手").FontSize(14.5).FontWeight(650).TextColor(kit.Ink)
		})
		if kit.RowButton(c, "new-thread", "新对话", false).Padding(8, 10).Radius(10).Background(kit.CardBg).Border(kit.Hair, kit.Line).TextColor(kit.Ink).Children(func() {
			ui.Icon(c, kit.IcPlus).FontSize(15).TextColor(kit.Ink2)
			ui.Text(c, "新对话").FontSize(kit.FSmall).SingleLine()
			ui.Spacer(c)
			ui.Text(c, "⌘N").FontSize(11).TextColor(kit.Ink3)
		}).Clicked() {
			a.NewThread()
		}
		sideHead(c, "对话")
		ui.Scroll(c).Grow(1).Shrink(1).Gap(2).Children(func() {
			for _, t := range a.threads {
				if len(t.Messages) == 0 && t != a.cur {
					continue
				}
				a.threadItem(c, t)
			}
		})
		ui.Column(c).Shrink(0).Gap(2).Children(func() {
			sideHead(c, "插件")
			for _, p := range a.reg.All() {
				for _, n := range p.Navs() {
					a.navItem(c, p, n)
				}
			}
		})
		ui.Column(c).Shrink(0).Gap(6).Padding(10, 8, 2, 8).BorderColor(kit.Line).BorderWidth(kit.Hair, 0, 0, 0).Children(func() {
			for _, p := range a.reg.All() {
				if p.Footer != nil {
					p.Footer(c, a)
				}
			}
		})
	})
}

func sideHead(c *ui.Context, label string) {
	ui.Text(c, label).FontSize(11).FontWeight(550).TextColor(kit.Ink3).Padding(14, 8, 4, 8)
}

func (a *App) threadItem(c *ui.Context, t *Thread) {
	on := t == a.cur
	fg := kit.Ink2
	if on {
		fg = kit.Ink
	}
	b := kit.RowButton(c, "thread-"+t.ID, t.Title, on).Children(func() {
		ui.Text(c, t.Title).FontSize(kit.FSmall).TextColor(fg).MaxLines(1).Ellipsis("…")
		ui.Spacer(c)
	})
	if b.Clicked() {
		a.Select(t.ID)
	}
}

func (a *App) navItem(c *ui.Context, p *plugin.Plugin, n plugin.NavItem) {
	on := a.ui.panelOpen && a.ui.panelPlugin == p.ID && a.ui.panelRoute == n.Route
	b := kit.RowButton(c, "nav-"+p.ID+"-"+n.Route, n.Label, on).TextColor(kit.Ink2).Children(func() {
		if n.Icon != nil {
			ui.Icon(c, n.Icon).FontSize(15)
		}
		ui.Text(c, n.Label).FontSize(kit.FSmall).SingleLine()
		ui.Spacer(c)
		if p.ID == "cockpit" && a.jobLive() {
			ui.Box(c).Size(6, 6).Radius(3).Background(kit.Brand)
			ui.Text(c, "运行中").FontSize(11).TextColor(kit.Brand)
		}
	})
	if b.Clicked() {
		if on {
			a.ClosePanel()
		} else {
			a.OpenPanel(p.ID, n.Route)
		}
	}
}
