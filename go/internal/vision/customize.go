package vision

import (
	"encoding/json"
	"sort"
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 特別指導（技能卡定制）页 SPECIAL_GUIDANCE（同 Python hiro.vision.customize），以及培育页头部体力 / P 点（pages.read_hud_resources）。

// ---------------------------------------------------------------- 头部体力 / P 点

// vote 三种读法至少两票一致才用；只有一种读法且读到了就用它。
func vote(values ...*int) any {
	counts := map[int]int{}
	var order []int
	n := 0
	for _, v := range values {
		if v == nil {
			continue
		}
		n++
		if _, ok := counts[*v]; !ok {
			order = append(order, *v)
		}
		counts[*v]++
	}
	if n == 0 {
		return nil
	}
	best := order[0]
	for _, v := range order[1:] {
		if counts[v] > counts[best] {
			best = v
		}
	}
	if counts[best] >= 2 || (n == 1 && len(values) == 1) {
		return best
	}
	return nil
}

func fontInt(b *img.BGR, box ocr.Box) *int {
	t, _ := ReadText(b, box, false)
	if isASCIIDigits(t) {
		v := mustAtoi(t)
		return &v
	}
	return nil
}

func ddddPtr(b *img.BGR, box ocr.Box) (*int, error) {
	v, ok, err := ReadDddd(b, box)
	if err != nil || !ok {
		return nil, err
	}
	return &v, nil
}

// ReadHudResources 培育页头部的体力 / P 点 → {stamina, max_stamina, produce_point}（OCR / 游戏字体 / ddddocr 投票）。
func ReadHudResources(b *img.BGR) (map[string]any, error) {
	pt, err := ocr.OCR(crop(b, pointsBox), 4, ocr.En)
	if err != nil {
		return nil, err
	}
	var ptv *int
	if m := digitRun.FindString(ocr.Joined(pt, "")); m != "" {
		v := mustAtoi(m)
		ptv = &v
	}
	cur, cp, ok, err := ReadStamina(b)
	if err != nil {
		return nil, err
	}
	var stCur, stCap *int
	if ok {
		stCur, stCap = &cur, &cp
	}
	sb := staminaBox
	hpBox, maxBox := ocr.Box{X1: sb.X1, Y1: sb.Y1, X2: 380, Y2: sb.Y2}, ocr.Box{X1: 382, Y1: sb.Y1, X2: sb.X2, Y2: sb.Y2}
	d1, err := ddddPtr(b, hpBox)
	if err != nil {
		return nil, err
	}
	d2, err := ddddPtr(b, maxBox)
	if err != nil {
		return nil, err
	}
	d3, err := ddddPtr(b, pointsBox)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"stamina": vote(stCur, fontInt(b, hpBox), d1), "max_stamina": vote(stCap, d2),
		"produce_point": vote(ptv, fontInt(b, pointsBox), d3),
	}, nil
}

// ---------------------------------------------------------------- 特別指導

var (
	guidanceGrid   = ocr.Box{X1: 0, Y1: 600, X2: 720, Y2: 1000}
	guidanceScroll = ocr.Box{X1: 0, Y1: 625, X2: 720, Y2: 1000}
	guidanceLabel  = ocr.Box{X1: 20, Y1: 20, X2: 210, Y2: 80}
	menuCols       = [][2]int{{30, 250}, {250, 470}, {470, 690}}
	smallKana      = strings.NewReplacer("ァ", "ア", "ィ", "イ", "ゥ", "ウ", "ェ", "エ", "ォ", "オ", "ャ", "ヤ", "ュ", "ユ", "ョ", "ヨ", "ッ", "ツ", "ヮ", "ワ")
	foldKanaRe     = pyRe(`[ー一－\-\s]`)
	menuLeftRe     = pyRe(`あと(\d+)回`)
	menuPriceRe    = pyRe(`^\d{2,3}$`)
	menuNameSkip   = pyRe(`あと\d+回|上限|^\d+$|^P$`)
	menuTotalRe    = pyRe(`合計あと(\d+)回`)
	cardsLeftRe    = pyRe(`あと(\d+)枚`)
)

const (
	guidanceScrollX = 702
	cursorMinPx     = 500
)

// Offer 是一个定制项（主数据或画面上读到的）。
type Offer struct {
	ID        *string `json:"id"`
	ItemIndex int     `json:"item_index"`
	Price     *int    `json:"price"`
	Levels    []int   `json:"levels"`
	Available *bool   `json:"available"`
	Source    string  `json:"source"`
}

// MarshalJSON 画面上读到的定制项（source=screen）没有 levels 键（同 Python）。
func (o Offer) MarshalJSON() ([]byte, error) {
	m := map[string]any{"id": o.ID, "item_index": o.ItemIndex, "price": o.Price, "available": o.Available, "source": o.Source}
	if o.Source == "master" {
		m["levels"] = o.Levels
	}
	return json.Marshal(m)
}

// MasterOffers 主数据里这张卡（这个强化等级）的定制项。
func MasterOffers(cardID *string, upgrade *int) []Offer {
	out := []Offer{}
	if cardID == nil || *cardID == "" || upgrade == nil {
		return out
	}
	levels := master.CustomizeLevels()
	for k, cid := range master.CardCustomizeIDs(*cardID, *upgrade) {
		rows := append([]master.CustomizeLevel(nil), levels[cid]...)
		sort.SliceStable(rows, func(i, j int) bool { return rows[i].Count < rows[j].Count })
		o := Offer{ID: sptr(cid), ItemIndex: k, Levels: []int{}, Source: "master"}
		if len(rows) > 0 {
			p := rows[0].Price
			o.Price = &p
		}
		for _, r := range rows {
			o.Levels = append(o.Levels, r.Price)
		}
		out = append(out, o)
	}
	return out
}

// IsSpecialGuidance 左上角行动标签「特別指導」，且画面上有「カスタマイズ」字样。
func IsSpecialGuidance(boxes []TextBox) bool {
	r := guidanceLabel
	label, cust := false, false
	for _, tb := range boxes {
		if strings.Contains(tb.Text, "特別指導") && r.X1 <= cx(tb.Box) && cx(tb.Box) <= r.X2 && r.Y1 <= cy(tb.Box) && cy(tb.Box) <= r.Y2 {
			label = true
		}
		if strings.Contains(tb.Text, "カスタマイズ") {
			cust = true
		}
	}
	return label && cust
}

func orangeGuide(p []byte) bool { return p[0] >= 8 && p[0] <= 22 && p[1] >= 150 && p[2] >= 200 }

// cornerPx 矩形四角外侧的橙色角框像素数。
func cornerPx(orange *img.Gray, x1, y1, x2, y2 int) int {
	const pad, arm = 14, 30
	total := 0
	for _, c := range [][2]int{{x1, y1}, {x2, y1}, {x1, y2}, {x2, y2}} {
		xa, xb := max(0, min(c[0]-pad, c[0]+pad-arm)), min(orange.W, max(c[0]+pad, c[0]-pad+arm))
		ya, yb := max(0, min(c[1]-pad, c[1]+pad-arm)), min(orange.H, max(c[1]+pad, c[1]-pad+arm))
		total += orange.Slice(xa, ya, xb, yb).CountNonZero()
	}
	return total
}

func cursorOf(grid []GridCard, orange *img.Gray, sel *CardName, ignoreUpgrade bool) *int {
	type hit struct {
		px int
		c  GridCard
	}
	var hits []hit
	for _, c := range grid {
		x, y, w, h := c.Rect[0], c.Rect[1], c.Rect[2], c.Rect[3]
		if px := cornerPx(orange, x, y, x+w, y+h); px >= cursorMinPx {
			hits = append(hits, hit{px, c})
		}
	}
	if len(hits) == 0 {
		return nil
	}
	pick := func(hs []hit) *int {
		if len(hs) == 0 {
			return nil
		}
		best := hs[0]
		for _, h := range hs[1:] {
			if h.px > best.px {
				best = h
			}
		}
		i := best.c.Index
		return &i
	}
	if sel != nil && sel.CardID != "" {
		var same []hit
		for _, h := range hits {
			if h.c.CardID != nil && *h.c.CardID == sel.CardID && (ignoreUpgrade || h.c.Upgrade == nil || *h.c.Upgrade == sel.Upgrade) {
				same = append(same, h)
			}
		}
		return pick(same)
	}
	return pick(hits)
}

func foldKana(s string) string { return foldKanaRe.ReplaceAllString(smallKana.Replace(s), "") }

// MenuItem 是「カスタマイズメニュー」的一块。
type MenuItem struct {
	ID        *string
	ItemIndex int
	Name      *string
	Price     *int
	Left      *int
	Maxed     bool
	Available *bool
	Selected  bool
	Center    [2]int
	hasID     bool
	cornerPx  int
}

// MarshalJSON 有详情卡时才带 id 键（同 Python）。
func (it *MenuItem) MarshalJSON() ([]byte, error) {
	m := map[string]any{
		"item_index": it.ItemIndex, "name": it.Name, "price": it.Price, "left": it.Left, "maxed": it.Maxed,
		"available": it.Available, "selected": it.Selected, "center": it.Center,
	}
	if it.hasID {
		m["id"] = it.ID
	}
	return json.Marshal(m)
}

// Menu 是「カスタマイズメニュー」。
type Menu struct {
	Items     []*MenuItem `json:"items"`
	TotalLeft *int        `json:"total_left"`
	Y         int         `json:"y"`
}

// ReadMenu 「カスタマイズメニュー」；没有菜单时 nil。
func ReadMenu(b *img.BGR, boxes []TextBox) *Menu {
	var hdr *ocr.Box
	for i, tb := range boxes {
		if strings.Contains(tb.Text, "カスタマイズメニュー") {
			hdr = &boxes[i].Box
			break
		}
	}
	if hdr == nil {
		if eng, _ := ocr.Engine(); eng != "vision" {
			want := foldKana("カスタマイズメニュー")
			for i, tb := range boxes {
				if containsNear(strings.ReplaceAll(tb.Text, " ", ""), "カスタマイズメニュー") || strings.Contains(foldKana(tb.Text), want) {
					hdr = &boxes[i].Box
					break
				}
			}
		}
	}
	if hdr == nil {
		return nil
	}
	hy := cy(*hdr)
	orange := maskOf(cv.HSV(b), orangeGuide)
	m := &Menu{Items: []*MenuItem{}, Y: hy}
	for _, xr := range menuCols {
		var col []TextBox
		for _, tb := range boxes {
			if c := cx(tb.Box); xr[0] <= c && c < xr[1] && hy+10 <= cy(tb.Box) && cy(tb.Box) <= hy+165 {
				col = append(col, TextBox{strings.ReplaceAll(tb.Text, " ", ""), tb.Box})
			}
		}
		sort.SliceStable(col, func(i, j int) bool {
			if cy(col[i].Box) != cy(col[j].Box) {
				return cy(col[i].Box) < cy(col[j].Box)
			}
			return col[i].Box.X1 < col[j].Box.X1
		})
		if len(col) == 0 {
			continue
		}
		it := &MenuItem{ItemIndex: len(m.Items), Center: [2]int{floorDiv(xr[0]+xr[1], 2), hy + 75}}
		var name []string
		for _, tb := range col {
			if it.Left == nil {
				if mm := menuLeftRe.FindStringSubmatch(tb.Text); mm != nil {
					v := mustAtoi(mm[1])
					it.Left = &v
				}
			}
			it.Maxed = it.Maxed || strings.Contains(tb.Text, "上限")
			if it.Price == nil && menuPriceRe.MatchString(tb.Text) && cy(tb.Box) >= hy+85 {
				v := mustAtoi(tb.Text)
				it.Price = &v
			}
			if !menuNameSkip.MatchString(tb.Text) && cy(tb.Box) < hy+100 {
				name = append(name, tb.Text)
			}
		}
		if s := strings.Join(name, ""); s != "" {
			it.Name = &s
		}
		if it.Name == nil && it.Price == nil {
			continue
		}
		if it.Maxed || it.Left != nil || it.Price != nil {
			a := !it.Maxed
			it.Available = &a
		}
		it.cornerPx = cornerPx(orange, xr[0]+8, hy+22, xr[1]-8, hy+140)
		m.Items = append(m.Items, it)
	}
	var best *MenuItem
	for _, it := range m.Items {
		if best == nil || it.cornerPx > best.cornerPx {
			best = it
		}
	}
	for _, it := range m.Items {
		it.Selected = it == best && it.cornerPx >= 400
	}
	for _, tb := range boxes {
		if mm := menuTotalRe.FindStringSubmatch(strings.ReplaceAll(tb.Text, " ", "")); mm != nil {
			v := mustAtoi(mm[1])
			m.TotalLeft = &v
			break
		}
	}
	return m
}

// GuidanceCard 是选卡画面的一格。
type GuidanceCard struct {
	Index          int     `json:"index"`
	Row            int     `json:"row"`
	Col            int     `json:"col"`
	CardID         *string `json:"card_id"`
	Name           *string `json:"name"`
	Upgrade        *int    `json:"upgrade"`
	Customized     *bool   `json:"customized"`
	Available      bool    `json:"available"`
	Center         [2]int  `json:"center"`
	Customizations []Offer `json:"customizations"`
}

// ReadSpecialGuidance 特別指導页 → {stamina, max_stamina, produce_point, view, cards, selected, selected_card, menu, cards_left, scrollable, at_top, complete}。
func ReadSpecialGuidance(b *img.BGR, boxes []TextBox) (map[string]any, error) {
	if boxes == nil {
		var err error
		if boxes, err = pageTexts(b); err != nil {
			return nil, err
		}
	}
	view := "select"
	for _, tb := range boxes {
		if strings.Contains(tb.Text, "一覧に戻る") {
			view = "menu"
			break
		}
	}
	var parts []string
	for _, tb := range boxes {
		if c := cy(tb.Box); 165 <= c && c <= 220 && tb.Box.X1 >= 180 {
			parts = append(parts, tb.Text)
		}
	}
	name := strings.ReplaceAll(strings.ReplaceAll(strings.Join(parts, ""), "＋", "+"), " ", "")
	var sel *CardName
	if name != "" {
		sel = CardFromName(name, nil)
	}
	menu := ReadMenu(b, boxes)
	eng, _ := ocr.Engine()
	loose := eng != "vision"
	if loose && menu != nil && sel != nil && len(menu.Items) > 0 {
		cid, up, n := sel.CardID, sel.Upgrade, len(menu.Items)
		if len(MasterOffers(&cid, &up)) != n {
			for u := up + 1; u < up+3; u++ {
				if len(MasterOffers(&cid, &u)) == n {
					up = u
					break
				}
			}
			s := *sel
			s.Upgrade = up
			sel = &s
		}
	}
	if menu != nil && sel != nil {
		offers := MasterOffers(&sel.CardID, &sel.Upgrade)
		for _, it := range menu.Items {
			it.hasID = true
			if len(offers) == len(menu.Items) {
				it.ID = offers[it.ItemIndex].ID
			}
		}
	}
	var cards any
	var selected *int
	if view == "select" {
		orange := maskOf(cv.HSV(b), orangeGuide)
		grid := ReadCardGrid(b, guidanceGrid, "guidance")
		selected = cursorOf(grid, orange, sel, false)
		if selected == nil && loose && sel != nil {
			selected = cursorOf(grid, orange, sel, true)
			for _, c := range grid {
				if selected != nil && c.Index == *selected && c.Upgrade != nil {
					s := *sel
					s.Upgrade = *c.Upgrade
					sel = &s
				}
			}
		}
		list := []GuidanceCard{}
		for _, c := range grid {
			gc := GuidanceCard{
				Index: c.Index, Row: c.Row, Col: c.Col, CardID: c.CardID, Name: c.Name, Upgrade: c.Upgrade, Customized: c.Customized,
				Available: !c.Dimmed, Center: c.Center,
			}
			same := sel != nil && selected != nil && *selected == c.Index && c.CardID != nil && sel.CardID == *c.CardID
			switch {
			case same && menu != nil:
				gc.Customizations = []Offer{}
				for _, it := range menu.Items {
					gc.Customizations = append(gc.Customizations, Offer{ID: it.ID, ItemIndex: it.ItemIndex, Price: it.Price, Available: it.Available, Source: "screen"})
				}
			case c.Dimmed:
				gc.Customizations = []Offer{}
			default:
				gc.Customizations = MasterOffers(c.CardID, c.Upgrade)
			}
			list = append(list, gc)
		}
		cards = list
	}
	var left any
	for _, tb := range boxes {
		if m := cardsLeftRe.FindStringSubmatch(tb.Text); m != nil {
			left = mustAtoi(m[1])
			break
		}
	}
	scroll := map[string]any{"scrollable": nil, "at_top": nil}
	if view == "select" {
		scroll = DialogScroll(b, guidanceScroll, guidanceScrollX)
	}
	out, err := ReadHudResources(b)
	if err != nil {
		return nil, err
	}
	var complete any
	if view == "select" {
		complete = !scroll["scrollable"].(bool)
	}
	var selAny any
	if sel != nil {
		selAny = sel
	}
	var menuAny any
	if menu != nil {
		menuAny = menu
	}
	var selIdx any
	if selected != nil {
		selIdx = *selected
	}
	for k, v := range map[string]any{
		"view": view, "cards": cards, "selected": selIdx, "selected_card": selAny, "menu": menuAny, "cards_left": left,
		"scrollable": scroll["scrollable"], "at_top": scroll["at_top"], "complete": complete,
	} {
		out[k] = v
	}
	return out, nil
}
