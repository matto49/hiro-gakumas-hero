package img

import "math"

// 照 OpenCV 5（modules/imgproc/src/resize.cpp）8 位图 INTER_LINEAR 的通用算法实现，每个平台结果一样。
// 注意 cv2 自己在各平台上走不同的加速库（Mac：Carotene / KleidiCV；x86：IPP），结果互相差 1–2 个灰度级；
// 本实现和 Mac 上 cv2 比：放大逐字节相同，缩小最多差 2（resize_test 对照）。识别阈值不受这点差别影响。
//
//   - 尺寸一样：复制。
//   - 正好缩一半（宽高都是 2 倍整数）：OpenCV 改走 INTER_AREA 的快速路径，每 2×2 求平均 (a+b+c+d+2)>>2。
//   - 其他：两个方向各取两个点，系数是 11 位定点（×2048 后四舍六入五取偶）。横向结果是整数；纵向合成时，
//     每行前面整 16 个、再整 8 个元素一组走 SIMD 的算法（先右移 4 位再取乘积高 16 位、最后 +2 右移 2），剩下不满 8 个的走
//     标量算法（+2^21 右移 22）。两种舍入差 1 的情况都有，所以要按 OpenCV 的分法来（128 位 SIMD：x86 SSE / ARM NEON；
//     VResizeLinearVec_32s8u 先按 v_uint8 的 16 个一组，再按 v_int16 的 8 个一组）。
const (
	coefBits  = 11
	coefScale = 1 << coefBits // 2048
	simdLanes = 8             // SIMD 能覆盖到的最小一组：v_int16 在 128 位 SIMD 下的元素数
)

// Resize 把图缩放到 w×h，同 cv2.resize(src, (w, h))（缺省 INTER_LINEAR）。
func Resize(src *BGR, w, h int) *BGR {
	if src.W == w && src.H == h {
		return src.Clone()
	}
	scaleX := 1.0 / (float64(w) / float64(src.W))
	scaleY := 1.0 / (float64(h) / float64(src.H))
	iscaleX, iscaleY := roundEven(scaleX), roundEven(scaleY)
	areaFast := math.Abs(scaleX-float64(iscaleX)) < epsilon && math.Abs(scaleY-float64(iscaleY)) < epsilon
	if areaFast && iscaleX == 2 && iscaleY == 2 {
		return halfArea(src, w, h)
	}
	return linear(src, w, h, scaleX, scaleY)
}

const epsilon = 2.220446049250313e-16 // DBL_EPSILON

// roundEven 同 OpenCV 的 cvRound / saturate_cast<int>（四舍六入五取偶）。
func roundEven(v float64) int { return int(math.RoundToEven(v)) }

func saturateShort(v float32) int32 {
	r := math.RoundToEven(float64(v))
	switch {
	case r > math.MaxInt16:
		return math.MaxInt16
	case r < math.MinInt16:
		return math.MinInt16
	}
	return int32(r)
}

func halfArea(src *BGR, w, h int) *BGR {
	dst := New(w, h)
	sw := src.W * 3
	for y := range h {
		r0 := src.Pix[(2*y)*sw:]
		r1 := src.Pix[(2*y+1)*sw:]
		d := dst.Pix[y*w*3:]
		for x := range w {
			for c := range 3 {
				i := 2*x*3 + c
				d[x*3+c] = sat8((int32(r0[i]) + int32(r0[i+3]) + int32(r1[i]) + int32(r1[i+3]) + 2) >> 2)
			}
		}
	}
	return dst
}

// linear 是 OpenCV resizeGeneric_ + HResizeLinear + VResizeLinear（8u，定点）。
func linear(src *BGR, w, h int, scaleX, scaleY float64) *BGR {
	const cn = 3
	// ---- 横向：每个目标列取源的 sx、sx+1 两列，系数 (1-fx, fx)
	xofs := make([]int, w*cn)
	alpha := make([]int32, w*cn*2)
	xmax := w
	for dx := range w {
		fx := float32((float64(dx)+0.5)*scaleX - 0.5)
		sx := int(math.Floor(float64(fx)))
		fx -= float32(sx)
		if sx < 0 {
			fx, sx = 0, 0
		}
		if sx+1 >= src.W {
			xmax = min(xmax, dx)
			if sx >= src.W-1 {
				fx, sx = 0, src.W-1
			}
		}
		a0, a1 := saturateShort((1-fx)*coefScale), saturateShort(fx*coefScale)
		for k := range cn {
			xofs[dx*cn+k] = sx*cn + k
			alpha[(dx*cn+k)*2], alpha[(dx*cn+k)*2+1] = a0, a1
		}
	}
	dwidth, xmaxE := w*cn, xmax*cn
	hrow := func(sy int) []int32 {
		s := src.Pix[sy*src.W*cn:]
		out := make([]int32, dwidth)
		for dx := range dwidth {
			sx := xofs[dx]
			if dx < xmaxE {
				out[dx] = int32(s[sx])*alpha[dx*2] + int32(s[sx+cn])*alpha[dx*2+1]
			} else {
				out[dx] = int32(s[sx]) * coefScale
			}
		}
		return out
	}
	clip := func(v, hi int) int {
		if v < 0 {
			return 0
		}
		if v >= hi {
			return hi - 1
		}
		return v
	}
	// ---- 纵向：每个目标行取源的 sy、sy+1 两行（夹在图内），系数 (1-fy, fy)
	dst := New(w, h)
	cache := map[int][]int32{}
	row := func(sy int) []int32 {
		if r, ok := cache[sy]; ok {
			return r
		}
		r := hrow(sy)
		cache[sy] = r
		return r
	}
	simdEnd := dwidth - dwidth%simdLanes
	for dy := range h {
		fy := float32((float64(dy)+0.5)*scaleY - 0.5)
		sy := int(math.Floor(float64(fy)))
		fy -= float32(sy)
		b0, b1 := saturateShort((1-fy)*coefScale), saturateShort(fy*coefScale)
		s0, s1 := row(clip(sy, src.H)), row(clip(sy+1, src.H))
		d := dst.Pix[dy*dwidth : (dy+1)*dwidth]
		for x := range simdEnd { // SIMD：v_rshr_pack_u<2>(v_mul_hi(S0>>4, b0) + v_mul_hi(S1>>4, b1))
			v := mulHi(pack16(s0[x]>>4), b0) + mulHi(pack16(s1[x]>>4), b1)
			d[x] = sat8((v + 2) >> 2)
		}
		for x := simdEnd; x < dwidth; x++ { // 标量：FixedPtCast<int, uchar, 22>
			d[x] = sat8((s0[x]*b0 + s1[x]*b1 + (1 << (coefBits*2 - 1))) >> (coefBits * 2))
		}
	}
	return dst
}

// pack16 同 v_pack：int32 饱和到 int16。
func pack16(v int32) int32 {
	if v > math.MaxInt16 {
		return math.MaxInt16
	}
	if v < math.MinInt16 {
		return math.MinInt16
	}
	return v
}

// mulHi 同 v_mul_hi（int16 × int16 取高 16 位，算术右移）。
func mulHi(a, b int32) int32 { return (a * b) >> 16 }

func sat8(v int32) byte {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return byte(v)
}
