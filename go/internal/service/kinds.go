// Package service 是 hiro 服务：常驻后台，跑对局和扫描（同一时间只跑一个），命令行和桌面程序都是它的客户端。
// 同 Python 版 hiro.cli.service / jobs / client：同一把锁、同一个 service.json、同一套接口，两边能互相连。
//
// 阶段 1–4 对局还是 Python 子进程（hiro.run_hif、hiro.runtime.hajime …）；自检（selftest）是 hiro 自己的隐藏子命令。
package service

import (
	"path/filepath"
	"strconv"
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/catalog"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// Runtime 是起任务子进程要的东西：Python、仓库根、hiro 自己（自检用）。
type Runtime struct {
	Python string
	Repo   string
	Self   string
}

// Kind 是一种任务。
type Kind struct {
	Name     string
	Help     string
	Argv     func(p *jsonx.Object, dir string, rt Runtime) []string // (参数, 任务目录, 运行时) → 子进程命令
	Summary  func(p *jsonx.Object) string                           // 确认卡上的一句话
	Output   string                                                 // 任务目录里 out.json 的类别（扫描任务）；结束后导入信息库
	Env      map[string]string
	Pausable bool // 主循环认 HIRO_PAUSE_FLAG（对局、自检）；日常和扫描脚本不认，只能停止
}

// SelftestCommand 是 hiro 的隐藏子命令：自检任务（只打印几行，不碰游戏）。
const SelftestCommand = "__selftest"

// HomeReader 是首页顶栏读数的模块（scan-account）。
const HomeReader = "hiro.vision.home_values"

var (
	// DailyItems 是日常的项目。
	DailyItems = []string{"activity_funds", "presents", "assignment", "contest", "mission_reward", "club_reward"}
	// dailyOwn 已迁到 hiro.daily 的（不经 KAA）。
	dailyOwn   = map[string]bool{"activity_funds": true, "presents": true, "mission_reward": true, "club_reward": true}
	dailyLabel = map[string]string{
		"activity_funds": "活動費", "presents": "礼物", "assignment": "工作", "contest": "竞赛",
		"mission_reward": "任务奖励", "club_reward": "社团",
	}
	ownRuntime = map[string]string{"HIRO_RUNTIME": "own"}
)

func str(p *jsonx.Object, k string) string { return jsonx.Str(p, k) }

func or(p *jsonx.Object, k, def string) string {
	if s := str(p, k); s != "" {
		return s
	}
	return def
}

// numStr 取参数里的整数，转成命令行文字；没给返回 ""。
func numStr(p *jsonx.Object, k string) string {
	if n, ok := jsonx.Int(p, k); ok && n != 0 {
		return strconv.FormatInt(n, 10)
	}
	return ""
}

func flag(p *jsonx.Object, k string) bool { v, _ := p.Get(k); return jsonx.Truthy(v) }

func items(p *jsonx.Object) []string {
	var out []string
	for _, i := range strings.Split(str(p, "items"), ",") {
		if i != "" {
			out = append(out, i)
		}
	}
	return out
}

func hifArgv(p *jsonx.Object, dir string, rt Runtime) []string {
	a := []string{rt.Python, "-m", "hiro.run_hif", or(p, "command", "new"), "--mode", or(p, "mode", "qualify"), "--trace", filepath.Base(dir)}
	if s := str(p, "idol"); s != "" {
		a = append(a, "--idol", s)
	}
	if s := str(p, "main_attr"); s != "" {
		a = append(a, "--main-attr", s)
	}
	if flag(p, "no_llm") {
		a = append(a, "--no-llm")
	}
	return a
}

func hifSummary(p *jsonx.Object) string {
	if or(p, "command", "new") == "resume" {
		return "接着打进行中的 H.I.F 培育"
	}
	if or(p, "mode", "qualify") == "main" {
		return "开一局 H.I.F 本战（会用掉一份选拔回忆）"
	}
	s := "开一局 H.I.F 选拔"
	if idol := str(p, "idol"); idol != "" {
		s += "（" + catalog.IdolName(idol) + "）"
	}
	return s
}

// produceArgv：初（hiro.runtime.hajime）/ N.I.A（hiro.runtime.nia_sample）：新主循环，编成号走参数，存帧放任务目录。
func produceArgv(module, defaultLevel string) func(*jsonx.Object, string, Runtime) []string {
	return func(p *jsonx.Object, dir string, rt Runtime) []string {
		a := []string{
			rt.Python, "-m", module, or(p, "command", "new"), "--idol", str(p, "idol"),
			"--difficulty", or(p, "difficulty", defaultLevel), "--trace", filepath.Base(dir), "--out", filepath.Join(dir, "frames"),
		}
		for _, kf := range [][2]string{{"main_attr", "--main-attr"}, {"support_set", "--support-set"}, {"memory_set", "--memory-set"}} {
			v := str(p, kf[0])
			if v == "" {
				v = numStr(p, kf[0])
			}
			if v != "" {
				a = append(a, kf[1], v)
			}
		}
		if flag(p, "no_llm") {
			a = append(a, "--no-llm")
		}
		return a
	}
}

func produceSummary(name, defaultLevel string) func(*jsonx.Object) string {
	return func(p *jsonx.Object) string {
		if or(p, "command", "new") == "resume" {
			return "接着打进行中的" + name + "培育"
		}
		s := "开一局" + name + " " + strings.ToUpper(or(p, "difficulty", defaultLevel))
		if idol := str(p, "idol"); idol != "" {
			s += "（" + catalog.IdolName(idol) + "）"
		}
		return s
	}
}

func dailyArgv(p *jsonx.Object, _ string, rt Runtime) []string {
	a := []string{rt.Python, filepath.Join(rt.Repo, "tools", "daily", "run_daily.py")}
	if its := items(p); len(its) > 0 {
		a = append(a, "--only", strings.Join(its, ","))
		own := true
		for _, i := range its {
			own = own && dailyOwn[i]
		}
		if own {
			a = append(a, "--own")
		}
	}
	return a
}

func dailySummary(p *jsonx.Object) string {
	its := items(p)
	if len(its) == 0 {
		return "领日常奖励"
	}
	labels := make([]string, len(its))
	for i, it := range its {
		labels[i] = it
		if l, ok := dailyLabel[it]; ok {
			labels[i] = l
		}
	}
	return "领日常奖励（" + strings.Join(labels, "、") + "）"
}

var scenarioLabel = map[string]string{"hif": "H.I.F", "hajime": "初", "nia": "N.I.A"}

func writeFormationArgv(p *jsonx.Object, _ string, rt Runtime) []string {
	a := []string{rt.Python, filepath.Join(rt.Repo, "tools", "write_formation.py"), "--mode", or(p, "scenario", "hif")}
	if s := str(p, "what"); s != "" {
		a = append(a, "--what", s)
	}
	if flag(p, "dry_run") {
		a = append(a, "--dry-run")
	}
	return a
}

func writeFormationSummary(p *jsonx.Object) string {
	sc := or(p, "scenario", "hif")
	mode, ok := scenarioLabel[sc]
	if !ok {
		mode = sc
	}
	if flag(p, "dry_run") {
		return "对照编成方案看 " + mode + " 的编成差在哪几格（只看，不改）"
	}
	return "把编成方案写进游戏（" + mode + " 的支援卡 / 回忆编成）"
}

func out(dir string) string { return filepath.Join(dir, "out.json") }

func fixed(s string) func(*jsonx.Object) string { return func(*jsonx.Object) string { return s } }

// KindNames 是任务种类（顺序同 Python KINDS）。
var KindNames = []string{
	"selftest", "hif", "tower", "hajime", "nia", "daily", "write-formation", "check-settings", "fit-window",
	"scan-account", "scan-supports", "scan-memories",
}

// Kinds 是全部任务种类。
var Kinds = map[string]*Kind{
	"selftest": {
		Name: "selftest", Help: "自检：只打印几行，不碰游戏", Pausable: true,
		Argv: func(p *jsonx.Object, dir string, rt Runtime) []string {
			s := numStr(p, "seconds")
			if s == "" {
				s = "1"
			}
			return []string{rt.Self, SelftestCommand, s, filepath.Base(dir)}
		},
		Summary: fixed("跑一次自检（不碰游戏）"),
	},
	"hif": {Name: "hif", Help: "H.I.F 培育：选拔 / 本战 / 续跑", Argv: hifArgv, Summary: hifSummary, Env: ownRuntime, Pausable: true},
	"tower": {
		Name: "tower", Help: "偶像之路：按偶像路线往上爬", Env: ownRuntime, Pausable: true,
		Argv: func(p *jsonx.Object, dir string, rt Runtime) []string {
			layers := numStr(p, "layers")
			if layers == "" {
				layers = "1"
			}
			a := []string{rt.Python, "-m", "hiro.runtime.tower_play", "--idol-name", str(p, "idol"), "--layers", layers, "--trace", filepath.Base(dir)}
			if flag(p, "no_llm") {
				a = append(a, "--no-llm")
			}
			return a
		},
		Summary: func(p *jsonx.Object) string {
			layers := numStr(p, "layers")
			if layers == "" {
				layers = "1"
			}
			return "爬偶像之路（" + or(p, "idol", "?") + "，" + layers + " 层）"
		},
	},
	"hajime": {
		Name: "hajime", Help: "「初」培育（レギュラー / プロ / マスター）", Argv: produceArgv("hiro.runtime.hajime", "regular"),
		Summary: produceSummary("「初」", "regular"), Env: ownRuntime, Pausable: true,
	},
	"nia": {
		Name: "nia", Help: "N.I.A 培育（プロ / マスター；认不出的页会停下交给你）", Argv: produceArgv("hiro.runtime.nia_sample", "pro"),
		Summary: produceSummary(" N.I.A", "pro"), Env: ownRuntime, Pausable: true,
	},
	"daily": {Name: "daily", Help: "日常：活動費、礼物、工作、竞赛、任务奖励、社团（可只选几项）", Argv: dailyArgv, Summary: dailySummary},
	"write-formation": {
		Name: "write-formation", Help: "把开局编成方案写进游戏的编成位，写完回读核对（--dry-run 只报差在哪几格，不改）",
		Argv: writeFormationArgv, Summary: writeFormationSummary,
	},
	"check-settings": {
		Name: "check-settings", Help: "检查游戏设置里快进 / 跳过演出那几项开没开（只读：打开菜单看完回首页，不点开关）",
		Argv: func(_ *jsonx.Object, dir string, rt Runtime) []string {
			return []string{rt.Python, filepath.Join(rt.Repo, "tools", "check_settings.py"), "--json", out(dir)}
		},
		Summary: fixed("检查游戏设置（只读，不改开关）"), Output: "game_settings",
	},
	"fit-window": {
		Name: "fit-window", Help: "Windows DMM：把游戏窗口调成适合识别的大小（9:16，按显示器自适应）",
		Argv: func(_ *jsonx.Object, _ string, rt Runtime) []string {
			return []string{rt.Python, "-m", "hiro.devices.win_window", "fit"}
		},
		Summary: fixed("调整 DMM 游戏窗口大小"),
	},
	"scan-account": {
		Name: "scan-account", Help: "读首页顶栏的 PLv、AP、ジュエル（只截一张图，不点）",
		Argv: func(_ *jsonx.Object, dir string, rt Runtime) []string {
			return []string{rt.Python, "-m", HomeReader, "--live", "--out", out(dir)}
		},
		Summary: fixed("读首页的 PLv / AP / ジュエル（只截图，不点）"), Output: "account",
	},
	"scan-supports": {
		Name: "scan-supports", Help: "扫描支援卡（只读：在「選択」列表里滚动，最后点キャンセル）",
		Argv: func(_ *jsonx.Object, dir string, rt Runtime) []string {
			return []string{rt.Python, filepath.Join(rt.Repo, "tools", "support_inventory.py"), "--list", "--out", out(dir)}
		},
		Summary: fixed("扫描支援卡库存（只读，不改编成）"), Output: "supports",
	},
	"scan-memories": {
		Name: "scan-memories", Help: "扫描回忆（只读：在「メモリー選択」里逐格读）",
		Argv: func(_ *jsonx.Object, dir string, rt Runtime) []string {
			return []string{rt.Python, "-m", "hiro.memory_scan", "scan", "--out", out(dir)}
		},
		Summary: fixed("扫描回忆库存（只读，不改编成）"), Output: "memories",
	},
}
