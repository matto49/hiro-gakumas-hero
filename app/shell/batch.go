package shell

import (
	"fmt"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/labels"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 一条回复里的卡片收拢（11-25 review）：
//   - 禁止同时出现两张确认卡：agent 一轮发起的多条待确认合成一张，卡里逐项列出（像 codex 那样），「全部开始」按顺序做；
//   - 同一种信息卡只留最后一张（agent 先查全部、再按名字筛，两次结果都变成卡片贴进来过）。
//
// 只有一个任务能同时跑，待确认 10 分钟就过期：点「全部开始」时确认第 1 项、退掉其余的待确认，
// 前一项正常做完后由程序按同样的参数重新发起下一项并确认（用户已经同意过这一串）；前一项出错 / 被停下，后面的都不做。

// tidyCards 收拢一条回复的卡片。
func tidyCards(cards []plugin.Card) []plugin.Card {
	lastInfo := map[string]int{}
	choice := map[string]bool{}
	for i, c := range cards {
		switch c["type"] {
		case "info":
			k, _ := c["kind"].(string)
			lastInfo[k] = i
		case "choice":
			k, _ := c["kind"].(string)
			choice[k] = true
		}
	}
	var out []plugin.Card
	var merged plugin.Card
	var batch []any
	for i, c := range cards {
		switch c["type"] {
		case "info":
			// 同种信息卡只留最后一张；已经有同种选择卡（卡面都在里面）时信息卡不要
			if k, _ := c["kind"].(string); lastInfo[k] != i || choice[k] {
				continue
			}
		case "confirm":
			from, _ := c["from"].(string)
			pm := pendingMap(c["pending"])
			if from == "" && c["status"] == "waiting" && pm != nil {
				batch = append(batch, pm)
				if merged == nil {
					merged = plugin.Card{"type": "confirm", "status": "waiting", "pending": pm}
					out = append(out, merged)
				}
				continue
			}
		}
		out = append(out, c)
	}
	if len(batch) > 1 {
		merged["batch"] = batch
	}
	return out
}

// pendingMap 把卡里的待确认统一成 map（对话存盘后是 map；刚从命令拿到的可能是结构体）。
func pendingMap(v any) map[string]any {
	switch p := v.(type) {
	case map[string]any:
		return p
	case hiroapi.Pending:
		return p.Map()
	case *hiroapi.Pending:
		if p != nil {
			return p.Map()
		}
	}
	return nil
}

func batchOf(card plugin.Card) []map[string]any {
	raw, _ := card["batch"].([]any)
	out := make([]map[string]any, 0, len(raw))
	for _, x := range raw {
		if m := pendingMap(x); m != nil {
			out = append(out, m)
		}
	}
	return out
}

func setBatch(card plugin.Card, items []map[string]any) {
	raw := make([]any, len(items))
	for i, m := range items {
		raw[i] = m
	}
	card["batch"] = raw
	if len(items) > 0 {
		card["pending"] = items[0]
	}
}

// afterBatchResolved：「全部开始」确认了第 1 项之后，退掉其余待确认、把它们记成排队（等第 1 项做完）；「先不」时一起退掉。
func (a *App) afterBatchResolved(card plugin.Card, action, jobID string) {
	items := batchOf(card)
	if len(items) < 2 {
		return
	}
	rest := items[1:]
	call := a.d.Call
	ids := []string{}
	for _, it := range rest {
		if id, _ := it["id"].(string); id != "" {
			ids = append(ids, id)
		}
	}
	a.d.Run.Go(func() func() {
		for _, id := range ids {
			call(a.ctx, []string{"cancel", id})
		}
		return nil
	})
	if action == "confirm" && jobID != "" {
		q := make([]any, len(rest))
		for i, it := range rest {
			q[i] = it
		}
		card["queue"], card["queueJob"] = q, jobID
	}
}

// batchFirstFailed：合并卡第 1 项就没开起来，退掉其余待确认，在对话里列出没做的几项。
func (a *App) batchFirstFailed(t *Thread, card plugin.Card) {
	items := batchOf(card)
	if len(items) < 2 {
		return
	}
	a.afterBatchResolved(card, "cancel", "")
	rest := make([]any, 0, len(items)-1)
	for _, it := range items[1:] {
		rest = append(rest, it)
	}
	// 带上第 1 项没开起来的原因（页面上只看得到这一句，原因原来只存在卡上看不到，CodeRabbit #138 / #197）
	why := ""
	if msg, _ := card["message"].(string); msg != "" {
		why = "（" + msg + "）"
	}
	a.addTo(t, "agent", fmt.Sprintf("第 1 项没能开始%s，后面 %d 项没做：%s。可以改一下再发起。", why, len(rest), queueNames(rest)), nil)
}

// removeBatchItem 去掉合并确认卡里的一项（退掉它的待确认）。
func (a *App) removeBatchItem(t *Thread, card plugin.Card, i int) {
	items := batchOf(card)
	if i < 0 || i >= len(items) {
		return
	}
	if first, _ := items[0]["id"].(string); a.ui.resolving[first] {
		return // 「全部开始」还没回来：这时去掉会让排队错位
	}
	id, _ := items[i]["id"].(string)
	items = append(items[:i:i], items[i+1:]...)
	setBatch(card, items)
	if len(items) == 0 {
		card["status"] = "cancelled"
	}
	call := a.d.Call
	a.d.Run.Go(func() func() {
		if id != "" {
			call(a.ctx, []string{"cancel", id})
		}
		return func() { a.save(t) }
	})
}

// continueBatch 在任务结束时接着做排队的下一项（jobEnded 调）。
func (a *App) continueBatch(id string, v *plugin.JobView) {
	for _, t := range a.allThreads() {
		for _, m := range t.Messages {
			for _, card := range m.Cards {
				if card["type"] != "confirm" || card["queueJob"] != id {
					continue
				}
				queue, _ := card["queue"].([]any)
				delete(card, "queueJob")
				if len(queue) == 0 {
					return
				}
				if v.State != "done" {
					card["queue"] = []any{}
					name := id
					if v.Job != nil && v.Job.Summary != "" {
						name = labels.Title(v.Job.Summary)
					}
					a.addTo(t, "agent", fmt.Sprintf("「%s」没有做完，后面 %d 项没做：%s", name, len(queue), queueNames(queue)), nil)
					a.save(t)
					return
				}
				next := pendingMap(queue[0])
				card["queue"] = queue[1:]
				a.startQueued(t, m, card, next)
				return
			}
		}
	}
}

func queueNames(queue []any) string {
	s := ""
	for i, x := range queue {
		if i > 0 {
			s += "、"
		}
		sum, _ := pendingMap(x)["summary"].(string)
		s += "「" + sum + "」"
	}
	return s
}

// startQueued 按排队项的参数重新发起并确认（用户在「全部开始」时已经同意过）。
func (a *App) startQueued(t *Thread, m *Message, card plugin.Card, item map[string]any) {
	p, _ := hiroapi.PendingFromMap(item)
	argv := hiroapi.ArgvOf(p.Command, p.Args)
	call := a.d.Call
	auto := ""
	if lv, _ := card["auto"].(string); lv != "" {
		auto = a.permission() // 按当前档位重查（CodeRabbit #79）
	}
	a.ui.starting++ // 排队的下一项正在发起 / 确认：这时不能关游戏 / 关助手（Pending 算上，CodeRabbit #197）
	a.d.Run.Go(func() func() {
		r := call(a.ctx, argv)
		var started hiroapi.Result
		var why string
		var ask *hiroapi.Pending // 按权限自动开始的一串：重新发起后档位不允许了，留给用户确认
		switch {
		case r.Pending != nil && auto != "" && !Allows(auto, r.Pending.Access):
			ask = r.Pending
		case r.Pending != nil:
			started = call(a.ctx, []string{"confirm", r.Pending.ID})
			if !started.OK && started.Error != nil {
				why = started.Error.Message
			}
		case r.Error != nil:
			why = r.Error.Message
		default:
			why = "没拿到待确认"
		}
		return func() {
			a.ui.starting--
			if ask != nil {
				queue, _ := card["queue"].([]any)
				card["queue"] = []any{}
				text := "「" + ask.Summary + "」现在要你确认（重新查过，操作权限不够自动做）"
				if len(queue) > 0 {
					text += fmt.Sprintf("；后面 %d 项先不做：%s", len(queue), queueNames(queue))
				}
				a.addTo(t, "agent", text, []plugin.Card{ConfirmCard(*ask)})
				a.save(t)
				return
			}
			if why != "" {
				queue, _ := card["queue"].([]any)
				card["queue"] = []any{}
				text := fmt.Sprintf("没能开始「%s」：%s", p.Summary, why)
				if len(queue) > 0 {
					text += fmt.Sprintf("。后面 %d 项没做：%s", len(queue), queueNames(queue))
				}
				a.addTo(t, "agent", text, nil)
				a.save(t)
				return
			}
			for _, c := range CardsFor(started) {
				if c["type"] == "run" {
					m.Cards = append(m.Cards, c)
					a.onJobStarted(c)
					if jid, _ := c["jobId"].(string); jid != "" {
						card["queueJob"] = jid
					}
				}
			}
			a.save(t)
		}
	})
}

// batchItems 是合并确认卡里逐项的那几行：序号、标题、副标题、代价，右边「去掉」。
func (a *App) batchItems(c *ui.Context, card plugin.Card, items []map[string]any) {
	first, _ := items[0]["id"].(string)
	busy := a.ui.resolving[first]
	for i, it := range items {
		p, _ := hiroapi.PendingFromMap(it)
		e, ok := a.reg.Explain(a, p)
		if !ok {
			e = plugin.Explained{Title: p.Summary, Cost: "会操作游戏"}
		}
		ui.Row(c).Key(fmt.Sprintf("batch-%s", p.ID)).Gap(10).Padding(8, 0).AlignItems(ui.Center).
			BorderColor(kit.Line).BorderWidth(0, 0, kit.Hair, 0).Children(func() {
			ui.Row(c).Size(22, 22).Radius(11).Background(kit.WarnT).Justify(ui.Center).AlignItems(ui.Center).Shrink(0).Children(func() {
				ui.Text(c, fmt.Sprintf("%d", i+1)).FontSize(12).FontWeight(700).TextColor(kit.WarnInk)
			})
			if e.Art != "" {
				kit.Thumb(c, e.Art, 34, 34, 8)
			}
			ui.Column(c).Gap(2).Shrink(1).Children(func() {
				title := e.Title
				if e.Subtitle != "" {
					title += " · " + e.Subtitle
				}
				ui.Text(c, title).FontSize(kit.FSmall).FontWeight(600).TextColor(kit.Ink).Wrap()
				ui.Text(c, e.Cost).FontSize(11.5).TextColor(kit.Ink3)
			})
			ui.Spacer(c)
			label := fmt.Sprintf("去掉第 %d 项", i+1)
			if ui.Button(c, "去掉").Key("rm-"+p.ID).Label(label).Disabled(busy).FontSize(11.5).TextColor(kit.Ink3).Padding(3, 8).Radius(6).Clicked() && !busy {
				a.removeBatchItem(a.cur, card, i)
			}
		})
	}
}
