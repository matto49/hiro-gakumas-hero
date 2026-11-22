package vision

import (
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/vision/ui"
)

// 培育「行动选择」页（同 Python hiro.vision.action）：头部（距考试天数、体力、P 点、建议）、中部三维 / 上限 / 成长率 / 星、底部行动按钮。

var attrs3 = []string{"vo", "da", "vi"}

var (
	statBoxes      = map[string]ocr.Box{"vo": {X1: 200, Y1: 684, X2: 275, Y2: 719}, "da": {X1: 345, Y1: 682, X2: 420, Y2: 720}, "vi": {X1: 478, Y1: 684, X2: 558, Y2: 718}}
	statIconHue    = map[string][2]byte{"vo": {155, 178}, "da": {98, 112}, "vi": {10, 28}}
	statIconSearch = map[string][2]int{"vo": {120, 300}, "da": {260, 440}, "vi": {400, 590}}
	capBoxes       = map[string]ocr.Box{"vo": {X1: 178, Y1: 718, X2: 265, Y2: 748}, "da": {X1: 320, Y1: 718, X2: 406, Y2: 748}, "vi": {X1: 462, Y1: 718, X2: 548, Y2: 748}}
	growthBoxes    = map[string]ocr.Box{"vo": {X1: 176, Y1: 750, X2: 262, Y2: 780}, "da": {X1: 312, Y1: 750, X2: 404, Y2: 780}, "vi": {X1: 456, Y1: 750, X2: 548, Y2: 780}}
	starBox        = ocr.Box{X1: 64, Y1: 686, X2: 122, Y2: 718}
	staminaBox     = ocr.Box{X1: 322, Y1: 50, X2: 436, Y2: 94}
	pointsBox      = ocr.Box{X1: 330, Y1: 94, X2: 440, Y2: 136}
	daysDigitBox   = ocr.Box{X1: 78, Y1: 82, X2: 118, Y2: 132}
	attrHue        = map[string][2]byte{"vo": {160, 180}, "da": {95, 115}, "vi": {15, 35}}
)

const (
	statRowY1, statRowY2 = 686, 718
	statDigitsW          = 112
	statRightLimit       = 590
	headerBand           = 140
	adviceY1, adviceY2   = 160, 240
	buttonY1, buttonY2   = 840, 1120
	weeksMax             = 12
)

type actionPattern struct {
	re   *regexp.Regexp
	kind string
}

var actionPatterns = []actionPattern{
	{pyRe(`(Vo|Da|Vi)?\.?公開レッ[スズ]ン`), "open_lesson"},
	{pyRe(`授業`), "class"},
	{pyRe(`休む`), "rest"},
	{pyRe(`相談`), "consult"},
	{pyRe(`差し入れ`), "present"},
	{pyRe(`特別指導`), "special_training"},
	{pyRe(`おでかけ`), "outing"},
	{pyRe(`^営業$`), "sales"},
	{pyRe(`活動支給`), "allowance"},
	{pyRe(`(Vo|Da|Vi)?\.?自主レッ[スズ]ン`), "self_lesson"},
	{pyRe(`(Vo|Da|Vi)?\.?レッ[スズ]ン`), "lesson"},
}

// ActionOption 是一个可选行动。
type ActionOption struct {
	Kind   string  `json:"kind"`
	Label  string  `json:"label"`
	Attr   *string `json:"attr"`
	Center [2]int  `json:"center"`
	HPCost *int    `json:"hp_cost"`
	HPGain *int    `json:"hp_gain"`
	SP     *bool   `json:"sp"`
}

// ActionSelectState 是选行动页的读数。
type ActionSelectState struct {
	Exam       *string             `json:"exam"`
	DaysLeft   *int                `json:"days_left"`
	Stamina    *int                `json:"stamina"`
	MaxStamina *int                `json:"max_stamina"`
	PPoints    *int                `json:"p_points"`
	Params     map[string]*int     `json:"params"`
	ParamCaps  map[string]*int     `json:"param_caps"`
	GrowthPct  map[string]*float64 `json:"growth_pct"`
	Star       *int                `json:"star"`
	Advice     *string             `json:"advice"`
	Options    []ActionOption      `json:"options"`
	ExamNext   *string             `json:"exam_next"`
	WeeksLeft  *int                `json:"weeks_left"`
	Complete   bool                `json:"complete"`
}

func (s *ActionSelectState) complete() bool {
	need := map[string]bool{"class": true, "open_lesson": true}
	if s.ExamNext != nil {
		need = map[string]bool{"open_lesson": true}
	}
	for _, o := range s.Options {
		if need[o.Kind] && (o.Attr == nil || *o.Attr == "") {
			return false
		}
	}
	if s.DaysLeft == nil || s.Stamina == nil || s.PPoints == nil || len(s.Options) == 0 {
		return false
	}
	for _, v := range s.Params {
		if v == nil {
			return false
		}
	}
	return true
}

func iptr(v int, ok bool) *int {
	if !ok {
		return nil
	}
	return &v
}

func sptr(s string) *string { return &s }

// hueVote 鲜艳像素按属性色相计数，取最多的（并列取 vo / da / vi 里靠前的）。
func hueVote(hsv *img.BGR, sMin, vMin byte) (string, int, int) {
	counts := map[string]int{}
	total := 0
	for i := range hsv.W * hsv.H {
		p := hsv.Pix[i*3 : i*3+3]
		if p[1] > sMin && p[2] > vMin {
			total++
			for _, a := range attrs3 {
				if r := attrHue[a]; p[0] >= r[0] && p[0] <= r[1] {
					counts[a]++
				}
			}
		}
	}
	best := attrs3[0]
	for _, a := range attrs3[1:] {
		if counts[a] > counts[best] {
			best = a
		}
	}
	return best, counts[best], total
}

func attrByBadge(b *img.BGR, cx, cy int) *string {
	best, n, total := hueVote(cv.HSV(b.Slice(cx+10, cy-50, cx+60, cy+10)), 120, 120)
	if total < 40 || n < 30 {
		return nil
	}
	return &best
}

func attrByBand(b *img.BGR, cx, ty int) *string {
	band := b.Slice(max(cx-75, 0), max(ty-14, 0), min(cx+75, b.W), min(ty+14, b.H))
	if size(band) == 0 {
		return nil
	}
	best, n, total := hueVote(cv.HSV(band), 120, 120)
	if total < 200 || float64(n) < 0.5*float64(total) {
		return nil
	}
	return &best
}

var oikomiRe = pyRe(`(ボーカル|ダンス|ビジュアル)を重点的`)

var oikomiAttr = map[string]string{"ボーカル": "vo", "ダンス": "da", "ビジュアル": "vi"}

func oikomiOptions(b *img.BGR) ([]ActionOption, error) {
	const y1, y2 = 600, 1000
	items, err := ocr.Boxes(b.Rows(y1, y2), 2, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	var out []ActionOption
	for _, it := range items {
		text := strings.ReplaceAll(it.Text, " ", "")
		m := oikomiRe.FindStringSubmatch(text)
		if m == nil {
			continue
		}
		cyy := cy(it.Box) + y1
		best, n, _ := hueVote(cv.HSV(b.Slice(55, cyy-22, 115, cyy+22)), 100, 120)
		attr := oikomiAttr[m[1]]
		if n >= 40 {
			attr = best
		}
		out = append(out, ActionOption{Kind: "oikomi", Label: text, Attr: &attr, Center: [2]int{cx(it.Box), cyy}})
	}
	if len(out) != 3 {
		return nil, nil
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Center[1] < out[j].Center[1] })
	return out, nil
}

var trailingNum = pyRe(`(\d{2,4})$`)

func oikomiParams(b *img.BGR) (map[string]*int, error) {
	items, err := ocr.Boxes(b.Rows(425, 485), 2, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	type xv struct {
		x float64
		v int
	}
	var nums []xv
	for _, it := range items {
		if m := trailingNum.FindStringSubmatch(pyStrip(it.Text)); m != nil {
			nums = append(nums, xv{float64(it.Box.X1+it.Box.X2) / 2, mustAtoi(m[1])})
		}
	}
	out := map[string]*int{}
	if len(nums) != 3 {
		for _, a := range attrs3 {
			out[a] = nil
		}
		return out, nil
	}
	sort.SliceStable(nums, func(i, j int) bool { return nums[i].x < nums[j].x || (nums[i].x == nums[j].x && nums[i].v < nums[j].v) })
	for i, a := range attrs3 {
		v := nums[i].v
		out[a] = &v
	}
	return out, nil
}

var costRe = pyRe(`^\s*-?\s*(\d{1,2})\s*$`)

func spBadges(b *img.BGR) []ocr.Point {
	var out []ocr.Point
	for _, m := range ui.Tpl("InProduce.IconSp").FindAll(b, ui.Opt{}) {
		out = append(out, m.Center())
	}
	return out
}

func actionOptions(b *img.BGR, hajime bool) ([]ActionOption, error) {
	items, err := ocr.Boxes(b.Rows(buttonY1, buttonY2), 2, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	type cost struct {
		x, y float64
		c    int
	}
	var costs []cost
	tokens := make([]TextBox, len(items))
	for i, it := range items {
		bx := it.Box
		tokens[i] = TextBox{it.Text, ocr.Box{X1: bx.X1, Y1: bx.Y1 + buttonY1, X2: bx.X2, Y2: bx.Y2 + buttonY1}}
		if m := costRe.FindStringSubmatch(it.Text); m != nil {
			tb := tokens[i].Box
			costs = append(costs, cost{float64(tb.X1+tb.X2) / 2, float64(tb.Y1+tb.Y2) / 2, mustAtoi(m[1])})
		}
	}
	var badges []ocr.Point
	gotBadges := false
	out := []ActionOption{}
	eng, _ := ocr.Engine()
	for _, tk := range tokens {
		compact := strings.ReplaceAll(tk.Text, " ", "")
		for _, p := range actionPatterns {
			m := p.re.FindStringSubmatch(compact)
			if m == nil {
				continue
			}
			cxx, ty := cx(tk.Box), cy(tk.Box)
			center := [2]int{cxx, ty - 70}
			if p.kind == "rest" {
				center = [2]int{cxx, ty - 22}
			}
			var attr *string
			group := len(m) > 1 && m[1] != ""
			switch {
			case p.kind == "self_lesson":
				attr = attrByBadge(b, cxx, ty-20)
				if attr == nil && group {
					attr = sptr(strings.ToLower(m[1]))
				}
			case group:
				attr = sptr(strings.ToLower(m[1]))
			case p.kind == "class" && !hajime:
				attr = attrByBadge(b, cxx, ty-20)
			}
			if attr == nil && (p.kind == "open_lesson" || p.kind == "lesson") {
				attr = attrByBand(b, cxx, ty)
			}
			o := ActionOption{Kind: p.kind, Label: compact, Attr: attr, Center: center}
			if p.kind == "lesson" || p.kind == "self_lesson" {
				if !gotBadges {
					badges, gotBadges = spBadges(b), true
				}
				sp := false
				for _, bp := range badges {
					if dx, dy := bp.X-cxx, bp.Y-ty; -110 < dx && dx < -20 && -190 < dy && dy < -100 {
						sp = true
						break
					}
				}
				o.SP = &sp
			}
			if p.kind == "rest" {
				v, ok, err := ReadNumber(b, ocr.Box{X1: cxx - 58, Y1: ty - 96, X2: cxx - 14, Y2: ty - 54}, false)
				if err != nil {
					return nil, err
				}
				o.HPGain = iptr(v, ok)
			} else {
				for _, c := range costs {
					if d := c.x - float64(cxx); d > -80 && d < 80 && float64(ty-200) < c.y && c.y < float64(ty-40) {
						v := c.c
						o.HPCost = &v
						break
					}
				}
				if eng != "vision" {
					hc, err := costByHeart(b, cxx, ty, o.HPCost)
					if err != nil {
						return nil, err
					}
					o.HPCost = hc
				}
			}
			out = append(out, o)
			break
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		li, lj := out[i].Center[1] > 950, out[j].Center[1] > 950
		if li != lj {
			return !li
		}
		return out[i].Center[0] < out[j].Center[0]
	})
	return out, nil
}

var heartDigits = pyRe(`(\d{1,2})`)

func costByHeart(b *img.BGR, cxx, ty int, read *int) (*int, error) {
	x1, y1 := max(0, cxx-90), max(0, ty-210)
	band := b.Slice(x1, y1, cxx+90, ty-30)
	if size(band) == 0 {
		return read, nil
	}
	hsv := cv.HSV(band)
	m := img.NewGray(hsv.W, hsv.H)
	for i := range m.Pix {
		if p := hsv.Pix[i*3:]; p[0] >= 35 && p[0] <= 85 && p[1] >= 120 && p[2] >= 120 {
			m.Pix[i] = 1
		}
	}
	_, comps := cv.Components(m)
	var heart *cv.Component
	for i, c := range comps {
		if c.Area >= 150 && c.Area <= 1200 && c.W >= 12 && c.W <= 45 && c.H >= 15 && c.H <= 45 {
			heart = &comps[i]
			break
		}
	}
	if heart == nil {
		return nil, nil
	}
	if read != nil {
		return read, nil
	}
	box := ocr.Box{X1: x1 + heart.X + heart.W, Y1: y1 + heart.Y - 4, X2: x1 + heart.X + heart.W + 60, Y2: y1 + heart.Y + heart.H + 4}
	items, err := ocr.Boxes(crop(b, box), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	if mm := heartDigits.FindStringSubmatch(ocr.Joined(items, "")); mm != nil {
		v := mustAtoi(mm[1])
		return &v, nil
	}
	return nil, nil
}

var (
	slashRe       = regexp.MustCompile(`[lI|／\\]`)
	staminaFullRe = pyRe(`^\D*(\d{1,3})/(\d{2,3})\D*$`)
	staminaRe     = pyRe(`(\d+)\s*/\s*(\d+)`)
)

// ParseStamina 「33/33」→ (33, 33)；斜杠被认成 1 / 0 或漏掉时按规则拆。读不出 ok=false。
func ParseStamina(text string) (int, int, bool) {
	text = slashRe.ReplaceAllString(strings.ReplaceAll(text, " ", ""), "/")
	if m := staminaFullRe.FindStringSubmatch(text); m != nil {
		cur, cp := mustAtoi(m[1]), mustAtoi(m[2])
		if 0 < cp && cur <= cp {
			return cur, cp, true
		}
		return 0, 0, false
	}
	if strings.Contains(text, "/") {
		return 0, 0, false
	}
	digits := []rune(nonDigit.ReplaceAllString(text, ""))
	if len(digits) < 4 || len(digits) > 5 {
		return 0, 0, false
	}
	cp := mustAtoi(string(digits[len(digits)-2:]))
	var heads []string
	if c := digits[len(digits)-3]; c == '1' || c == '0' {
		heads = append(heads, string(digits[:len(digits)-3]))
	}
	heads = append(heads, string(digits[:len(digits)-2]))
	for _, h := range heads {
		if n := len([]rune(h)); n >= 1 && n <= 2 && mustAtoi(h) <= cp {
			return mustAtoi(h), cp, true
		}
	}
	return 0, 0, false
}

// ReadStamina 体力「当前/上限」：放大 4 倍、2 倍各严格读一次，都不成再按斜杠误读规则拆。
func ReadStamina(b *img.BGR) (int, int, bool, error) {
	c := crop(b, staminaBox)
	var texts []string
	for _, s := range []float64{4, 2} {
		items, err := ocr.OCR(c, s, ocr.En)
		if err != nil {
			return 0, 0, false, err
		}
		text := ocr.Joined(items, "")
		if m := staminaRe.FindStringSubmatch(text); m != nil {
			if cur, cp := mustAtoi(m[1]), mustAtoi(m[2]); cur <= cp {
				return cur, cp, true, nil
			}
		}
		texts = append(texts, text)
	}
	for _, t := range texts {
		if cur, cp, ok := ParseStamina(t); ok {
			return cur, cp, true, nil
		}
	}
	return 0, 0, false, nil
}

func daysDddd(b *img.BGR) (*int, error) {
	v, ok, err := ReadDddd(b, daysDigitBox)
	if err != nil || !ok || v < 1 || v > 30 {
		return nil, err
	}
	return &v, nil
}

var hifRe = regexp.MustCompile(`H[\s.．。、,，「」|｜lI1Ｉ]{0,4}F`)

// NormalizeHIF 「H.I.F」被 OCR 读走样时统一回「H.I.F」。
func NormalizeHIF(text string) string { return hifRe.ReplaceAllString(text, "H.I.F") }

var (
	hajimeHeader = [][2]string{{"中間", "mid1"}, {"1次", "mid1"}, {"2次", "mid2"}, {"最終", "final"}}
	weeksRe      = pyRe(`([\dろ]{1,2})\s*週`)
	examMadeRe   = pyRe(`(\S+?)まで`)
	daysRe       = pyRe(`(\d+)\s*日`)
	growthRe     = pyRe(`(\d+(?:\.\d)?)\s*%`)
)

func weeks(header string, b *img.BGR) (*int, error) {
	if m := weeksRe.FindStringSubmatch(header); m != nil {
		if v, ok := pyAtoi(strings.ReplaceAll(m[1], "ろ", "3")); ok && v >= 1 && v <= weeksMax {
			return &v, nil
		}
	}
	v, err := daysDddd(b)
	if err != nil || v == nil || *v > weeksMax {
		return nil, err
	}
	return v, nil
}

func smallText(b *img.BGR, box ocr.Box) (string, error) {
	items, err := ocr.OCR(crop(b, box), 4, ocr.En)
	if err != nil {
		return "", err
	}
	return ocr.Joined(items, ""), nil
}

// ReadActionSelect 选行动页的全部读数。
func ReadActionSelect(b *img.BGR) (*ActionSelectState, error) {
	hItems, err := ocr.OCR(b.Slice(0, 0, 280, headerBand), 2, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	header := ocr.Joined(hItems, " ")
	st := &ActionSelectState{ParamCaps: map[string]*int{}, GrowthPct: map[string]*float64{}}
	exam := examMadeRe.FindStringSubmatch(header)
	for _, m := range daysRe.FindAllStringSubmatch(header, -1) {
		if v := mustAtoi(m[1]); v >= 1 && v <= 30 {
			st.DaysLeft = &v
			break
		}
	}
	cur, cp, ok, err := ReadStamina(b)
	if err != nil {
		return nil, err
	}
	if ok {
		st.Stamina, st.MaxStamina = &cur, &cp
	}
	pt, err := smallText(b, pointsBox)
	if err != nil {
		return nil, err
	}
	if m := digitRun.FindString(pt); m != "" {
		v := mustAtoi(m)
		st.PPoints = &v
	}
	for _, a := range attrs3 {
		t, err := smallText(b, capBoxes[a])
		if err != nil {
			return nil, err
		}
		d := []rune(nonDigit.ReplaceAllString(strings.ReplaceAll(t, "O", "0"), ""))
		st.ParamCaps[a] = nil
		if len(d) >= 4 {
			v := mustAtoi(string(d[len(d)-4:]))
			st.ParamCaps[a] = &v
		}
		g, err := smallText(b, growthBoxes[a])
		if err != nil {
			return nil, err
		}
		st.GrowthPct[a] = nil
		if m := growthRe.FindStringSubmatch(g); m != nil {
			if f, err := strconv.ParseFloat(normDigits(m[1]), 64); err == nil {
				st.GrowthPct[a] = &f
			}
		}
	}
	aItems, err := ocr.OCR(b.Rows(adviceY1, adviceY2), 2, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	if adv := ocr.Joined(aItems, ""); adv != "" {
		st.Advice = &adv
	}
	if exam != nil {
		for _, kv := range hajimeHeader {
			if strings.Contains(exam[1], kv[0]) {
				st.ExamNext = sptr(kv[1])
				break
			}
		}
		st.Exam = sptr(NormalizeHIF(exam[1]))
	}
	if st.ExamNext != nil {
		if st.WeeksLeft, err = weeks(header, b); err != nil {
			return nil, err
		}
	}
	var opts []ActionOption
	if st.ExamNext != nil {
		if opts, err = oikomiOptions(b); err != nil {
			return nil, err
		}
	}
	if st.DaysLeft == nil {
		if st.DaysLeft, err = daysDddd(b); err != nil {
			return nil, err
		}
	}
	if len(opts) > 0 {
		if st.Params, err = oikomiParams(b); err != nil {
			return nil, err
		}
	} else {
		st.Params = map[string]*int{}
		for a, box := range StatBoxes(b) {
			v, ok, err := ReadNumber(b, box, false)
			if err != nil {
				return nil, err
			}
			st.Params[a] = iptr(v, ok)
		}
	}
	v, ok, err := ReadNumber(b, starBox, false)
	if err != nil {
		return nil, err
	}
	st.Star = iptr(v, ok)
	if len(opts) > 0 {
		st.Options = opts
	} else if st.Options, err = actionOptions(b, st.ExamNext != nil); err != nil {
		return nil, err
	}
	st.Complete = st.complete()
	return st, nil
}

// normDigits 全角等 Unicode 数字转 ASCII（Python float() 认 Unicode 数字）。
func normDigits(s string) string {
	var sb strings.Builder
	for _, r := range s {
		if d := digitValue(r); d >= 0 {
			sb.WriteByte("0123456789"[d])
		} else {
			sb.WriteRune(r)
		}
	}
	return sb.String()
}

func iconSpan(hsv *img.BGR, attr string) (int, int, bool) {
	r, s := statIconHue[attr], statIconSearch[attr]
	reg := hsv.Slice(s[0], 0, s[1], hsv.H)
	var cols []int
	for x := range reg.W {
		k := 0
		for y := range reg.H {
			if p := reg.Pix[(y*reg.W+x)*3:]; p[0] >= r[0] && p[0] <= r[1] && p[1] >= 120 && p[2] >= 150 {
				k++
			}
		}
		if float64(k)/float64(reg.H) >= 0.3 {
			cols = append(cols, x)
		}
	}
	if len(cols) == 0 {
		return 0, 0, false
	}
	start, end := cols[0], cols[0]
	for _, c := range cols[1:] {
		if c-end >= 12 {
			break
		}
		end = c
	}
	if end-start < 20 {
		return 0, 0, false
	}
	return s[0] + start, s[0] + end + 1, true
}

// StatBoxes 三个属性数字框：从图标右缘开始、到下一个图标（Vi 到「P手帳」）之前；图标找不到时用固定框。
func StatBoxes(b *img.BGR) map[string]ocr.Box {
	hsv := cv.HSV(b.Rows(statRowY1, statRowY2))
	type span struct {
		a, b int
		ok   bool
	}
	spans := map[string]span{}
	for _, a := range attrs3 {
		x1, x2, ok := iconSpan(hsv, a)
		spans[a] = span{x1, x2, ok}
	}
	out := map[string]ocr.Box{}
	for i, a := range attrs3 {
		if !spans[a].ok {
			out[a] = statBoxes[a]
			continue
		}
		limit := statRightLimit
		if i+1 < len(attrs3) && spans[attrs3[i+1]].ok {
			limit = spans[attrs3[i+1]].a - 2
		}
		left := spans[a].b + 2
		out[a] = ocr.Box{X1: left, Y1: 684, X2: min(left+statDigitsW, limit), Y2: 719}
	}
	return out
}
