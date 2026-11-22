// Package catalog 是 `hiro catalog …`：游戏资料库（卡牌、P 道具、饮料、偶像卡、回忆技能）。同 Python 版 hiro.cli.catalog。
//
// 数据来自初星攻略站的公开接口（GKMS 主数据生成，中文取学マス汉化插件的翻译），下载到 HIRO_HOME/catalog，
// 不随仓库发布（AGENTS.md：不往仓库加游戏素材）：
//
//	/api/catalog、/api/catalog?lang=zh   技能卡、饮料、P 道具、回忆、偶像卡（catalog.ja.json / catalog.zh.json）
//	/api/supports                       支援卡（中日文名、类型、SP 发生率、各凸等级上限、按等级的能力、事件；supports.json）
//	/sprites/<图名>.png 或 .webp         小图，按需下载、缓存（支援卡卡面、偶像卡是 webp，其余是 png）
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
	defaultURL  = "https://gakumas.wiki" // 初星攻略站（旧地址 hiro.matto.top/gkbench 会 301 过来）
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
				raw, err := fetch(baseURL()+"/api/supports", 60*time.Second)
				if err != nil {
					return nil, err
				}
				sup, err := jsonx.DecodeObject(raw)
				if err != nil || jsonx.Str(sup, "format") != supportsFormat {
					return nil, clierr.New("bad_catalog", "支援卡资料格式不对："+baseURL()+"/api/supports", "稍后重试；一直这样请反馈")
				}
				if err := writeAtomic(filepath.Join(d, "supports.json"), raw); err != nil {
					return nil, err
				}
				counts.Set("supports", jsonx.Obj(sup, "cards").Len())
				return jsonx.NewObject().Set("langs", Langs).Set("counts", counts).Set("dir", d), nil
			},
		},
		&cli.Command{
			Name: "catalog supports", Risk: cli.Read, NoAgent: true,
			Help:    "本机资料库里的支援卡资料（中日文名、SP 发生率、能力、事件），界面的支援卡页用。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) { return LoadSupports() },
		},
		&cli.Command{
			Name: "catalog lookup", Risk: cli.Read, NoAgent: true,
			Help: "按编号一次取多条记录的名字、效果文字和图名（界面给信息页配中文名和图标用）。",
			Args: []cli.Arg{
				{Name: "--ids", Help: "编号，逗号分隔"},
				{Name: "--texts", Help: "回忆能力的日文原文，一行一条：给出 --lang 那种语言的同一条（对不上的不列）"},
				{Name: "--lang", Help: "语言", Choices: Langs, Default: "zh"},
			},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				return Lookup(strings.Split(a.Str("ids"), ","), strings.Split(a.Str("texts"), "\n"), a.Str("lang"))
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

// SpriteExts 是小图可能的格式（攻略站上支援卡卡面、偶像卡是 webp，其余是 png）。
var SpriteExts = []string{".png", ".webp"}

// SpritePath 返回小图的本机路径，没有就下载（先试 png 再试 webp，按实际格式存）。
func SpritePath(asset string) (string, error) {
	if !assetPattern.MatchString(asset) {
		return "", clierr.New("bad_args", "图名不合法："+asset, "用资料库记录里的 asset 字段")
	}
	d, err := home.Sub("catalog", "sprites")
	if err != nil {
		return "", err
	}
	for _, ext := range SpriteExts {
		if p := filepath.Join(d, asset+ext); exists(p) {
			return p, nil
		}
	}
	var last error
	for _, ext := range SpriteExts {
		data, err := fetch(baseURL()+"/sprites/"+asset+ext, 30*time.Second)
		if err != nil {
			last = err
			continue
		}
		p := filepath.Join(d, asset+ext)
		return p, writeAtomic(p, data)
	}
	return "", last
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// ---------------------------------------------------------------- 支援卡、按编号批量取

const supportsFormat = "gkbench.supports.v1"

// LoadSupports 读本机的支援卡资料（catalog sync 下载的 supports.json）；没下载报 catalog_missing。
func LoadSupports() (*jsonx.Object, error) {
	d, err := Dir()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(d, "supports.json"))
	if err != nil {
		return nil, clierr.New("catalog_missing", "支援卡资料还没下载", "运行 hiro catalog sync")
	}
	o, err := jsonx.DecodeObject(data)
	if err != nil || jsonx.Str(o, "format") != supportsFormat {
		return nil, clierr.New("bad_catalog", "支援卡资料文件坏了", "重新运行 hiro catalog sync")
	}
	return o, nil
}

// Lookup 按编号取记录的类别、名字、效果文字、稀有度和图名（查不到的编号不列）。偶像卡编号可以是 skin id。
// texts 是回忆能力的日文原文，按资料库里同一条回忆能力换成 lang 的文字（对不上的不列：多半是扫描时 OCR 读错了字）。
func Lookup(ids, texts []string, lang string) (*jsonx.Object, error) {
	cat, err := Load(lang)
	if err != nil {
		return nil, err
	}
	trans := jsonx.NewObject()
	if want := nonEmpty(texts); len(want) > 0 {
		ja, err := Load("ja")
		if err != nil {
			return nil, err
		}
		byText := map[string]string{}
		mja, mto := jsonx.Obj(ja, "memories"), jsonx.Obj(cat, "memories")
		for _, id := range mja.Keys() {
			t := jsonx.Str(jsonx.Obj(mja, id), "text")
			if _, ok := byText[t]; t != "" && !ok {
				if to := jsonx.Str(jsonx.Obj(mto, id), "text"); to != "" {
					byText[t] = to
				}
			}
		}
		for _, t := range want {
			if to, ok := byText[t]; ok {
				trans.Set(t, to)
			}
		}
	}
	out := jsonx.NewObject()
	for _, raw := range ids {
		id := strings.TrimSpace(raw)
		if id == "" || out.Has(id) {
			continue
		}
		key := strings.Replace(id, "i_card-skin-", "i_card-", 1)
		for _, k := range Kinds {
			e := jsonx.Obj(jsonx.Obj(cat, k), key)
			if e == nil {
				continue
			}
			row := brief(k, key, e)
			if k == "idols" && jsonx.Str(row, "asset") == "" {
				row.Set("asset", "idol-"+key) // 攻略站的偶像卡缩略图
			}
			out.Set(id, row)
			break
		}
	}
	return jsonx.NewObject().Set("lang", lang).Set("items", out).Set("texts", trans), nil
}

func nonEmpty(v []string) []string {
	var out []string
	for _, s := range v {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}
