// Package img 是识别用的图像：BGR 三通道、每像素 3 字节、行紧挨着（同 OpenCV 的 8UC3 Mat、Python 版的 numpy 数组）。
// 缩放用 OpenCV 的通用 INTER_LINEAR 算法（见 resize.go）：判页模板和 OCR 是在 OpenCV 缩放后的画面上调出来的。
package img

import (
	"fmt"
	"image"
	"image/color"
)

// BGR 是一张 BGR 图。Pix[(y*W+x)*3 + c]，c：0 蓝、1 绿、2 红。
type BGR struct {
	W, H int
	Pix  []byte
}

// New 建一张全黑的图。
func New(w, h int) *BGR { return &BGR{W: w, H: h, Pix: make([]byte, w*h*3)} }

// Clone 复制一份。
func (b *BGR) Clone() *BGR { return &BGR{W: b.W, H: b.H, Pix: append([]byte(nil), b.Pix...)} }

// Valid 尺寸和数据长度对得上、不是空图。
func (b *BGR) Valid() bool { return b != nil && b.W > 0 && b.H > 0 && len(b.Pix) == b.W*b.H*3 }

// Crop 复制出 (x, y) 起 w×h 的一块（要在图内）。
func (b *BGR) Crop(x, y, w, h int) *BGR {
	out := New(w, h)
	for r := range h {
		copy(out.Pix[r*w*3:(r+1)*w*3], b.Pix[((y+r)*b.W+x)*3:((y+r)*b.W+x+w)*3])
	}
	return out
}

// AnyNonZero 有没有不是全黑的像素。
func (b *BGR) AnyNonZero() bool {
	for _, v := range b.Pix {
		if v != 0 {
			return true
		}
	}
	return false
}

// FromImage 把 Go 的 image.Image 转成 BGR（截图驱动给 RGBA / NRGBA 时用）。
func FromImage(m image.Image) *BGR {
	r := m.Bounds()
	out := New(r.Dx(), r.Dy())
	if rgba, ok := m.(*image.RGBA); ok {
		for y := range out.H {
			row := rgba.Pix[y*rgba.Stride:]
			for x := range out.W {
				o := (y*out.W + x) * 3
				out.Pix[o], out.Pix[o+1], out.Pix[o+2] = row[x*4+2], row[x*4+1], row[x*4]
			}
		}
		return out
	}
	for y := range out.H {
		for x := range out.W {
			c := color.NRGBAModel.Convert(m.At(r.Min.X+x, r.Min.Y+y)).(color.NRGBA)
			o := (y*out.W + x) * 3
			out.Pix[o], out.Pix[o+1], out.Pix[o+2] = c.B, c.G, c.R
		}
	}
	return out
}

// FromRGBA 把紧挨着的 RGBA（或 RGBX）字节转成 BGR（adb screencap 的原始输出）。
func FromRGBA(w, h int, rgba []byte) (*BGR, error) {
	if w <= 0 || h <= 0 || len(rgba) < w*h*4 {
		return nil, fmt.Errorf("RGBA 数据太短：%dx%d 要 %d 字节，只有 %d", w, h, w*h*4, len(rgba))
	}
	out := New(w, h)
	for i := range w * h {
		out.Pix[i*3], out.Pix[i*3+1], out.Pix[i*3+2] = rgba[i*4+2], rgba[i*4+1], rgba[i*4]
	}
	return out, nil
}

// ToRGBA 转成 Go 的 image.RGBA（写 PNG / JPEG 用）。
func (b *BGR) ToRGBA() *image.RGBA {
	out := image.NewRGBA(image.Rect(0, 0, b.W, b.H))
	for i := range b.W * b.H {
		out.Pix[i*4], out.Pix[i*4+1], out.Pix[i*4+2], out.Pix[i*4+3] = b.Pix[i*3+2], b.Pix[i*3+1], b.Pix[i*3], 255
	}
	return out
}
