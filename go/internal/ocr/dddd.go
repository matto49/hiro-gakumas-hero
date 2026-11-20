package ocr

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/onnx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pil"
)

// ddddocr（同 Python 的 ddddocr.DdddOcr(show_ad=False).classification，1.6.1 的缺省模型 common_old.onnx + 旧字符集）：
// 识别层读 HUD 数字、分数的退路。预处理和 ddddocr 一样走 Pillow：高缩放到 64（Lanczos）、转灰度、除以 255；
// 输出每步取最大类，CTC 去重、去空白。模型文件放 cache/models/ddddocr/common_old.onnx（见 onnx.Model）。

//go:embed dddd/charset_old.json
var ddddCharsetJSON []byte

var (
	ddddOnce    sync.Once
	ddddSession *onnx.Session
	ddddCharset []string
	ddddErr     error
)

func dddd() (*onnx.Session, []string, error) {
	ddddOnce.Do(func() {
		if ddddErr = json.Unmarshal(ddddCharsetJSON, &ddddCharset); ddddErr != nil {
			return
		}
		path, err := onnx.Model("ddddocr/common_old.onnx")
		if err != nil {
			ddddErr = err
			return
		}
		ddddSession, ddddErr = onnx.Open(path)
	})
	return ddddSession, ddddCharset, ddddErr
}

// Dddd 彩色图（cv2.imencode 成 PNG 交给 ddddocr 的那张）的识别结果。
func Dddd(b *img.BGR) (string, error) {
	rgb := &pil.Image{W: b.W, H: b.H, Bands: 3, Pix: make([]byte, len(b.Pix))}
	for i := 0; i+2 < len(b.Pix); i += 3 {
		rgb.Pix[i], rgb.Pix[i+1], rgb.Pix[i+2] = b.Pix[i+2], b.Pix[i+1], b.Pix[i]
	}
	return ddddRun(rgb)
}

// DdddGray 单通道图（灰度 / 二值图编成的 PNG，Pillow 读出来是 L）。
func DdddGray(g *img.Gray) (string, error) {
	return ddddRun(&pil.Image{W: g.W, H: g.H, Bands: 1, Pix: g.Pix})
}

func ddddRun(src *pil.Image) (string, error) {
	sess, charset, err := dddd()
	if err != nil {
		return "", err
	}
	if src.W <= 0 || src.H <= 0 {
		return "", fmt.Errorf("ddddocr：空图")
	}
	tw := int(float64(src.W) * (64.0 / float64(src.H)))
	if tw <= 0 {
		return "", fmt.Errorf("ddddocr：缩放后宽度为 0（%dx%d）", src.W, src.H) // Pillow 同样报错
	}
	l := pil.ToL(pil.Resize(src, tw, 64))
	in := make([]float32, len(l.Pix))
	for i, v := range l.Pix {
		in[i] = float32(v) / 255
	}
	outs, err := sess.RunF32([]int64{1, 1, 64, int64(tw)}, in)
	if err != nil {
		return "", err
	}
	o := outs[0]
	if len(o.Shape) != 3 {
		return "", fmt.Errorf("ddddocr：输出形状 %v", o.Shape)
	}
	steps, classes := int(o.Shape[0]), int(o.Shape[2])
	if o.Shape[1] != 1 { // (1, seq, classes)
		steps = int(o.Shape[1])
	}
	var out []rune
	prev := -1
	for s := range steps {
		row := o.Data[s*classes : (s+1)*classes]
		best := 0
		for c := 1; c < classes; c++ {
			if row[c] > row[best] {
				best = c
			}
		}
		if best != prev && best != 0 && best < len(charset) {
			out = append(out, []rune(charset[best])...)
		}
		prev = best
	}
	return string(out), nil
}
