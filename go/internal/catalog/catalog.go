// Package catalog 是 `hiro catalog …`：游戏资料库（卡牌、P 道具、饮料、偶像卡、回忆技能）。同 Python 版 hiro.cli.catalog。
//
// 数据来自初星攻略站的公开接口（GKMS 主数据生成，中文取学マス汉化插件的翻译），下载到 HIRO_HOME/catalog，
// 不随仓库发布（AGENTS.md：不往仓库加游戏素材）。小图按需下载、缓存。
package catalog

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

const (
	defaultURL  = "https://hiro.matto.top/gkbench"
	searchLimit = 50
	userAgent   = "hiro-console"
)

var (
	// Langs 是资料库的语言。
	Langs = []string{"ja", "zh"}
	// Kinds 是能搜的类别（顺序同 Python）。
	Kinds        = []string{"cards", "items", "drinks", "idols", "memories"}
	assetPattern = regexp.MustCompile(`^[A-Za-z0-9_.\-]+$`)
	httpClient   = &http.Client{}
)

func baseURL() string {
	if v := os.Getenv("HIRO_CATALOG_URL"); v != "" {
		return strings.TrimRight(v, "/")
	}
	return defaultURL
}

// Dir 是 HIRO_HOME/catalog。
func Dir() (string, error) { return home.Sub("catalog") }

func fetch(url string, timeout time.Duration) ([]byte, error) {
	netErr := func(err error) error {
		return clierr.New("network", fmt.Sprintf("下载失败：%s（%v）", url, err), "检查网络；国内网络不通时稍后重试")
	}
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, netErr(err)
	}
	req.Header.Set("User-Agent", userAgent)
	c := *httpClient
	c.Timeout = timeout
	resp, err := c.Do(req)
	if err != nil {
		return nil, netErr(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, netErr(fmt.Errorf("HTTP %d", resp.StatusCode))
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, netErr(err)
	}
	return data, nil
}

func writeAtomic(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// ---------------------------------------------------------------- 读资料库（按文件修改时间缓存：服务里会反复查）

type cached struct {
	mod  time.Time
	size int64
	cat  *jsonx.Object
}

var (
	cacheMu sync.Mutex
	cache   = map[string]cached{}
)

// Load 读一种语言的资料库；没下载报 catalog_missing。
func Load(lang string) (*jsonx.Object, error) {
	d, err := Dir()
	if err != nil {
		return nil, err
	}
	p := filepath.Join(d, "catalog."+lang+".json")
	st, err := os.Stat(p)
	if err != nil {
		return nil, clierr.New("catalog_missing", "资料库还没下载", "运行 hiro catalog sync")
	}
	cacheMu.Lock()
	defer cacheMu.Unlock()
	if c, ok := cache[p]; ok && c.mod.Equal(st.ModTime()) && c.size == st.Size() {
		return c.cat, nil
	}
	data, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	cat, err := jsonx.DecodeObject(data)
	if err != nil {
		return nil, clierr.New("bad_catalog", "资料库文件坏了："+p, "重新运行 hiro catalog sync")
	}
	cache[p] = cached{mod: st.ModTime(), size: st.Size(), cat: cat}
	return cat, nil
}

// IdolName 偶像卡编号（可以是 skin id）→ 卡名（给人看的摘要用）；资料库没下载或查不到时原样返回编号。
func IdolName(idol string) string {
	cat, err := Load("ja")
	if err != nil {
		return idol
	}
	row := jsonx.Obj(jsonx.Obj(cat, "idols"), strings.Replace(idol, "i_card-skin-", "i_card-", 1))
	if name := jsonx.Str(row, "name"); name != "" {
		return name
	}
	return idol
}

func texts(e *jsonx.Object) []string {
	out := []string{jsonx.Str(e, "name"), jsonx.Str(e, "text")}
	up := jsonx.Obj(e, "up")
	for _, k := range up.Keys() {
		u := jsonx.Obj(up, k)
		out = append(out, jsonx.Str(u, "name"), jsonx.Str(u, "text"))
	}
	return out
}

func get(o *jsonx.Object, k string) any { v, _ := o.Get(k); return v }

func brief(kind, id string, e *jsonx.Object) *jsonx.Object {
	text, has := e.Get("text")
	if (!has || text == nil) && jsonx.Obj(e, "up").Len() > 0 {
		up := jsonx.Obj(e, "up")
		text = get(jsonx.Obj(up, up.Keys()[0]), "text")
	}
	return jsonx.NewObject().Set("kind", kind).Set("id", id).Set("name", get(e, "name")).Set("rarity", get(e, "rarity")).
		Set("plan", get(e, "plan")).Set("text", text).Set("asset", get(e, "asset"))
}

// Search 按名字、编号或效果文字搜（不分大小写），最多 50 条。kind 空就搜全部类别。
func Search(q, kind, lang string) ([]any, error) {
	cat, err := Load(lang)
	if err != nil {
		return nil, err
	}
	ql := strings.ToLower(q)
	kinds := Kinds
	if kind != "" {
		kinds = []string{kind}
	}
	hits := []any{}
	for _, k := range kinds {
		entries := jsonx.Obj(cat, k)
		for _, id := range entries.Keys() {
			e := jsonx.Obj(entries, id)
			if e == nil {
				continue
			}
			found := strings.Contains(strings.ToLower(id), ql)
			for _, t := range texts(e) {
				if found {
					break
				}
				found = strings.Contains(strings.ToLower(t), ql)
			}
			if found {
				hits = append(hits, brief(k, id, e))
				if len(hits) >= searchLimit {
					return hits, nil
				}
			}
		}
	}
	return hits, nil
}

// Register 登记 `hiro catalog …` 命令。
func Register(r *cli.Registry) {
	r.MustRegister(
		&cli.Command{
			Name: "catalog sync", Risk: cli.Read,
			Help: "下载 / 更新游戏资料库（日文和中文两份）。第一次用资料库前运行。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) {
				d, err := Dir()
				if err != nil {
					return nil, err
				}
				counts := jsonx.NewObject()
				for _, lang := range Langs {
					url := baseURL() + "/api/catalog"
					if lang == "zh" {
						url += "?lang=zh"
					}
					raw, err := fetch(url, 60*time.Second)
					if err != nil {
						return nil, err
					}
					cat, err := jsonx.DecodeObject(raw)
					if err != nil {
						return nil, clierr.New("bad_catalog", "资料库数据不是 JSON："+url, "稍后重试；一直这样请反馈")
					}
					if err := writeAtomic(filepath.Join(d, "catalog."+lang+".json"), raw); err != nil {
						return nil, err
					}
					if lang == "ja" {
						for _, k := range Kinds {
							counts.Set(k, jsonx.Obj(cat, k).Len())
						}
					}
				}
				return jsonx.NewObject().Set("langs", Langs).Set("counts", counts).Set("dir", d), nil
			},
		},
		&cli.Command{
			Name: "catalog search", Risk: cli.Read,
			Help: "按名字或效果文字搜资料库（最多 50 条）。",
			Args: []cli.Arg{
				{Name: "query", Help: "要搜的词，例如 エウレカ 或 好調"},
				{Name: "--kind", Help: "只搜这一类", Choices: Kinds},
				{Name: "--lang", Help: "名字和效果文字用哪种语言", Choices: Langs, Default: "ja"},
			},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				items, err := Search(a.Str("query"), a.Str("kind"), a.Str("lang"))
				if err != nil {
					return nil, err
				}
				return jsonx.NewObject().Set("query", a.Str("query")).Set("items", items).Set("truncated", len(items) >= searchLimit), nil
			},
		},
		&cli.Command{
			Name: "catalog get", Risk: cli.Read,
			Help: "按编号取资料库里的一条完整记录。",
			Args: []cli.Arg{
				{Name: "id", Help: "编号，例如 p_card-01-ido-3_105"},
				{Name: "--lang", Help: "语言", Choices: Langs, Default: "ja"},
			},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				cat, err := Load(a.Str("lang"))
				if err != nil {
					return nil, err
				}
				id := a.Str("id")
				for _, k := range append(append([]string(nil), Kinds...), "enchants") {
					if e, ok := jsonx.Obj(cat, k).Get(id); ok && e != nil {
						return jsonx.NewObject().Set("kind", k).Set("id", id).Set("entry", e), nil
					}
				}
				return nil, clierr.New("not_found", "资料库里没有 "+id, "先用 hiro catalog search 查编号")
			},
		},
		&cli.Command{
			Name: "catalog sprite", Risk: cli.Read, NoAgent: true,
			Help: "取一张小图的本机路径（没有就下载）。",
			Args: []cli.Arg{{Name: "asset", Help: "图名，即记录里的 asset 字段"}},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				asset := a.Str("asset")
				p, err := SpritePath(asset)
				if err != nil {
					return nil, err
				}
				return jsonx.NewObject().Set("asset", asset).Set("path", p), nil
			},
		},
	)
}

// SpritePath 返回小图的本机路径，没有就下载。
func SpritePath(asset string) (string, error) {
	if !assetPattern.MatchString(asset) {
		return "", clierr.New("bad_args", "图名不合法："+asset, "用资料库记录里的 asset 字段")
	}
	d, err := home.Sub("catalog", "sprites")
	if err != nil {
		return "", err
	}
	p := filepath.Join(d, asset+".png")
	if _, err := os.Stat(p); err == nil {
		return p, nil
	}
	data, err := fetch(baseURL()+"/sprites/"+asset+".png", 30*time.Second)
	if err != nil {
		return "", err
	}
	return p, writeAtomic(p, data)
}
