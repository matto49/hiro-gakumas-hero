package vision

import (
	"regexp"
	"sort"
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 偶像之路的非考试页面（同 Python hiro.vision.tower 后半）：判据都是整帧 OCR 的固定字样，字段只读画面上的，读不到不写。

// find 第一段包含 word、中心 y 在 [y1, y2] 的文字框（不做 OCR 容错，同 Python tower._find）。
func find(boxes []TextBox, word string, y1, y2 int) *ocr.Box {
	for i, tb := range boxes {
		if strings.Contains(tb.Text, word) && y1 <= cy(tb.Box) && cy(tb.Box) <= y2 {
			return &boxes[i].Box
		}
	}
	return nil
}

func findAny(boxes []TextBox, word string) *ocr.Box { return find(boxes, word, 0, 1280) }

func center(b ocr.Box) []int { return []int{cx(b), cy(b)} }

var (
	intCommaRe  = pyRe(`\d[\d,]*`)
	progRe      = pyRe(`^\d+/\d+$`)
	stageRe     = pyRe(`^STAGE(\d+)$`)
	anyDigitRe  = pyRe(`\d`)
	towerPctRe  = pyRe(`^\D?(\d{2,5})%$`)
	rating3to6  = pyRe(`^\d{3,6}$`)
	pageRe      = regexp.MustCompile(`^([12])/2$`)
	ptRe        = pyRe(`^(\d{1,3}(?:,\d{3})+|\d+)[Pp][tTlI1]?$`)
	rankLabelRe = pyRe(`^([1-6IlS])(st|nd|rd|th)$`)
	rankPosRe   = pyRe(`(\d)位`)
	starX       = []int{250, 360, 470}
	nodeStarX   = []int{382, 462, 540}
	towerLayer  = ocr.Box{X1: 280, Y1: 385, X2: 440, Y2: 470}
	retryWords  = []string{"ジュエル", "アイテム", "チケット", "回数"}
)

func commaInt(text string) (int, bool) {
	m := intCommaRe.FindString(text)
	if m == "" {
		return 0, false
	}
	return mustAtoi(strings.ReplaceAll(m, ",", "")), true
}

func listFields(boxes []TextBox) map[string]any {
	type pb struct {
		t string
		b ocr.Box
	}
	var progs []pb
	for _, tb := range boxes {
		if progRe.MatchString(tb.Text) && cy(tb.Box) > 600 {
			progs = append(progs, pb{tb.Text, tb.Box})
		}
	}
	sort.SliceStable(progs, func(i, j int) bool {
		a, b := floorDiv(cy(progs[i].b), 40), floorDiv(cy(progs[j].b), 40)
		if a != b {
			return a < b
		}
		return cx(progs[i].b) < cx(progs[j].b)
	})
	used := map[int]bool{}
	idols := []map[string]any{}
	for _, p := range progs {
		px, py := cx(p.b), cy(p.b)
		name := -1
		for i, tb := range boxes {
			if abs(cx(tb.Box)-px) < 90 && py-cy(tb.Box) >= 30 && py-cy(tb.Box) <= 100 && len([]rune(tb.Text)) >= 2 && !anyDigitRe.MatchString(tb.Text) {
				name = i
				break
			}
		}
		st := -1
		for i, tb := range boxes {
			if stageRe.MatchString(tb.Text) && abs(cx(tb.Box)-px) < 110 && py-cy(tb.Box) >= 230 && py-cy(tb.Box) <= 320 {
				st = i
				break
			}
		}
		if st >= 0 {
			used[st] = true
		}
		c := []int{px, py - 60}
		switch {
		case name >= 0:
			c = center(boxes[name].Box)
		case st >= 0:
			c = center(boxes[st].Box)
		}
		d := map[string]any{"stage": nil, "name": nil, "progress": p.t, "center": c}
		if st >= 0 {
			d["stage"] = mustAtoi(boxes[st].Text[5:])
		}
		if name >= 0 {
			d["name"] = boxes[name].Text
		}
		idols = append(idols, d)
	}
	for i, tb := range boxes {
		if m := stageRe.FindStringSubmatch(tb.Text); m != nil && !used[i] {
			idols = append(idols, map[string]any{"stage": mustAtoi(m[1]), "name": nil, "progress": nil, "center": center(tb.Box)})
		}
	}
	return map[string]any{"idols": idols}
}

func formationFields(b *img.BGR, boxes []TextBox) map[string]any {
	out := map[string]any{}
	for _, tb := range boxes {
		if stageRe.MatchString(tb.Text) {
			out["layer"] = mustAtoi(tb.Text[5:])
			break
		}
	}
	if tl := find(boxes, "ターン", 60, 130); tl != nil {
		tx := cx(*tl)
		out["turns"] = nil
		for _, tb := range boxes {
			if isASCIIDigits(tb.Text) && abs(cx(tb.Box)-tx) < 30 && 30 <= cy(tb.Box) && cy(tb.Box) <= 80 {
				out["turns"] = mustAtoi(tb.Text)
				break
			}
		}
	}
	type yp struct{ y, p int }
	var pcts []yp
	for _, tb := range boxes {
		if m := towerPctRe.FindStringSubmatch(tb.Text); m != nil && cx(tb.Box) > 560 {
			pcts = append(pcts, yp{cy(tb.Box), mustAtoi(m[1])})
		}
	}
	sort.SliceStable(pcts, func(i, j int) bool { return pcts[i].y < pcts[j].y })
	if len(pcts) == 3 {
		permils := make([]int, 3)
		params := make([]int, 3)
		all := true
		for i, p := range pcts {
			permils[i] = p.p * 10
			found := false
			for _, tb := range boxes {
				if c := cx(tb.Box); 480 <= c && c <= 600 && abs(cy(tb.Box)-p.y) <= 20 {
					if v, ok := commaInt(tb.Text); ok && v >= 10 {
						params[i], found = v, true
						break
					}
				}
			}
			all = all && found
		}
		out["score_bonus_permils"] = permils
		if all {
			out["params"] = params
		}
	}
	var name, title *string
	for i, tb := range boxes {
		if c := cy(tb.Box); name == nil && 390 <= c && c <= 425 && 180 <= cx(tb.Box) && cx(tb.Box) <= 400 {
			name = &boxes[i].Text
		}
		if c := cy(tb.Box); title == nil && 360 <= c && c <= 390 && 180 <= cx(tb.Box) && cx(tb.Box) <= 330 {
			title = &boxes[i].Text
		}
	}
	if name != nil && title != nil && *name != "" && *title != "" {
		out["idol_name"], out["idol_card_title"] = *name, *title
	}
	ratings := make([]int, 0, 3)
	for _, r := range [][2]int{{60, 300}, {300, 530}, {530, 720}} {
		v := 0
		for _, tb := range boxes {
			if c := cy(tb.Box); 880 <= c && c <= 925 && r[0] <= cx(tb.Box) && cx(tb.Box) < r[1] && rating3to6.MatchString(tb.Text) {
				if n := mustAtoi(tb.Text); n != 0 {
					v = n
					break
				}
			}
		}
		ratings = append(ratings, v)
	}
	if ratings[0] != 0 && ratings[1] != 0 && ratings[2] != 0 {
		out["memory_ratings"] = ratings
	}
	if hp := find(boxes, "体力", 500, 600); hp != nil {
		out["max_stamina"] = nil
		for _, tb := range boxes {
			if d := cy(tb.Box) - cy(*hp); isASCIIDigits(tb.Text) && abs(cx(tb.Box)-cx(*hp)) < 30 && 15 <= d && d <= 50 {
				out["max_stamina"] = mustAtoi(tb.Text)
				break
			}
		}
	}
	for _, kw := range [][2]string{{"challenge", "挑戦する"}, {"omakase", "おまかせ"}, {"reset", "リセット"}} {
		if bx := find(boxes, kw[1], 1000, 1280); bx != nil {
			out[kw[0]] = center(*bx)
		}
	}
	for _, tb := range boxes {
		if m := pageRe.FindStringSubmatch(tb.Text); m != nil && 600 <= cy(tb.Box) && cy(tb.Box) <= 720 {
			out["page"] = mustAtoi(m[1])
			c := center(tb.Box)
			out["next_page"], out["prev_page"] = []int{c[0] + 97, c[1]}, []int{c[0] - 97, c[1]}
			break
		}
	}
	_ = b
	return out
}

func towerDeckCard(c *img.BGR) *master.Card {
	if card := MatchGridCard(c); card != nil {
		return card
	}
	votes := map[string]int{}
	var order []string
	for _, h := range CardQuery(c) {
		a := BaseAsset(h.Key)
		if _, ok := votes[a]; !ok {
			order = append(order, a)
		}
		votes[a] += h.Votes
	}
	if len(order) == 0 {
		return nil
	}
	vals := make([]int, 0, len(votes))
	for _, v := range votes {
		vals = append(vals, v)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(vals)))
	if vals[0] < 2 || (len(vals) > 1 && vals[0] == vals[1]) {
		return nil
	}
	best := order[0]
	for _, a := range order[1:] {
		if votes[a] > votes[best] {
			best = a
		}
	}
	return master.CardByAsset(best)
}

var deckRows = []struct {
	y  int
	xs []int
}{{415, []int{70, 160, 250, 340, 430, 520}}, {515, []int{195, 285, 375, 465, 555, 645}}, {607, []int{195, 285, 375, 465, 555, 645}}}

// ReadFormationDeck 编成页第 2/2 页 → {cards, rows, unread}。
func ReadFormationDeck(b *img.BGR) map[string]any {
	const half = 42
	hsv := cv.HSV(b)
	rows := []any{}
	cards := []string{}
	unread := 0
	for _, r := range deckRows {
		row := []any{}
		for _, x := range r.xs {
			cell := hsv.Slice(x-half, r.y-half, x+half, r.y+half)
			s := 0
			for i := range cell.W * cell.H {
				s += int(cell.Pix[i*3+2])
			}
			dim := float64(s)/float64(cell.W*cell.H) < dimVMax
			corner := hsv.Slice(x+15, r.y-22, x+45, r.y+2)
			red := countWhere(corner, func(p []byte) bool { return (p[0] <= 8 || p[0] >= 172) && p[1] >= 150 && p[2] >= 170 })
			upgrade := 0
			if red >= 15 {
				upgrade = 1
			}
			var card *master.Card
			if !dim {
				card = towerDeckCard(b.Slice(x-half, r.y-half, x+half, r.y+half))
			}
			d := map[string]any{"name": nil, "card_id": nil, "upgrade": upgrade, "dim": dim}
			if card != nil {
				d["name"], d["card_id"] = card.Name, card.ID
			}
			row = append(row, d)
			if !dim {
				if card == nil {
					unread++
				} else {
					id := card.ID
					if upgrade == 1 {
						id += "+1"
					}
					cards = append(cards, id)
				}
			}
		}
		rows = append(rows, row)
	}
	return map[string]any{"cards": cards, "rows": rows, "unread": unread}
}

func resultTitle(boxes []TextBox) *ocr.Box {
	for _, w := range []string{"CLEAR", "PERFECT"} {
		if bx := find(boxes, w, 150, 320); bx != nil {
			return bx
		}
	}
	return nil
}

func pinkStars(hsv *img.BGR, xs []int, half, sMin int) int {
	n := 0
	for _, x := range xs {
		cell := hsv.Slice(x-half, 0, x+half, hsv.H)
		if fracWhere(cell, func(p []byte) bool { return (p[0] >= 150 || p[0] <= 8) && int(p[1]) >= sMin && p[2] >= 150 }) >= 0.2 {
			n++
		}
	}
	return n
}

func resultFields(b *img.BGR, boxes []TextBox) (map[string]any, error) {
	stars := pinkStars(cv.HSV(b.Rows(550, 660)), starX, 45, 80)
	v, ok, err := ReadNumber(b, towerLayer, false)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"cleared": resultTitle(boxes) != nil, "layer": nil, "stars": stars}
	if ok {
		out["layer"] = v
	}
	for _, kw := range [][2]string{{"retry", "再挑戦"}, {"back", "一覧に戻る"}, {"next", "次へ"}} {
		if bx := find(boxes, kw[1], 1080, 1260); bx != nil {
			out[kw[0]] = center(*bx)
		}
	}
	return out, nil
}

func layersFields(b *img.BGR, boxes []TextBox) map[string]any {
	btn := find(boxes, "挑戦する", 900, 1100)
	var name, layer any
	for _, tb := range boxes {
		if cy(tb.Box) < 200 && cx(tb.Box) < 220 && len([]rune(tb.Text)) >= 3 {
			name = tb.Text
			break
		}
	}
	for _, tb := range boxes {
		if isASCIIDigits(tb.Text) && 690 <= cy(tb.Box) && cy(tb.Box) <= 745 && 380 <= cx(tb.Box) && cx(tb.Box) <= 560 {
			layer = mustAtoi(tb.Text)
			break
		}
	}
	hsv := cv.HSV(b)
	stars := pinkStars(hsv.Rows(765, 825), nodeStarX, 30, 90)
	if btn == nil {
		return map[string]any{"idol": name, "stage": layer, "stars": stars, "locked": true, "challenge": nil}
	}
	c := center(*btn)
	reg := hsv.Slice(max(0, c[0]-120), c[1]-20, c[0]+80, c[1]+20)
	orange := fracWhere(reg, func(p []byte) bool { return p[0] >= 5 && p[0] <= 22 && p[1] >= 150 })
	return map[string]any{"idol": name, "stage": layer, "stars": stars, "locked": orange < 0.55, "challenge": c}
}

func rankingFields(boxes []TextBox) map[string]any {
	type ys struct {
		y int
		v int
	}
	var pts []ys
	for _, tb := range boxes {
		if m := ptRe.FindStringSubmatch(tb.Text); m != nil {
			pts = append(pts, ys{tb.Box.Y1, mustAtoi(strings.ReplaceAll(m[1], ",", ""))})
		}
	}
	sort.SliceStable(pts, func(i, j int) bool { return pts[i].y < pts[j].y })
	var labels []ys
	for _, tb := range boxes {
		if m := rankLabelRe.FindStringSubmatch(tb.Text); m != nil && cx(tb.Box) < 150 {
			r := map[string]int{"I": 1, "l": 1, "S": 5}[m[1]]
			if r == 0 {
				r = mustAtoi(m[1])
			}
			labels = append(labels, ys{cy(tb.Box), r})
		}
	}
	type yt struct {
		y int
		t string
	}
	var names []yt
	for _, tb := range boxes {
		if c := cx(tb.Box); 200 <= c && c <= 400 && !ptRe.MatchString(tb.Text) && !anyDigitRe.MatchString(tb.Text) && cy(tb.Box) > 300 {
			names = append(names, yt{tb.Box.Y1, tb.Text})
		}
	}
	sort.SliceStable(names, func(i, j int) bool { return names[i].y < names[j].y })
	rows := []map[string]any{}
	for i, p := range pts {
		var name any
		for _, n := range names {
			if d := p.y - n.y; d > 0 && d < 70 {
				name = n.t
				break
			}
		}
		rank := i + 1
		for _, l := range labels {
			if d := l.y - p.y; d > -60 && d < 30 {
				rank = l.v
				break
			}
		}
		rows = append(rows, map[string]any{"rank": rank, "name": name, "score": p.v})
	}
	var badge, next any
	for _, tb := range boxes {
		if isASCIIDigits(tb.Text) && len([]rune(tb.Text)) == 1 && cy(tb.Box) < 260 && cx(tb.Box) < 200 {
			badge = mustAtoi(tb.Text)
			break
		}
	}
	if nx := find(boxes, "次へ", 1080, 1260); nx != nil {
		next = center(*nx)
	}
	return map[string]any{"my_rank": badge, "rows": rows, "next": next}
}

// RetryCost 再挑戦确认框上的消耗字样；没有返回 nil（同 Python hiro.runtime.tower.retry_cost）。
func RetryCost(d *Dialog) any {
	if d == nil {
		return "（没读到确认框）"
	}
	text := d.Title + d.Body
	for _, w := range retryWords {
		if strings.Contains(text, w) {
			return w
		}
	}
	return nil
}

func joinTexts(boxes []TextBox) string {
	var sb strings.Builder
	for _, tb := range boxes {
		sb.WriteString(tb.Text)
	}
	return sb.String()
}

// TowerDialog 偶像之路的弹窗：层详情「条件」、再挑戦确认。不是返回空页面名。
func TowerDialog(d *Dialog, boxes []TextBox) (string, map[string]any) {
	all := d.Body + joinTexts(boxes)
	if strings.Contains(d.Title, "条件") && strings.Contains(all, "位") {
		type yt struct {
			y int
			t string
		}
		var conds []yt
		for _, tb := range boxes {
			if strings.Contains(tb.Text, "クリア") {
				conds = append(conds, yt{tb.Box.Y1, pyStrip(strings.TrimLeft(tb.Text, "・•"))})
			}
		}
		sort.SliceStable(conds, func(i, j int) bool {
			return conds[i].y < conds[j].y || (conds[i].y == conds[j].y && conds[i].t < conds[j].t)
		})
		rows := []map[string]any{}
		for _, c := range conds {
			var star any
			if m := rankPosRe.FindStringSubmatch(c.t); m != nil {
				star = 4 - mustAtoi(m[1])
			}
			rows = append(rows, map[string]any{"star": star, "text": c.t})
		}
		var cl any
		if c := find(boxes, "閉じる", 1080, 1260); c != nil {
			cl = center(*c)
		}
		return "TOWER_LAYER_INFO", map[string]any{"conditions": rows, "close": cl}
	}
	if strings.Contains(d.Title, "再挑戦") && strings.Contains(all, "ステージを再挑戦") {
		out := map[string]any{"cost": RetryCost(d)}
		for _, kw := range [][2]string{{"rebuild", "編成して再挑戦"}, {"same", "同じ編成で再挑戦"}, {"cancel", "キャンセル"}, {"ok", "OK"}} {
			if bx := find(boxes, kw[1], 900, 1260); bx != nil {
				out[kw[0]] = center(*bx)
			}
		}
		return "TOWER_RETRY_CONFIRM", out
	}
	return "", nil
}

// TowerPage 偶像之路的非弹窗页面；不是返回空页面名。
func TowerPage(b *img.BGR, boxes []TextBox) (string, map[string]any, error) {
	if find(boxes, "アイドルへの道", 0, 120) != nil && find(boxes, "STAGE", 600, 1100) != nil {
		return "TOWER_LIST", listFields(boxes), nil
	}
	if findAny(boxes, "ステージ別ランキング") != nil && (find(boxes, "挑戦する", 900, 1100) != nil || find(boxes, "STAGE", 660, 720) != nil) {
		return "TOWER_LAYERS", layersFields(b, boxes), nil
	}
	if find(boxes, "応援/トラブル", 0, 60) != nil && findAny(boxes, "審査基準") != nil && find(boxes, "ターン", 60, 130) != nil {
		f := formationFields(b, boxes)
		if f["page"] == 2 {
			f["deck"] = ReadFormationDeck(b)
		}
		return "TOWER_FORMATION", f, nil
	}
	if findAny(boxes, "編成中") != nil && findAny(boxes, "審査基準") != nil {
		var dec any
		for _, tb := range boxes {
			if strings.Contains(tb.Text, "決定") && cy(tb.Box) > 1050 {
				dec = center(tb.Box)
				break
			}
		}
		return "TOWER_MEMORY_SELECT", map[string]any{"decide": dec}, nil
	}
	ptCount, ptRegex := 0, 0
	for _, tb := range boxes {
		if strings.HasSuffix(strings.ToLower(tb.Text), "pt") {
			ptCount++
		}
		if ptRe.MatchString(tb.Text) {
			ptRegex++
		}
	}
	if find(boxes, "次へ", 1080, 1260) != nil && find(boxes, "位", 130, 260) != nil && ptCount >= 3 {
		return "TOWER_RANKING", rankingFields(boxes), nil
	}
	if findAny(boxes, "再挑戦確認") != nil || findAny(boxes, "ステージを再挑戦") != nil {
		return "", nil, nil
	}
	if find(boxes, "不合格", 100, 260) != nil && ptRegex >= 3 && (find(boxes, "一覧に戻る", 1080, 1260) != nil || find(boxes, "再挑戦", 1080, 1260) != nil) {
		f := rankingFields(boxes)
		f["failed"] = true
		for _, kw := range [][2]string{{"retry", "再挑戦"}, {"back", "一覧に戻る"}} {
			if bx := find(boxes, kw[1], 1080, 1260); bx != nil {
				f[kw[0]] = center(*bx)
			}
		}
		return "TOWER_RANKING", f, nil
	}
	if resultTitle(boxes) != nil && (find(boxes, "一覧に戻る", 1080, 1260) != nil || find(boxes, "再挑戦", 1080, 1260) != nil) {
		f, err := resultFields(b, boxes)
		return "TOWER_RESULT", f, err
	}
	return "", nil, nil
}
