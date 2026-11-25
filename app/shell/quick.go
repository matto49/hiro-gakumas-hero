package shell

import (
	"encoding/json"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// CardsFor 把一条命令的结果做成卡片：待确认 → 确认卡；失败 → 出错卡；开了任务 → 对局卡；有 items → 信息卡。
func CardsFor(r hiroapi.Result) []plugin.Card {
	if !r.OK {
		e := r.Error
		if e == nil {
			e = &hiroapi.CliError{Code: "error", Message: "出错了"}
		}
		return []plugin.Card{{"type": "error", "error": map[string]any{"code": e.Code, "message": e.Message, "hint": e.Hint}}}
	}
	if r.Pending != nil {
		return []plugin.Card{ConfirmCard(*r.Pending)}
	}
	var d map[string]any
	_ = json.Unmarshal(r.Data, &d)
	if job, ok := d["job"].(map[string]any); ok {
		if id, _ := job["id"].(string); id != "" {
			return []plugin.Card{{"type": "run", "jobId": id}}
		}
	}
	if items, ok := d["items"].([]any); ok {
		kind, _ := d["kind"].(string)
		return []plugin.Card{{"type": "info", "title": "", "kind": kind, "items": items, "scannedAt": d["scanned_at"], "hint": d["hint"]}}
	}
	return []plugin.Card{}
}

// ConfirmCard 是一张等确认的卡。
func ConfirmCard(p hiroapi.Pending) plugin.Card {
	return plugin.Card{"type": "confirm", "status": "waiting",
		"pending": p.Map()}
}
