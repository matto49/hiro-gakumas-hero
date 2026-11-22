// Package cv 是识别层用的 OpenCV 操作（gocv，同 Python 版的 cv2 调用）。识别代码只碰 img.BGR / img.Gray，
// gocv 的 Mat 只在这里的函数里建、用完就关，不往外漏（gocv 的 Mat 要手动 Close，漏了就是内存泄漏）。
package cv

import (
	"errors"
	"image"
	"image/color"
	"math"

	"gocv.io/x/gocv"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// Interp 是缩放插值（同 cv2.INTER_*）。
type Interp = gocv.InterpolationFlags

const (
	Linear = gocv.InterpolationLinear
	Cubic  = gocv.InterpolationCubic
	Area   = gocv.InterpolationArea
)

func bgrMat(b *img.BGR) (gocv.Mat, error) {
	m, err := gocv.NewMatFromBytes(b.H, b.W, gocv.MatTypeCV8UC3, b.Pix)
	if err != nil {
		return m, err
	}
	c := m.Clone() // NewMatFromBytes 直接引用 Go 切片：克隆一份，免得 GC 挪走
	_ = m.Close()
	return c, nil
}

func grayMat(g *img.Gray) (gocv.Mat, error) {
	m, err := gocv.NewMatFromBytes(g.H, g.W, gocv.MatTypeCV8UC1, g.Pix)
	if err != nil {
		return m, err
	}
	c := m.Clone()
	_ = m.Close()
	return c, nil
}

func toBGR(m gocv.Mat) *img.BGR {
	if m.Empty() {
		return img.New(0, 0)
	}
	if m.Channels() == 1 {
		c := gocv.NewMat()
		defer c.Close()
		check(gocv.CvtColor(m, &c, gocv.ColorGrayToBGR))
		return toBGR(c)
	}
	return &img.BGR{W: m.Cols(), H: m.Rows(), Pix: m.ToBytes()}
}

func toGray(m gocv.Mat) *img.Gray {
	if m.Empty() {
		return img.NewGray(0, 0)
	}
	return &img.Gray{W: m.Cols(), H: m.Rows(), Pix: m.ToBytes()}
}

// Error 是 OpenCV 内部报错（同 Python 里 cv2 抛的 cv2.error）：这里的函数遇到它就 panic，
// 识别层的入口（判页、读数）用 Recover 接住，转成普通的 error，和 Python 版「异常一路抛上去」一样。
type Error struct{ Err error }

func (e *Error) Error() string { return "OpenCV：" + e.Err.Error() }
func (e *Error) Unwrap() error { return e.Err }

func check(err error) {
	if err != nil {
		panic(&Error{Err: err})
	}
}

// Recover 接住本包的 OpenCV 报错，写进 *errp；别的 panic 照常往上抛。用法：defer cv.Recover(&err)。
func Recover(errp *error) {
	if r := recover(); r != nil {
		e, ok := r.(*Error)
		if !ok {
			panic(r)
		}
		*errp = e
	}
}

// must 是 Mat 建不出来时的处理：空图、尺寸 / 数据对不上（Python 版这时 cv2 抛 cv2.error，一样按 OpenCV 报错处理）。
func must(m gocv.Mat, err error) gocv.Mat {
	if err != nil {
		panic(&Error{Err: err})
	}
	return m
}

// ---------------------------------------------------------------- 颜色、缩放

// Gray BGR → 灰度（cv2.COLOR_BGR2GRAY）。
func Gray(b *img.BGR) *img.Gray {
	if b.Empty() {
		return img.NewGray(0, 0)
	}
	m := must(bgrMat(b))
	defer m.Close()
	g := gocv.NewMat()
	defer g.Close()
	check(gocv.CvtColor(m, &g, gocv.ColorBGRToGray))
	return toGray(g)
}

// GrayToBGR 灰度 → BGR（cv2.COLOR_GRAY2BGR）。
func GrayToBGR(g *img.Gray) *img.BGR {
	if !g.Valid() {
		return img.New(0, 0)
	}
	m := must(grayMat(g))
	defer m.Close()
	return toBGR(m)
}

// HSV BGR → HSV（cv2.COLOR_BGR2HSV，H 0–179）。结果按 BGR 的三个通道存：Pix[i*3] = H、+1 = S、+2 = V。
func HSV(b *img.BGR) *img.BGR {
	m := must(bgrMat(b))
	defer m.Close()
	h := gocv.NewMat()
	defer h.Close()
	check(gocv.CvtColor(m, &h, gocv.ColorBGRToHSV))
	return &img.BGR{W: h.Cols(), H: h.Rows(), Pix: h.ToBytes()}
}

// Resize 缩放到 w×h（cv2.resize(src, (w, h), interpolation=…)）。
func Resize(b *img.BGR, w, h int, interp Interp) *img.BGR {
	m := must(bgrMat(b))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.Resize(m, &out, image.Pt(w, h), 0, 0, interp))
	return toBGR(out)
}

// ResizeScale 按比例缩放（cv2.resize(src, None, fx=s, fy=s, interpolation=…)）。
func ResizeScale(b *img.BGR, s float64, interp Interp) *img.BGR {
	m := must(bgrMat(b))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.Resize(m, &out, image.Point{}, s, s, interp))
	return toBGR(out)
}

// ResizeGray 灰度图缩放到 w×h。
func ResizeGray(g *img.Gray, w, h int, interp Interp) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.Resize(m, &out, image.Pt(w, h), 0, 0, interp))
	return toGray(out)
}

// ResizeGrayScale 灰度图按比例缩放。
func ResizeGrayScale(g *img.Gray, s float64, interp Interp) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.Resize(m, &out, image.Point{}, s, s, interp))
	return toGray(out)
}

// ---------------------------------------------------------------- 读写

// ErrDecode 是图读不出来。
var ErrDecode = errors.New("图读不出来")

// Read 读一张彩色图（cv2.imread(path, IMREAD_COLOR)）。
func Read(path string) (*img.BGR, error) {
	m := gocv.IMRead(path, gocv.IMReadColor)
	defer m.Close()
	if m.Empty() {
		return nil, ErrDecode
	}
	return toBGR(m), nil
}

// ReadGray 读一张灰度图（cv2.imread(path, IMREAD_GRAYSCALE)）。
func ReadGray(path string) (*img.Gray, error) {
	m := gocv.IMRead(path, gocv.IMReadGrayScale)
	defer m.Close()
	if m.Empty() {
		return nil, ErrDecode
	}
	return toGray(m), nil
}

// ReadUnchanged 原样读（带透明通道的图标：cv2.IMREAD_UNCHANGED）：返回 BGR 和透明通道（没有就是 nil）。
func ReadUnchanged(path string) (*img.BGR, *img.Gray, error) {
	m := gocv.IMRead(path, gocv.IMReadUnchanged)
	defer m.Close()
	if m.Empty() {
		return nil, nil, ErrDecode
	}
	if m.Channels() != 4 {
		if m.Channels() == 1 {
			return toBGR(m), nil, nil
		}
		return toBGR(m), nil, nil
	}
	bgr := gocv.NewMat()
	defer bgr.Close()
	check(gocv.CvtColor(m, &bgr, gocv.ColorBGRAToBGR))
	alpha := gocv.NewMat()
	defer alpha.Close()
	check(gocv.ExtractChannel(m, &alpha, 3))
	return toBGR(bgr), toGray(alpha), nil
}

// Decode 解一段图片字节（cv2.imdecode，彩色）。
func Decode(data []byte) (*img.BGR, error) {
	m, err := gocv.IMDecode(data, gocv.IMReadColor)
	if err != nil {
		return nil, err
	}
	defer m.Close()
	if m.Empty() {
		return nil, ErrDecode
	}
	return toBGR(m), nil
}

// EncodePNG 编成 PNG（cv2.imencode(".png", img)）。
func EncodePNG(b *img.BGR) ([]byte, error) {
	m := must(bgrMat(b))
	defer m.Close()
	buf, err := gocv.IMEncode(gocv.PNGFileExt, m)
	if err != nil {
		return nil, err
	}
	defer buf.Close()
	return append([]byte(nil), buf.GetBytes()...), nil
}

// EncodeGrayPNG 灰度图编成 PNG。
func EncodeGrayPNG(g *img.Gray) ([]byte, error) {
	m := must(grayMat(g))
	defer m.Close()
	buf, err := gocv.IMEncode(gocv.PNGFileExt, m)
	if err != nil {
		return nil, err
	}
	defer buf.Close()
	return append([]byte(nil), buf.GetBytes()...), nil
}

// ---------------------------------------------------------------- 阈值、掩码、边框

// Threshold 二值化（cv2.threshold(gray, t, maxv, THRESH_BINARY)）。
func Threshold(g *img.Gray, t, maxv float32) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	gocv.Threshold(m, &out, t, maxv, gocv.ThresholdBinary)
	return toGray(out)
}

// ThresholdBGR 对三个通道各自二值化（cv2.threshold 作用在 BGR 图上）。
func ThresholdBGR(b *img.BGR, t, maxv float32) *img.BGR {
	m := must(bgrMat(b))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	gocv.Threshold(m, &out, t, maxv, gocv.ThresholdBinary)
	return toBGR(out)
}

// Otsu 大津法二值化，返回阈值和结果（cv2.threshold(..., 0, 255, THRESH_BINARY | THRESH_OTSU)）。
func Otsu(g *img.Gray) (float32, *img.Gray) {
	m := must(grayMat(g))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	t := gocv.Threshold(m, &out, 0, 255, gocv.ThresholdBinary|gocv.ThresholdOtsu)
	return t, toGray(out)
}

// InRange 三通道各自落在 [lo, hi] 里的像素为 255（cv2.inRange；HSV 图也用它）。
func InRange(b *img.BGR, lo, hi [3]float64) *img.Gray {
	m := must(bgrMat(b))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.InRangeWithScalar(m, gocv.NewScalar(lo[0], lo[1], lo[2], 0), gocv.NewScalar(hi[0], hi[1], hi[2], 0), &out))
	return toGray(out)
}

// InRangeGray 单通道落在 [lo, hi] 里的像素为 255。
func InRangeGray(g *img.Gray, lo, hi float64) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.InRangeWithScalar(m, gocv.NewScalar(lo, 0, 0, 0), gocv.NewScalar(hi, 0, 0, 0), &out))
	return toGray(out)
}

// BorderConstant 四边补常数色（cv2.copyMakeBorder(..., BORDER_CONSTANT, value=v)）。
func BorderConstant(b *img.BGR, top, bottom, left, right int, v [3]float64) *img.BGR {
	m := must(bgrMat(b))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.CopyMakeBorder(m, &out, top, bottom, left, right, gocv.BorderConstant, bgrColor(v)))
	return toBGR(out)
}

// BorderConstantGray 灰度图四边补常数。
func BorderConstantGray(g *img.Gray, top, bottom, left, right int, v float64) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.CopyMakeBorder(m, &out, top, bottom, left, right, gocv.BorderConstant, bgrColor([3]float64{v, v, v})))
	return toGray(out)
}

// bgrColor：gocv 把 color.RGBA 的 B、G、R 字段依次映射到 OpenCV 的通道 0、1、2（core.go），正好是 BGR 顺序。
func bgrColor(v [3]float64) color.RGBA {
	return color.RGBA{B: clamp8(v[0]), G: clamp8(v[1]), R: clamp8(v[2])}
}

func clamp8(v float64) uint8 { return uint8(math.Max(0, math.Min(255, math.Round(v)))) }

// ConvertScaleAbs 同 cv2.convertScaleAbs(src, alpha=a)：|src·a| 四舍五入、饱和到 0–255。
func ConvertScaleAbs(b *img.BGR, alpha float64) *img.BGR {
	m := must(bgrMat(b))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.ConvertScaleAbs(m, &out, alpha, 0))
	return toBGR(out)
}
