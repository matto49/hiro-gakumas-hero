package vision

import (
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 数字读取（同 Python hiro.vision.digits）：游戏字体字形比对为主，ddddocr 兜底。

// ddddFix ddddocr 常把 0 / 1 读成字母。
var ddddFix = strings.NewReplacer("o", "0", "O", "0", "l", "1", "I", "1")

var digitRun = pyRe(`\d+`)

// ReadDddd 通用 OCR（ddddocr）读 box 里的数字：放大 2 倍（线性）交给 ddddocr，取第一串数字；读不到 ok=false。
func ReadDddd(b *img.BGR, box ocr.Box) (int, bool, error) {
	c := cv.ResizeScale(crop(b, box), 2, cv.Linear)
	s, err := ocr.Dddd(c)
	if err != nil {
		return 0, false, err
	}
	m := digitRun.FindString(ddddFix.Replace(s))
	if m == "" {
		return 0, false, nil
	}
	return mustAtoi(m), true, nil
}

// DdddDigits ddddocr 读一块里的数字（灰度、放大 2 倍、Otsu 二值化），返回读到的各个整数（同 KAA _digits_from_image）。
func DdddDigits(c *img.BGR) ([]int, error) {
	g := cv.Gray(c)
	g = cv.ResizeGrayScale(g, 2, cv.Linear)
	_, bw := cv.Otsu(g)
	s, err := ocr.DdddGray(bw)
	if err != nil {
		return nil, err
	}
	out := []int{}
	for _, m := range digitRun.FindAllString(s, -1) {
		out = append(out, mustAtoi(m))
	}
	return out, nil
}
