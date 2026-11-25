package shell

import (
	"fmt"
	"sort"
	"strings"

	"github.com/egoist/mygo/ui"
	"github.com/egoist/mygo/ui/markdown"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func (a *App) transcript(c *ui.Context) {
	var msgs []*Message
	if a.cur != nil {
		msgs = a.cur.Messages
	}
	n := len(msgs)
	a.ui.list.Key = func(i int) any {
		if i < len(msgs) {
			return msgs[i].ID
		}
		return "busy"
	}
	a.ui.list.FollowEnd = true
	rows := n
	if a.busy {
		rows++
	}
	ui.List(c, &a.ui.list, rows, func(i int) {
		if i < n {
			a.message(c, msgs[i])
			return
		}
		a.thinking(c)
	}).Grow(1).Padding(20, a.chatPad()).Gap(14).Children(func() {
		if rows == 0 {
			ui.Column(c).Margin(ui.Auto).Gap(8).AlignItems(ui.Center).Children(func() {
				ui.Text(c, "想做什么？").FontSize(18).FontWeight(600).TextColor(kit.Ink)
				ui.Text(c, "比如「帮我用月村手毬打一把初」，或者输入「/」看看插件能做什么。").FontSize(kit.FSmall).TextColor(kit.Ink3)
			})
		}
	})
}

func (a *App) chatPad() float32 {
	if a.ui.panelOpen {
		return 24
	}
	return 40
}

func (a *App) thinking(c *ui.Context) {
	ui.Column(c).Gap(6).Children(func() {
		for _, l := range a.tools {
			toolLine(c, l)
		}
		ui.Text(c, "初星助手在想…").FontSize(kit.FSmall).TextColor(kit.Ink3)
	})
}

func toolLine(c *ui.Context, line string) {
	ui.Row(c).AlignItems(ui.Center).Gap(7).Children(func() {
		ui.Icon(c, kit.IcCheck).FontSize(13).TextColor(kit.Ink3)
		name, rest, _ := strings.Cut(line, " · ")
		ui.Text(c, labels.Tool(name)).FontSize(kit.FNote).TextColor(kit.Ink3)
		if rest != "" {
			ui.Text(c, rest).FontSize(kit.FNote).TextColor(kit.Ink3).MaxLines(1).Ellipsis("…")
		}
	})
}

func (a *App) message(c *ui.Context, m *Message) {
	if m.Role == "user" {
		ui.Row(c).Children(func() {
			ui.Spacer(c)
			ui.Text(c, m.Text).Selectable().FontSize(kit.FBody).TextColor(kit.Ink).Padding(9, 14).Radius(kit.R16 - 2).Background(kit.BrandT).MaxWidth(520).Wrap()
		})
		return
	}
	ui.Column(c).Gap(10).MaxWidth(760).Children(func() {
		for _, l := range m.Tools {
			toolLine(c, l)
		}
		if strings.TrimSpace(m.Text) != "" {
			markdown.View(c, m.Text, &a.ui.md).FontSize(kit.FBody).TextColor(kit.Ink)
		}
		for i, card := range m.Cards {
			ui.Column(c).Key(fmt.Sprintf("%s-card-%d", m.ID, i)).Children(func() { a.card(c, m, card) })
		}
	})
}

func (a *App) card(c *ui.Context, m *Message, card plugin.Card) {
	typ, _ := card["type"].(string)
	switch typ {
	case "confirm":
		if a.waiting(card) {
			a.confirmCard(c, m, card)
		} else {
			a.confirmLine(c, card)
		}
		return
	case "error":
		errorCard(c, card)
		return
	case "retry":
		a.retryCard(c, m, card)
		return
	}
	if _, v := a.reg.CardView(typ); v != nil {
		v(c, a, card)
		return
	}
	genericCard(c, card)
}

// confirmLine 是对话里确认卡留下的一行：等确认时指向下面的确认卡，之后写结果。
func (a *App) confirmLine(c *ui.Context, card plugin.Card) {
	status, _ := card["status"].(string)
	if status == "waiting" {
		status = "expired" // 不在等了还是 waiting，就是过期了
	}
	p, _ := card["pending"].(map[string]any)
	summary, _ := p["summary"].(string)
	text, col := "", kit.Ink3
	switch status {
	case "confirmed":
		text = "已开始「" + summary + "」· 你点了「开始」"
		if n := len(batchOf(card)); n > 1 {
			text = fmt.Sprintf("已开始「%s」等 %d 项 · 你点了「全部开始」", summary, n)
		}
		if lv, _ := card["auto"].(string); lv != "" {
			text = "已自动开始「" + summary + "」· 操作权限是「" + PermName[lv] + "」，不用确认"
			if n := len(batchOf(card)); n > 1 {
				text = fmt.Sprintf("已自动开始「%s」等 %d 项 · 操作权限是「%s」，不用确认", summary, n, PermName[lv])
			}
		}
	case "cancelled":
		text = "已取消「" + summary + "」"
	case "expired":
		text = "「" + summary + "」的确认过期了，再说一次就行"
	case "failed":
		msg, _ := card["message"].(string)
		text, col = "没能开始「"+summary+"」："+msg, kit.Bad
	default:
		text = summary
	}
	ui.Row(c).AlignItems(ui.Center).Gap(7).Children(func() {
		ui.Icon(c, kit.IcShield).FontSize(13).TextColor(col)
		ui.Text(c, text).FontSize(kit.FNote).TextColor(col).Wrap()
	})
}

// retryCard 是任务出错后的「再试一次」：按原参数重新发起（照样走权限档 / 确认卡）；点过一次就变成一行字。
func (a *App) retryCard(c *ui.Context, m *Message, card plugin.Card) {
	summary, _ := card["summary"].(string)
	var args []string
	switch x := card["args"].(type) {
	case []string:
		args = x
	case []any: // 存盘读回来是 []any
		for _, v := range x {
			if s, ok := v.(string); ok {
				args = append(args, s)
			}
		}
	}
	if used, _ := card["used"].(bool); used || len(args) == 0 {
		ui.Text(c, "已重新发起「"+summary+"」").FontSize(kit.FNote).TextColor(kit.Ink3)
		return
	}
	if kit.Secondary(c, "再试一次").Key("retry-" + m.ID).Label("再试一次").Clicked() {
		card["used"] = true
		if a.cur != nil {
			a.save(a.cur)
		}
		a.RunArgs("再试一次「"+summary+"」", args)
	}
}

func errorCard(c *ui.Context, card plugin.Card) {
	e, _ := card["error"].(map[string]any)
	msg, _ := e["message"].(string)
	hint, _ := e["hint"].(string)
	ui.Row(c).Gap(10).Padding(10, 12).Radius(kit.R12).Background(kit.BadT).MaxWidth(560).Children(func() {
		ui.Icon(c, kit.IcAlert).FontSize(15).TextColor(kit.Bad)
		ui.Column(c).Gap(3).Shrink(1).Children(func() {
			ui.Text(c, msg).FontSize(kit.FSmall).TextColor(kit.Ink).Wrap().Selectable()
			if hint != "" {
				ui.Text(c, hint).FontSize(kit.FNote).TextColor(kit.Ink2).Wrap()
			}
		})
	})
}

// genericCard 是没人认领的卡：键值列出来。
func genericCard(c *ui.Context, card plugin.Card) {
	keys := make([]string, 0, len(card))
	for k := range card {
		if k != "type" {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	kit.Card(c).MaxWidth(560).Children(func() {
		for _, k := range keys {
			kit.KV(c, k, fmt.Sprint(card[k]))
		}
	})
}
