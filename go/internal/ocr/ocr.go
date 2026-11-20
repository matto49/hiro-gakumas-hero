// Package ocr 是固定区域的数字和短文本 OCR，同 Python 版 hiro.vision.ocr。后端：
//
//   - vision：Apple Vision（macOS 系统文字识别，cgo），macOS 上默认用它。区域先放大 3 倍（双三次）再识别。
//   - rapidocr：RapidOCR（PaddleOCR PP-OCRv3 日文 / 英文识别模型，onnxruntime），非 macOS 的缺省。（阶段 3 后段迁）
//
// HIRO_OCR=auto（缺省：macOS → vision，否则 rapidocr）/ vision / rapidocr。
package ocr

import (
	"errors"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"

	"golang.org/x/text/unicode/norm"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// Point 是整帧坐标的点。
type Point struct{ X, Y int }

// Box 是外接框 (x1, y1, x2, y2)。
type Box struct{ X1, Y1, X2, Y2 int }

// W 宽。
func (b Box) W() int { return b.X2 - b.X1 }

// H 高。
func (b Box) H() int { return b.Y2 - b.Y1 }

// Center 中心点（整除，同 Python）。
func (b Box) Center() Point { return Point{b.X1 + b.W()/2, b.Y1 + b.H()/2} }

// XYWH 转成 (x, y, w, h)。
func (b Box) XYWH() (int, int, int, int) { return b.X1, b.Y1, b.W(), b.H() }

// Item 是一段识别出的文字（区域内坐标）。
type Item struct {
	Text string
	Conf float64
	Box  Box
}

// Text 是一段识别出的文字（整帧坐标）。
type Text struct {
	Text string
	Rect Box
	Conf float64
}

// 缺省语言。
var (
	JaEn = []string{"ja-JP", "en-US"}
	En   = []string{"en-US"}
)

var engines = []string{"auto", "vision", "rapidocr"}

var (
	engineOnce sync.Once
	engineName string
	engineErr  error
)

// Engine 当前后端（首次调用时按 HIRO_OCR 和平台确定，之后不变）。
func Engine() (string, error) {
	engineOnce.Do(func() {
		want := strings.ToLower(strings.TrimSpace(os.Getenv("HIRO_OCR")))
		if want == "" {
			want = "auto"
		}
		ok := false
		for _, e := range engines {
			ok = ok || e == want
		}
		if !ok {
			engineErr = fmt.Errorf("HIRO_OCR=%q 不认识；可选：%s", want, strings.Join(engines, ", "))
			return
		}
		if want == "auto" {
			want = "rapidocr"
			if visionAvailable {
				want = "vision"
			}
		}
		engineName = want
	})
	return engineName, engineErr
}

// ErrEngine 是后端还不可用。
var ErrEngine = errors.New("OCR 后端不可用")

// Boxes 每段文字、置信度和它在 b 里的外接框（同 Python ocr_boxes）。空图返回空，不报错。
func Boxes(b *img.BGR, scale float64, languages []string) ([]Item, error) {
	if b.Empty() {
		return nil, nil
	}
	w, h := b.W, b.H
	if scale != 1 {
		b = cv.ResizeScale(b, scale, cv.Cubic)
	}
	eng, err := Engine()
	if err != nil {
		return nil, err
	}
	switch eng {
	case "vision":
		png, err := cv.EncodePNG(b)
		if err != nil {
			return nil, err
		}
		return visionRaw(png, w, h, languages)
	default:
		return nil, fmt.Errorf("%w：%s 还没迁到 Go（阶段 3 后段）", ErrEngine, eng)
	}
}

// OCR 区域里的文字和置信度（同 Python ocr）。
func OCR(b *img.BGR, scale float64, languages []string) ([]Item, error) {
	return Boxes(b, scale, languages)
}

// Joined 把识别结果拼成一段（Python 的 "".join(t for t, _ in ocr(...))）。
func Joined(items []Item, sep string) string {
	parts := make([]string, len(items))
	for i, it := range items {
		parts[i] = it.Text
	}
	return strings.Join(parts, sep)
}

// charFix 是 RapidOCR 日文模型的常见误读。
var charFix = strings.NewReplacer("ó", "6", "ą", "a")

// Sanitize NFKC 规整（全角英数 / 括号转半角）+ 常见误读修正。
func Sanitize(s string) string { return charFix.Replace(norm.NFKC.String(s)) }

// ScreenScale 是整帧文字的放大倍数。
const ScreenScale = 1.5

// ScreenTexts 整帧（或 rect=(x,y,w,h) 区域）里的文字，坐标是整帧坐标，自上而下、同行从左到右（同 Python screen_texts）。
func ScreenTexts(b *img.BGR, rect *[4]int, scale float64) ([]Text, error) {
	if b == nil {
		return nil, nil
	}
	ox, oy := 0, 0
	if rect != nil {
		ox, oy = rect[0], rect[1]
		b = b.Slice(ox, oy, ox+rect[2], oy+rect[3])
	}
	items, err := Boxes(b, scale, JaEn)
	if err != nil {
		return nil, err
	}
	out := make([]Text, 0, len(items))
	for _, it := range items {
		x1, y1, x2, y2 := it.Box.X1, it.Box.Y1, it.Box.X2, it.Box.Y2
		out = append(out, Text{Text: Sanitize(it.Text), Rect: Box{ox + x1, oy + y1, ox + max(x2, x1+1), oy + max(y2, y1+1)}, Conf: it.Conf})
	}
	sort.SliceStable(out, func(i, j int) bool {
		ci, cj := out[i].Rect.Center(), out[j].Rect.Center()
		if floorDiv(ci.Y, 12) != floorDiv(cj.Y, 12) {
			return floorDiv(ci.Y, 12) < floorDiv(cj.Y, 12)
		}
		return ci.X < cj.X
	})
	return out, nil
}

func floorDiv(a, b int) int {
	q := a / b
	if (a%b != 0) && ((a < 0) != (b < 0)) {
		q--
	}
	return q
}

// FindText ScreenTexts 里第一段满足 match 的文字。
func FindText(b *img.BGR, match func(string) bool, rect *[4]int) (*Text, error) {
	texts, err := ScreenTexts(b, rect, ScreenScale)
	if err != nil {
		return nil, err
	}
	for i := range texts {
		if match(texts[i].Text) {
			return &texts[i], nil
		}
	}
	return nil, nil
}

// Contains 是 FindText 用的「包含这个字符串」。
func Contains(s string) func(string) bool {
	return func(t string) bool { return strings.Contains(t, s) }
}

// DigitsImage 白色描边数字 → 白底黑字：取接近白色的像素作为字形，加边框（同 Python digits_image）。
func DigitsImage(b *img.BGR, whiteMin float32) *img.Gray {
	mask := cv.Threshold(cv.Gray(b), whiteMin, 255)
	inv := img.NewGray(mask.W, mask.H)
	for i, v := range mask.Pix {
		inv.Pix[i] = 255 - v
	}
	return cv.BorderConstantGray(inv, 12, 12, 12, 12, 255)
}

var (
	digitsRe   = regexp.MustCompile(`\d+`)
	digitFixes = strings.NewReplacer(",", "", "O", "0", "o", "0", "l", "1", "I", "1")
	intFixes   = strings.NewReplacer(",", "", "O", "0", "o", "0")
)

// Digits 读白色描边数字（同 Python ocr_digits）；读不到返回 ok=false。
func Digits(b *img.BGR, whiteMin float32, scale float64) (int, bool, error) {
	pre := cv.GrayToBGR(DigitsImage(b, whiteMin))
	items, err := OCR(pre, scale, En)
	if err != nil {
		return 0, false, err
	}
	return firstInt(digitFixes.Replace(Joined(items, " ")))
}

// Int 读一个整数（同 Python ocr_int）。
func Int(b *img.BGR, scale float64, languages []string) (int, bool, error) {
	items, err := OCR(b, scale, languages)
	if err != nil {
		return 0, false, err
	}
	return firstInt(intFixes.Replace(Joined(items, " ")))
}

func firstInt(s string) (int, bool, error) {
	m := digitsRe.FindString(s)
	if m == "" {
		return 0, false, nil
	}
	n, err := strconv.Atoi(m)
	return n, err == nil, nil
}

// Batch 多块区域各自 OCR，返回每块拼接的文字（同 Python ocr_batch）。
func Batch(crops []*img.BGR, scale float64, languages []string) ([]string, error) {
	out := make([]string, len(crops))
	for i, c := range crops {
		items, err := OCR(c, scale, languages)
		if err != nil {
			return nil, err
		}
		out[i] = Joined(items, "")
	}
	return out, nil
}
