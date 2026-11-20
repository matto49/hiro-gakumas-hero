package ocr

import (
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/golden"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

var ddddBoxes = [][4]int{{0, 0, 160, 45}, {222, 128, 262, 166}, {100, 900, 400, 980}, {500, 10, 690, 80}, {300, 600, 420, 660}, {20, 1180, 700, 1260}}

// ddddocr 在黄金截图的几块区域上和 Python 版读得一样（彩色、二值图两种喂法）。
func TestDdddMatchesPython(t *testing.T) {
	if _, _, err := dddd(); err != nil {
		t.Skip(err)
	}
	golden.Compare(t, golden.Spec{Call: "probes:dddd_crops", Sample: 300, Allow: 3}, func(b *img.BGR) (any, error) {
		out := []any{}
		for _, r := range ddddBoxes {
			c := b.Slice(r[0], r[1], r[2], r[3])
			if c.Empty() {
				out = append(out, nil)
				continue
			}
			color, err := Dddd(c)
			if err != nil {
				return nil, err
			}
			_, bw := cv.Otsu(cv.Gray(cv.ResizeScale(c, 2, cv.Linear)))
			gray, err := DdddGray(bw)
			if err != nil {
				return nil, err
			}
			out = append(out, []string{color, gray})
		}
		return out, nil
	})
}
