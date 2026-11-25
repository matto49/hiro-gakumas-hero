package shell

import (
	"strings"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func (a *App) composer(c *ui.Context) {
	pad := a.chatPad()
	ui.Column(c).Shrink(0).Padding(0, pad, 18, pad).Gap(10).Children(func() {
		if strings.HasPrefix(a.input, "/") {
			a.palette(c)
		} else {
			a.chips(c)
		}
		a.inputBox(c)
	})
}

// inputBox 是输入框那一行（底部和新对话页共用）。
func (a *App) inputBox(c *ui.Context) {
	{
		ui.Row(c).AlignItems(ui.End).Gap(10).Padding(10, 10, 10, 16).Radius(kit.R20).Background(kit.CardBg).Border(kit.Hair, kit.LineStr).
			Shadow(0, 4, 18, 0, ui.RGBA(0, 0, 0, 0.04)).Children(func() {
			ph := "说点什么，或者输入「/」用插件"
			if _, w := a.latestWaiting(); w != nil {
				ph = "回复，或者直接点上面的「开始」"
			}
			if !a.hasModel() {
				ph = "先到设置里配一个模型，才能直接说话；现在可以用「/」命令"
			}
			ui.TextAreaBase(c, &a.input).Lines(1, 6).AutoFocus().Placeholder(ph).FontSize(kit.FBody).TextColor(kit.Ink).Grow(1).Padding(5, 0).
				Label("输入框").OnTextKey(func(k ui.TextKey) bool {
				if (k.Key == ui.KeyEnter || k.Key == ui.KeyEscape) && k.Mods == 0 && strings.TrimSpace(k.Text()) == "" {
					return a.keyOnEmptyInput(k.Key)
				}
				if k.Key == ui.KeyEnter && k.Mods == 0 {
					text := k.Text()
					k.SetText("")
					a.submit(text)
					return true
				}
				return false
			})
			send := ui.PrimaryButton(c, "").Label("发送").Size(30, 30).Padding(0).Radius(15).Disabled(a.busy || strings.TrimSpace(a.input) == "").
				Children(func() { ui.Icon(c, kit.IcArrowUp).FontSize(15) })
			if send.Clicked() {
				text := a.input
				a.input = ""
				a.submit(text)
			}
		})
	}
}

// submit 处理输入框交上来的一句：「/命令」走插件，别的发给 agent。
func (a *App) submit(text string) {
	text = strings.TrimSpace(text)
	a.input = ""
	if strings.HasPrefix(text, "/") {
		name := strings.TrimPrefix(text, "/")
		for _, r := range a.reg.Commands(name) {
			if r.Command.Name == name {
				a.runCommand(r)
				return
			}
		}
	}
	a.Send(text)
}

func (a *App) runCommand(r plugin.CommandRef) {
	a.input = ""
	if r.Command.Run != nil {
		r.Command.Run(a)
		return
	}
	a.OpenPanel(r.Plugin.ID, r.Command.Route)
}

func (a *App) palette(c *ui.Context) {
	cmds := a.reg.Commands(strings.TrimSpace(a.input))
	ui.Column(c).Gap(2).Padding(6).Radius(kit.R12).Background(kit.CardBg).Border(kit.Hair, kit.Line).Shadow(0, 6, 20, 0, ui.RGBA(0, 0, 0, 0.06)).Children(func() {
		if len(cmds) == 0 {
			ui.Text(c, "没有这个命令").FontSize(kit.FNote).TextColor(kit.Ink3).Padding(6, 8)
			return
		}
		for _, r := range cmds {
			b := kit.RowButton(c, "cmd-"+r.Plugin.ID+"-"+r.Command.Name, "/"+r.Command.Name, false).Padding(7, 10).Children(func() {
				ui.Text(c, "/"+r.Command.Name).FontSize(kit.FSmall).TextColor(kit.Ink).SingleLine()
				if r.Command.Hint != "" {
					ui.Text(c, r.Command.Hint).FontSize(kit.FNote).TextColor(kit.Ink3).SingleLine()
				}
				ui.Spacer(c)
				ui.Text(c, r.Plugin.Name).FontSize(11).TextColor(kit.Ink3).SingleLine()
			})
			if b.Clicked() {
				a.runCommand(r)
			}
		}
	})
}

func (a *App) chips(c *ui.Context) {
	chips := a.reg.Chips()
	if len(chips) == 0 {
		return
	}
	ui.Row(c).Gap(6).Wrap().Children(func() {
		for _, ch := range chips {
			b := ui.Button(c, ch.Label).Key("chip-"+ch.Label).FontSize(kit.FNote).TextColor(kit.Ink2).Padding(4, 10).Radius(99).Border(kit.Hair, kit.Line).Background(kit.CardBg)
			if b.Clicked() {
				switch {
				case ch.Run != nil:
					ch.Run(a)
				case strings.HasPrefix(ch.Say, "/"):
					a.submit(ch.Say)
				default:
					a.Send(ch.Say)
				}
			}
		}
	})
}
