package shell

import (
	"regexp"
	"strings"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
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

// PermDesc 是每一档的一句说明（输入框的档位菜单里显示）。
var PermDesc = map[string]string{
	PermAsk:       "每件会动游戏的事都先出确认卡",
	PermRead:      "扫描、检查、领日常、写空白编成位直接做",
	PermReadWrite: "除了开局、接着打，都直接做",
	PermAll:       "开局也直接开始；花钻石、放弃培育永远不做",
}

// PermOrder 是档位从严到松的顺序。
var PermOrder = []string{PermAsk, PermRead, PermReadWrite, PermAll}

// permMenu 是输入框左下角的档位按钮（11-25 review：放进对话框里，ui 同 codex）：显示当前档位，点开换档。
// 第一次切到「允许所有行为」先弹一次二次确认。
func (a *App) permMenu(c *ui.Context) {
	cur := a.permission()
	if a.d.SetPermission == nil {
		ui.Text(c, PermName[cur]).FontSize(kit.FNote).TextColor(kit.Ink3).SingleLine()
		return
	}
	c.Key("perm-menu")
	ui.PopoverMenuButton(c, PermName[cur], func(m *ui.Menu) {
		for _, lv := range PermOrder {
			if m.Item(PermName[lv]).Subtitle(PermDesc[lv]).Radio(lv == cur).Chosen() && lv != cur {
				a.choosePermission(lv)
			}
		}
	}).Label("操作权限："+PermName[cur]).Tooltip("操作权限：哪些事不用确认就直接做").
		FontSize(kit.FNote).TextColor(kit.Ink2).Padding(4, 8).Radius(kit.R8).Background(ui.RGBA(0, 0, 0, 0))
	if a.confirmAll {
		ui.Modal(c, &a.confirmAll, func() {
			ui.Column(c).Width(420).Gap(12).Padding(20).Radius(kit.R16).Background(kit.CardBg).Children(func() {
				ui.Text(c, "确定允许所有行为？").FontSize(16).FontWeight(700).TextColor(kit.Ink)
				kit.Note(c, "开局、接着打、初星課題都会直接开始，不再出确认卡，会用掉培育次数和 AP。花钻石、放弃培育这些不管哪一档都不会做。这个提示只出这一次，之后可以随时改回来。")
				ui.Row(c).Gap(8).Children(func() {
					ui.Spacer(c)
					if kit.Secondary(c, "取消").Key("perm-all-cancel").Clicked() {
						a.confirmAll = false
					}
					if kit.Primary(c, "确定，允许所有行为").Key("perm-all-ok").Clicked() {
						if err := a.d.SetPermission(PermAll); err != nil {
							a.permErr = "没存上：" + err.Error() + "，再点一次试试"
						} else {
							a.confirmAll, a.permErr = false, ""
						}
					}
				})
				if a.permErr != "" {
					ui.Text(c, a.permErr).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
				}
			})
		})
	}
}

func (a *App) choosePermission(level string) {
	a.permErr = ""
	if level == PermAll && (a.d.AllowAllConfirmed == nil || !a.d.AllowAllConfirmed()) {
		a.confirmAll = true
		return
	}
	if err := a.d.SetPermission(level); err != nil {
		// 存不上就还是原来的档（可能是更高的档），要说清楚、让人再试（CodeRabbit #91）
		a.permErr = "操作权限没存上：" + err.Error() + "。现在还是「" + PermName[a.permission()] + "」，再选一次试试"
	}
}

// permError 是输入框下面一行：上次换档没存上的原因（换档成功后清掉）。
func (a *App) permError(c *ui.Context) {
	if a.permErr != "" && !a.confirmAll {
		ui.Text(c, a.permErr).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
	}
}

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
	// N6（11-26 G1）：回复里那句「要开始「…」吗？点下面的确认卡。」是按要确认写的；自动开始了就去掉，下面那行会写「已自动开始…不用确认」
	m.Text = strings.TrimSpace(askConfirm.ReplaceAllString(m.Text, ""))
	a.resolve(m, card, "confirm")
}

var askConfirm = regexp.MustCompile(`要开始「[^」]*」吗？点下面的确认卡。`)

// firstScanKinds 是第一次用时要扫的库存（info summary 的类别 → run start 的种类）。
// 一键全收集的 5 步（顺序同 hiro.collect_all.STEPS）；首次进入只做信息库里从没扫过的（设计 5.7）
var collectKinds = []string{"account", "idols", "supports", "formations", "memories"}

// firstScanKinds：「回忆按片扫描」关着（缺省）时首次进入逐项扫的三种（原来的做法）
var firstScanKinds = [][2]string{{"supports", "scan-supports"}, {"memories", "scan-memories"}, {"idols", "scan-idols"}}

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
				t := a.actionThread() // 页面模式：提示条 + 确认框
				a.addTo(t, "agent", "本来想先扫一遍你的支援卡、回忆和偶像卡，但读不到信息库。之后可以说「扫描支援卡」再试。", []plugin.Card{
					{"type": "error", "error": map[string]any{"code": "first_scan", "message": "读不到信息库：" + err.Error(), "hint": "到设置里看运行环境，或者之后说「扫描支援卡」"}}})
				a.save(t)
			}
		}
		var cards, errs []plugin.Card
		scanned := func(k string) bool {
			v, ok := sum.Kinds[k]
			return ok && v.ScannedAt != nil && *v.ScannedAt != ""
		}
		if a.d.MemorySlices == nil || !a.d.MemorySlices() { // 缺省：照旧逐项扫没扫过的三种（#116 审查重要 5）
			for _, k := range firstScanKinds {
				if scanned(k[0]) {
					continue
				}
				r := call(a.ctx, []string{"run", "start", k[1]})
				switch {
				case r.Pending != nil:
					cards = append(cards, ConfirmCard(*r.Pending))
				case r.Error != nil:
					errs = append(errs, plugin.Card{"type": "error", "error": map[string]any{"code": r.Error.Code,
						"message": "没能开始扫描（" + k[1] + "）：" + r.Error.Message, "hint": r.Error.Hint}})
				}
			}
		}
		var missing []string
		for _, k := range collectKinds {
			if a.d.MemorySlices == nil || !a.d.MemorySlices() || scanned(k) {
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
		msg := "第一次用：先扫一遍你的支援卡、回忆和偶像卡（只看画面，不改东西），出编成方案时才知道你有什么。"
		if a.d.MemorySlices != nil && a.d.MemorySlices() {
			msg = "第一次用：先把你的首页数值、偶像卡、支援卡、回忆和编成扫一遍（只看画面，不改东西），出编成方案时才知道你有什么。"
		}
		return func() {
			t := a.actionThread()
			a.addTo(t, "agent", msg, cards)
			a.save(t)
		}
	})
}
