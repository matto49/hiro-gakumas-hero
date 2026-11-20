package cv

import (
	"image"
	"unsafe"

	"gocv.io/x/gocv"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// F32 是单通道 float32 图（numpy 的 astype(np.float32)）。Val[y*W+x]。
type F32 struct {
	W, H int
	Val  []float32
}

// NewF32 全 0。
func NewF32(w, h int) *F32 { return &F32{W: w, H: h, Val: make([]float32, w*h)} }

// F32Of 灰度图转 float32。
func F32Of(g *img.Gray) *F32 {
	out := NewF32(g.W, g.H)
	for i, v := range g.Pix {
		out.Val[i] = float32(v)
	}
	return out
}

func f32Mat(f *F32) gocv.Mat {
	if len(f.Val) == 0 {
		panic(&Error{Err: ErrDecode})
	}
	raw := unsafe.Slice((*byte)(unsafe.Pointer(&f.Val[0])), len(f.Val)*4) //nolint:gosec // float32 切片按字节交给 OpenCV，随即克隆
	m := must(gocv.NewMatFromBytes(f.H, f.W, gocv.MatTypeCV32F, raw))
	c := m.Clone()
	_ = m.Close()
	return c
}

// MatchTemplateF32 float32 图上的 matchTemplate（cv2.matchTemplate 收 float32 数组）；画面比模板小时返回 nil。
func MatchTemplateF32(screen, tpl *F32, method MatchMethod) *Scores {
	if screen.W < tpl.W || screen.H < tpl.H || tpl.W == 0 || tpl.H == 0 {
		return nil
	}
	sm := f32Mat(screen)
	defer sm.Close()
	tm := f32Mat(tpl)
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

// BGRA 是带透明通道的图（cv2.IMREAD_UNCHANGED 读 PNG 图标）。Pix[(y*W+x)*4 + c]。
type BGRA struct {
	W, H int
	Pix  []byte
}

// ReadBGRA 原样读四通道图；不是四通道的补一个全 255 的透明通道（cv2 读出三通道时 Python 那边取 [:, :, 3] 会出错，调用方只给带透明的图）。
func ReadBGRA(path string) (*BGRA, error) {
	m := gocv.IMRead(path, gocv.IMReadUnchanged)
	defer m.Close()
	if m.Empty() {
		return nil, ErrDecode
	}
	if m.Channels() == 4 {
		return &BGRA{W: m.Cols(), H: m.Rows(), Pix: m.ToBytes()}, nil
	}
	c := gocv.NewMat()
	defer c.Close()
	code := gocv.ColorBGRToBGRA
	if m.Channels() == 1 {
		code = gocv.ColorGrayToBGRA
	}
	check(gocv.CvtColor(m, &c, code))
	return &BGRA{W: c.Cols(), H: c.Rows(), Pix: c.ToBytes()}, nil
}

// ResizeBGRA 四通道一起缩放（cv2.resize 收四通道图）。
func ResizeBGRA(b *BGRA, w, h int, interp Interp) *BGRA {
	m := must(gocv.NewMatFromBytes(b.H, b.W, gocv.MatTypeCV8UC4, b.Pix))
	c := m.Clone()
	_ = m.Close()
	defer c.Close()
	out := gocv.NewMat()
	defer out.Close()
	check(gocv.Resize(c, &out, image.Pt(w, h), 0, 0, interp))
	return &BGRA{W: out.Cols(), H: out.Rows(), Pix: out.ToBytes()}
}

// Split 拆成 BGR 和透明通道。
func (b *BGRA) Split() (*img.BGR, *img.Gray) {
	bgr, a := img.New(b.W, b.H), img.NewGray(b.W, b.H)
	for i := range b.W * b.H {
		copy(bgr.Pix[i*3:i*3+3], b.Pix[i*4:i*4+3])
		a.Pix[i] = b.Pix[i*4+3]
	}
	return bgr, a
}
