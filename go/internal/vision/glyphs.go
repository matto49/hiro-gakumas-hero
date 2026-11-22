package vision

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"image"
	"math"
	"os"
	"sort"
	"strings"
	"sync"

	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"
	"golang.org/x/image/math/fixed"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 用游戏自带字体读数字（同 Python hiro.vision.digits）：区域内取白色字形、按连通块切字，外圈大多是深色描边的才算字，
// 每个字按高度缩放后和用字体渲染的 0–9、% 模板比重合度（IoU）。
//
// 字体模板：Python 用 Pillow（FreeType，带字形微调）渲染。随仓库的字体直接用 Pillow 渲染好的模板（data/glyph_templates.json）；
// 换了字体才用 x/image/font/opentype 渲染（不微调，缩到 40 像素高后每个字差 1–2% 的像素，阈值附近的字可能认得不一样）。

const (
	glyphChars    = "0123456789%"
	glyphH        = 40
	darkV         = 140
	darkVSoft     = 170
	ringDarkMin   = 0.55
	glyphMinScore = 0.7
	minHeightRat  = 0.6
	groupGap      = 0.45
	glyphMargin   = 14
	maxGlyphBox   = 1.2
	alignTop      = 0.25
	alignHLo      = 0.75
	alignHHi      = 1.25
	glyphHLo      = 12
	glyphHHi      = 20
	percentRing   = 0.65
	whiteDigitH   = 0.7
	whiteDigitTop = 0.3
	spanPad       = 4
)

var (
	whiteLo = [3]float64{0, 0, 190}
	whiteHi = [3]float64{180, 70, 255}
)

// mask 是布尔图。
type mask struct {
	w, h int
	v    []bool
}

type glyphTpl struct {
	ch string
	m  mask
}

var (
	fontOnce sync.Once
	fontTpls []glyphTpl // 按 glyphChars 的顺序
)

func glyphTemplates() []glyphTpl {
	fontOnce.Do(func() {
		path := assets.Default().FontPath()
		if path == "" {
			return // 没有游戏数字字体：字形比对关闭，读数全走 ddddocr
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return
		}
		if t := bundledGlyphs(raw); t != nil {
			fontTpls = t
			return
		}
		f, err := opentype.Parse(raw)
		if err != nil {
			return
		}
		face, err := opentype.NewFace(f, &opentype.FaceOptions{Size: 160, DPI: 72, Hinting: font.HintingNone})
		if err != nil {
			return
		}
		for _, ch := range glyphChars {
			im := image.NewGray(image.Rect(0, 0, 240, 240))
			d := &font.Drawer{Dst: im, Src: image.White, Face: face, Dot: fixed.P(20, 20+face.Metrics().Ascent.Ceil())}
			d.DrawString(string(ch))
			x0, y0, x1, y1 := 240, 240, -1, -1
			for y := range 240 {
				for x := range 240 {
					if im.Pix[y*im.Stride+x] > 127 {
						x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
					}
				}
			}
			if x1 < 0 {
				continue
			}
			m := mask{w: x1 - x0 + 1, h: y1 - y0 + 1}
			m.v = make([]bool, m.w*m.h)
			for y := range m.h {
				for x := range m.w {
					m.v[y*m.w+x] = im.Pix[(y0+y)*im.Stride+x0+x] > 127
				}
			}
			fontTpls = append(fontTpls, glyphTpl{string(ch), scaleMask(m)})
		}
	})
	return fontTpls
}

// bundledGlyphs 随仓库那份字体的模板（Python / Pillow 渲染后存在 data/glyph_templates.json）：字体文件的 SHA-256 对上才用。
func bundledGlyphs(font []byte) []glyphTpl {
	raw, err := data.ReadFile("data/glyph_templates.json")
	if err != nil {
		return nil
	}
	var f struct {
		SHA   string `json:"font_sha256"`
		Chars map[string]struct {
			W    int      `json:"w"`
			H    int      `json:"h"`
			Rows []string `json:"rows"`
		} `json:"chars"`
	}
	if json.Unmarshal(raw, &f) != nil {
		return nil
	}
	sum := sha256.Sum256(font)
	if hex.EncodeToString(sum[:]) != f.SHA {
		return nil
	}
	var out []glyphTpl
	for _, ch := range glyphChars {
		c, ok := f.Chars[string(ch)]
		if !ok {
			return nil
		}
		m := mask{w: c.W, h: c.H, v: make([]bool, c.W*c.H)}
		for y, row := range c.Rows {
			for x := range row {
				m.v[y*c.W+x] = row[x] == '1'
			}
		}
		out = append(out, glyphTpl{string(ch), m})
	}
	return out
}

// scaleMask 缩到 40 像素高（INTER_AREA，> 127 算字）。
func scaleMask(m mask) mask {
	w := max(1, int(math.RoundToEven(float64(m.w)*glyphH/float64(m.h))))
	g := img.NewGray(m.w, m.h)
	for i, v := range m.v {
		if v {
			g.Pix[i] = 255
		}
	}
	s := cv.ResizeGray(g, w, glyphH, cv.Area)
	out := mask{w: s.W, h: s.H, v: make([]bool, len(s.Pix))}
	for i, v := range s.Pix {
		out.v[i] = v > 127
	}
	return out
}

// iou 两个 40 像素高的掩码水平居中对齐后的交并比。
func iou(a, b mask) float64 {
	w := max(a.w, b.w)
	oa, ob := (w-a.w)/2, (w-b.w)/2
	inter, union := 0, 0
	for y := range glyphH {
		for x := range w {
			va := x >= oa && x < oa+a.w && a.v[y*a.w+x-oa]
			vb := x >= ob && x < ob+b.w && b.v[y*b.w+x-ob]
			if va || vb {
				union++
				if va && vb {
					inter++
				}
			}
		}
	}
	if union == 0 {
		return 0
	}
	return float64(inter) / float64(union)
}

// bestChar 字形与字体模板比对的 (最高分, 字符)；并列时取字符大的（同 Python max((分, 字符))）。没有字体时 (0, "")。
func bestChar(g mask) (float64, string) {
	best, bc := -1.0, ""
	for _, t := range glyphTemplates() {
		s := iou(g, t.m)
		if s > best || (s == best && t.ch > bc) {
			best, bc = s, t.ch
		}
	}
	if bc == "" {
		return 0, ""
	}
	return best, bc
}

// segmentation 是一块区域按白字 + 深色描边切出来的连通块。
type segmentation struct {
	w, h     int
	labels   []int32
	comps    []cv.Component // comps[i] 是标签 i+1
	dark     []bool
	accepted []int // 标签号
	rejected []int
}

func (s *segmentation) stat(l int) cv.Component { return s.comps[l-1] }

func (s *segmentation) labelMask(l int) mask {
	c := s.stat(l)
	m := mask{w: c.W, h: c.H, v: make([]bool, c.W*c.H)}
	for y := range c.H {
		for x := range c.W {
			m.v[y*c.W+x] = s.labels[(c.Y+y)*s.w+c.X+x] == int32(l) //nolint:gosec // 标签号
		}
	}
	return m
}

// ringDark 连通块外圈（9x9 膨胀减去自身）里深色像素的占比；外圈为空返回 -1。
func (s *segmentation) ringDark(l, r int) float64 {
	c := s.stat(l)
	x0, y0 := max(0, c.X-r), max(0, c.Y-r)
	x1, y1 := min(s.w, c.X+c.W+r), min(s.h, c.Y+c.H+r)
	bw, bh := x1-x0, y1-y0
	in := func(x, y int) bool { return s.labels[y*s.w+x] == int32(l) } //nolint:gosec // 标签号
	// 先横向再纵向求 (2r+1) 窗口里有没有这个连通块的像素
	row := make([]bool, bw*bh)
	for y := range bh {
		for x := range bw {
			for dx := -r; dx <= r; dx++ {
				if xx := x0 + x + dx; xx >= 0 && xx < s.w && in(xx, y0+y) {
					row[y*bw+x] = true
					break
				}
			}
		}
	}
	n, d := 0, 0
	for y := range bh {
		for x := range bw {
			hit := false
			for dy := -r; dy <= r && !hit; dy++ {
				if yy := y + dy; yy >= 0 && yy < bh && row[yy*bw+x] {
					hit = true
				}
			}
			if hit && !in(x0+x, y0+y) {
				n++
				if s.dark[(y0+y)*s.w+x0+x] {
					d++
				}
			}
		}
	}
	if n == 0 {
		return -1
	}
	return float64(d) / float64(n)
}

// segment 放大 3 倍的区域里切字：白色像素去掉贴着深色描边的、开运算，再按连通块。
func segment(c *img.BGR, dv byte) *segmentation {
	hsv := cv.HSV(c)
	w, h := hsv.W, hsv.H
	dark := make([]bool, w*h)
	for i := range dark {
		dark[i] = hsv.Pix[i*3+2] < dv
	}
	fill := cv.InRange(hsv, whiteLo, whiteHi)
	for y := range h { // fill[dilate(dark, 3x3) > 0] = 0
		for x := range w {
			if fill.Pix[y*w+x] == 0 {
				continue
			}
		near:
			for dy := -1; dy <= 1; dy++ {
				for dx := -1; dx <= 1; dx++ {
					if yy, xx := y+dy, x+dx; yy >= 0 && yy < h && xx >= 0 && xx < w && dark[yy*w+xx] {
						fill.Pix[y*w+x] = 0
						break near
					}
				}
			}
		}
	}
	fill = cv.Morphology(fill, cv.MorphOpen, cv.RectKernel{W: 3, H: 3})
	labels, comps := cv.Components(fill)
	return &segmentation{w: w, h: h, labels: labels, comps: comps, dark: dark}
}

// glyphInfo 是所取那组字形的信息。
type glyphInfo struct {
	Span     ocr.Box // 原图坐标
	Glyphs   int
	Occluded bool
	Left     int
	Parts    []glyphPart
}

type glyphPart struct {
	ch    string
	score float64
	w, h  int
}

type cbox struct{ l, x, y, w, h int }

func readGlyphsAt(b *img.BGR, box ocr.Box, align bool, dv byte) (string, bool, *glyphInfo) {
	const scale = 3
	x1, y1 := max(0, box.X1-glyphMargin), max(0, box.Y1-glyphMargin/2)
	x2, y2 := min(b.W, box.X2+glyphMargin), min(b.H, box.Y2+glyphMargin/2)
	c := b.Slice(x1, y1, x2, y2)
	if c.Empty() {
		panic(&cv.Error{Err: errEmpty})
	}
	inner := [4]int{(box.X1 - x1) * scale, (box.Y1 - y1) * scale, (box.X2 - x1) * scale, (box.Y2 - y1) * scale}
	s := segment(cv.ResizeScale(c, scale, cv.Cubic), dv)
	for l := 1; l <= len(s.comps); l++ {
		st := s.stat(l)
		if st.Area < 20*scale {
			continue
		}
		if st.X+st.W <= inner[0] || st.X >= inner[2] {
			continue
		}
		if mid := float64(st.Y) + float64(st.H)/2; !(float64(inner[1]) <= mid && mid <= float64(inner[3])) {
			continue
		}
		if rd := s.ringDark(l, scale+1); rd >= 0 && rd >= ringDarkMin {
			s.accepted = append(s.accepted, l)
		} else {
			s.rejected = append(s.rejected, l)
		}
	}
	if len(s.accepted) == 0 {
		return "", false, nil
	}
	acc := append([]int(nil), s.accepted...)
	sort.SliceStable(acc, func(i, j int) bool { return s.stat(acc[i]).X < s.stat(acc[j]).X })
	boxes := make([]cbox, len(acc))
	for i, l := range acc {
		st := s.stat(l)
		boxes[i] = cbox{l, st.X, st.Y, st.W, st.H}
	}
	if align {
		if al := alignedBoxes(s, boxes, (box.Y2-box.Y1)*scale); len(al) > 0 {
			boxes = al
		}
	}
	tallest := 0
	for _, bx := range boxes {
		tallest = max(tallest, bx.h)
	}
	kept := boxes[:0:0]
	for _, bx := range boxes {
		if float64(bx.h) >= float64(tallest)*minHeightRat {
			kept = append(kept, bx)
		}
	}
	boxes = kept
	left := math.MaxInt
	for _, l := range append(append([]int(nil), s.accepted...), s.rejected...) {
		if st := s.stat(l); float64(st.H) >= float64(tallest)*minHeightRat {
			left = min(left, st.X)
		}
	}
	left = x1 + floorDiv(left, scale)
	var groups [][]cbox
	lastRight, started := 0, false
	for _, bx := range boxes {
		if !started || float64(bx.x-lastRight) > float64(tallest)*groupGap {
			groups = append(groups, nil)
		}
		groups[len(groups)-1] = append(groups[len(groups)-1], bx)
		lastRight, started = bx.x+bx.w, true
	}
	overlap := func(g []cbox) int {
		n := 0
		for _, bx := range g {
			n += max(0, min(bx.x+bx.w, inner[2])-max(bx.x, inner[0]))
		}
		return n
	}
	group := groups[0]
	for _, g := range groups[1:] {
		if overlap(g) > overlap(group) {
			group = g
		}
	}
	gx1, gy1, gx2, gy2 := math.MaxInt, math.MaxInt, 0, 0
	for _, bx := range group {
		gx1, gy1 = min(gx1, bx.x), min(gy1, bx.y)
		gx2, gy2 = max(gx2, bx.x+bx.w), max(gy2, bx.y+bx.h)
	}
	info := &glyphInfo{
		Span:   ocr.Box{X1: x1 + floorDiv(gx1, scale), Y1: y1 + floorDiv(gy1, scale), X2: x1 - floorDiv(-gx2, scale), Y2: y1 - floorDiv(-gy2, scale)},
		Glyphs: len(group), Left: left,
	}
	var out []string
	uncertain := false
	for _, bx := range group {
		score, ch := bestChar(scaleMask(s.labelMask(bx.l)))
		info.Parts = append(info.Parts, glyphPart{ch, pyRound(score, 2), bx.w, bx.h})
		if score >= glyphMinScore {
			out = append(out, ch)
		} else {
			uncertain = true
		}
	}
	info.Occluded = occludedNeighbor(s, group)
	if len(out) > 0 && !uncertain {
		uncertain = info.Occluded
	}
	return strings.Join(out, ""), uncertain, info
}

func alignedBoxes(s *segmentation, boxes []cbox, boxH int) []cbox {
	tpls := glyphTemplates()
	if len(tpls) == 0 {
		return nil
	}
	var sized []cbox
	for _, bx := range boxes {
		if float64(bx.h) <= float64(boxH)*maxGlyphBox {
			sized = append(sized, bx)
		}
	}
	readable := map[int]bool{}
	for _, bx := range sized {
		g := scaleMask(s.labelMask(bx.l))
		best := 0.0
		for _, t := range tpls {
			best = max(best, iou(g, t.m))
		}
		if best >= glyphMinScore {
			readable[bx.l] = true
		}
	}
	row := func(ref cbox) []cbox {
		var out []cbox
		for _, bx := range sized {
			if math.Abs(float64(bx.y-ref.y)) <= float64(ref.h)*alignTop && alignHLo*float64(ref.h) <= float64(bx.h) && float64(bx.h) <= alignHHi*float64(ref.h) {
				out = append(out, bx)
			}
		}
		return out
	}
	best := 0
	var out []cbox
	for _, bx := range sized {
		if !readable[bx.l] {
			continue
		}
		members := row(bx)
		good := 0
		for _, m := range members {
			if readable[m.l] {
				good++
			}
		}
		if good > best {
			best, out = good, members
		}
	}
	return out
}

func occludedNeighbor(s *segmentation, group []cbox) bool {
	hs, ws := make([]int, len(group)), make([]int, len(group))
	for i, bx := range group {
		hs[i], ws[i] = bx.h, bx.w
	}
	sort.Ints(hs)
	sort.Ints(ws)
	h, w := float64(hs[len(hs)/2]), ws[len(ws)/2]
	left, right := group[0].x-w, group[len(group)-1].x+group[len(group)-1].w+w
	for _, l := range s.rejected {
		st := s.stat(l)
		if 0.75*h <= float64(st.H) && float64(st.H) <= 1.35*h && st.W <= 2*w && st.X < right && st.X+st.W > left {
			return true
		}
	}
	return false
}

func readGlyphs(b *img.BGR, box ocr.Box, align bool) (string, bool, *glyphInfo) {
	text, unc, info := readGlyphsAt(b, box, align, darkV)
	if (text == "" || unc) && len(glyphTemplates()) > 0 {
		if t2, u2, i2 := readGlyphsAt(b, box, align, darkVSoft); t2 != "" && !u2 {
			return t2, u2, i2
		}
	}
	return text, unc, info
}

// ReadText box 里从左到右识别出的字符串（只含 0–9 与 %）和是否有字符没认出。
func ReadText(b *img.BGR, box ocr.Box, align bool) (string, bool) {
	t, u, _ := readGlyphs(b, box, align)
	return t, u
}

// ReadInt 只用字体比对；读不出或有字符没认出时 ok=false。
func ReadInt(b *img.BGR, box ocr.Box, align bool) (int, bool) {
	text, unc := ReadText(b, box, align)
	m := digitRun.FindString(text)
	if m == "" || unc {
		return 0, false
	}
	return mustAtoi(m), true
}

// ReadLeadingInt 读「数字 + 单位」开头的数字，返回 (数值, ok, 后缀)。后缀：""、"%"、"text"；ok=false 时后缀 "empty" 表示框里没有任何字。
func ReadLeadingInt(b *img.BGR, box ocr.Box) (int, bool, string) {
	tpls := glyphTemplates()
	if len(tpls) == 0 {
		return 0, false, ""
	}
	const scale = 3
	x1, y1 := max(0, box.X1-glyphMargin), max(0, box.Y1-glyphMargin/2)
	x2, y2 := min(b.W, box.X2+glyphMargin), min(b.H, box.Y2+glyphMargin/2)
	innerX1 := (box.X1 - x1) * scale
	s := segment(cv.ResizeScale(b.Slice(x1, y1, x2, y2), scale, cv.Cubic), darkV)
	innerY := [2]int{(box.Y1 - y1) * scale, (box.Y2 - y1) * scale}
	for l := 1; l <= len(s.comps); l++ {
		st := s.stat(l)
		mid := float64(st.Y) + float64(st.H)/2
		if st.Area < 20*scale || !(float64(innerY[0]) <= mid && mid <= float64(innerY[1])) || st.X+st.W <= innerX1 {
			continue
		}
		if rd := s.ringDark(l, scale+1); rd >= 0 && rd >= ringDarkMin {
			s.accepted = append(s.accepted, l)
		} else {
			s.rejected = append(s.rejected, l)
		}
	}
	comps := append([]int(nil), s.accepted...)
	sort.SliceStable(comps, func(i, j int) bool { return s.stat(comps[i]).X < s.stat(comps[j]).X })
	best := func(l int) (float64, string) { return bestChar(scaleMask(s.labelMask(l))) }
	lo, hi := glyphHLo*scale, glyphHHi*scale
	var tall []int
	for _, l := range comps {
		if h := s.stat(l).H; lo <= h && h <= hi {
			tall = append(tall, l)
		}
	}
	if len(tall) == 0 {
		for _, l := range s.rejected {
			if st := s.stat(l); lo <= st.H && st.H <= hi && st.W <= hi {
				return 0, false, ""
			}
		}
		return 0, false, "empty"
	}
	height := 0
	for _, l := range tall {
		height = max(height, s.stat(l).H)
	}
	kept := tall[:0:0]
	for _, l := range tall {
		if float64(s.stat(l).H) >= float64(height)*minHeightRat {
			kept = append(kept, l)
		}
	}
	tall = kept
	var digs []string
	firstX, last, k := -1, -1, len(tall)
	for n, l := range tall {
		st := s.stat(l)
		score, ch := best(l)
		if score < glyphMinScore || !isDigit(ch) || (last >= 0 && float64(st.X-last) > float64(height)*groupGap) {
			if len(digs) == 0 && st.X < innerX1 {
				continue
			}
			k = n
			break
		}
		digs = append(digs, ch)
		if firstX < 0 {
			firstX = st.X
		}
		last = st.X + st.W
	}
	if len(digs) == 0 || firstX > innerX1+height {
		return 0, false, ""
	}
	hf := float64(height)
	for _, l := range s.rejected {
		st := s.stat(l)
		nearLeft := st.X >= innerX1 && firstX-height <= st.X+st.W && st.X+st.W <= firstX+2
		if 0.75*hf <= float64(st.H) && float64(st.H) <= 1.35*hf && (nearLeft || (last-2 <= st.X && float64(st.X) <= float64(last)+hf)) {
			return 0, false, ""
		}
	}
	value := mustAtoi(strings.Join(digs, ""))
	for _, l := range comps {
		st := s.stat(l)
		if float64(st.H) <= hf*percentRing && last-2 <= st.X && float64(st.X) <= float64(last)+hf*0.6 {
			if sc, ch := best(l); ch == "0" && sc >= glyphMinScore {
				return value, true, "%"
			}
		}
	}
	if len(tall[k:]) > 0 {
		return value, true, "text"
	}
	return value, true, ""
}

func isDigit(s string) bool { return len(s) == 1 && s[0] >= '0' && s[0] <= '9' }

// ReadWhiteDigits 白色填充 + 深色描边的数字（考试 HUD 元気角标）：只留和最高那块等高、顶边对齐的白色连通块，交 ddddocr。
func ReadWhiteDigits(b *img.BGR, box ocr.Box) (int, bool, error) {
	c := crop(b, box)
	if c.Empty() {
		return 0, false, nil
	}
	m := cv.InRange(cv.HSV(c), whiteLo, whiteHi)
	labels, comps := cv.Components(m)
	if len(comps) == 0 {
		return 0, false, nil
	}
	tallest := 0
	for i, cc := range comps {
		if cc.H > comps[tallest].H {
			tallest = i
		}
	}
	th, ty := float64(comps[tallest].H), comps[tallest].Y
	keep := map[int32]bool{}
	for i, cc := range comps {
		if float64(cc.H) >= th*whiteDigitH && math.Abs(float64(cc.Y-ty)) <= th*whiteDigitTop {
			keep[int32(i+1)] = true
		}
	}
	out := img.New(c.W, c.H)
	for i, l := range labels {
		if keep[l] {
			out.Pix[i*3], out.Pix[i*3+1], out.Pix[i*3+2] = 255, 255, 255
		}
	}
	s, err := ocr.Dddd(cv.ResizeScale(out, 2, cv.Linear))
	if err != nil {
		return 0, false, err
	}
	if d := digitRun.FindString(ddddFix.Replace(s)); d != "" {
		return mustAtoi(d), true, nil
	}
	return 0, false, nil
}

// ReadNumber 先按游戏字体比对；没把握时退回 ddddocr。
func ReadNumber(b *img.BGR, box ocr.Box, align bool) (int, bool, error) {
	if v, ok := ReadInt(b, box, align); ok {
		return v, true, nil
	}
	if len(glyphTemplates()) == 0 {
		return numberWithoutFont(b, box, align)
	}
	return ReadDddd(b, box)
}

var circled = strings.NewReplacer("⓪", "0", "①", "1", "②", "2", "③", "3", "④", "4", "⑤", "5", "⑥", "6", "⑦", "7", "⑧", "8", "⑨", "9")

var nonDigit = pyRe(`\D`)

func numberWithoutFont(b *img.BGR, box ocr.Box, align bool) (int, bool, error) {
	whole, wok, err := ReadDddd(b, box)
	if err != nil {
		return 0, false, err
	}
	_, _, info := readGlyphs(b, box, align)
	if info == nil {
		return whole, wok, nil
	}
	tb := ocr.Box{X1: max(box.X1, info.Left-spanPad), Y1: box.Y1, X2: box.X2, Y2: box.Y2}
	trimmed, tok := whole, wok
	if tb != box {
		if trimmed, tok, err = ReadDddd(b, tb); err != nil {
			return 0, false, err
		}
	}
	if wok == tok && whole == trimmed {
		return whole, wok, nil
	}
	items, err := ocr.OCR(crop(b, box), 6, ocr.JaEn)
	if err != nil {
		return 0, false, err
	}
	text := nonDigit.ReplaceAllString(circled.Replace(ocr.Joined(items, "")), "")
	if text == "" {
		return 0, false, nil
	}
	v := mustAtoi(text)
	if (wok && v == whole) || (tok && v == trimmed) {
		return v, true, nil
	}
	return 0, false, nil
}

// ScoreMeta 是 ReadScore 的读法说明。
type ScoreMeta struct {
	Method     *string  `json:"method"`
	Confidence *float64 `json:"confidence"`
	Note       *string  `json:"note"`
}

func meta(method string, conf float64, note string) ScoreMeta {
	m := ScoreMeta{}
	if method != "" {
		m.Method, m.Confidence = &method, &conf
	}
	if note != "" {
		m.Note = &note
	}
	return m
}

func subsequence(short, long string) bool {
	lr := []rune(long)
	i := 0
	for _, c := range short {
		for i < len(lr) && lr[i] != c {
			i++
		}
		if i == len(lr) {
			return false
		}
		i++
	}
	return true
}

var allDigits = pyRe(`^\d+$`)

// ReadScore 读分数这类长数字（同 Python digits.read_score）：返回 (值, ok, 读法说明)。
func ReadScore(b *img.BGR, box ocr.Box) (int, bool, ScoreMeta, error) {
	text, unc, info := readGlyphs(b, box, false)
	if allDigits.MatchString(text) && !unc {
		return mustAtoi(text), true, meta("digits", 1.0, ""), nil
	}
	if info == nil {
		return 0, false, meta("", 0, "没有找到数字字形"), nil
	}
	sp := info.Span
	span := ocr.Box{X1: max(0, sp.X1-spanPad), Y1: max(0, sp.Y1-spanPad), X2: min(b.W, sp.X2+spanPad), Y2: min(b.H, sp.Y2+spanPad)}
	if len(glyphTemplates()) == 0 {
		return scoreWithoutFont(b, span, info)
	}
	value, ok, err := ReadDddd(b, span)
	if err != nil {
		return 0, false, ScoreMeta{}, err
	}
	fnt := nonDigit.ReplaceAllString(text, "")
	noise := 0
	for _, p := range info.Parts {
		if p.score < glyphMinScore {
			noise++
		}
	}
	shown := fnt
	if shown == "" {
		shown = "无"
	}
	if !ok {
		return 0, false, meta("", 0, "字体比对不确定（认出 "+shown+"），OCR 也读不出"), nil
	}
	ot := itoa(value)
	if ot == fnt {
		c := 0.9
		if noise > 0 {
			c = 0.8
		}
		return value, true, meta("dddd", c, "字体比对与 OCR 一致"), nil
	}
	extra := len([]rune(ot)) - len([]rune(fnt))
	occ := 0
	if info.Occluded {
		occ = 1
	}
	if extra > 0 && extra <= noise+occ && subsequence(fnt, ot) {
		return value, true, meta("dddd", 0.6, "字体只认出 "+shown+"，OCR 补出 "+ot), nil
	}
	return 0, false, meta("", 0, "字体认出 "+shown+"、OCR 读到 "+ot+"，对不上，丢弃"), nil
}

func fullGlyphs(parts []glyphPart) int {
	if len(parts) == 0 {
		return 0
	}
	hs, ws := make([]int, len(parts)), make([]int, len(parts))
	for i, p := range parts {
		hs[i], ws[i] = p.h, p.w
	}
	sort.Ints(hs)
	sort.Ints(ws)
	mh, mw := float64(hs[len(hs)/2]), float64(ws[len(ws)/2])
	n := 0
	for _, p := range parts {
		if float64(p.h) >= 0.85*mh && float64(p.w) <= 1.6*mw {
			n++
		}
	}
	return n
}

func pyIntStr(v int, ok bool) string {
	if !ok {
		return "None"
	}
	return itoa(v)
}

func scoreWithoutFont(b *img.BGR, span ocr.Box, info *glyphInfo) (int, bool, ScoreMeta, error) {
	dddd, dok, err := ReadDddd(b, span)
	if err != nil {
		return 0, false, ScoreMeta{}, err
	}
	items, err := ocr.OCR(crop(b, span), 9, ocr.JaEn)
	if err != nil {
		return 0, false, ScoreMeta{}, err
	}
	vt := strings.ReplaceAll(strings.ReplaceAll(ocr.Joined(items, ""), ",", ""), " ", "")
	vision, vok := 0, false
	if allDigits.MatchString(vt) {
		vision, vok = mustAtoi(vt), true
	}
	full := fullGlyphs(info.Parts)
	note := "无数字字体：ddddocr " + pyIntStr(dddd, dok) + "、Vision " + pyIntStr(vision, vok) + "、整高字形 " + itoa(full) + " 个"
	if dok && vok && dddd == vision {
		return dddd, true, meta("dddd", 0.8, "无数字字体：ddddocr 与 Vision 一致"), nil
	}
	if dok && vok {
		a, c := itoa(dddd), itoa(vision)
		if len(c) < len(a) {
			a, c = c, a
		}
		if len(a) < len(c) && (strings.HasPrefix(c, a) || strings.HasSuffix(c, a)) {
			return mustAtoi(c), true, meta("dddd", 0.6, note), nil
		}
	}
	if dok && len(itoa(dddd)) == full {
		return dddd, true, meta("dddd", 0.6, note), nil
	}
	return 0, false, meta("", 0, note+"，对不上，丢弃"), nil
}
