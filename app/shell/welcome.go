package shell

import (
	"fmt"
	"strings"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 新对话页：问候、偶像卡面、Vo / Da / Vi 点缀、居中的输入框和几张引导卡。发出第一句后变回普通对话。

func (a *App) welcomeMode() bool {
	return !a.busy && (a.cur == nil || len(a.cur.Messages) == 0)
}

// WelcomeTitle 是新对话页的问候语（制作人口吻）。
const WelcomeTitle = "制作人，今天也来培育吧"

func (a *App) welcome(c *ui.Context) {
	starters := a.reg.Starters(a)
	greet := a.reg.Greeting(a)
	art := ""
	for _, s := range starters {
		if s.Art != "" {
			art = s.Art
			break
		}
	}
	ui.Scroll(c).Grow(1).Children(func() {
		ui.Column(c).Fill().MinHeight(560).Justify(ui.Center).AlignItems(ui.Center).Padding(32, a.chatPad()).Gap(18).Children(func() {
			hero(c, art)
			ui.Column(c).Gap(6).AlignItems(ui.Center).Children(func() {
				ui.Text(c, WelcomeTitle).FontSize(22).FontWeight(700).TextColor(kit.Ink)
				sub := "想打哪一局、查什么卡，直接说就行"
				if greet != "" {
					sub = greet
				}
				ui.Text(c, sub).FontSize(kit.FSmall).TextColor(kit.Ink3)
			})
			ui.Column(c).Width(680).Shrink(1).Gap(10).Children(func() {
				if strings.HasPrefix(a.input, "/") {
					a.palette(c)
				}
				a.inputBox(c)
			})
			if len(starters) > 0 {
				ui.Row(c).Width(680).Shrink(1).Gap(10).Wrap().Children(func() {
					for i, s := range starters {
						a.starterCard(c, i, s)
					}
				})
			}
			ui.Text(c, "输入「/」看看插件能做什么 · ⌘N 新对话").FontSize(kit.FNote).TextColor(kit.Ink3)
		})
	})
}

// hero 是标题上方那块：偶像卡面（圆角、橙色细边），没有卡面时是初星助手的标志（探头的广）；下面一条 Vo / Da / Vi 三色。
func hero(c *ui.Context, art string) {
	ui.Column(c).Gap(10).AlignItems(ui.Center).Children(func() {
		ui.Box(c).Padding(3).Radius(26).Background(kit.CardBg).Border(1.5, kit.Brand).Shadow(0, 8, 24, 0, ui.RGBA(238, 106, 31, 0.18)).Children(func() {
			if b := kit.Bitmap(art); b != nil {
				ui.Image(c, b).Size(84, 84).Fit(ui.Cover).Radius(22)
				return
			}
			kit.Logo(c, 84)
		})
		ui.Row(c).Gap(4).Children(func() {
			for _, col := range []ui.Color{kit.Vo, kit.Da, kit.Vi} {
				ui.Box(c).Size(22, 4).Radius(2).Background(col)
			}
		})
	})
}

func (a *App) starterCard(c *ui.Context, i int, s plugin.Starter) {
	b := ui.ButtonBase(c).Key(fmt.Sprintf("starter-%d", i)).Label(s.Label).Justify(ui.Start).AlignItems(ui.Center).Gap(10).
		Width(335).Padding(10, 12).Radius(kit.R16).Background(kit.CardBg).Border(kit.Hair, kit.LineStr)
	if b.Hovered() {
		b = b.Border(1, kit.Brand).Background(ui.Hex("#FFF8F1"))
	}
	if b.Children(func() {
		switch {
		case s.Art != "":
			kit.Thumb(c, s.Art, 40, 40, 10)
		case s.Icon != nil:
			ui.Column(c).Size(40, 40).Radius(10).Background(kit.BrandT).Center().Shrink(0).Children(func() {
				ui.Icon(c, s.Icon).FontSize(18).TextColor(kit.Brand)
			})
		default:
			ui.Column(c).Size(40, 40).Radius(10).Background(kit.BrandT).Shrink(0)
		}
		ui.Column(c).Gap(3).Shrink(1).Children(func() {
			ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
				if s.Attr != "" {
					ui.Box(c).Size(7, 7).Radius(4).Background(kit.AttrColor(s.Attr))
				}
				ui.Text(c, s.Label).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink).MaxLines(1).Ellipsis("…")
			})
			if s.Sub != "" {
				ui.Text(c, s.Sub).FontSize(kit.FNote).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…")
			}
		})
	}).Clicked() {
		switch {
		case s.Run != nil:
			s.Run(a)
		case strings.HasPrefix(s.Say, "/"):
			a.submit(s.Say)
		default:
			a.Send(s.Say)
		}
	}
}
