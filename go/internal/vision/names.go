package vision

import (
	"regexp"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 按名称认卡 / 饮料 / P 道具（同 Python hiro.vision.deck 的 card_from_name、_item_from_name 一组）。

type cardHit struct {
	id      string
	upgrade int
}

type nameTables struct {
	full   map[string][]cardHit // 主数据卡名（带「+」）→ [(card_id, upgrade)]
	dash   map[string]dashEntry // 长音 / 感叹号问号统一后的卡名 → (原名, hits)；撞名的不收
	drinks map[string]string    // 饮料名 → id（同名取后一个，同 Python dict 推导式）
	pitems map[string][]string  // P 道具名 → [id]
	names  []string             // 全部名称（_near_name 用）
}

type dashEntry struct {
	name string
	hits []cardHit
}

var (
	namesOnce sync.Once
	namesTab  nameTables
)

// dashes 统一长音（RapidOCR 常把「ー」读成「一」）和半角感叹号、问号。
var dashes = strings.NewReplacer("一", "ー", "－", "ー", "-", "ー", "―", "ー", "ｰ", "ー", "!", "！", "?", "？")

func names() *nameTables {
	namesOnce.Do(func() {
		t := nameTables{full: map[string][]cardHit{}, dash: map[string]dashEntry{}, drinks: map[string]string{}, pitems: map[string][]string{}}
		var order []string
		for _, c := range master.Cards() {
			if _, ok := t.full[c.Name]; !ok {
				order = append(order, c.Name)
			}
			t.full[c.Name] = append(t.full[c.Name], cardHit{c.ID, c.UpgradeCount})
		}
		clash := map[string]bool{}
		for _, n := range order {
			key := dashes.Replace(n)
			if e, ok := t.dash[key]; ok && e.name != n {
				clash[key] = true
			}
			t.dash[key] = dashEntry{n, t.full[n]}
		}
		for k := range clash {
			delete(t.dash, k)
		}
		for _, d := range master.Drinks() {
			t.drinks[d.Name] = d.ID
		}
		for _, p := range master.PItems() {
			t.pitems[p.Name] = append(t.pitems[p.Name], p.ID)
		}
		seen := map[string]bool{}
		for _, group := range [][]string{order, keys(t.drinks), keysList(t.pitems)} {
			for _, n := range group {
				if !seen[n] {
					seen[n] = true
					t.names = append(t.names, n)
				}
			}
		}
		namesTab = t
	})
	return &namesTab
}

func keys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func keysList(m map[string][]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// CardName 是按名称认出的卡。
type CardName struct {
	CardID  string `json:"card_id"`
	Name    string `json:"name"`
	Upgrade int    `json:"upgrade"`
}

// CardFromName 卡名（OCR，带「+」）精确命中主数据 → 卡；同名多张时按 candidates（如本页卡格认出的 id）取，取不定给 nil。
// 精确没命中时把长音符号统一再查。
func CardFromName(text string, candidates map[string]bool) *CardName {
	name := pyStrip(strings.ReplaceAll(strings.ReplaceAll(text, "＋", "+"), " ", ""))
	t := names()
	hits := t.full[name]
	if len(hits) == 0 {
		if alt, ok := t.dash[dashes.Replace(name)]; ok {
			name, hits = alt.name, alt.hits
		}
	}
	if len(candidates) > 0 {
		var keep []cardHit
		for _, h := range hits {
			if candidates[h.id] {
				keep = append(keep, h)
			}
		}
		if len(keep) > 0 {
			hits = keep
		}
	}
	ids := map[string]bool{}
	for _, h := range hits {
		ids[h.id] = true
	}
	if len(ids) != 1 {
		return nil
	}
	return &CardName{CardID: hits[0].id, Name: strings.TrimRight(name, "+"), Upgrade: hits[0].upgrade}
}

// ItemName 是名称栏认出的卡 / 饮料 / P 道具（字段同 Python 的字典，按 kind 只填对应的几项）。
type ItemName struct {
	Kind     string
	Card     *CardName
	DrinkID  string
	PItemID  *string
	PItemIDs []string
	Name     string
}

// Fields 转成 Python 那边的字典。
func (it *ItemName) Fields() map[string]any {
	o := map[string]any{"kind": it.Kind}
	switch it.Kind {
	case "card":
		o["card_id"], o["name"], o["upgrade"] = it.Card.CardID, it.Card.Name, it.Card.Upgrade
	case "drink":
		o["drink_id"], o["name"] = it.DrinkID, it.Name
	case "pitem":
		o["pitem_id"], o["pitem_ids"], o["name"] = it.PItemID, it.PItemIDs, it.Name
	}
	return o
}

// itemFromName 名称栏文字 → 卡 / 饮料 / P 道具。名称前的图标常被 OCR 成一个字，精确匹配不到时去掉首字再试。
func itemFromName(text string) *ItemName {
	text = strings.ReplaceAll(strings.ReplaceAll(text, " ", ""), "＋", "+")
	t := names()
	for _, s := range []string{text, dropFirst(text)} {
		if s == "" {
			continue
		}
		if c := CardFromName(s, nil); c != nil {
			return &ItemName{Kind: "card", Card: c}
		}
		if id, ok := t.drinks[s]; ok {
			return &ItemName{Kind: "drink", DrinkID: id, Name: s}
		}
		if ids, ok := t.pitems[s]; ok {
			var one *string
			if len(ids) == 1 {
				one = &ids[0]
			}
			return &ItemName{Kind: "pitem", PItemID: one, PItemIDs: ids, Name: s}
		}
	}
	if eng, _ := ocr.Engine(); eng != "vision" { // RapidOCR 等常错一个字：只差一个字且唯一时按主数据名称再认一次
		for _, s := range []string{text, dropFirst(text)} {
			if near := nearName(s); near != "" {
				return itemFromName(near)
			}
		}
	}
	return nil
}

// dropFirst 去掉第一个字（Python 的 text[1:]）。
func dropFirst(s string) string {
	if s == "" {
		return ""
	}
	_, n := utf8.DecodeRuneInString(s)
	return s[n:]
}

// nearName 主数据里与 text 等长（≥3 字）、只差一个字的名称；恰好一个才返回。
func nearName(text string) string {
	tr := []rune(text)
	if len(tr) < 3 {
		return ""
	}
	found := ""
	n := 0
	for _, name := range names().names {
		nr := []rune(name)
		if len(nr) != len(tr) {
			continue
		}
		d := 0
		for i := range nr {
			if nr[i] != tr[i] {
				d++
			}
		}
		if d == 1 {
			found, n = name, n+1
		}
	}
	if n == 1 {
		return found
	}
	return ""
}

// jaRe 假名 / 汉字。
var jaRe = regexp.MustCompile(`[\x{3040}-\x{30ff}\x{3400}-\x{9fff}]`)

// strayFragment 名称栏里不当文字的碎片：不超过 3 个字、没有假名 / 汉字（被遮挡的 logo 角、图标边）。
func strayFragment(text string) bool {
	t := pyStrip(text)
	return utf8.RuneCountInString(t) <= 3 && !jaRe.MatchString(t)
}
