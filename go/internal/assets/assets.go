// Package assets 是识别层取素材的唯一入口（同 Python 版 hiro.vision.assets + hiro.defaults.assets.PublicAssetSource）。
//
// 三个来源（都可以用环境变量改位置）：
//
//   - KAA 公开数据（kaa-game-data 发布包）：game.db（主数据）、skill_cards/、drinks/、idol_cards/。HIRO_KAA_GAME_DATA，缺省 resources/game_data。
//   - 使用者自建目录：HIRO_USER_ASSETS，缺省 resources/user_assets（cards/、icons/<kind>/、font/、layout.json、sprites/、support/）。
//   - 随仓库的素材包 assets/pack/（目录结构同自建目录）：HIRO_BUNDLED_ASSETS 改位置，设成 0 / off 关掉；
//     缺省从程序所在目录、当前目录往上找 assets/pack。
//
// 同类图库取文件更多的一个；layout.json 和字体先自建后随仓库。拿不到的项返回空字符串 / nil，识别层各自退路。
// 界面模板（assets/pack/ui）不受 HIRO_BUNDLED_ASSETS 开关影响（同 Python 的 ui_templates.PACK），见 [UIPack]。
package assets

import (
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	_ "modernc.org/sqlite" // 主数据 game.db（纯 Go）

	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
)

// Kind 是图标类别（插件约定的名字）。
const (
	KindDrink  = "drink"  // P 饮料
	KindPItem  = "pitem"  // P 道具
	KindStatus = "status" // 考试效果
)

// DigitFont 是 HUD 数字所用字体的文件名。
const DigitFont = "Campus-Alphanumeric-SemiBold.ttf"

// Source 是一套素材位置。
type Source struct {
	KAA     string // KAA 公开数据目录
	User    string // 使用者自建目录
	Bundled string // 随仓库的素材包；空 = 关掉或找不到

	dbOnce sync.Once
	db     *sql.DB
}

var (
	defOnce sync.Once
	def     *Source
)

// Default 按环境变量建的素材来源（首次调用时定，之后不变；测试换环境用 [Reset]）。
func Default() *Source {
	defOnce.Do(func() { def = FromEnv() })
	return def
}

// Reset 测试用：环境变量改了以后重新取。
func Reset() {
	defOnce = sync.Once{}
	def = nil
	packOnce = sync.Once{}
}

func env(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

// FromEnv 按 HIRO_KAA_GAME_DATA / HIRO_USER_ASSETS / HIRO_BUNDLED_ASSETS 建。
func FromEnv() *Source {
	s := &Source{KAA: env("HIRO_KAA_GAME_DATA", filepath.Join("resources", "game_data")), User: env("HIRO_USER_ASSETS", filepath.Join("resources", "user_assets"))}
	raw, set := os.LookupEnv("HIRO_BUNDLED_ASSETS")
	switch {
	case set && isOff(raw):
	case set && raw != "":
		if isDir(raw) {
			s.Bundled = raw
		}
	default:
		s.Bundled = PackRoot()
	}
	return s
}

func isOff(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "0", "off", "no", "false", "none":
		return true
	}
	return false
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func isFile(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.Mode().IsRegular()
}

var (
	packOnce sync.Once
	packRoot string
)

// PackRoot 随仓库素材包 assets/pack 的位置（不看 HIRO_BUNDLED_ASSETS 开关）：HIRO_PACK → 从程序所在目录、当前目录往上找。找不到返回空。
func PackRoot() string {
	packOnce.Do(func() {
		if v := os.Getenv("HIRO_PACK"); v != "" {
			packRoot = v
			return
		}
		var starts []string
		if exe, err := os.Executable(); err == nil {
			if real, err := filepath.EvalSymlinks(exe); err == nil {
				exe = real
			}
			starts = append(starts, filepath.Dir(exe))
		}
		if wd, err := os.Getwd(); err == nil {
			starts = append(starts, wd)
		}
		for _, s := range starts {
			for d := s; ; d = filepath.Dir(d) {
				if p := filepath.Join(d, "assets", "pack"); isDir(filepath.Join(p, "ui")) {
					packRoot = p
					return
				}
				if filepath.Dir(d) == d {
					break
				}
			}
		}
	})
	return packRoot
}

// ErrNoPack 是找不到随仓库的素材包。
var ErrNoPack = errors.New("找不到素材包 assets/pack（设 HIRO_PACK 指过去）")

// UIPack 界面模板目录 assets/pack/ui。
func UIPack() (string, error) {
	r := PackRoot()
	if r == "" {
		return "", ErrNoPack
	}
	return filepath.Join(r, "ui"), nil
}

func (s *Source) roots() []string {
	out := []string{s.User}
	if s.Bundled != "" {
		out = append(out, s.Bundled)
	}
	return out
}

func count(dir string) int {
	es, err := os.ReadDir(dir)
	if err != nil {
		return 0
	}
	return len(es)
}

// fullest 候选目录里文件最多的一个（并列取靠前的）。
func fullest(dirs ...string) string {
	best, n := "", 0
	for _, d := range dirs {
		if c := count(d); c > n {
			best, n = d, c
		}
	}
	return best
}

func (s *Source) under(parts ...string) []string {
	var out []string
	for _, r := range s.roots() {
		out = append(out, filepath.Join(append([]string{r}, parts...)...))
	}
	return out
}

// CardArtDir 技能卡卡面目录。
func (s *Source) CardArtDir() string {
	return fullest(append(s.under("cards"), filepath.Join(s.KAA, "skill_cards"))...)
}

// GameData KAA 公开数据里的原图目录（idol_cards / skill_cards / drinks）；空目录当没有。
func (s *Source) GameData(category string) string {
	d := filepath.Join(s.KAA, category)
	if count(d) > 0 {
		return d
	}
	return ""
}

// IconDir 图标目录（kind：drink / pitem / status）。
func (s *Source) IconDir(kind string) string {
	dirs := s.under("icons", kind)
	if kind == KindDrink {
		dirs = append(dirs, filepath.Join(s.KAA, "drinks"))
	}
	return fullest(dirs...)
}

// FontPath 数字字体：font/ 下优先 DigitFont，没有就取唯一的一个字体文件（多个时不猜）。
func (s *Source) FontPath() string {
	for _, r := range s.roots() {
		folder := filepath.Join(r, "font")
		if !isDir(folder) {
			continue
		}
		if p := filepath.Join(folder, DigitFont); isFile(p) {
			return p
		}
		es, _ := os.ReadDir(folder)
		var fonts []string
		for _, e := range es {
			if ext := strings.ToLower(filepath.Ext(e.Name())); ext == ".ttf" || ext == ".otf" {
				fonts = append(fonts, filepath.Join(folder, e.Name()))
			}
		}
		sort.Strings(fonts)
		if len(fonts) == 1 {
			return fonts[0]
		}
	}
	return ""
}

// Layout layout.json 的内容（先自建后随仓库）；没有返回 nil。
func (s *Source) Layout() (map[string]any, error) {
	for _, r := range s.roots() {
		p := filepath.Join(r, "layout.json")
		if !isFile(p) {
			continue
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return nil, err
		}
		var out map[string]any
		if err := json.Unmarshal(b, &out); err != nil {
			return nil, err
		}
		return out, nil
	}
	return nil, nil
}

// SpritesDir 界面 Sprite 目录（页面签名用）。
func (s *Source) SpritesDir() string { return fullest(s.under("sprites")...) }

// SupportDir 支援卡图目录。
func (s *Source) SupportDir() string { return fullest(s.under("support")...) }

// Available 某类素材拿不拿得到（kind：font / cards / sprites / support / drink / pitem / status）。
func (s *Source) Available(kind string) bool {
	switch kind {
	case "font":
		return s.FontPath() != ""
	case "cards":
		return s.CardArtDir() != ""
	case "sprites":
		return s.SpritesDir() != ""
	case "support":
		return s.SupportDir() != ""
	}
	return s.IconDir(kind) != ""
}

// CacheDir 识别层的缓存目录（卡面描述子、索引等算一次就存的东西）：HIRO_CACHE → 仓库的 cache/（开发机，素材包所在仓库）→ HIRO_HOME/cache。
func CacheDir() string {
	if v := os.Getenv("HIRO_CACHE"); v != "" {
		return v
	}
	if r := PackRoot(); r != "" {
		return filepath.Join(filepath.Dir(filepath.Dir(r)), "cache")
	}
	return filepath.Join(home.Dir(), "cache")
}
