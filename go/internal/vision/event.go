package vision

import (
	"errors"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// 授業（STUDY）/ おでかけ（OUTING）三选一（同 Python hiro.vision.event）。

var (
	errSmall = errors.New("画面比模板小")
	errEmpty = errors.New("空图")
)

// eventRowY 三行选项的中心 y（720x1280）。
var eventRowY = [3]int{698, 799, 901}

// 选不了的选项（P 不够）：整条灰底，亮度约 158；普通未选中约 220、白底 255、选中是彩色。
const (
	greyVLo, greyVHi = 125, 190
	greyUniform      = 0.9
)

// greyRow 第 y 行的按钮是灰的：按钮右半段几乎全是低饱和、亮度在 greyV 里。
func greyRow(hsv *img.BGR, y int) bool {
	r := hsv.Slice(330, y-12, 560, y+12)
	if size(r) == 0 {
		return false
	}
	v := median(chanOf(r, 2))
	lowS := fracWhere(r, func(p []byte) bool { return p[1] < 40 })
	return greyVLo <= v && v <= greyVHi && lowS >= greyUniform && median(chanOf(r, 1)) < 20
}

// GreyRows 灰色（选不了）的选项行号（eventRowY 的下标）。
func GreyRows(b *img.BGR) []int {
	hsv := cv.HSV(b)
	out := []int{}
	for i, y := range eventRowY {
		if greyRow(hsv, y) {
			out = append(out, i)
		}
	}
	return out
}
