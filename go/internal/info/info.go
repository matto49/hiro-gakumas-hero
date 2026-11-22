// Package info 是 `hiro info …`：查本机信息库里的账号信息（最新一次扫描），导入扫描工具的输出。同 Python 版 hiro.cli.cmd_info。
package info

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/store"
)

// scanHint 是没扫描过时的提示。
var scanHint = map[string]string{
	"account":       "还没读过首页数值：游戏停在首页时运行 hiro run start scan-account",
	"idols":         "还没扫描偶像卡：游戏停在首页时运行 hiro run start scan-idols",
	"supports":      "还没扫描支援卡：运行 hiro run start scan-supports",
	"memories":      "还没扫描回忆：运行 hiro run start scan-memories",
	"affection":     "还没扫描好感度（这一项的扫描还没做，先在游戏里看）",
	"game_settings": "还没检查游戏设置：运行 hiro run start check-settings",
}

// ImportKinds 是能导入的类别（顺序同 Python NORMALIZE）。
var ImportKinds = []string{"account", "idols", "supports", "memories", "game_settings"}

// ---------------------------------------------------------------- 扫描输出 → 每条一行

// rarity：SupportCardRarity_Ssr / IdolCardRarity_Ssr / ssr → SSR。
func rarity(raw any) any {
	if !jsonx.Truthy(raw) {
		return nil
	}
	s := pyStr(raw)
	if i := strings.LastIndex(s, "_"); i >= 0 {
		s = s[i+1:]
	}
	return strings.ToUpper(s)
}

func get(o *jsonx.Object, k string) any { v, _ := o.Get(k); return v }

// or 同 Python 的 a or b。
func or(a, b any) any {
	if jsonx.Truthy(a) {
		return a
	}
	return b
}

func normalizeSupports(inv *jsonx.Object) []any {
	out := []any{}
	cards := jsonx.Obj(inv, "cards")
	for _, cid := range cards.Keys() {
		c := jsonx.Obj(cards, cid)
		if c == nil {
			c = jsonx.NewObject()
		}
		out = append(out, jsonx.NewObject().Set("id", or(get(c, "support_card_id"), cid)).Set("name", get(c, "name")).
			Set("rarity", rarity(get(c, "rarity"))).Set("attr", get(c, "attr")).Set("type", get(c, "type")).
			Set("level", get(c, "level")).Set("breaks", get(c, "breaks")))
	}
	return out
}

// normalizeIdols：偶像卡扫描（hiro.produce.idol_scan）→ 每张一行。idol_card_id 扫描时按主数据对上，对不上（新卡）留空。
func normalizeIdols(inv *jsonx.Object) []any {
	out := []any{}
	for _, v := range jsonx.List(inv, "idols") {
		c, _ := v.(*jsonx.Object)
		if c == nil {
			continue
		}
		title, chara := jsonx.Str(c, "title"), jsonx.Str(c, "character")
		out = append(out, jsonx.NewObject().Set("id", get(c, "idol_card_id")).Set("name", "【"+title+"】"+chara).
			Set("title", title).Set("idol", chara).Set("rarity", rarity(get(c, "rarity"))).Set("plan", get(c, "plan")).
			Set("effect", get(c, "effect")))
	}
	return out
}

func normalizeMemories(inv *jsonx.Object) []any {
	out := []any{}
	for _, v := range jsonx.List(inv, "entries") {
		e, _ := v.(*jsonx.Object)
		if e == nil {
			continue
		}
		card := jsonx.Obj(e, "card")
		if card == nil {
			card = jsonx.NewObject()
		}
		abilities := []any{}
		for _, av := range jsonx.List(e, "abilities") {
			a, _ := av.(*jsonx.Object)
			if a == nil || jsonx.Truthy(get(a, "disabled")) {
				continue
			}
			abilities = append(abilities, get(a, "text"))
		}
		out = append(out, jsonx.NewObject().Set("card_id", get(card, "card_id")).Set("card", or(get(card, "name"), get(e, "card_text"))).
			Set("timing", get(e, "timing")).Set("timing_text", get(e, "timing_text")).Set("stats", get(e, "stats")).
			Set("abilities", abilities).Set("page", get(e, "page")).Set("slot", get(e, "slot")))
	}
	return out
}

func normalizeGameSettings(inv *jsonx.Object) []any {
	out := []any{}
	items := jsonx.Obj(inv, "items")
	for _, k := range items.Keys() {
		out = append(out, jsonx.NewObject().Set("item", k).Set("state", get(items, k)))
	}
	return out
}

func normalizeAccount(inv *jsonx.Object) []any {
	o := jsonx.NewObject()
	for _, k := range []string{"plv", "ap", "ap_max", "jewel"} {
		o.Set(k, get(inv, k))
	}
	return []any{o}
}

var normalize = map[string]func(*jsonx.Object) []any{
	"account": normalizeAccount, "idols": normalizeIdols, "supports": normalizeSupports, "memories": normalizeMemories, "game_settings": normalizeGameSettings,
}

// ImportInventory 把扫描工具的输出存进信息库，返回 {kind, snapshot, count}。扫描任务结束时由服务调用。
func ImportInventory(kind string, inv *jsonx.Object, source string) (*jsonx.Object, error) {
	norm, ok := normalize[kind]
	if !ok {
		return nil, clierr.Usage("bad_args", "不能导入这一类："+kind, "可选："+strings.Join(ImportKinds, "、"))
	}
	rows := norm(inv)
	var complete *bool
	if v, ok := inv.Get("complete"); ok && v != nil {
		b := jsonx.Truthy(v)
		complete = &b
	}
	if kind == "game_settings" {
		all := true
		for _, r := range rows {
			if jsonx.IsNull(r.(*jsonx.Object), "state") {
				all = false
			}
		}
		complete = &all
	}
	if complete == nil && kind == "supports" {
		b := jsonx.Str(inv, "source") == "list" // 编成页扫描只含编成里出现过的卡
		complete = &b
	}
	scanned := ""
	if v := get(inv, "scanned_at"); jsonx.Truthy(v) {
		scanned = pyStr(v)
	} else if n, ok := get(inv, "generated").(json.Number); ok { // support_inventory：epoch 秒
		if f, err := n.Float64(); err == nil {
			scanned = time.Unix(int64(f), 0).Format("2006-01-02 15:04:05")
		}
	}
	st, err := store.OpenHome()
	if err != nil {
		return nil, err
	}
	defer st.Close()
	sid, err := st.SaveSnapshot(kind, rows, store.Snapshot{Source: source, ScannedAt: scanned, Complete: complete, Raw: inv})
	if err != nil {
		return nil, err
	}
	return jsonx.NewObject().Set("kind", kind).Set("snapshot", sid).Set("count", len(rows)), nil
}

// ---------------------------------------------------------------- 查询

var countKeys = [][2]string{{"rarity", "by_rarity"}, {"attr", "by_attr"}, {"timing", "by_timing"}, {"idol", "by_idol"}}

// Counts 是这次筛出来的条数，按稀有度 / 属性 / 获得时机 / 偶像分组（只统计有这个字段的类别，多的在前）。
func Counts(items []any) *jsonx.Object {
	out := jsonx.NewObject().Set("total", len(items))
	for _, kn := range countKeys {
		key, name := kn[0], kn[1]
		present := false
		for _, it := range items {
			if o, _ := it.(*jsonx.Object); o.Has(key) {
				present = true
				break
			}
		}
		if !present {
			continue
		}
		var order []string
		n := map[string]int{}
		for _, it := range items {
			o, _ := it.(*jsonx.Object)
			k := "未知"
			if v := get(o, key); v != nil {
				k = pyStr(v)
			}
			if _, seen := n[k]; !seen {
				order = append(order, k)
			}
			n[k]++
		}
		sort.SliceStable(order, func(i, j int) bool { return n[order[i]] > n[order[j]] })
		c := jsonx.NewObject()
		for _, k := range order {
			c.Set(k, n[k])
		}
		out.Set(name, c)
	}
	return out
}

// pyStr 同 Python 的 str(v)（用于比较和分组）。
func pyStr(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case json.Number:
		return t.String()
	case bool:
		if t {
			return "True"
		}
		return "False"
	case int:
		return strconv.Itoa(t)
	case int64:
		return strconv.FormatInt(t, 10)
	case nil:
		return "None"
	}
	b, _ := jsonx.Marshal(v)
	return string(b)
}

func match(v any, want string) bool {
	return want == "" || (v != nil && strings.EqualFold(pyStr(v), want))
}

func contains(v any, want string) bool {
	return want == "" || (v != nil && strings.Contains(strings.ToLower(pyStr(v)), strings.ToLower(want)))
}

func listing(kind string, keep func(*jsonx.Object) bool) (*jsonx.Object, error) {
	st, err := store.OpenHome()
	if err != nil {
		return nil, err
	}
	defer st.Close()
	meta, rows, err := st.Latest(kind)
	if err != nil {
		return nil, err
	}
	if meta == nil {
		return jsonx.NewObject().Set("kind", kind).Set("scanned_at", nil).Set("complete", nil).Set("items", []any{}).
			Set("hint", scanHint[kind]), nil
	}
	items := []any{}
	for _, r := range rows {
		o, _ := r.(*jsonx.Object)
		if keep == nil || keep(o) {
			items = append(items, r)
		}
	}
	// counts 放在 items 前面：模型按它报数量，不用自己数列表；结果太长被截断时统计也还在
	return jsonx.NewObject().Set("kind", kind).Set("scanned_at", meta.ScannedAt).Set("complete", meta.Complete).
		Set("source", meta.Source).Set("total", meta.Count).Set("counts", Counts(items)).Set("items", items), nil
}

// ---------------------------------------------------------------- 命令

// Register 登记 `hiro info …` 命令。
func Register(r *cli.Registry) {
	r.MustRegister(
		&cli.Command{
			Name: "info summary", Risk: cli.Read,
			Help: "账号概况：首页数值（PLv / AP / ジュエル）和每类信息的数量、上次扫描时间。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) {
				st, err := store.OpenHome()
				if err != nil {
					return nil, err
				}
				defer st.Close()
				kinds := jsonx.NewObject()
				for _, k := range store.Kinds {
					meta, _, err := st.Latest(k)
					if err != nil {
						return nil, err
					}
					o := jsonx.NewObject().Set("count", 0).Set("scanned_at", nil).Set("complete", nil)
					if meta != nil {
						o.Set("count", meta.Count).Set("scanned_at", meta.ScannedAt).Set("complete", meta.Complete)
					}
					kinds.Set(k, o)
				}
				_, rows, err := st.Latest("account")
				if err != nil {
					return nil, err
				}
				var account any
				if len(rows) > 0 {
					account = rows[0]
				}
				return jsonx.NewObject().Set("account", account).Set("kinds", kinds), nil
			},
		},
		&cli.Command{
			Name: "info supports", Risk: cli.Read,
			Help: "支援卡库存（最新一次扫描）：名字、稀有度、属性、等级、突破。",
			Args: []cli.Arg{
				{Name: "--rarity", Help: "只看这个稀有度", Choices: []string{"SSR", "SR", "R"}},
				{Name: "--attr", Help: "只看这个属性", Choices: []string{"Vo", "Da", "Vi", "Assist"}},
				{Name: "--name", Help: "名字包含这几个字"},
			},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				return listing("supports", func(o *jsonx.Object) bool {
					return match(get(o, "rarity"), a.Str("rarity")) && match(get(o, "attr"), a.Str("attr")) && contains(get(o, "name"), a.Str("name"))
				})
			},
		},
		&cli.Command{
			Name: "info memories", Risk: cli.Read,
			Help: "回忆库存（最新一次扫描）：回忆技能卡、获得时机、参数、能力。",
			Args: []cli.Arg{
				{Name: "--timing", Help: "获得时机", Choices: []string{"start", "mid", "final"}},
				{Name: "--card", Help: "回忆技能卡名字包含这几个字"},
			},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				return listing("memories", func(o *jsonx.Object) bool {
					return match(get(o, "timing"), a.Str("timing")) && contains(get(o, "card"), a.Str("card"))
				})
			},
		},
		&cli.Command{
			Name: "info idols", Risk: cli.Read,
			Help: "偶像卡（最新一次扫描）：卡名、偶像、稀有度、突破、潜力。",
			Args: []cli.Arg{
				{Name: "--idol", Help: "偶像名字包含这几个字"},
				{Name: "--rarity", Help: "只看这个稀有度", Choices: []string{"SSR", "SR", "R"}},
			},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				return listing("idols", func(o *jsonx.Object) bool {
					return contains(get(o, "idol"), a.Str("idol")) && match(get(o, "rarity"), a.Str("rarity"))
				})
			},
		},
		&cli.Command{
			Name: "info affection", Risk: cli.Read,
			Help:    "每位偶像的好感度（親愛度，最新一次扫描）。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) { return listing("affection", nil) },
		},
		&cli.Command{
			Name: "info game-settings", Risk: cli.Read,
			Help: "游戏设置里快进 / 跳过演出那 5 项开没开（最近一次检查的结果，ON / OFF / null 读不到）。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) {
				out, err := listing("game_settings", nil)
				if err != nil {
					return nil, err
				}
				st, err := store.OpenHome()
				if err != nil {
					return nil, err
				}
				defer st.Close()
				raw, err := st.LatestRaw("game_settings")
				if err != nil {
					return nil, err
				}
				ro, _ := raw.(*jsonx.Object)
				return out.Set("verdict", get(ro, "verdict")), nil
			},
		},
		&cli.Command{
			Name: "info import", Risk: cli.Read, NoAgent: true,
			Help: "把扫描工具的输出 JSON 导入信息库（扫描任务结束时自动调用）。",
			Args: []cli.Arg{
				{Name: "--kind", Help: "类别", Choices: ImportKinds, Required: true},
				{Name: "file", Help: "扫描输出的 JSON 文件"},
			},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				p := a.Str("file")
				data, err := os.ReadFile(p)
				var inv *jsonx.Object
				if err == nil {
					inv, err = jsonx.DecodeObject(data)
				}
				if err != nil {
					return nil, clierr.New("bad_file", fmt.Sprintf("读不了 %s：%v", p, err), "确认文件存在、是扫描工具写出的 JSON")
				}
				return ImportInventory(a.Str("kind"), inv, "import:"+filepath.Base(p))
			},
		},
	)
}
