package shell

import (
	"strings"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 操作权限（11-25 review，参考 dsh 的权限范围）：确认卡按用户选的档位决定要不要等用户点。
// 待确认里的 access 是 CLI 算的操作类别：read 只看画面；daily 领日常；blank 写空白编成位；
// write 改游戏（コミュ、竞赛编队）；overwrite 覆盖已有编成；all 用掉培育次数 / 资源（开局、续打、初星課題）。
const (
	PermAsk       = "ask"       // 总是询问
	PermRead      = "read"      // 允许读操作（缺省）：读，加上用户说不用确认的领日常、写空白编成位
	PermReadWrite = "readwrite" // 允许读 / 写操作：除了用掉培育次数的
	PermAll       = "all"       // 允许所有行为
)

// PermName 是档位的中文名。
var PermName = map[string]string{PermAsk: "总是询问", PermRead: "允许读操作", PermReadWrite: "允许读写操作", PermAll: "允许所有行为"}

// Allows 是这一档下这类操作能不能不确认直接开始。没设过档位按「允许读操作」；不知道类别（旧版 CLI）按最高的算。
func Allows(level, access string) bool {
	switch level {
	case PermAll:
		return true
	case PermAsk:
		return false
	case PermReadWrite:
		return access == "read" || access == "daily" || access == "blank" || access == "write" || access == "overwrite"
	default:
		return access == "read" || access == "daily" || access == "blank"
	}
}

func (a *App) permission() string {
	if a.d.Permission == nil {
		return PermRead
	}
	if p := a.d.Permission(); p != "" {
		return p
	}
	return PermRead
}

// autoStart：新加进对话的确认卡，档位允许时直接开始（卡上记下按哪一档自动开始的）。
// 外部插件发起的（from）一律等用户点；合并卡要每一项都允许。
func (a *App) autoStart(m *Message, card plugin.Card) {
	if card["type"] != "confirm" || card["status"] != "waiting" {
		return
	}
	if from, _ := card["from"].(string); from != "" {
		return
	}
	items := batchOf(card)
	if len(items) == 0 {
		if pm := pendingMap(card["pending"]); pm != nil {
			items = []map[string]any{pm}
		}
	}
	if len(items) == 0 {
		return
	}
	level := a.permission()
	for _, it := range items {
		access, _ := it["access"].(string)
		if !Allows(level, access) {
			return
		}
	}
	card["auto"] = level
	a.resolve(m, card, "confirm")
}

// firstScanKinds 是第一次用时要扫的库存（info summary 的类别 → run start 的种类）。
// 一键全收集的 5 步（顺序同 hiro.collect_all.STEPS）；首次进入只做信息库里从没扫过的（设计 5.7）
var collectKinds = []string{"account", "idols", "supports", "memories", "formations"}

// FirstScan（R11，11-25 review「首次进入的话，自动执行就好了」）：向导走完时，信息库里从没扫过的种类
// 由初星助手自己发起一次一键全收集（collect-all --only，设计 5.7），扫过的不重扫；扫描是读操作，缺省权限下不用确认（「总是询问」时照样等用户点）。
func (a *App) FirstScan() {
	call := a.d.Call
	a.d.Run.Go(func() func() {
		var sum struct {
			Kinds map[string]struct {
				ScannedAt *string `json:"scanned_at"`
			} `json:"kinds"`
		}
		if err := call(a.ctx, []string{"info", "summary"}).Decode(&sum); err != nil {
			return func() {
				t := a.ensureThread()
				a.addTo(t, "agent", "本来想先扫一遍你的支援卡、回忆和偶像卡，但读不到信息库。之后可以说「扫描支援卡」再试。", []plugin.Card{
					{"type": "error", "error": map[string]any{"code": "first_scan", "message": "读不到信息库：" + err.Error(), "hint": "到设置里看运行环境，或者之后说「扫描支援卡」"}}})
				a.save(t)
			}
		}
		var cards, errs []plugin.Card
		var missing []string
		for _, k := range collectKinds {
			if v, ok := sum.Kinds[k]; ok && v.ScannedAt != nil && *v.ScannedAt != "" {
				continue
			}
			missing = append(missing, k)
		}
		if len(missing) > 0 {
			r := call(a.ctx, []string{"run", "start", "collect-all", "--only", strings.Join(missing, ",")})
			switch {
			case r.Pending != nil:
				cards = append(cards, ConfirmCard(*r.Pending))
			case r.Error != nil:
				errs = append(errs, plugin.Card{"type": "error", "error": map[string]any{"code": r.Error.Code,
					"message": "没能开始一键全收集：" + r.Error.Message, "hint": r.Error.Hint}})
			}
		}
		cards = append(tidyCards(cards), errs...)
		if len(cards) == 0 {
			return nil
		}
		return func() {
			t := a.ensureThread()
			a.addTo(t, "agent", "第一次用：先把你的首页数值、偶像卡、支援卡、回忆和编成扫一遍（只看画面，不改东西），出编成方案时才知道你有什么。", cards)
			a.save(t)
		}
	})
}
