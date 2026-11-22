package vision

import (
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// H.I.F 考试画面（同 Python hiro.vision.hif）：场景判定与 HUD / 手牌读取。

var (
	hifTopStrip   = ocr.Box{X1: 0, Y1: 0, X2: 720, Y2: 175}
	finalsBadge   = ocr.Box{X1: 220, Y1: 2, X2: 480, Y2: 45} // 本战排名条顶上的 1st / 2nd / 3rd 名次徽章（橙金色）
	hifTurnsLabel = ocr.Box{X1: 0, Y1: 0, X2: 160, Y2: 45}
)

const (
	breakthroughMin    = 0.6
	breakthroughStrong = 0.75
	finalsTurnsMin     = 0.45
	finalsBadgeMin     = 400
	deckWhiteSat       = 60 // hif / deck 的白色掩码饱和度上限
)

// matchAt 彩色模板在 box 里的最高分和位置（整帧坐标）。
func matchAt(b *img.BGR, name string, box ocr.Box) (float64, ocr.Point) {
	res := cv.MatchTemplateBGR(crop(b, box), localTpl(name), cv.CcoeffNormed)
	if res == nil {
		panic(&cv.Error{Err: errSmall})
	}
	v, x, y := res.Max()
	return float64(v), ocr.Point{X: x + box.X1, Y: y + box.Y1}
}

// matchWhite 只比对白色字形（同 Python hif._match_white）。
func matchWhite(b *img.BGR, name string, box ocr.Box) (float64, ocr.Point) {
	tpl := memo("white60:"+name, func() *img.Gray { return whiteMask(localTpl(name), deckWhiteSat) })
	res := cv.MatchTemplate(whiteMask(crop(b, box), deckWhiteSat), tpl, cv.CcoeffNormed)
	if res == nil {
		panic(&cv.Error{Err: errSmall})
	}
	v, x, y := res.Max()
	return float64(v), ocr.Point{X: x + box.X1, Y: y + box.Y1}
}

// IsHifExam H.I.F 考试画面（左上「残りターン」+ 顶部突破线或本战名次条）。
func IsHifExam(b *img.BGR) bool {
	score, _ := matchAt(b, "hif_exam_remaining_turns", hifTurnsLabel)
	if score < 0.6 {
		if score < finalsTurnsMin {
			return false
		}
		if IsFinalsStrip(b) {
			return true
		}
		s, _ := matchWhite(b, "hif_exam_breakthrough", hifTopStrip)
		return s >= breakthroughStrong
	}
	s, _ := matchWhite(b, "hif_exam_breakthrough", hifTopStrip)
	return s >= breakthroughMin || IsFinalsStrip(b)
}

// IsFinalsStrip 本战考试顶部是 3 人名次条（1st / 2nd / 3rd 徽章）。
func IsFinalsStrip(b *img.BGR) bool {
	hsv := cv.HSV(crop(b, finalsBadge))
	return countWhere(hsv, func(p []byte) bool { return p[0] >= 5 && p[0] <= 25 && p[1] >= 150 && p[2] >= 200 }) >= finalsBadgeMin
}

// IsHifResult H.I.F 考试结算页（「H.I.F選抜試験 第N回結果」+ 突破/敗退名单）。
func IsHifResult(b *img.BGR) bool {
	if s, _ := matchWhite(b, "hif_result_title", ocr.Box{X1: 0, Y1: 90, X2: 360, Y2: 230}); s < 0.6 {
		return false
	}
	s, _ := matchWhite(b, "hif_result_kekka", ocr.Box{X1: 0, Y1: 130, X2: 360, Y2: 230})
	return s >= 0.6
}

// ---------------------------------------------------------------- P 道具选择页（同 Python hiro.vision.pitem_select）

var pitemTitleBox = ocr.Box{X1: 60, Y1: 560, X2: 660, Y2: 700}

// IsSelectPItem H.I.F 考试后的「P 道具选择」页（标题白色字形）。
func IsSelectPItem(b *img.BGR) bool {
	s, _ := matchWhite(b, "select_pitem_title", pitemTitleBox)
	return s >= 0.6
}

// ---------------------------------------------------------------- 开局确认页（同 Python hiro.vision.support.is_start_confirm）

var (
	startStepBox    = ocr.Box{X1: 120, Y1: 25, X2: 280, Y2: 75}
	startSupportBox = ocr.Box{X1: 40, Y1: 535, X2: 170, Y2: 590}
)

// IsStartConfirm 培育开始确认页：顶部「4.開始確認」与「サポート」标签的白色字形。
func IsStartConfirm(b *img.BGR) bool {
	if s, _ := matchWhite(b, "start_confirm_step", startStepBox); s < 0.6 {
		return false
	}
	s, _ := matchWhite(b, "start_confirm_support", startSupportBox)
	return s >= 0.6
}

// ---------------------------------------------------------------- 饮料满红标（同 Python hiro.vision.items.drink_full_badge）

var fullBadgeBox = ocr.Box{X1: 240, Y1: 1105, X2: 480, Y2: 1160}

// DrinkFullBadge 选饮料页「受け取る」下方的红标「Pドリンク所持上限」在不在（饮料满）。
func DrinkFullBadge(b *img.BGR) (bool, error) {
	c := crop(b, fullBadgeBox)
	red := countWhere(cv.HSV(c), func(p []byte) bool { return (p[0] < 5 || p[0] >= 170) && p[1] >= 120 && p[2] >= 170 })
	if red < 1500 {
		return false, nil
	}
	items, err := ocr.Boxes(c, 3, ocr.JaEn)
	if err != nil {
		return false, err
	}
	for _, it := range items {
		if strings.Contains(it.Text, "上限") {
			return true, nil
		}
	}
	return false, nil
}

// boxGenki 元気框：布局框左放宽 20、右放宽 10（四位数元気首位 / 末位在布局框外）。
func boxGenki() ocr.Box {
	g := LayoutBox(examView, "GaugeRoot/GenkiGauge/IconRoot/Icon/GenkiValue")
	return ocr.Box{X1: g.X1 - 20, Y1: g.Y1, X2: min(720, g.X2+10), Y2: g.Y2}
}
