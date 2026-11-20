// Package doctor 是 `hiro doctor`：新手向导和出问题时的自检。每项给 ok / 白话说明 / 下一步。同 Python 版 hiro.cli.cmd_doctor。
//
// ok：true 没问题；false 要处理；null 程序判断不了，要用户自己确认（例如游戏内声音）。
// 默认不连游戏；加 --probe 才真的连一次设备、截一张图（只读，阶段 2 迁完设备层之前交给 Python 版）。
package doctor

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
	"github.com/matto49/hiro-gakumas-hero/go/internal/secret"
	"github.com/matto49/hiro-gakumas-hero/go/internal/store"
)

var (
	providers = map[string]string{"deepseek": "DeepSeek", "codex": "Codex", "claude": "Claude"}
	scanNames = [][2]string{{"account", "首页数值"}, {"idols", "偶像卡"}, {"supports", "支援卡"}, {"memories", "回忆"}, {"affection", "好感度"}}
)

func item(id, title string, ok any, detail, next string, optional bool) *jsonx.Object {
	return jsonx.NewObject().Set("id", id).Set("title", title).Set("ok", ok).Set("detail", detail).Set("next", next).Set("optional", optional)
}

const reinstall = "重新安装（设置 → 环境 → 重新安装）"

// pythonCheck：对局还在 Python 里跑（阶段 5 迁完之前），要 Python 3.10 以上、能导入 numpy 和 OpenCV。
func pythonCheck() *jsonx.Object {
	py, err := pybridge.Python()
	if err != nil {
		return item("python", "Python 环境", false, "找不到运行对局用的 Python", reinstall, false)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, py, "-c",
		"import sys\nassert sys.version_info >= (3, 10), 'Python %d.%d 太旧，要 3.10 以上' % sys.version_info[:2]\nimport numpy, cv2\nprint('%d.%d.%d' % sys.version_info[:3])")
	if repo, err := pybridge.Repo(); err == nil {
		cmd.Env = pybridge.Env(repo)
	}
	proc.Hide(cmd)
	out, err := cmd.Output()
	if err != nil {
		detail := "缺依赖或版本不对"
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			if s := lastLine(string(ee.Stderr)); s != "" {
				detail += "：" + s
			}
		}
		return item("python", "Python 环境", false, detail, reinstall, false)
	}
	return item("python", "Python 环境", true, "Python "+strings.TrimSpace(string(out))+"，依赖齐全", "", false)
}

func lastLine(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	return strings.TrimSpace(lines[len(lines)-1])
}

func platform(probe bool) *jsonx.Object {
	dev := strings.ToLower(os.Getenv("HIRO_DEVICE"))
	if dev == "" {
		dev = "auto"
	}
	var label string
	testing := false
	switch {
	case dev == "mumu" || dev == "adb" || dev == "leidian":
		label = map[string]string{"mumu": "MuMu 模拟器", "adb": "安卓设备（adb）", "leidian": "雷电模拟器"}[dev]
		testing = true
	case dev == "dmm" || (dev == "auto" && runtime.GOOS == "windows"):
		label = "Windows · DMM 版"
	case runtime.GOOS == "darwin":
		label = "Mac · PlayCover"
	default:
		return item("platform", "游戏平台", false, "这个系统还不支持", "目前支持 Mac（PlayCover）和 Windows（DMM 版 / MuMu）", false)
	}
	suffix := ""
	if testing {
		suffix = "（测试中）"
	}
	if runtime.GOOS == "darwin" && (dev == "auto" || dev == "maatools") {
		if _, err := os.Stat("/Applications/PlayCover.app"); err != nil {
			return item("platform", "游戏平台", false, "没找到 PlayCover", "安装 hguandl 分支的 PlayCover，并在游戏设置里打开 maaTools", false)
		}
	}
	if !probe {
		return item("platform", "游戏平台", nil, label+suffix+"：还没试连游戏", "打开游戏停在首页，再点「试连」", false)
	}
	// 真连一次游戏：设备层还在 Python（阶段 2 迁）
	data, err := pybridge.CLI("doctor", "--probe")
	if err == nil {
		for _, v := range jsonx.List(data.(*jsonx.Object), "items") {
			if o, ok := v.(*jsonx.Object); ok && jsonx.Str(o, "id") == "platform" {
				return o
			}
		}
	}
	return item("platform", "游戏平台", false, label+suffix+"：连不上游戏（自检没能运行）", "运行 hiro doctor 看 Python 环境那一项", false)
}

func settings() *jsonx.Object {
	data, err := os.ReadFile(filepath.Join(home.Dir(), "settings.json"))
	if err != nil {
		return jsonx.NewObject()
	}
	o, err := jsonx.DecodeObject(data)
	if err != nil {
		return jsonx.NewObject()
	}
	return o
}

func model() *jsonx.Object {
	st := settings()
	p := jsonx.Str(st, "provider")
	if p == "" {
		p = "deepseek" // 缺省 DeepSeek
	}
	hasKey, _ := st.Get("has_key")
	switch {
	case p == "none":
		return item("model", "AI 模型", false, "选了不用模型：只能用快捷指令，对局决策只用规则和算法",
			"到设置里选 DeepSeek 并填 API Key（可以跳过）", true)
	case p == "deepseek" && !jsonx.Truthy(hasKey):
		return item("model", "AI 模型", false, "默认用 DeepSeek，但还没填 API Key：现在只能用快捷指令，对局决策只用规则和算法",
			"到设置里填 DeepSeek 的 API Key（可以跳过）", true)
	}
	name, ok := providers[p]
	if !ok {
		name = p
	}
	return item("model", "AI 模型", true, "已选 "+name, "", true)
}

func secrets() *jsonx.Object {
	have := map[string]bool{}
	for _, n := range secret.Names() {
		have[n] = true
	}
	if have["game-account"] && have["game-password"] {
		return item("secrets", "游戏账号密码", true, "已保存（只用于掉登录时自动粘贴）", "", true)
	}
	return item("secrets", "游戏账号密码", false, "没保存：掉登录时要你自己登", "可以跳过；要用就到设置 → 账号密码里填", true)
}

func gameSettings(st *store.Store) *jsonx.Object {
	meta, rows, err := st.Latest("game_settings")
	if err != nil || meta == nil {
		return item("game_settings", "游戏快进设置", nil, "还没检查过快进 / 跳过演出那几项",
			"游戏停在首页时让初星助手检查（hiro run start check-settings，只读）", false)
	}
	var off, unknown []string
	for _, r := range rows {
		o, _ := r.(*jsonx.Object)
		switch {
		case jsonx.Str(o, "state") == "OFF":
			off = append(off, jsonx.Str(o, "item"))
		case jsonx.IsNull(o, "state"):
			unknown = append(unknown, jsonx.Str(o, "item"))
		}
	}
	when := meta.ScannedAt
	if len(when) >= 16 {
		when = when[5:16]
	}
	switch {
	case len(off) > 0:
		return item("game_settings", "游戏快进设置", false, "这几项没开："+strings.Join(off, "、")+"（"+when+" 检查）",
			"在游戏里打开 メニュー → 設定 → プロデュース，把它们打开；不开也能打，开局会多处理几个弹框", false)
	case len(unknown) > 0:
		return item("game_settings", "游戏快进设置", nil, "有几项没读到："+strings.Join(unknown, "、"), "游戏停在首页时再检查一次", false)
	}
	return item("game_settings", "游戏快进设置", true, "5 项都开着（"+when+" 检查）", "", false)
}

func catalogCheck() *jsonx.Object {
	p := filepath.Join(home.Dir(), "catalog", "catalog.ja.json")
	data, err := os.ReadFile(p)
	if err != nil {
		return item("catalog", "游戏资料库", false, "还没下载", "运行 hiro catalog sync（向导里点「下载资料库」）", false)
	}
	var cat struct {
		Cards map[string]json.RawMessage `json:"cards"`
	}
	if json.Unmarshal(data, &cat) != nil {
		return item("catalog", "游戏资料库", false, "文件坏了", "重新运行 hiro catalog sync", false)
	}
	return item("catalog", "游戏资料库", true, "已下载（"+itoa(len(cat.Cards))+" 张技能卡）", "", false)
}

func scan(st *store.Store) *jsonx.Object {
	var done []string
	for _, kn := range scanNames {
		if meta, _, err := st.Latest(kn[0]); err == nil && meta != nil {
			done = append(done, kn[1])
		}
	}
	if len(done) == 0 {
		return item("scan", "账号信息", false, "还没扫描过账号", "让初星助手扫描支援卡、回忆（向导最后一步）", false)
	}
	return item("scan", "账号信息", true, "已扫描："+strings.Join(done, "、"), "", false)
}

func itoa(n int) string { b, _ := jsonx.Marshal(n); return string(b) }

// Register 登记 `hiro doctor`。
func Register(r *cli.Registry) {
	r.MustRegister(&cli.Command{
		Name: "doctor", Risk: cli.Read,
		Help: "自检：Python 环境、游戏平台、游戏声音、模型、账号密码、资料库、账号扫描，每项给原因和下一步。--probe 真的连一次游戏（只读截图）。",
		Args: []cli.Arg{{Name: "--probe", Help: "真的连一次游戏，截一张图", Type: cli.Bool}},
		Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
			st, err := store.OpenHome()
			if err != nil {
				return nil, err
			}
			defer st.Close()
			items := []*jsonx.Object{
				pythonCheck(),
				platform(a.Bool("probe")),
				item("sound", "游戏声音", nil, "程序看不到游戏里的声音设置", "在游戏里打开设置 → 声音，把 BGM、语音、效果音都关掉（不用动电脑的音量）", false),
				gameSettings(st),
				model(),
				secrets(),
				catalogCheck(),
				scan(st),
			}
			ready := true
			list := make([]any, len(items))
			for i, it := range items {
				list[i] = it
				optional, _ := it.Get("optional")
				ok, _ := it.Get("ok")
				if optional != true && jsonx.Str(it, "id") != "scan" && ok == false {
					ready = false
				}
			}
			return jsonx.NewObject().Set("items", list).Set("ready", ready), nil
		},
	})
}
