package vision

import (
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 快速判页（同 Python hiro.vision.quick）：只用模板、像素和局部 OCR，目标每帧 ≤ 0.3 秒；认不出返回 nil，照旧走原来的流程。
// 结果 {page, tap, dispatch, fields, ms}，见 Python 模块说明。

var (
	skipTap        = []int{12, 12}
	loginBonusTap  = []int{360, 1180}
	deckListClose  = []int{360, 1157}
	pitemNoteTap   = []int{360, 250}
	scheduleClose  = []int{360, 1200}
	purchaseWords  = []string{"引く", "購入", "ガシャ", "パック", "円"}
	scheduleBtnBox = ocr.Box{X1: 335, Y1: 1180, X2: 385, Y2: 1230}
	receiveBtnBox  = ocr.Box{X1: 250, Y1: 1050, X2: 470, Y2: 1130}
)

const (
	dialogHeaderMaxY = 1100
	refreshTitleMin  = 0.8
	flashCyanMin     = 0.6
	loadingPaleMin   = 0.5
	whiteX1, whiteX2 = 60, 660
	panelY1, panelY2 = 650, 940
	bubbleY1         = 940
	bubbleY2         = 1135
	receiveBtnMin    = 0.4
)

type quickTpl struct {
	box ocr.Box
	thr float64
}

// 灰度模板：名称 → (搜索区域, 阈值)。
var grayTpl = map[string]quickTpl{
	"quick_pitem_custom_title": {ocr.Box{X1: 100, Y1: 640, X2: 620, Y2: 760}, 0.85},
	"quick_gift_label":         {ocr.Box{X1: 40, Y1: 20, X2: 200, Y2: 90}, 0.9},
	"quick_drink_gain":         {ocr.Box{X1: 150, Y1: 660, X2: 560, Y2: 800}, 0.8},
	"quick_tap_en":             {ocr.Box{X1: 300, Y1: 1180, X2: 420, Y2: 1260}, 0.85},
	"quick_tap_ja":             {ocr.Box{X1: 240, Y1: 1160, X2: 490, Y2: 1240}, 0.85},
	"quick_hud_label":          {ocr.Box{X1: 500, Y1: 10, X2: 690, Y2: 80}, 0.8},
	"quick_pass_label":         {ocr.Box{X1: 20, Y1: 580, X2: 220, Y2: 680}, 0.9},
	"quick_finals_label":       {ocr.Box{X1: 20, Y1: 580, X2: 220, Y2: 680}, 0.8},
	"quick_ptecho_label":       {ocr.Box{X1: 560, Y1: 660, X2: 720, Y2: 760}, 0.8},
}

// 白色字形模板（背景会变）：名称 → (搜索区域, 阈值)。
var whiteTpl = map[string]quickTpl{
	"quick_commu_skip":        {ocr.Box{X1: 150, Y1: 1160, X2: 300, Y2: 1240}, 0.6},
	"quick_commu_auto":        {ocr.Box{X1: 410, Y1: 1160, X2: 570, Y2: 1240}, 0.6},
	"quick_now_loading":       {ocr.Box{X1: 400, Y1: 1150, X2: 720, Y2: 1280}, 0.75},
	"quick_now_loading2":      {ocr.Box{X1: 400, Y1: 1150, X2: 720, Y2: 1280}, 0.5},
	"quick_custom_cutin":      {ocr.Box{X1: 150, Y1: 740, X2: 570, Y2: 870}, 0.8},
	"quick_pdrink_max_title":  {ocr.Box{X1: 40, Y1: 60, X2: 560, Y2: 170}, 0.8},
	"quick_event_period":      {ocr.Box{X1: 40, Y1: 180, X2: 500, Y2: 290}, 0.85},
	"quick_select_bonus":      {ocr.Box{X1: 100, Y1: 100, X2: 620, Y2: 240}, 0.8},
	"quick_star_gain":         {ocr.Box{X1: 100, Y1: 340, X2: 620, Y2: 470}, 0.7},
	"quick_toppa":             {ocr.Box{X1: 200, Y1: 760, X2: 520, Y2: 960}, 0.85},
	"quick_custom_pitem":      {ocr.Box{X1: 60, Y1: 370, X2: 660, Y2: 500}, 0.8},
	"quick_custom_pitem_get":  {ocr.Box{X1: 10, Y1: 190, X2: 710, Y2: 330}, 0.8},
	"quick_tap_de_next":       {ocr.Box{X1: 200, Y1: 1100, X2: 520, Y2: 1200}, 0.8},
	"quick_select_eval_title": {ocr.Box{X1: 60, Y1: 100, X2: 660, Y2: 280}, 0.8},
}

// frame 是这一帧的共用中间结果（同 Python 的 _Frame：白色掩码、标题条只算一次）。
type frame struct {
	b     *img.BGR
	white *img.Gray
	bands []Band
	got   bool
}

func (f *frame) whiteMask() *img.Gray {
	if f.white == nil {
		f.white = whiteMask(f.b, 50)
	}
	return f.white
}

func (f *frame) headerBands() []Band {
	if !f.got {
		f.bands, f.got = HeaderBands(f.b), true
	}
	return f.bands
}

func quickGray(gray *img.Gray, name string) float64 {
	t := grayTpl[name]
	return matchGray(gray.Slice(t.box.X1, t.box.Y1, t.box.X2, t.box.Y2), localTplGray(name))
}

func quickHit(gray *img.Gray, name string) bool { return quickGray(gray, name) >= grayTpl[name].thr }

func whiteTplOf(name string) *img.Gray {
	return memo("white50:"+name, func() *img.Gray { return whiteMask(localTpl(name), 50) })
}

func (f *frame) whiteHit(name string) bool {
	t := whiteTpl[name]
	region := f.whiteMask().Slice(t.box.X1, t.box.Y1, t.box.X2, t.box.Y2)
	return matchGray(region, whiteTplOf(name)) >= t.thr
}

// CommuBar 剧情底栏「SKIP」「AUTO」都在（快速判页认 commu 的同一条件）。
func CommuBar(b *img.BGR) bool {
	if b == nil {
		return false
	}
	f := &frame{b: b}
	return f.whiteHit("quick_commu_skip") && f.whiteHit("quick_commu_auto")
}

// sub4 隔 4 取 1（numpy 的 hsv[::4, ::4]）里满足 f 的比例。
func sub4(hsv *img.BGR, f func(p []byte) bool) float64 {
	n, k := 0, 0
	for y := 0; y < hsv.H; y += 4 {
		for x := 0; x < hsv.W; x += 4 {
			n++
			if f(hsv.Pix[(y*hsv.W+x)*3:]) {
				k++
			}
		}
	}
	return float64(k) / float64(n)
}

func cyan(hsv *img.BGR) float64 {
	return sub4(hsv, func(p []byte) bool { return p[0] >= 88 && p[0] <= 100 && p[1] >= 200 && p[2] >= 180 })
}

func pale(hsv *img.BGR) float64 {
	return sub4(hsv, func(p []byte) bool { return p[0] >= 88 && p[0] <= 108 && p[1] >= 15 && p[1] <= 90 && p[2] >= 200 })
}

func nearWhite25(p []byte) bool { return p[2] >= 235 && p[1] <= 25 }

// whiteRows 每行 x 60–660 里近白像素占比 ≥ 0.85。
func whiteRows(hsv *img.BGR) []bool {
	frac := rowFrac(hsv.Slice(whiteX1, 0, whiteX2, hsv.H), nearWhite25)
	out := make([]bool, len(frac))
	for i, v := range frac {
		out[i] = v >= 0.85
	}
	return out
}

type run struct{ a, b int }

func runs(flags []bool, y1, y2, minLen int) []run {
	var out []run
	start := -1
	for y := y1; y < y2; y++ {
		switch {
		case flags[y] && start < 0:
			start = y
		case !flags[y] && start >= 0:
			if y-start >= minLen {
				out = append(out, run{start, y})
			}
			start = -1
		}
	}
	if start >= 0 && y2-start >= minLen {
		out = append(out, run{start, y2})
	}
	return out
}

func acquireBarRows(hsv *img.BGR) bool {
	frac := rowFrac(hsv.Slice(160, 840, 560, 920), func(p []byte) bool { return p[2] >= 230 && p[1] <= 30 })
	n := 0
	for _, v := range frac {
		if v >= 0.6 {
			n++
		}
	}
	return 16 <= n && n <= 26
}

func colFracMax(b *img.BGR, f func(p []byte) bool) float64 {
	best := 0.0
	for x := range b.W {
		k := 0
		for y := range b.H {
			if f(b.Pix[(y*b.W+x)*3:]) {
				k++
			}
		}
		best = max(best, float64(k)/float64(b.H))
	}
	return best
}

func maxOf(v []float64) float64 {
	best := v[0]
	for _, x := range v[1:] {
		best = max(best, x)
	}
	return best
}

// pitemNote P 道具效果说明浮条：整行高亮的连续带 + 左右两道白色竖边，上下各 30 行都不是高亮 → (y1, y2)。
func pitemNote(hsv *img.BGR) *run {
	const y0, y3 = 380, 720
	bright := func(p []byte) bool { return p[2] >= 215 }
	fr := rowFrac(hsv.Slice(60, y0, 660, y3), bright)
	var rs []run
	start := -1
	for i := 0; i <= len(fr); i++ {
		f := i < len(fr) && fr[i] >= 0.6
		switch {
		case f && start < 0:
			start = i
		case !f && start >= 0:
			rs = append(rs, run{start, i})
			start = -1
		}
	}
	for _, r := range rs {
		y1, y2 := y0+r.a, y0+r.b
		if y2-y1 < 80 || y2-y1 > 200 {
			continue
		}
		band := hsv.Slice(0, y1+5, hsv.W, y2-5)
		white := func(p []byte) bool { return p[1] < 35 && p[2] >= 235 }
		ok := true
		for _, xr := range [][2]int{{20, 30}, {690, 700}} {
			if colFracMax(band.Slice(xr[0], 0, xr[1], band.H), white) < 0.9 {
				ok = false
				break
			}
		}
		if !ok {
			continue
		}
		above := rowFrac(hsv.Slice(60, max(0, y1-35), 660, y1-5), bright)
		below := rowFrac(hsv.Slice(60, y2+5, 660, y2+35), bright)
		if len(above) > 0 && len(below) > 0 && maxOf(above) < 0.3 && maxOf(below) < 0.3 {
			return &run{y1, y2}
		}
	}
	return nil
}

func receiveButton(hsv *img.BGR) bool {
	return fracWhere(crop(hsv, receiveBtnBox), func(p []byte) bool { return p[0] >= 3 && p[0] <= 20 && p[1] >= 100 && p[2] >= 150 }) >= receiveBtnMin
}

// refreshDialog 「リフレッシュ確認」：带白字标题的橙黄标题条里对上标题模板 → 标题条中心 y；不是返回 nil。
func (f *frame) refreshDialog() *int {
	tpl := whiteTplOf("quick_refresh_title")
	bs := f.headerBands()
	for i := len(bs) - 1; i >= 0; i-- {
		y1, y2 := bs[i].Y1, bs[i].Y2
		if !titleWhite(f.b, y1, y2) {
			continue
		}
		reg := whiteMask(f.b.Slice(40, max(0, y1-6), 420, y2+6), 50)
		if reg.H >= tpl.H && matchGray(reg, tpl) >= refreshTitleMin {
			c := floorDiv(y1+y2, 2)
			return &c
		}
	}
	return nil
}

// dialog 确认框 / 弹窗：橙黄标题条且左段有白色标题字（不做 OCR）。
func (f *frame) dialog() bool {
	for _, bd := range f.headerBands() {
		if bd.Y1 >= dialogHeaderMaxY {
			continue
		}
		if fracWhere(cv.HSV(f.b.Slice(40, bd.Y1, 320, bd.Y2)), func(p []byte) bool { return p[1] < 40 && p[2] > 235 }) >= titleWhiteMin {
			return true
		}
	}
	return false
}

func hasPurchaseWords(texts []string) bool {
	for _, t := range texts {
		t = strings.ReplaceAll(t, " ", "")
		for _, w := range purchaseWords {
			if strings.Contains(t, w) {
				return true
			}
		}
	}
	return false
}

func eventPurchasePage(b *img.BGR) (bool, error) {
	items, err := ocr.OCR(b.Slice(0, 900, b.W, 1280), 3, ocr.JaEn)
	if err != nil {
		return false, err
	}
	texts := make([]string, len(items))
	for i, it := range items {
		texts[i] = it.Text
	}
	return hasPurchaseWords(texts), nil
}

// dayCountdown 倒计时横幅的文字（含「まで」和「日」），不是返回空串。
func dayCountdown(b *img.BGR) (string, error) {
	items, err := ocr.OCR(b.Slice(120, 60, 620, 180), 3, ocr.JaEn)
	if err != nil {
		return "", err
	}
	text := strings.ReplaceAll(ocr.Joined(items, ""), " ", "")
	if strings.Contains(text, "まで") && strings.Contains(text, "日") {
		return text, nil
	}
	return "", nil
}

func scheduleOverlay(hsv *img.BGR) bool {
	n, ok := 0, 0
	for y := 1078; y < min(1168, hsv.H); y += 6 {
		row := hsv.Slice(20, y, 700, y+1)
		n++
		if fracWhere(row, func(p []byte) bool { return p[0] >= 85 && p[0] <= 100 && p[1] >= 120 && p[2] >= 150 }) >= 0.3 {
			ok++
		}
	}
	if n == 0 || float64(ok)/float64(n) < 0.9 {
		return false
	}
	return fracWhere(crop(hsv, scheduleBtnBox), nearWhite25) >= 0.5
}

// SplitCardNames 「仕草の基本と距離感の基本+」→ 两张卡。卡名里也可能有「と」，按每个「と」试切，两边都精确命中卡名才算。
func SplitCardNames(text string) []CardName {
	text = strings.ReplaceAll(strings.ReplaceAll(text, " ", ""), "＋", "+")
	if one := CardFromName(text, nil); one != nil {
		return []CardName{*one}
	}
	rs := []rune(text)
	for i, ch := range rs {
		if ch == 'と' && i > 0 && i < len(rs)-1 {
			left, right := CardFromName(string(rs[:i]), nil), SplitCardNames(string(rs[i+1:]))
			if left != nil && len(right) > 0 {
				return append([]CardName{*left}, right...)
			}
		}
	}
	return nil
}

var (
	bubbleFixRe    = pyRe(`を(強化|削.|カスタマイズ)しまし`)
	bubbleDeleteRe = pyRe(`を(削.|除去)しまし`)
)

// ReadBubble 底部消息气泡的文字 → (页面名, 字段)。
func ReadBubble(b *img.BGR, y1, y2 int) (string, map[string]any, error) {
	c := b.Slice(30, max(0, y1-10), 690, y2+10)
	items, err := ocr.OCR(c, 2, ocr.JaEn)
	if err != nil {
		return "", nil, err
	}
	text := strings.ReplaceAll(ocr.Joined(items, ""), " ", "")
	if eng, _ := ocr.Engine(); eng != "vision" && bubbleFixRe.MatchString(text) {
		alt, err := ocr.OCR(c, 3, ocr.JaEn)
		if err != nil {
			return "", nil, err
		}
		at := strings.ReplaceAll(ocr.Joined(alt, ""), " ", "")
		if strings.ReplaceAll(at, "+", "") == strings.ReplaceAll(text, "+", "") && strings.Count(at, "+") > strings.Count(text, "+") {
			text = at
		}
	}
	if i := strings.Index(text, "を強化しました"); i >= 0 {
		return "UPGRADE_RESULT", ReadUpgradeResult(text[:i+len("を強化しました")]), nil
	}
	if strings.Contains(text, "をカスタマイズしまし") {
		return "CUSTOMIZE_RESULT", ReadCustomizeResult(text), nil
	}
	if loc := bubbleDeleteRe.FindStringIndex(text); loc != nil {
		var cards any
		if cs := SplitCardNames(text[:loc[0]]); cs != nil {
			cards = cs
		}
		return "DELETE_RESULT", map[string]any{"cards": cards, "text": text}, nil
	}
	return "TRANSITION", map[string]any{"kind": "message", "text": text}, nil
}

// BottomBubble 底部白色消息气泡（上方没有选择面板）→ ReadBubble 的 (页面名, 字段)；没有气泡返回空页面名。
func BottomBubble(b, hsv *img.BGR, white []bool) (string, map[string]any, error) {
	if hsv == nil {
		hsv = cv.HSV(b)
	}
	if white == nil {
		white = whiteRows(hsv)
	}
	rs := runs(white, bubbleY1, bubbleY2, 40)
	if len(rs) == 0 || len(runs(white, panelY1, panelY2, 40)) > 0 {
		return "", nil, nil
	}
	frac := rowFrac(hsv.Slice(whiteX1, 0, whiteX2, hsv.H), nearWhite25)
	top := rs[0].a
	for top > panelY1 && frac[top-1] >= 0.5 {
		top--
	}
	return ReadBubble(b, top, rs[len(rs)-1].b)
}

// Quick 是快速判页结果。
type Quick struct {
	Page     string         `json:"page"`
	Tap      []int          `json:"tap"`
	Dispatch *string        `json:"dispatch"`
	Fields   map[string]any `json:"fields"`
	Ms       float64        `json:"ms"`
}

// QuickPage 快速判页；认不出返回 nil。
func QuickPage(b *img.BGR) (q *Quick, err error) {
	defer cv.Recover(&err)
	if b == nil || b.H < b.W {
		return nil, nil // 横屏帧：区域都按竖屏定
	}
	t0 := time.Now()
	done := func(page string, tap []int, dispatch string, fields map[string]any) *Quick {
		if fields == nil {
			fields = map[string]any{}
		}
		out := &Quick{Page: page, Tap: tap, Fields: fields, Ms: float64(time.Since(t0).Microseconds()) / 1000}
		if dispatch != "" {
			out.Dispatch = &dispatch
		}
		return out
	}
	f := &frame{b: b}
	if IsDeckList(b) {
		return done("DECK_LIST", deckListClose, "", nil), nil
	}
	gray := cv.Gray(b)
	if IsSelectPItem(b) || quickHit(gray, "quick_pitem_custom_title") {
		return done("SELECT_PITEM", nil, "SELECT_PITEM", nil), nil
	}
	hsv := cv.HSV(b)
	if pale(hsv) >= loadingPaleMin {
		return done("LOADING", nil, "", nil), nil
	}
	if f.whiteHit("quick_event_period") {
		buy, err := eventPurchasePage(b)
		if err != nil {
			return nil, err
		}
		if buy {
			return done("TRANSITION", nil, "", map[string]any{"kind": "event_purchase"}), nil
		}
		return done("TRANSITION", loginBonusTap, "", map[string]any{"kind": "login_bonus"}), nil
	}
	if f.whiteHit("quick_now_loading") || f.whiteHit("quick_now_loading2") {
		return done("LOADING", nil, "", nil), nil
	}
	if cyan(hsv) >= flashCyanMin {
		return done("TRANSITION", nil, "", map[string]any{"kind": "flash"}), nil
	}
	if f.whiteHit("quick_custom_cutin") {
		return done("TRANSITION", skipTap, "", map[string]any{"kind": "custom_cutin"}), nil
	}
	if f.whiteHit("quick_commu_skip") && f.whiteHit("quick_commu_auto") {
		r := whiteTpl["quick_commu_skip"].box
		return done("commu", []int{floorDiv(r.X1+r.X2, 2), floorDiv(r.Y1+r.Y2, 2)}, "", nil), nil
	}
	if acquireBarRows(hsv) {
		acquired, err := ReadAcquirePopup(b, false)
		if err != nil {
			return nil, err
		}
		if acquired != nil {
			return done("ACQUIRE", skipTap, "", acquired), nil
		}
	}
	if ry := f.refreshDialog(); ry != nil {
		confirm, err := ReadRefreshConfirm(b, ry)
		if err != nil {
			return nil, err
		}
		tap, _ := confirm["cancel"].([]int)
		return done("DIALOG", tap, "", map[string]any{"kind": "refresh_confirm", "title": "リフレッシュ確認", "danger": false, "confirm": confirm}), nil
	}
	if f.dialog() {
		return nil, nil // 确认框 / 弹窗：原流程处理
	}
	white := whiteRows(hsv)
	var hintXY []int
	for _, name := range []string{"quick_tap_en", "quick_tap_ja"} {
		if quickHit(gray, name) {
			r := grayTpl[name].box
			hintXY = []int{floorDiv(r.X1+r.X2, 2), floorDiv(r.Y1+r.Y2, 2)}
			break
		}
	}
	if quickHit(gray, "quick_pass_label") || quickHit(gray, "quick_finals_label") {
		if hintXY != nil {
			boxes, err := pageTexts(b)
			if err != nil {
				return nil, err
			}
			brief, err := ReadExamBriefing(b, boxes)
			if err != nil {
				return nil, err
			}
			if brief["complete"] == true {
				return done("HIF_EXAM_BRIEFING", hintXY, "", brief), nil
			}
		}
		return done("TRANSITION", nil, "", map[string]any{"kind": "exam_bonus"}), nil
	}
	if hintXY != nil {
		return done("TRANSITION", hintXY, "", map[string]any{"kind": "tap_hint"}), nil
	}
	if f.whiteHit("quick_tap_de_next") && f.whiteHit("quick_select_eval_title") {
		r := whiteTpl["quick_tap_de_next"].box
		return done("TRANSITION", []int{floorDiv(r.X1+r.X2, 2), floorDiv(r.Y1+r.Y2, 2)}, "", map[string]any{"kind": "tap_hint"}), nil
	}
	if f.whiteHit("quick_star_gain") {
		sg, err := ReadStarGain(b)
		if err != nil {
			return nil, err
		}
		return done("STAR_GAIN", skipTap, "", sg), nil
	}
	for _, nk := range [][2]string{{"quick_select_bonus", "select_bonus"}, {"quick_toppa", "toppa"}, {"quick_custom_pitem_get", "custom_pitem_get"}, {"quick_custom_pitem", "custom_pitem"}} {
		if f.whiteHit(nk[0]) {
			return done("TRANSITION", skipTap, "", map[string]any{"kind": nk[1]}), nil
		}
	}
	hud := quickHit(gray, "quick_hud_label")
	gift := quickHit(gray, "quick_gift_label")
	if hud && !gift && pitemNote(hsv) != nil {
		return done("TRANSITION", pitemNoteTap, "", map[string]any{"kind": "pitem_note"}), nil
	}
	if gift && quickHit(gray, "quick_drink_gain") {
		return done("TRANSITION", nil, "", map[string]any{"kind": "gift_effect"}), nil
	}
	if gift && len(runs(white, panelY1, panelY2, 40)) == 0 && !receiveButton(hsv) {
		g := ReadGift(b)
		if bag, ok := g["bag"].([]int); ok {
			return done("GIFT", bag, "", map[string]any{"bag": bag}), nil
		}
	}
	if !hud {
		cd, err := dayCountdown(b)
		if err != nil {
			return nil, err
		}
		if cd != "" {
			return done("TRANSITION", nil, "", map[string]any{"kind": "day_countdown", "text": cd}), nil
		}
	}
	if scheduleOverlay(hsv) {
		return done("TRANSITION", scheduleClose, "", map[string]any{"kind": "schedule"}), nil
	}
	if hud {
		page, fields, err := BottomBubble(b, hsv, white)
		if err != nil {
			return nil, err
		}
		if page != "" {
			return done(page, skipTap, "", fields), nil
		}
	}
	return nil, nil
}

// FallbackPage 模板判 UNKNOWN 之后的兜底（只看模板 / 像素）：ACTION_SELECT（「P手帳」标签）、PDRINK_MAX（「Pドリンク所持上限」标题）。
func FallbackPage(b *img.BGR) string {
	if b == nil || b.H < b.W {
		return ""
	}
	f := &frame{b: b}
	if f.whiteHit("quick_pdrink_max_title") && f.dialog() {
		return "PDRINK_MAX"
	}
	if quickHit(cv.Gray(b), "quick_ptecho_label") && !f.dialog() {
		return "ACTION_SELECT"
	}
	return ""
}
