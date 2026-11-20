// Package pil 是 Pillow 的几个图像操作，逐位照它的 C 实现（libImaging/Resample.c、Convert.c）写：
// ddddocr 的预处理用 Pillow 缩放（Image.resize(..., LANCZOS)）再转灰度（convert('L')），模型对输入很敏感，要和 Python 版一样。
package pil

import "math"

// Image 是 8 位、Bands 个通道、行紧挨着的图（L：1 通道；RGB：3 通道，R、G、B 顺序）。
type Image struct {
	W, H, Bands int
	Pix         []byte
}

// precisionBits 是 8 位图卷积系数的定点位数（Resample.c：32 - 8 - 2）。
const precisionBits = 32 - 8 - 2

func sinc(x float64) float64 {
	if x == 0 {
		return 1
	}
	x *= math.Pi
	return math.Sin(x) / x
}

func lanczos(x float64) float64 {
	if -3.0 <= x && x < 3.0 {
		return sinc(x) * sinc(x/3)
	}
	return 0
}

// coeffs 同 precompute_coeffs + normalize_coeffs_8bpc：每个输出位置的起点、个数和定点系数。
func coeffs(inSize, outSize int) (bounds []int, kk []int32, ksize int) {
	in0, in1 := float32(0), float32(inSize) // C 里 box 是 float
	scale := float64(in1-in0) / float64(outSize)
	filterscale := max(scale, 1.0)
	support := 3.0 * filterscale
	ksize = int(math.Ceil(support))*2 + 1
	pre := make([]float64, outSize*ksize)
	bounds = make([]int, outSize*2)
	for xx := range outSize {
		center := float64(in0) + (float64(xx)+0.5)*scale
		ww := 0.0
		ss := 1.0 / filterscale
		xmin := int(center - support + 0.5) // C 的 (int) 向零取整
		if xmin < 0 {
			xmin = 0
		}
		xmax := int(center + support + 0.5)
		if xmax > inSize {
			xmax = inSize
		}
		xmax -= xmin
		k := pre[xx*ksize : (xx+1)*ksize]
		for x := range xmax {
			w := lanczos((float64(x+xmin) - center + 0.5) * ss)
			k[x] = w
			ww += w
		}
		for x := range xmax {
			if ww != 0 {
				k[x] /= ww
			}
		}
		bounds[xx*2], bounds[xx*2+1] = xmin, xmax
	}
	kk = make([]int32, len(pre))
	for i, v := range pre {
		if v < 0 {
			kk[i] = int32(-0.5 + v*(1<<precisionBits))
		} else {
			kk[i] = int32(0.5 + v*(1<<precisionBits))
		}
	}
	return bounds, kk, ksize
}

func clip8(v int32) byte {
	if v >= 1<<precisionBits<<8 {
		return 255
	}
	if v <= 0 {
		return 0
	}
	return byte(v >> precisionBits)
}

// Resize 同 Image.resize((w, h), Image.LANCZOS)（不带 box、不带 reducing_gap）：先横向再纵向两遍卷积，中间结果存 8 位。
func Resize(src *Image, w, h int) *Image {
	if src.W == w && src.H == h {
		return &Image{W: w, H: h, Bands: src.Bands, Pix: append([]byte(nil), src.Pix...)}
	}
	needH := w != src.W
	needV := h != src.H
	bh, kh, ksh := coeffs(src.W, w)
	bv, kv, ksv := coeffs(src.H, h)
	yFirst := bv[0]
	yLast := bv[h*2-2] + bv[h*2-1]
	cur := src
	if needH {
		for i := range h {
			bv[i*2] -= yFirst
		}
		tmp := &Image{W: w, H: yLast - yFirst, Bands: src.Bands, Pix: make([]byte, w*(yLast-yFirst)*src.Bands)}
		horizontal(tmp, src, yFirst, ksh, bh, kh)
		cur = tmp
	}
	if needV {
		out := &Image{W: cur.W, H: h, Bands: cur.Bands, Pix: make([]byte, cur.W*h*cur.Bands)}
		vertical(out, cur, ksv, bv, kv)
		cur = out
	}
	return cur
}

func horizontal(out, in *Image, offset, ksize int, bounds []int, kk []int32) {
	nb := in.Bands
	for yy := range out.H {
		row := in.Pix[(yy+offset)*in.W*nb:]
		for xx := range out.W {
			xmin, xmax := bounds[xx*2], bounds[xx*2+1]
			k := kk[xx*ksize:]
			for c := range nb {
				ss := int32(1 << (precisionBits - 1))
				for x := range xmax {
					ss += int32(row[(x+xmin)*nb+c]) * k[x]
				}
				out.Pix[(yy*out.W+xx)*nb+c] = clip8(ss)
			}
		}
	}
}

func vertical(out, in *Image, ksize int, bounds []int, kk []int32) {
	nb := in.Bands
	for yy := range out.H {
		ymin, ymax := bounds[yy*2], bounds[yy*2+1]
		k := kk[yy*ksize:]
		for xx := range out.W {
			for c := range nb {
				ss := int32(1 << (precisionBits - 1))
				for y := range ymax {
					ss += int32(in.Pix[((y+ymin)*in.W+xx)*nb+c]) * k[y]
				}
				out.Pix[(yy*out.W+xx)*nb+c] = clip8(ss)
			}
		}
	}
}

// ToL 同 convert('L')：L = (R*19595 + G*38470 + B*7471 + 0x8000) >> 16（Convert.c 的 L24）。已经是 L 的原样返回。
func ToL(src *Image) *Image {
	if src.Bands == 1 {
		return src
	}
	out := &Image{W: src.W, H: src.H, Bands: 1, Pix: make([]byte, src.W*src.H)}
	for i := range out.Pix {
		p := src.Pix[i*src.Bands:]
		out.Pix[i] = byte((uint32(p[0])*19595 + uint32(p[1])*38470 + uint32(p[2])*7471 + 0x8000) >> 16) //nolint:gosec // 系数和为 65536，结果 ≤ 255
	}
	return out
}
