// Package common 是内置插件共用的小件：资料库名字 / 小图缓存、命令解析。
package common

import (
	"context"
	"strings"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// Entry 是资料库里一条记录的简要（catalog lookup 的 items 值）。
type Entry struct {
	Kind   string `json:"kind"`
	Name   string `json:"name"`
	Rarity string `json:"rarity"`
	Asset  string `json:"asset"`
	Type   string `json:"type"`
	Attr   string `json:"attr"`
	// Text 是效果文字（中文资料库的，悬停说明用）。
	Text string `json:"text"`
}

// Both 是「中文名（日文原名）」：卡名中文优先，日文原名跟在后面（app/DESIGN.md）；两者一样或缺一个时只给一个。
func Both(zh, ja string) string {
	switch {
	case zh == "" || zh == ja:
		return ja
	case ja == "":
		return zh
	}
	return zh + "（" + ja + "）"
}

// Tip 是一张卡的悬停说明：名字（稀有度）、效果文字，再加调用方给的几行（等级、租借等）。没有资料时只用 extra。
func Tip(e *Entry, name string, extra ...string) string {
	var lines []string
	head := name
	if e != nil && e.Rarity != "" {
		head += "（" + e.Rarity + "）"
	}
	if head != "" {
		lines = append(lines, head)
	}
	if e != nil && strings.TrimSpace(e.Text) != "" {
		lines = append(lines, strings.TrimSpace(e.Text))
	}
	for _, x := range extra {
		if x != "" {
			lines = append(lines, x)
		}
	}
	return strings.Join(lines, "\n")
}

// Catalog 缓存资料库查询和小图路径：view 里同步问，没有就在后台查一次，查到后界面会重画。
type Catalog struct {
	mu       sync.Mutex
	gen      int               // Reset 时加一：之前发出去的查询回来后丢掉，不写进新缓存
	entries  map[string]*Entry // nil 值表示查过、没有
	texts    map[string]string // 回忆能力日文原文 → 所选语言（"" 表示对不上）
	sprites  map[string]string // asset → 本机路径（"" 表示取不到）
	inflight map[string]bool
}

// Shared 是内置插件共用的那一份。
var Shared = &Catalog{}

func (c *Catalog) init() {
	if c.entries == nil {
		c.entries, c.texts, c.sprites, c.inflight = map[string]*Entry{}, map[string]string{}, map[string]string{}, map[string]bool{}
	}
}

// Lookup 取一批编号的简要；还没查过的在后台查（catalog lookup --ids …，中文），这次返回已有的。
func (c *Catalog) Lookup(h plugin.Host, ids ...string) map[string]*Entry {
	c.mu.Lock()
	c.init()
	out := map[string]*Entry{}
	var miss []string
	for _, id := range ids {
		if id == "" {
			continue
		}
		if e, ok := c.entries[id]; ok {
			if e != nil {
				out[id] = e
			}
			continue
		}
		if !c.inflight["id:"+id] {
			c.inflight["id:"+id] = true
			miss = append(miss, id)
		}
	}
	gen := c.gen
	c.mu.Unlock()
	if len(miss) > 0 {
		h.Go(func() func() {
			var d struct {
				Items map[string]*Entry `json:"items"`
			}
			_ = h.Call(context.Background(), []string{"catalog", "lookup", "--ids", strings.Join(miss, ","), "--lang", "zh"}).Decode(&d)
			return func() {
				c.mu.Lock()
				defer c.mu.Unlock()
				if c.gen != gen || c.entries == nil {
					return // 中途清了缓存
				}
				for _, id := range miss {
					c.entries[id] = d.Items[id]
					delete(c.inflight, "id:"+id)
				}
			}
		})
		// 后台已经回来了（测试里同步执行）就把新查到的也带上
		c.mu.Lock()
		for _, id := range miss {
			if e := c.entries[id]; e != nil {
				out[id] = e
			}
		}
		c.mu.Unlock()
	}
	return out
}

// Get 取一条（可能为 nil，查到后重画）。
func (c *Catalog) Get(h plugin.Host, id string) *Entry { return c.Lookup(h, id)[id] }

// Name 是给人看的名字：资料库里的中文名；查不到（还没查完、新卡、编号没对上）用 fallback（多半是扫描读到的日文）。
func (c *Catalog) Name(h plugin.Host, id, fallback string) string {
	if id != "" {
		if e := c.Get(h, id); e != nil && e.Name != "" {
			return e.Name
		}
	}
	return fallback
}

// Texts 把回忆能力的日文原文换成中文（catalog lookup --texts）；对不上的原样返回。
func (c *Catalog) Texts(h plugin.Host, ja []string) []string {
	out := append([]string(nil), ja...)
	if len(ja) == 0 {
		return out
	}
	c.mu.Lock()
	c.init()
	var miss []string
	for i, t := range ja {
		if v, ok := c.texts[t]; ok {
			if v != "" {
				out[i] = v
			}
			continue
		}
		if t != "" && !c.inflight["tx:"+t] {
			c.inflight["tx:"+t] = true
			miss = append(miss, t)
		}
	}
	gen := c.gen
	c.mu.Unlock()
	if len(miss) > 0 {
		h.Go(func() func() {
			var d struct {
				Texts map[string]string `json:"texts"`
			}
			r := h.Call(context.Background(), []string{"catalog", "lookup", "--ids", "", "--texts", strings.Join(miss, "\n"), "--lang", "zh"})
			ok := r.OK && r.Decode(&d) == nil
			return func() {
				c.mu.Lock()
				defer c.mu.Unlock()
				for _, t := range miss {
					delete(c.inflight, "tx:"+t)
				}
				if !ok || c.gen != gen || c.texts == nil {
					return // 查询失败不缓存空译文，下次再查（#121 review）
				}
				for _, t := range miss {
					c.texts[t] = d.Texts[t]
				}
			}
		})
	}
	return out
}

// Sprite 是一张小图的本机路径；没有就在后台取一次（catalog sprite），这次返回 ""。
func (c *Catalog) Sprite(h plugin.Host, asset string) string {
	if asset == "" {
		return ""
	}
	c.mu.Lock()
	c.init()
	if p, ok := c.sprites[asset]; ok {
		c.mu.Unlock()
		return p
	}
	if c.inflight["sp:"+asset] {
		c.mu.Unlock()
		return ""
	}
	c.inflight["sp:"+asset] = true
	c.mu.Unlock()
	h.Go(func() func() {
		var d struct {
			Path string `json:"path"`
		}
		_ = h.Call(context.Background(), []string{"catalog", "sprite", asset}).Decode(&d)
		return func() {
			c.mu.Lock()
			defer c.mu.Unlock()
			if c.sprites == nil {
				return // Reset 过
			}
			c.sprites[asset] = d.Path
			delete(c.inflight, "sp:"+asset)
		}
	})
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.sprites[asset]
}

// Reset 清空缓存（资料库同步后）。
func (c *Catalog) Reset() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.gen++
	c.entries, c.texts, c.sprites, c.inflight = nil, nil, nil, nil
}

// SplitIdolName 把资料库的偶像卡名「【卡名】偶像名」拆成 (卡名, 偶像名)；不是这个形状时偶像名为 ""。
func SplitIdolName(name string) (title, chara string) {
	rest, ok := strings.CutPrefix(name, "【")
	if !ok {
		return "", ""
	}
	t, ch, ok := strings.Cut(rest, "】")
	if !ok || strings.TrimSpace(ch) == "" {
		return "", ""
	}
	return t, strings.TrimSpace(ch)
}
