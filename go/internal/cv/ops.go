package cv

import (
	"encoding/binary"
	"image"
	"image/color"

	"gocv.io/x/gocv"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// Scores 是 matchTemplate 的结果：W×H 的分数图，Val[y*W+x]。
type Scores struct {
	W, H int
	Val  []float32
}

// At 取 (x, y) 处的分数。
func (s *Scores) At(x, y int) float32 { return s.Val[y*s.W+x] }

// Max 最高分和它的位置（cv2.minMaxLoc 的 maxVal / maxLoc；并列取第一个，同 OpenCV 的扫描顺序）。
func (s *Scores) Max() (float32, int, int) {
	best, bx, by := float32(-1e30), 0, 0
	for i, v := range s.Val {
		if v > best {
			best, bx, by = v, i%s.W, i/s.W
		}
	}
	return best, bx, by
}

// MatchMethod 是 matchTemplate 的方法。
type MatchMethod = gocv.TemplateMatchMode

const (
	CcoeffNormed = gocv.TmCcoeffNormed
	Ccorr        = gocv.TmCcorr
	CcorrNormed  = gocv.TmCcorrNormed
	SqdiffNormed = gocv.TmSqdiffNormed
)

// MatchTemplate 灰度图里找模板（cv2.matchTemplate）；画面比模板小时返回 nil（cv2 会报错，调用方都先判了尺寸）。
func MatchTemplate(screen, tpl *img.Gray, method MatchMethod) *Scores {
	if screen.W < tpl.W || screen.H < tpl.H || !tpl.Valid() {
		return nil
	}
	sm := must(grayMat(screen))
	defer sm.Close()
	tm := must(grayMat(tpl))
	defer tm.Close()
	res := gocv.NewMat()
	defer res.Close()
	mask := gocv.NewMat()
	defer mask.Close()
	check(gocv.MatchTemplate(sm, tm, &res, method, mask))
	out := &Scores{W: res.Cols(), H: res.Rows(), Val: make([]float32, res.Cols()*res.Rows())}
	data, _ := res.DataPtrFloat32()
	copy(out.Val, data)
	return out
}

// MatchTemplateBGR 彩色图里找彩色模板（cv2.matchTemplate 三通道）。
func MatchTemplateBGR(screen, tpl *img.BGR, method MatchMethod) *Scores {
	if screen.W < tpl.W || screen.H < tpl.H || !tpl.Valid() {
		return nil
	}
	sm := must(bgrMat(screen))
	defer sm.Close()
	tm := must(bgrMat(tpl))
	defer tm.Close()
	res := gocv.NewMat()
	defer res.Close()
	mask := gocv.NewMat()
	defer mask.Close()
	check(gocv.MatchTemplate(sm, tm, &res, method, mask))
	out := &Scores{W: res.Cols(), H: res.Rows(), Val: make([]float32, res.Cols()*res.Rows())}
	data, _ := res.DataPtrFloat32()
	copy(out.Val, data)
	return out
}

// ---------------------------------------------------------------- 形态学、边缘、轮廓

// Rect 是 (x, y, w, h)。
type Rect struct{ X, Y, W, H int }

// RectKernel 矩形结构元素（cv2.getStructuringElement(MORPH_RECT, (w, h))）。
type RectKernel struct{ W, H int }

func (k RectKernel) mat() gocv.Mat {
	return gocv.GetStructuringElement(gocv.MorphRect, image.Pt(k.W, k.H))
}

// Dilate 膨胀（cv2.dilate(src, kernel, iterations=n)）。
func Dilate(g *img.Gray, k RectKernel, iterations int) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	km := k.mat()
	defer km.Close()
	out := gocv.NewMat()
	defer out.Close()
	_ = gocv.DilateWithParams(m, &out, km, image.Pt(-1, -1), iterations, gocv.BorderConstant, color.RGBA{}) // 8 位图膨胀：边界 0 等同 OpenCV 缺省（最小值）
	return toGray(out)
}

// Erode 腐蚀（cv2.erode(src, kernel, iterations=n)）。
func Erode(g *img.Gray, k RectKernel, iterations int) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	km := k.mat()
	defer km.Close()
	out := gocv.NewMat()
	defer out.Close()
	_ = gocv.ErodeWithParams(m, &out, km, image.Pt(-1, -1), iterations, gocv.BorderConstant) // 边界值用 OpenCV 缺省（最大值）
	return toGray(out)
}

// MorphOp 是形态学运算。
type MorphOp = gocv.MorphType

const (
	MorphOpen  = gocv.MorphOpen
	MorphClose = gocv.MorphClose
)

// Morphology 开 / 闭运算（cv2.morphologyEx(src, op, kernel)）。
func Morphology(g *img.Gray, op MorphOp, k RectKernel) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	km := k.mat()
	defer km.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.MorphologyEx(m, &out, op, km))
	return toGray(out)
}

// Canny 边缘（cv2.Canny(src, t1, t2)）。src 可以是 BGR（cv2 收彩色图时按 OpenCV 的多通道做法）。
func Canny(b *img.BGR, t1, t2 float32) *img.Gray {
	m := must(bgrMat(b))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.Canny(m, &out, t1, t2))
	return toGray(out)
}

// CannyGray 灰度图边缘。
func CannyGray(g *img.Gray, t1, t2 float32) *img.Gray {
	m := must(grayMat(g))
	defer m.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.Canny(m, &out, t1, t2))
	return toGray(out)
}

// ExternalBoxes 最外层轮廓的外接矩形（cv2.findContours(RETR_EXTERNAL, CHAIN_APPROX_SIMPLE) + boundingRect），顺序同 OpenCV。
func ExternalBoxes(g *img.Gray) []Rect {
	m := must(grayMat(g))
	defer m.Close()
	contours := gocv.FindContours(m, gocv.RetrievalExternal, gocv.ChainApproxSimple)
	defer contours.Close()
	out := make([]Rect, 0, contours.Size())
	for i := range contours.Size() {
		r := gocv.BoundingRect(contours.At(i))
		out = append(out, Rect{r.Min.X, r.Min.Y, r.Dx(), r.Dy()})
	}
	return out
}

// Component 是一个连通块（cv2.connectedComponentsWithStats 的一行；不含背景 0 号）。
type Component struct {
	Rect
	Area   int
	Center [2]float64
}

// Components 连通块（8 邻接，同 cv2 缺省），返回标签图（Label[y*W+x]，0 是背景）和每块的统计（下标 i 对应标签 i+1）。
func Components(g *img.Gray) ([]int32, []Component) {
	m := must(grayMat(g))
	defer m.Close()
	labels, stats, cents := gocv.NewMat(), gocv.NewMat(), gocv.NewMat()
	defer labels.Close()
	defer stats.Close()
	defer cents.Close()
	n := gocv.ConnectedComponentsWithStats(m, &labels, &stats, &cents)
	raw := labels.ToBytes()
	outLab := make([]int32, len(raw)/4)
	for i := range outLab {
		outLab[i] = int32(binary.LittleEndian.Uint32(raw[i*4:])) //nolint:gosec // 标签号不会溢出
	}
	comps := make([]Component, 0, max(0, n-1))
	for i := 1; i < n; i++ {
		comps = append(comps, Component{
			Rect: Rect{
				X: int(stats.GetIntAt(i, int(gocv.CC_STAT_LEFT))), Y: int(stats.GetIntAt(i, int(gocv.CC_STAT_TOP))),
				W: int(stats.GetIntAt(i, int(gocv.CC_STAT_WIDTH))), H: int(stats.GetIntAt(i, int(gocv.CC_STAT_HEIGHT))),
			},
			Area:   int(stats.GetIntAt(i, int(gocv.CC_STAT_AREA))),
			Center: [2]float64{cents.GetDoubleAt(i, 0), cents.GetDoubleAt(i, 1)},
		})
	}
	return outLab, comps
}

// Hist 单通道直方图（cv2.calcHist([ch], [0], None, [bins], [lo, hi])），返回每箱个数。
func Hist(g *img.Gray, bins int, lo, hi float64) []float32 {
	m := must(grayMat(g))
	defer m.Close()
	mask := gocv.NewMat()
	defer mask.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.CalcHist([]gocv.Mat{m}, []int{0}, mask, &out, []int{bins}, []float64{lo, hi}, false))
	v, _ := out.DataPtrFloat32()
	return append([]float32(nil), v...)
}

// AbsDiff 逐像素差的绝对值（cv2.absdiff）。
func AbsDiff(a, b *img.Gray) *img.Gray {
	am := must(grayMat(a))
	defer am.Close()
	bm := must(grayMat(b))
	defer bm.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.AbsDiff(am, bm, &out))
	return toGray(out)
}

// Channel 取 BGR 的一个通道（numpy 的 img[..., c]）。
func Channel(b *img.BGR, c int) *img.Gray {
	out := img.NewGray(b.W, b.H)
	for i := range b.W * b.H {
		out.Pix[i] = b.Pix[i*3+c]
	}
	return out
}
