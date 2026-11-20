package vision

import (
	"math"
	"strings"
	"unicode/utf8"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// H.I.F 培育里模板认不出的页面（同 Python hiro.vision.pages）：弹窗按橙黄标题条（像素）+ 标题 OCR；其余页面整帧 OCR 按文字锚点判定。

const (
	headerX1, headerX2 = 30, 690
	headerMinFrac      = 0.55 // 行内橙黄像素占比（标题条 0.67–0.76）
	headerH1, headerH2 = 25, 70
	headerKeepFrac     = 0.45 // 滞回：标题条开始要 ≥ headerMinFrac，之后 ≥ 这个值就算同一条
	headerLongFrac     = 0.3  // 第二级滞回：长标题白字压低了中间几行的橙色占比
	titleWhiteMin      = 0.03 // 标题条左段白字像素占比
	orangeBgMin        = 0.8  // 标题条上方 60 行橙黄占比的平均
	headerOnOrangeH    = 65
	titleRetryH        = 40
	titleRetryUp       = 40
)

// Band 是一条标题条 [Y1, Y2)。
type Band struct{ Y1, Y2 int }

// rowFrac 每行满足 f 的像素占比（numpy 的 mask.mean(axis=1)）。
func rowFrac(b *img.BGR, f func(p []byte) bool) []float64 {
	out := make([]float64, b.H)
	if b.W == 0 {
		for i := range out {
			out[i] = math.NaN()
		}
		return out
	}
	for y := range b.H {
		n := 0
		row := b.Pix[y*b.W*3 : (y+1)*b.W*3]
		for x := range b.W {
			if f(row[x*3 : x*3+3]) {
				n++
			}
		}
		out[y] = float64(n) / float64(b.W)
	}
	return out
}

func orangeHeader(p []byte) bool { return p[0] >= 8 && p[0] <= 30 && p[1] >= 110 && p[2] >= 190 }

func bands(frac []float64, keep float64) []Band {
	var out []Band
	for y := 0; y < len(frac); y++ {
		if frac[y] >= headerMinFrac {
			start := y
			for y < len(frac) && frac[y] >= keep {
				y++
			}
			if headerH1 <= y-start && y-start <= headerH2 {
				out = append(out, Band{start, y})
			}
		}
	}
	return out
}

func anyTitleWhite(b *img.BGR, bs []Band) bool {
	for _, x := range bs {
		if titleWhite(b, x.Y1, x.Y2) {
			return true
		}
	}
	return false
}

// HeaderBands 弹窗的橙黄标题条（从上到下）。
func HeaderBands(b *img.BGR) []Band {
	hsv := cv.HSV(b.Slice(headerX1, 0, headerX2, b.H))
	frac := rowFrac(hsv, orangeHeader)
	out := bands(frac, headerMinFrac)
	for _, keep := range []float64{headerKeepFrac, headerLongFrac} {
		if anyTitleWhite(b, out) {
			return out
		}
		out = out[:0:0]
		for _, x := range bands(frac, keep) {
			if whiteBody(b, x.Y2) {
				out = append(out, x)
			}
		}
	}
	if !anyTitleWhite(b, out) {
		out = headerOnOrange(b, frac)
	}
	return out
}

func headerOnOrange(b *img.BGR, frac []float64) []Band {
	hsv := cv.HSV(b.Slice(headerX1, 0, headerX2, b.H))
	wh := rowFrac(hsv, func(p []byte) bool { return p[1] < 40 && p[2] > 225 })
	var out []Band
	for y := headerOnOrangeH; y < len(frac)-40; y++ {
		if !(wh[y-1] < 0.3) {
			continue
		}
		mx := math.Inf(-1)
		for _, v := range frac[y-4 : y] {
			mx = math.Max(mx, v)
		}
		if mx < headerMinFrac {
			continue
		}
		all := true
		for _, v := range wh[y : y+40] {
			if !(v >= 0.6) {
				all = false
				break
			}
		}
		if !all {
			continue
		}
		a1, a2 := max(0, y-headerOnOrangeH-60), y-headerOnOrangeH
		if a2 <= a1 || npMean(frac[a1:a2]) < orangeBgMin {
			continue // 标题条上方不是整片橙黄（获得演出的金色名称栏上面是场景）
		}
		if titleWhite(b, y-headerOnOrangeH, y) {
			out = append(out, Band{y - headerOnOrangeH, y})
		}
	}
	return out
}

// whiteBody 标题条正下方是确认框的白色正文：y2+10 起 50 行、x 60–660 里近白像素占比 ≥ 0.6。
func whiteBody(b *img.BGR, y2 int) bool {
	c := b.Slice(60, y2+10, 660, min(b.H, y2+60))
	if c.H < 10 {
		return false
	}
	return fracWhere(cv.HSV(c), func(p []byte) bool { return p[1] < 40 && p[2] > 225 }) >= 0.6
}

func titleWhite(b *img.BGR, y1, y2 int) bool {
	c := b.Slice(40, y1, 320, y2)
	if c.Empty() {
		panic(&cv.Error{Err: errEmpty}) // cv2.cvtColor 收空图报错
	}
	return fracWhere(cv.HSV(c), func(p []byte) bool { return p[1] < 40 && p[2] > 235 }) >= titleWhiteMin
}

// DialogHeader 只用像素判断有没有弹窗：最下面一条带白色标题字的橙黄标题条，没有返回 nil。
func DialogHeader(b *img.BGR) *Band {
	bs := HeaderBands(b)
	for i := len(bs) - 1; i >= 0; i-- {
		if titleWhite(b, bs[i].Y1, bs[i].Y2) {
			return &bs[i]
		}
	}
	return nil
}

// ---------------------------------------------------------------- 整帧文字锚点

// TextBox 是一段去掉空格的文字和它的框。
type TextBox struct {
	Text string
	Box  ocr.Box
}

// pageTexts 整帧 OCR（缺省 3 倍），文字去掉空格（同 Python _texts）。
func pageTexts(b *img.BGR) ([]TextBox, error) {
	items, err := ocr.Boxes(b, 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	out := make([]TextBox, len(items))
	for i, it := range items {
		out[i] = TextBox{strings.ReplaceAll(it.Text, " ", ""), it.Box}
	}
	return out, nil
}

// pageDashes 统一长音（同 Python pages._DASHES；没有感叹号问号）。
var pageDashes = strings.NewReplacer("一", "ー", "－", "ー", "-", "ー", "―", "ー", "ｰ", "ー")

func cy(b ocr.Box) int { return floorDiv(b.Y1+b.Y2, 2) }
func cx(b ocr.Box) int { return floorDiv(b.X1+b.X2, 2) }

func floorDiv(a, b int) int {
	q := a / b
	if (a%b != 0) && ((a < 0) != (b < 0)) {
		q--
	}
	return q
}

// has 第一段包含 word、中心 y 在 [y1, y2] 里的文字框；没有返回 nil。非 Vision 引擎对 ≥4 字的词放宽（错一个字也算）。
func has(boxes []TextBox, word string, y1, y2 int) *ocr.Box {
	eng, _ := ocr.Engine()
	loose := utf8.RuneCountInString(word) >= 4 && eng != "vision"
	if loose {
		word = pageDashes.Replace(word)
	}
	for _, tb := range boxes {
		t := tb.Text
		if loose {
			t = pageDashes.Replace(t)
		}
		if (strings.Contains(t, word) || (loose && containsNear(t, word))) && y1 <= cy(tb.Box) && cy(tb.Box) <= y2 {
			b := tb.Box
			return &b
		}
	}
	return nil
}

func hasAny(boxes []TextBox, word string) *ocr.Box { return has(boxes, word, 0, 1280) }

// containsNear text 里有一段与 word 等长、只差一个字；≥6 字的 word 掉了任意一个字也算。
func containsNear(text, word string) bool {
	tr, wr := []rune(text), []rune(word)
	n := len(wr)
	if n >= 6 {
		for i := range n {
			if strings.Contains(text, string(wr[:i])+string(wr[i+1:])) {
				return true
			}
		}
	}
	for i := 0; i+n <= len(tr); i++ {
		d := 0
		for j := range n {
			if tr[i+j] != wr[j] {
				d++
			}
		}
		if d <= 1 {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- 页面字段

// ReadUpgradeResult 「えいえいおーを強化しました」→ 强化前的卡名、card_id 与强化后的 upgrade。卡名认不出时都给 None。
func ReadUpgradeResult(text string) map[string]any {
	name := strings.TrimSuffix(text, "を強化しました")
	c := CardFromName(name, nil)
	out := map[string]any{"name": name, "card_id": nil, "upgrade": nil, "text": text}
	if c != nil {
		out["name"] = c.Name + strings.Repeat("+", c.Upgrade)
		out["card_id"], out["upgrade"] = c.CardID, c.Upgrade+1
	}
	return out
}

var giftRegion = ocr.Box{X1: 40, Y1: 520, X2: 680, Y2: 1100}

const (
	giftMinArea = 1500
)

// ReadGift 差し入れ礼物箱：{bag: [x, y] 发光礼物袋（彩虹色）的中心 | None}。取高饱和、高亮的最大连通块。
func ReadGift(b *img.BGR) map[string]any {
	hsv := cv.HSV(crop(b, giftRegion))
	mask := img.NewGray(hsv.W, hsv.H)
	for i := range mask.Pix {
		if hsv.Pix[i*3+1] >= 120 && hsv.Pix[i*3+2] >= 200 {
			mask.Pix[i] = 1
		}
	}
	mask = cv.Morphology(mask, cv.MorphClose, cv.RectKernel{W: 9, H: 9})
	_, comps := cv.Components(mask)
	best := -1
	for i, c := range comps {
		if best < 0 || c.Area > comps[best].Area {
			best = i
		}
	}
	if best < 0 || comps[best].Area < giftMinArea {
		return map[string]any{"bag": nil}
	}
	c := comps[best].Center
	return map[string]any{"bag": []int{int(c[0] + float64(giftRegion.X1)), int(c[1] + float64(giftRegion.Y1))}}
}

var (
	refreshCancel = ocr.Point{X: 215, Y: 1160}
	refreshOK     = ocr.Point{X: 505, Y: 1160}
	refreshNumRe  = pyRe(`^\d{1,5}$`)
)

// ReadRefreshConfirm 「リフレッシュ確認」→ {p_before, p_after, cost, jewel, cancel, ok}。headerY 为标题条中心 y（nil = 658）。
func ReadRefreshConfirm(b *img.BGR, headerY *int) (map[string]any, error) {
	hy := 658
	if headerY != nil {
		hy = *headerY
	}
	y0 := max(0, hy+30)
	items, err := ocr.Boxes(b.Slice(0, y0, b.W, min(b.H, y0+600)), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	boxes := make([]TextBox, len(items))
	for i, it := range items {
		bx := it.Box
		boxes[i] = TextBox{strings.ReplaceAll(it.Text, " ", ""), ocr.Box{X1: bx.X1, Y1: bx.Y1 + y0, X2: bx.X2, Y2: bx.Y2 + y0}}
	}
	type num struct{ x, v int }
	var nums []num
	for _, tb := range boxes {
		if refreshNumRe.MatchString(tb.Text) {
			nums = append(nums, num{tb.Box.X1, mustAtoi(tb.Text)})
		}
	}
	sortStable(nums, func(a, b num) bool { return a.x < b.x })
	out := map[string]any{"p_before": nil, "p_after": nil, "cost": nil}
	if len(nums) >= 2 {
		before, after := nums[0].v, nums[len(nums)-1].v
		out["p_before"], out["p_after"], out["cost"] = before, after, before-after
	}
	jewel := false
	for _, tb := range boxes {
		jewel = jewel || strings.Contains(tb.Text, "ジュエル")
	}
	button := func(word string, def ocr.Point) []int {
		var best *ocr.Box
		for i, tb := range boxes {
			if strings.Contains(tb.Text, word) && (best == nil || tb.Box.Y1 > best.Y1) {
				best = &boxes[i].Box
			}
		}
		if best == nil {
			return []int{def.X, def.Y}
		}
		return []int{cx(*best), cy(*best)}
	}
	out["jewel"], out["cancel"], out["ok"] = jewel, button("キャンセル", refreshCancel), button("更新する", refreshOK)
	return out, nil
}

// ReadCustomizeResult 「きらきら紙吹雪+をカスタマイズしました」→ {name, card_id, upgrade}（同 Python customize.read_customize_result）。
func ReadCustomizeResult(text string) map[string]any {
	name := strings.ReplaceAll(strings.ReplaceAll(text, " ", ""), "＋", "+")
	name, _, _ = strings.Cut(name, "をカスタマイズ")
	out := map[string]any{"name": name, "card_id": nil, "upgrade": nil}
	if c := CardFromName(name, nil); c != nil {
		out["card_id"], out["upgrade"] = c.CardID, c.Upgrade
	}
	return out
}

// ---------------------------------------------------------------- スター性獲得、考前说明

var (
	starGainBox = ocr.Box{X1: 160, Y1: 560, X2: 520, Y2: 880}
	starPlusRe  = pyRe(`^[+＋](\d+)$`)
	starShownRe = pyRe(`^(\d{1,4})[)）]?$`)
	briefRows   = []struct {
		attr string
		y    int
	}{{"Vo", 930}, {"Da", 1010}, {"Vi", 1090}}
	briefByCount = map[int]int{128: 1, 64: 2, 32: 3}
	briefExamRe  = pyRe(`試験(\d)`)
	briefLineRe  = pyRe(`(\d+)\s*/\s*(\d+)\s*人`)
	briefParamRe = pyRe(`^\d{2,4}$`)
	briefPctRe   = pyRe(`^(\d{2,6})%?$`)
	briefStarRe  = pyRe(`^\d{1,4}$`)
)

func shift(items []ocr.Item, dx, dy int) []TextBox {
	out := make([]TextBox, len(items))
	for i, it := range items {
		b := it.Box
		out[i] = TextBox{strings.ReplaceAll(it.Text, " ", ""), ocr.Box{X1: b.X1 + dx, Y1: b.Y1 + dy, X2: b.X2 + dx, Y2: b.Y2 + dy}}
	}
	return out
}

// ReadStarGain スター性獲得：{gain: 本次 +N, star: None, star_shown: 金字塔下的数字}。
func ReadStarGain(b *img.BGR) (map[string]any, error) {
	items, err := ocr.Boxes(crop(b, starGainBox), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	var gain any
	bestH, bestV := 0, 0
	found := false
	for _, tb := range shift(items, starGainBox.X1, starGainBox.Y1) {
		m := starPlusRe.FindStringSubmatch(tb.Text)
		if m == nil || cx(tb.Box) < 180 || cx(tb.Box) > 330 || cy(tb.Box) < 590 || cy(tb.Box) > 740 {
			continue
		}
		h, v := tb.Box.H(), mustAtoi(m[1])
		if !found || h > bestH || (h == bestH && v > bestV) {
			bestH, bestV, found = h, v, true
		}
	}
	if found {
		gain = bestV
	}
	var shown any
	for _, scale := range []float64{3, 2} {
		items, err := ocr.OCR(b.Slice(352, 775, 520, 840), scale, ocr.JaEn)
		if err != nil {
			return nil, err
		}
		if m := starShownRe.FindStringSubmatch(strings.ReplaceAll(ocr.Joined(items, ""), " ", "")); m != nil {
			shown = mustAtoi(m[1])
			break
		}
	}
	return map[string]any{"gain": gain, "star": nil, "star_shown": shown}, nil
}

// briefPct 说明页倍率：OCR 与 ddddocr 一致才给（只比前 len(OCR) 位；OCR 首位粘了箭头时去掉首位再比）。
func briefPct(b *img.BGR, digits string, box ocr.Box) (any, error) {
	d, ok, err := ReadDddd(b, ocr.Box{X1: max(0, box.X1-4), Y1: max(0, box.Y1-4), X2: box.X2 + 4, Y2: box.Y2 + 4})
	if err != nil || !ok {
		return nil, err
	}
	s := itoa(d)
	reads := []string{s, dropFirst(s)}
	for _, c := range []string{digits, dropFirst(digits)} {
		if utf8.RuneCountInString(c) < 2 {
			continue
		}
		for _, r := range reads {
			if strings.HasPrefix(r, c) {
				return mustAtoi(c), nil
			}
		}
	}
	return nil, nil
}

// ReadExamBriefing 考前说明页：{exam_no, rank_line, participants, star, star_ok, params, multiplier_pct, complete, finals}。
func ReadExamBriefing(b *img.BGR, boxes []TextBox) (map[string]any, error) {
	line := ""
	for _, tb := range boxes {
		if strings.Contains(tb.Text, "突破ライン") {
			line = tb.Text
			break
		}
	}
	var parts []string
	for _, tb := range boxes {
		if d := cy(tb.Box) - 635; d >= -25 && d <= 25 {
			parts = append(parts, tb.Text)
		}
	}
	joined := strings.Join(parts, " ")
	var rankLine, participants any
	pn := 0
	if m := briefLineRe.FindStringSubmatch(joined); m != nil {
		a, n := mustAtoi(m[1]), mustAtoi(m[2])
		participants, pn = n, n
		if a*2 == n {
			rankLine = a
		}
	}
	params, mult := map[string]any{}, map[string]any{}
	for _, row := range briefRows {
		var p, pct any
		var hitDigits string
		var hitBox *ocr.Box
		for _, tb := range boxes {
			if d := cy(tb.Box) - row.y; d < -30 || d > 30 {
				continue
			}
			if p == nil && briefParamRe.MatchString(tb.Text) && tb.Box.X1 < 230 {
				p = mustAtoi(tb.Text)
			}
			if hitBox == nil {
				if mm := briefPctRe.FindStringSubmatch(pyStrip(tb.Text)); mm != nil && 260 <= tb.Box.X1 && tb.Box.X1 <= 340 {
					bx := tb.Box
					hitDigits, hitBox = mm[1], &bx
				}
			}
		}
		if hitBox != nil {
			v, err := briefPct(b, hitDigits, *hitBox)
			if err != nil {
				return nil, err
			}
			pct = v
		}
		params[row.attr], mult[row.attr] = p, pct
	}
	var star any
	for _, tb := range boxes {
		if d := cy(tb.Box) - 852; briefStarRe.MatchString(tb.Text) && d >= -25 && d <= 25 && tb.Box.X1 < 230 {
			star = mustAtoi(tb.Text)
			break
		}
	}
	animating := hasAny(boxes, "ボーナス") != nil
	if animating {
		for k := range mult {
			mult[k] = nil
		}
	}
	var examNo any
	if m := briefExamRe.FindStringSubmatch(line); m != nil {
		examNo = mustAtoi(m[1])
	} else if rankLine != nil {
		if v, ok := briefByCount[pn]; ok {
			examNo = v
		}
	}
	var starOK any
	if has(boxes, "達成", 820, 880) != nil {
		starOK = true
	}
	return map[string]any{
		"exam_no": examNo, "rank_line": rankLine, "participants": participants, "star": star, "star_ok": starOK,
		"params": params, "multiplier_pct": mult, "complete": !animating, "finals": hasAny(boxes, "優勝条件") != nil,
	}, nil
}
