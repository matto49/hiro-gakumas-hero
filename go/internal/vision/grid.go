package vision

import (
	"encoding/gob"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/vision/ui"
)

// 牌组卡格（同 Python hiro.vision.deck 的 read_card_grid 一组）：按类别字母 A / M / T 推卡格，卡面图库认卡（票数临界时卡图相关核对），
// 「+」看红色像素，定制徽章看绿色像素。

type gridGeom struct {
	dx, dy, cell, x0, pitch int
	rowPitch                float64
}

var geometries = map[string]gridGeom{
	"list":     {-62, -124, 141, 48, 161, 153.3},
	"dialog":   {-51, -100, 118, 81, 147, 147.5},
	"guidance": {-64, -124, 141, 49, 160, 160},
}

const (
	pitchSplit      = 154
	rowTol          = 20
	dimVMax         = 160
	dimTargetV      = 225
	dimGainMax      = 2.5
	plusSpeckleFrac = 0.5
	plusMinPx125    = 40
	customMinPx125  = 120
	colTol          = 8
	plusMaxH        = 0.17
	plusMaxW        = 0.19
	gridArtSize     = 36
	gridArtInset    = 0.2
	gridSureVotes   = 4
	gridArtMin      = 0.55
	gridArtMargin   = 0.13
	gridAgreeMin    = 0.40
	gridKaaArtMin   = 0.15
)

var (
	plusBox   = [4]float64{0.33, 0.66, 0.74, 1.0} // (y1, y2, x1, x2)
	customBox = [4]float64{0.62, 0.86, 0.36, 0.64}
	dimGains  = []float64{0.8, 0.9, 1.0}
	listView  = ocr.Box{X1: 0, Y1: 395, X2: 720, Y2: 1010}
)

type mark struct{ x, y int }

// marks region 内类别字母 A / M / T 的左上角，按行再按 x 排序。
func marks(b *img.BGR, region ocr.Box) []mark {
	r := region.XYWH()
	var found []mark
	for _, n := range []string{"InProduce.A", "InProduce.M", "InProduce.T"} {
		for _, m := range ui.Tpl(n).FindAll(b, ui.Opt{Region: &r}) {
			found = append(found, mark{m.Rect.X1, m.Rect.Y1})
		}
	}
	sort.SliceStable(found, func(i, j int) bool {
		if found[i].y != found[j].y {
			return found[i].y < found[j].y
		}
		return found[i].x < found[j].x
	})
	var rows [][]mark
	for _, p := range found {
		if len(rows) > 0 && abs(rows[len(rows)-1][0].y-p.y) <= rowTol {
			rows[len(rows)-1] = append(rows[len(rows)-1], p)
		} else {
			rows = append(rows, []mark{p})
		}
	}
	var out []mark
	for _, row := range rows {
		sort.SliceStable(row, func(i, j int) bool { return row[i].x < row[j].x || (row[i].x == row[j].x && row[i].y < row[j].y) })
		out = append(out, row...)
	}
	return out
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

func geometryOf(ms []mark) gridGeom {
	xs := map[int]bool{}
	for _, m := range ms {
		xs[m.x] = true
	}
	var sx []int
	for x := range xs {
		sx = append(sx, x)
	}
	sort.Ints(sx)
	minGap := -1
	for i := 1; i < len(sx); i++ {
		if g := sx[i] - sx[i-1]; g > 60 && (minGap < 0 || g < minGap) {
			minGap = g
		}
	}
	if minGap < 0 || minGap >= pitchSplit {
		return geometries["list"]
	}
	return geometries["dialog"]
}

// cellHSV 卡格里相对框 (y1, y2, x1, x2) 那块的 HSV，和按 125 像素卡格换算的面积比。
func cellHSV(cell *img.BGR, box [4]float64) (*img.BGR, float64) {
	n := float64(cell.H)
	r := func(v float64) int { return int(math.RoundToEven(v * n)) }
	return cv.HSV(cell.Slice(r(box[2]), r(box[0]), r(box[3]), r(box[1]))), (n / 125) * (n / 125)
}

func maskOf(hsv *img.BGR, f func(p []byte) bool) *img.Gray {
	m := img.NewGray(hsv.W, hsv.H)
	for i := range m.Pix {
		if f(hsv.Pix[i*3 : i*3+3]) {
			m.Pix[i] = 1
		}
	}
	return m
}

// PlusShapeOK 红色像素的最大连通块像不像「+」的红色上半截：又扁又小。
func PlusShapeOK(red *img.Gray, cellSize int) bool {
	_, comps := cv.Components(red)
	if len(comps) == 0 {
		return false
	}
	bi := 0
	for i, c := range comps {
		if c.Area > comps[bi].Area {
			bi = i
		}
	}
	c := comps[bi]
	return float64(c.H) <= plusMaxH*float64(cellSize) && float64(c.W) <= plusMaxW*float64(cellSize)
}

func isRed(p []byte) bool { return (p[0] <= 12 || p[0] >= 170) && p[1] >= 140 && p[2] >= 150 }

// plusLevel 强化次数：看得清红黄「+」→ 1，明显没有 → 0，介于两者之间或形状不像「+」→ nil。
func plusLevel(cell *img.BGR) *int {
	hsv, area := cellHSV(cell, plusBox)
	m := maskOf(hsv, isRed)
	red := m.CountNonZero()
	need := plusMinPx125 * area
	one, zero := 1, 0
	if float64(red) >= need {
		if PlusShapeOK(m, cell.H) {
			return &one
		}
		return nil
	}
	if float64(red) < need/4 {
		return &zero
	}
	_, comps := cv.Components(m)
	biggest := 0
	for _, c := range comps {
		biggest = max(biggest, c.Area)
	}
	if float64(biggest) < need*plusSpeckleFrac {
		return &zero
	}
	return nil
}

func customized(cell *img.BGR) bool {
	hsv, area := cellHSV(cell, customBox)
	green := countWhere(hsv, func(p []byte) bool { return p[0] >= 45 && p[0] <= 85 && p[1] >= 120 && p[2] >= 100 })
	return float64(green) >= customMinPx125*area
}

// ---------------------------------------------------------------- 卡图相关（票数临界时核对）

type gridArt struct {
	Keys   []string
	Matrix []float32 // len(Keys) × 36²
	Stamp  string
}

var (
	gridArtOnce sync.Once
	gridArtIdx  *gridArt
)

// gridNorm 卡图中心区域（四边各去 20%）缩成 36x36 灰度，去均值、归一化。
func gridNorm(b *img.BGR) []float32 {
	i, j := int(float64(b.H)*gridArtInset), int(float64(b.W)*gridArtInset)
	g := cv.ResizeGray(cv.Gray(b.Slice(j, i, b.W-j, b.H-i)), gridArtSize, gridArtSize, cv.Area)
	out := make([]float32, len(g.Pix))
	var sum float64
	for k, v := range g.Pix {
		out[k] = float32(v)
		sum += float64(v)
	}
	mean := float32(sum / float64(len(out)))
	var ss float64
	for k := range out {
		out[k] -= mean
		ss += float64(out[k]) * float64(out[k])
	}
	norm := float32(math.Sqrt(ss)) + 1e-6
	for k := range out {
		out[k] /= norm
	}
	return out
}

func loadGridArt() *gridArt {
	gridArtOnce.Do(func() {
		gridArtIdx = &gridArt{}
		dir := assets.Default().GameData("skill_cards")
		if dir == "" {
			return
		}
		files := cardFiles(dir)
		st, _ := os.Stat(dir)
		stamp := fmt.Sprintf("%d-%d-%d-%v-base2", len(files), st.ModTime().Unix(), gridArtSize, gridArtInset)
		path := filepath.Join(assets.CacheDir(), "grid_art_go.gob")
		if f, err := os.Open(path); err == nil {
			var g gridArt
			ok := gob.NewDecoder(f).Decode(&g) == nil && g.Stamp == stamp
			_ = f.Close()
			if ok {
				gridArtIdx = &g
				return
			}
		}
		g := &gridArt{Stamp: stamp}
		rows := make([][]float32, len(files))
		parallelFor(len(files), func(i int) {
			if b, err := cv.Read(filepath.Join(dir, files[i])); err == nil {
				rows[i] = gridNorm(b)
			}
		})
		for i, f := range files {
			if rows[i] == nil {
				continue
			}
			g.Keys = append(g.Keys, BaseAsset(strings.TrimSuffix(f, ".png")))
			g.Matrix = append(g.Matrix, rows[i]...)
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err == nil {
			if f, err := os.Create(path + ".tmp"); err == nil {
				if gob.NewEncoder(f).Encode(g) == nil {
					_ = f.Close()
					_ = os.Rename(path+".tmp", path)
				} else {
					_ = f.Close()
				}
			}
		}
		gridArtIdx = g
	})
	return gridArtIdx
}

type artScore struct {
	key   string
	score float64
}

// GridArtScores 卡格缩略图和全部卡图的相关分，按 base asset 取各变体最高分，降序。
func GridArtScores(c *img.BGR) []artScore {
	g := loadGridArt()
	if len(g.Keys) == 0 {
		return nil
	}
	v := gridNorm(c)
	n := gridArtSize * gridArtSize
	best := map[string]float64{}
	var order []string
	for i, k := range g.Keys {
		var s float32
		row := g.Matrix[i*n : (i+1)*n]
		for j := range n {
			s += row[j] * v[j]
		}
		if cur, ok := best[k]; !ok {
			order = append(order, k)
			best[k] = float64(s)
		} else if float64(s) > cur {
			best[k] = float64(s)
		}
	}
	out := make([]artScore, len(order))
	for i, k := range order {
		out[i] = artScore{k, best[k]}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].score > out[j].score })
	return out
}

// MatchGridCard 卡格缩略图认卡：卡面图库票数为主，票数临界（≤3 票或并列）时用卡图相关核对。
func MatchGridCard(c *img.BGR) *master.Card {
	votes := map[string]int{}
	dist := map[string]float64{}
	var order []string
	for _, h := range CardQuery(c) {
		a := BaseAsset(h.Key)
		if _, ok := votes[a]; !ok {
			order = append(order, a)
			dist[a] = math.Inf(1)
		}
		votes[a] += h.Votes
		dist[a] = math.Min(dist[a], h.Distance)
	}
	if len(votes) == 0 {
		return nil
	}
	ranked := append([]string(nil), order...)
	sort.SliceStable(ranked, func(i, j int) bool {
		if votes[ranked[i]] != votes[ranked[j]] {
			return votes[ranked[i]] > votes[ranked[j]]
		}
		return dist[ranked[i]] < dist[ranked[j]]
	})
	top := votes[ranked[0]]
	second := 0
	if len(ranked) > 1 {
		second = votes[ranked[1]]
	}
	if top >= gridSureVotes && top > second {
		return master.CardByAsset(ranked[0])
	}
	art := GridArtScores(c)
	if len(art) >= 2 && art[0].score >= gridArtMin && art[0].score-art[1].score >= gridArtMargin {
		return master.CardByAsset(art[0].key)
	}
	var tied []string
	for _, b := range ranked {
		if votes[b] == top {
			tied = append(tied, b)
		}
	}
	if top >= 2 && len(art) >= 2 && contains(tied, art[0].key) && art[0].score >= gridAgreeMin && art[0].score-art[1].score >= gridArtMargin {
		return master.CardByAsset(art[0].key)
	}
	if top < cardMinVotes {
		return nil
	}
	if len(art) == 0 {
		return master.CardByAsset(ranked[0])
	}
	corr := map[string]float64{}
	for _, a := range art {
		corr[a.key] = a.score
	}
	get := func(k string) float64 {
		if v, ok := corr[k]; ok {
			return v
		}
		return -1
	}
	best := tied[0]
	for _, b := range tied[1:] {
		if get(b) > get(best) {
			best = b
		}
	}
	if get(best) >= gridKaaArtMin {
		return master.CardByAsset(best)
	}
	return nil
}

func contains(v []string, s string) bool {
	for _, x := range v {
		if x == s {
			return true
		}
	}
	return false
}

// dimmedCard 压暗的格子：几档提亮投票，多数一致（≥2）才用。
func dimmedCard(c *img.BGR, gain float64) *master.Card {
	var hits []*master.Card
	for _, k := range dimGains {
		if m := MatchGridCard(cv.ConvertScaleAbs(c, gain*k)); m != nil {
			hits = append(hits, m)
		}
	}
	if len(hits) == 0 {
		return nil
	}
	counts := map[string]int{}
	var order []string
	for _, h := range hits {
		if _, ok := counts[h.ID]; !ok {
			order = append(order, h.ID)
		}
		counts[h.ID]++
	}
	best := order[0]
	for _, id := range order[1:] {
		if counts[id] > counts[best] {
			best = id
		}
	}
	if counts[best] < 2 {
		return nil
	}
	for _, h := range hits {
		if h.ID == best {
			return h
		}
	}
	return nil
}

// GridCard 是卡格里认出的一张卡。
type GridCard struct {
	Index      int     `json:"index"`
	Row        int     `json:"row"`
	Col        int     `json:"col"`
	CardID     *string `json:"card_id"`
	Name       *string `json:"name"`
	Upgrade    *int    `json:"upgrade"`
	Customized *bool   `json:"customized"`
	Dimmed     bool    `json:"dimmed"`
	Rect       [4]int  `json:"rect"`
	Center     [2]int  `json:"center"`
}

// ReadCardGrid region 内整格可见的卡，从左到右、从上到下。geometry 为 "" 时按字母间距推断。
func ReadCardGrid(b *img.BGR, region ocr.Box, geometry string) []GridCard {
	ms := marks(b, region)
	out := []GridCard{}
	if len(ms) == 0 {
		return out
	}
	g := geometries[geometry]
	if geometry == "" {
		g = geometryOf(ms)
	}
	cell := g.cell
	var rowYs []int
	for _, m := range ms {
		x, y := m.x+g.dx, m.y+g.dy
		col := int(math.RoundToEven(float64(x-g.x0) / float64(g.pitch)))
		if y < region.Y1-4 || y+cell > region.Y2+8 || col < 0 || col > 3 || abs(x-g.x0-col*g.pitch) > colTol {
			continue
		}
		if len(rowYs) > 0 {
			k := math.RoundToEven(float64(y-rowYs[0]) / g.rowPitch)
			if math.Abs(float64(y-rowYs[0])-k*g.rowPitch) > colTol {
				continue
			}
		}
		if len(rowYs) == 0 || abs(rowYs[len(rowYs)-1]-y) > rowTol {
			rowYs = append(rowYs, y)
		}
		c := b.Slice(x, y, x+cell, y+cell)
		vMean := float64(0)
		hsv := cv.HSV(c)
		if n := hsv.W * hsv.H; n > 0 {
			s := 0
			for i := range n {
				s += int(hsv.Pix[i*3+2])
			}
			vMean = float64(s) / float64(n)
		}
		dimmed := vMean < dimVMax
		gain := math.Min(dimGainMax, dimTargetV/math.Max(vMean, 1.0))
		look := c
		var card *master.Card
		if dimmed {
			look = cv.ConvertScaleAbs(c, gain)
			card = dimmedCard(c, gain)
		} else {
			card = MatchGridCard(c)
		}
		gc := GridCard{Index: len(out), Row: len(rowYs) - 1, Col: col, Dimmed: dimmed, Rect: [4]int{x, y, cell, cell}, Center: [2]int{x + cell/2, y + cell/2}}
		if card != nil {
			id, name := card.ID, card.Name
			cu := customized(look)
			gc.CardID, gc.Name, gc.Upgrade, gc.Customized = &id, &name, plusLevel(look), &cu
		}
		out = append(out, gc)
	}
	return out
}

func leftFilledRows(cards []GridCard) []GridCard {
	rows := map[int][]int{}
	for _, c := range cards {
		rows[c.Row] = append(rows[c.Row], c.Col)
	}
	bad := map[int]bool{}
	for r, cols := range rows {
		s := append([]int(nil), cols...)
		sort.Ints(s)
		for i, v := range s {
			if v != i {
				bad[r] = true
				break
			}
		}
	}
	if len(bad) == 0 {
		return cards
	}
	out := []GridCard{}
	for _, c := range cards {
		if !bad[c.Row] {
			c.Index = len(out)
			out = append(out, c)
		}
	}
	return out
}

// DeckCard 是牌组里的一张（ProduceState.deck 格式）。
type DeckCard struct {
	CardID     *string `json:"card_id"`
	Upgrade    *int    `json:"upgrade"`
	Customized *bool   `json:"customized"`
}

// DeckList 是「所持スキルカード」一览页一屏的读数。
type DeckList struct {
	DeckSize    *int       `json:"deck_size"`
	Cards       []GridCard `json:"cards"`
	RowsVisible int        `json:"rows_visible"`
	Complete    bool       `json:"complete"`
	Deck        []DeckCard `json:"deck"`
	Problems    []string   `json:"problems"`
}

func pyList(ns []int) string {
	parts := make([]string, len(ns))
	for i, n := range ns {
		parts[i] = itoa(n)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

func finishDeck(cards []GridCard, size *int, problems []string) (bool, []DeckCard, []string) {
	problems = append([]string{}, problems...)
	switch {
	case size == nil:
		problems = append(problems, "没有读到总张数")
	case len(cards) < *size:
		problems = append(problems, fmt.Sprintf("只看到 %d / %d 张（要滑动再读）", len(cards), *size))
	case len(cards) > *size:
		problems = append(problems, fmt.Sprintf("看到 %d 张，多于标签的 %d 张", len(cards), *size))
	}
	var bad, vague []int
	for i, c := range cards {
		if c.CardID == nil {
			bad = append(bad, i+1)
		} else if c.Upgrade == nil {
			vague = append(vague, i+1)
		}
	}
	if len(bad) > 0 {
		problems = append(problems, "第 "+pyList(bad)+" 张认不出")
	}
	if len(vague) > 0 {
		problems = append(problems, "第 "+pyList(vague)+" 张强化标记看不清")
	}
	if len(problems) > 0 {
		return false, nil, problems
	}
	deck := make([]DeckCard, len(cards))
	for i, c := range cards {
		deck[i] = DeckCard{c.CardID, c.Upgrade, c.Customized}
	}
	return true, deck, problems
}

// ReadDeckList 所持スキルカード一览页（一屏）。
func ReadDeckList(b *img.BGR) (*DeckList, error) {
	n, ok, err := ReadDeckSize(b)
	if err != nil {
		return nil, err
	}
	size := iptr(n, ok)
	cards := leftFilledRows(ReadCardGrid(b, listView, "list"))
	out := &DeckList{DeckSize: size, Cards: cards}
	if len(cards) > 0 {
		out.RowsVisible = cards[len(cards)-1].Row + 1
	}
	out.Complete, out.Deck, out.Problems = finishDeck(cards, size, nil)
	return out, nil
}

// ---------------------------------------------------------------- 选卡弹窗：删除 / 强化 / 换卡第 2 步

var (
	removalGrid    = ocr.Box{X1: 0, Y1: 380, X2: 720, Y2: 1110}
	detailBox      = ocr.Box{X1: 170, Y1: 95, X2: 720, Y2: 160}
	change1Marks   = ocr.Box{X1: 40, Y1: 820, X2: 680, Y2: 1020}
	change1Text    = ocr.Box{X1: 0, Y1: 940, X2: 720, Y2: 1130}
	leftCountRe    = pyRe(`あと(\d+)回`)
	cursorExpand   = 16
	cursorCorner   = 30
	cursorMin118   = 600.0
	change1Gap     = 100
	change1DX      = -53
	change1DY      = -110
	change1Cell    = 124
	scrollThumbMin = 40
)

func orangeCursor(p []byte) bool { return p[0] >= 8 && p[0] <= 22 && p[1] >= 150 && p[2] >= 180 }

func cursorPx(hsv *img.BGR, rect [4]int) int {
	x, y, n := rect[0], rect[1], rect[2]
	e, sz := cursorExpand, cursorCorner
	total := 0
	for _, c := range [][2]int{{x - e, y - e}, {x + n + e - sz, y - e}, {x - e, y + n + e - sz}, {x + n + e - sz, y + n + e - sz}} {
		total += countWhere(hsv.Slice(max(0, c[0]), max(0, c[1]), c[0]+sz, c[1]+sz), orangeCursor)
	}
	return total
}

// CardDialog 是「从牌组里选一张」弹窗的读数。
type CardDialog struct {
	Cards        []GridCard `json:"cards"`
	Selected     *int       `json:"selected"`
	SelectedCard *CardName  `json:"selected_card"`
	Detail       *CardName  `json:"detail"`
	Agree        *bool      `json:"agree"`
}

// ReadCardDialog 相談削除 / 强化、换卡第 2 步等「从牌组里选一张」的弹窗。expect 为已知要选的 card_id（可空）。
func ReadCardDialog(b *img.BGR, region, detail ocr.Box, expect string) (*CardDialog, error) {
	cards := ReadCardGrid(b, region, "")
	hsv := cv.HSV(b)
	need := cursorMin118
	if len(cards) > 0 {
		r := float64(cards[0].Rect[2]) / 118
		need *= r * r
	}
	var hot []int
	for i, c := range cards {
		if float64(cursorPx(hsv, c.Rect)) >= need {
			hot = append(hot, i)
		}
	}
	out := &CardDialog{Cards: cards}
	if len(hot) == 1 {
		out.Selected = &hot[0]
	}
	ids := map[string]bool{}
	for _, c := range cards {
		if c.CardID != nil {
			ids[*c.CardID] = true
		}
	}
	if expect != "" {
		ids[expect] = true
	}
	items, err := ocr.Boxes(crop(b, detail), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	for _, it := range items {
		if hit := CardFromName(it.Text, ids); hit != nil {
			out.Detail = hit
			break
		}
	}
	var cell *GridCard
	if out.Selected != nil {
		cell = &cards[*out.Selected]
	}
	if cell != nil && cell.CardID != nil && out.Detail != nil {
		a := *cell.CardID == out.Detail.CardID && (cell.Upgrade == nil || *cell.Upgrade == out.Detail.Upgrade)
		out.Agree = &a
	}
	switch {
	case out.Agree != nil && !*out.Agree:
	case out.Detail != nil:
		out.SelectedCard = out.Detail
	case cell != nil && cell.CardID != nil && cell.Upgrade != nil:
		out.SelectedCard = &CardName{CardID: *cell.CardID, Name: *cell.Name, Upgrade: *cell.Upgrade}
	}
	return out, nil
}

// DialogScroll 选卡对话框的滚动条 → {scrollable, thumb, at_top}。
func DialogScroll(b *img.BGR, region ocr.Box, x int) map[string]any {
	reg := b.Slice(x-4, region.Y1, min(b.W, x+5), region.Y2)
	if reg.W < 9 {
		return map[string]any{"scrollable": false, "thumb": nil, "at_top": true}
	}
	vmax := func(y, c int) int {
		p := reg.Pix[(y*reg.W+c)*3:]
		return int(max(p[0], p[1], p[2]))
	}
	on := make([]bool, reg.H+1)
	for y := range reg.H {
		d := float64(vmax(y, 4)) - float64(vmax(y, 0)+vmax(y, 8))/2
		on[y] = d >= 25 && vmax(y, 4) >= 120
	}
	best := [2]int{-1, -1}
	start := -1
	for i, f := range on {
		switch {
		case f && start < 0:
			start = i
		case !f && start >= 0:
			if i-start >= scrollThumbMin && (best[0] < 0 || i-start > best[1]-best[0]) {
				best = [2]int{start, i}
			}
			start = -1
		}
	}
	if best[0] < 0 {
		return map[string]any{"scrollable": false, "thumb": nil, "at_top": true}
	}
	return map[string]any{"scrollable": true, "thumb": []int{region.Y1 + best[0], region.Y1 + best[1]}, "at_top": best[0] <= 15}
}

// ---------------------------------------------------------------- 换卡第 1 步

func change1Row(ms []mark) []mark {
	if len(ms) == 0 {
		return nil
	}
	ys := make([]int, len(ms))
	for i, m := range ms {
		ys[i] = m.y
	}
	sort.Ints(ys)
	mid := ys[len(ys)/2]
	var keep []mark
	for _, m := range ms {
		if abs(m.y-mid) <= rowTol {
			keep = append(keep, m)
		}
	}
	sort.SliceStable(keep, func(i, j int) bool { return keep[i].x < keep[j].x || (keep[i].x == keep[j].x && keep[i].y < keep[j].y) })
	var out []mark
	for _, p := range keep {
		if len(out) == 0 || p.x-out[len(out)-1].x >= change1Gap {
			out = append(out, p)
		}
	}
	return out
}

// ReadChangeCandidates 换卡第 1 步：候选三张、推荐、剩余除去 / 再抽选次数。
func ReadChangeCandidates(b *img.BGR) (map[string]any, error) {
	ms := change1Row(marks(b, change1Marks))
	items, err := ocr.Boxes(crop(b, change1Text), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	boxes := shift(items, change1Text.X1, change1Text.Y1)
	cands := []map[string]any{}
	complete := len(ms) == 3
	var rec any
	for i, m := range ms {
		x, y := m.x+change1DX, m.y+change1DY
		c := b.Slice(max(0, x), max(0, y), x+change1Cell, y+change1Cell)
		card := MatchGridCard(c)
		cxx := x + change1Cell/2
		r := false
		for _, tb := range boxes {
			if strings.Contains(tb.Text, "おすすめ") && abs(cx(tb.Box)-cxx) <= 60 {
				r = true
				break
			}
		}
		d := map[string]any{"index": i, "slot": i, "card_id": nil, "name": nil, "upgrade": nil, "recommended": r, "center": []int{cxx, y + change1Cell/2}}
		if card != nil {
			d["card_id"], d["name"] = card.ID, card.Name
			if u := plusLevel(c); u != nil {
				d["upgrade"] = *u
			}
		}
		if card == nil || d["upgrade"] == nil {
			complete = false
		}
		if r && rec == nil {
			rec = i
		}
		cands = append(cands, d)
	}
	leftCount := func(x1, x2 int) (any, error) {
		var col []TextBox
		var parts []string
		for _, tb := range boxes {
			if c := cx(tb.Box); x1 <= c && c <= x2 {
				col = append(col, tb)
				parts = append(parts, tb.Text)
			}
		}
		if m := leftCountRe.FindStringSubmatch(strings.Join(parts, "")); m != nil {
			return mustAtoi(m[1]), nil
		}
		for _, tb := range col {
			if strings.Contains(tb.Text, "あと") && strings.Contains(tb.Text, "回") {
				bx := tb.Box
				line, err := ocr.OCR(b.Slice(max(0, bx.X1-10), max(0, bx.Y1-10), bx.X2+10, bx.Y2+10), 2, ocr.JaEn)
				if err != nil {
					return nil, err
				}
				if m := leftCountRe.FindStringSubmatch(strings.ReplaceAll(ocr.Joined(line, ""), " ", "")); m != nil {
					return mustAtoi(m[1]), nil
				}
			}
		}
		return nil, nil
	}
	ex, err := leftCount(0, 260)
	if err != nil {
		return nil, err
	}
	rr, err := leftCount(460, 720)
	if err != nil {
		return nil, err
	}
	return map[string]any{"candidates": cands, "recommended": rec, "excludes_left": ex, "rerolls_left": rr, "complete": complete}, nil
}

// ---------------------------------------------------------------- 确认弹窗：相談「交換確認」、「スキルカード削除」

var (
	confirmStrip = ocr.Box{X1: 30, Y1: 440, X2: 380, Y2: 760}
	confirmBody  = ocr.Box{X1: 0, Y1: 560, X2: 720, Y2: 1200}
	confirmNums  = pyRe(`\d+`)
)

func matchWhiteTpl60(b *img.BGR, name string, box ocr.Box) float64 {
	tpl := memo("white60:"+name, func() *img.Gray { return whiteMask(localTpl(name), deckWhiteSat) })
	res := cv.MatchTemplate(whiteMask(crop(b, box), deckWhiteSat), tpl, cv.CcoeffNormed)
	if res == nil {
		panic(&cv.Error{Err: errSmall})
	}
	best := math.Inf(-1)
	for _, v := range res.Val { // np.nan_to_num(res).max()
		f := float64(v)
		switch {
		case math.IsNaN(f):
			f = 0
		case math.IsInf(f, 1):
			f = math.MaxFloat32
		case math.IsInf(f, -1):
			f = -math.MaxFloat32
		}
		best = math.Max(best, f)
	}
	return best
}

// ConfirmKind 交換確認 → exchange，スキルカード削除 → delete，都不是 → ""。
func ConfirmKind(b *img.BGR) string {
	ex := matchWhiteTpl60(b, "confirm_exchange", confirmStrip)
	de := matchWhiteTpl60(b, "confirm_delete", confirmStrip)
	kind, score := "exchange", ex
	if de > ex {
		kind, score = "delete", de
	}
	if score >= 0.75 {
		return kind
	}
	return ""
}

func isASCIIDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if digitValue(r) < 0 {
			return false
		}
	}
	return true
}

// ReadConfirmDialog 相談购买 / 删卡的确认弹窗：{kind, item, p_before, p_after, price, sale}；不是这类弹窗时 nil。
func ReadConfirmDialog(b *img.BGR) (map[string]any, error) {
	kind := ConfirmKind(b)
	if kind == "" {
		return nil, nil
	}
	items, err := ocr.Boxes(crop(b, confirmBody), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	boxes := shift(items, confirmBody.X1, confirmBody.Y1)
	var item any
	t := names()
	for _, tb := range boxes {
		if c := CardFromName(tb.Text, nil); c != nil {
			item = map[string]any{"kind": "card", "card_id": c.CardID, "name": c.Name, "upgrade": c.Upgrade}
			break
		}
		if id, ok := t.drinks[tb.Text]; ok {
			item = map[string]any{"kind": "drink", "drink_id": id, "name": tb.Text}
			break
		}
	}
	var arrow *ocr.Box
	for i, tb := range boxes {
		if tb.Text == "P" && tb.Box.Y2-tb.Box.Y1 >= 30 {
			arrow = &boxes[i].Box
			break
		}
	}
	if arrow == nil {
		for i := len(boxes) - 1; i >= 0; i-- {
			if strings.Contains(boxes[i].Text, "→") {
				arrow = &boxes[i].Box
				break
			}
		}
	}
	if arrow == nil {
		type row struct {
			cy int
			b  ocr.Box
		}
		var rows []row
		for _, tb := range boxes {
			if isASCIIDigits(tb.Text) && tb.Box.X1 >= 250 {
				rows = append(rows, row{cy(tb.Box), tb.Box})
			}
		}
		sort.SliceStable(rows, func(i, j int) bool { return lessRow(rows[i].cy, rows[i].b, rows[j].cy, rows[j].b) })
		for i := len(rows) - 1; i >= 0; i-- {
			n := 0
			for _, r := range rows {
				if abs(r.cy-rows[i].cy) <= 12 {
					n++
				}
			}
			if n >= 2 {
				bb := rows[i].b
				arrow = &bb
				break
			}
		}
	}
	var before, after any
	if arrow != nil {
		acy := cy(*arrow)
		type xt struct {
			x int
			t string
		}
		var row []xt
		for _, tb := range boxes {
			if abs(cy(tb.Box)-acy) <= 12 && tb.Box.X1 >= 250 {
				row = append(row, xt{tb.Box.X1, tb.Text})
			}
		}
		sort.SliceStable(row, func(i, j int) bool { return row[i].x < row[j].x || (row[i].x == row[j].x && row[i].t < row[j].t) })
		var nums []int
		for _, r := range row {
			for _, m := range confirmNums.FindAllString(r.t, -1) {
				nums = append(nums, mustAtoi(m))
			}
		}
		if len(nums) >= 2 {
			before, after = nums[0], nums[1]
		}
	}
	var price any
	if bv, ok := before.(int); ok {
		if av, ok := after.(int); ok && bv >= av {
			price = bv - av
		}
	}
	sale := false
	for _, tb := range boxes {
		sale = sale || tb.Text == "SALE"
	}
	return map[string]any{"kind": kind, "item": item, "p_before": before, "p_after": after, "price": price, "sale": sale}, nil
}

// lessRow 同 Python 对 ((cy, box)) 元组排序：先 cy，再框的四个坐标。
func lessRow(ay int, ab ocr.Box, by int, bb ocr.Box) bool {
	if ay != by {
		return ay < by
	}
	a, b := [4]int{ab.X1, ab.Y1, ab.X2, ab.Y2}, [4]int{bb.X1, bb.Y1, bb.X2, bb.Y2}
	for i := range 4 {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return false
}
