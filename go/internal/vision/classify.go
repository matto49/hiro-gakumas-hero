package vision

import (
	"sort"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/vision/ui"
)

// 判页（同 Python hiro.vision.classify）：Classify(img) → PageHit，纯函数，同一帧永远同样结果，不点、不读设备，可离线回放。

// PageHit 是判页结果：Name 是页面名（状态机用的名字），Area 是 produce / outside / unknown，Source 是判出它的那一层。
type PageHit struct {
	Name   string         `json:"name"`
	Source string         `json:"source"`
	Area   string         `json:"area"`
	Fields map[string]any `json:"fields"`
}

// Unknown 是认不出。
var Unknown = PageHit{Name: "unknown", Source: "none", Area: "unknown"}

// ---------------------------------------------------------------- KAA 那几样的自有实现

// Loading 加载画面：上方 35% 二值化后只剩不超过 2 种颜色（同 KAA tasks.actions.loading）。
func Loading(b *img.BGR) bool {
	th := cv.ThresholdBGR(b, 127, 255)
	top := th.Rows(0, int(float64(b.H)*0.35))
	seen := map[[3]byte]bool{}
	for i := 0; i+2 < len(top.Pix); i += 3 {
		seen[[3]byte{top.Pix[i], top.Pix[i+1], top.Pix[i+2]}] = true
		if len(seen) > 2 {
			return false
		}
	}
	return true
}

// AtHome 首页（首页按钮高亮）。
func AtHome(b *img.BGR) bool { return ui.Tpl("Daily.ButtonHomeCurrent").Exists(b, ui.Opt{}) }

// white 同 KAA game_ui.WhiteFilter：HSV (0,0,200)–(180,30,255)。
func white(b *img.BGR) *img.Gray {
	return cv.InRange(cv.HSV(b), [3]float64{0, 0, 200}, [3]float64{180, 30, 255})
}

var commuSkipWhite *img.Gray

// AtCommu 剧情 / 交流页：SKIP 按钮（同 KAA is_at_commu：阈值 0.6 原图找，找不到再两边都只留白色部分找）。
func AtCommu(b *img.BGR) bool {
	t := ui.Tpl("Common.ButtonCommuSkip")
	if t.Exists(b, ui.Opt{Threshold: ui.Th(0.6)}) {
		return true
	}
	if commuSkipWhite == nil {
		raw, err := cv.Read(t.Path())
		if err != nil {
			panic(err)
		}
		commuSkipWhite = white(raw)
	}
	res := cv.MatchTemplate(white(b), commuSkipWhite, cv.CcoeffNormed)
	if res == nil {
		panic(&cv.Error{Err: errSmall})
	}
	v, _, _ := res.Max()
	return float64(v) >= 0.6
}

// HasCardLetters 选卡弹窗里有没有卡（卡类型角标 A / M，阈值 0.65，只在卡区找）。
func HasCardLetters(b *img.BGR) bool {
	r := ui.Box("InProduce.SkillCardSelect.CardsBox").XYWH()
	for _, n := range []string{"InProduce.A", "InProduce.M"} {
		if ui.Tpl(n).Exists(b, ui.Opt{Threshold: ui.Th(0.65), Region: &r}) {
			return true
		}
	}
	return false
}

// OptionButtons 授業 / おでかけ / 开局二选一的选项按钮数。
func OptionButtons(b *img.BGR) int { return len(OptionRegions(b)) }

// OptionRegions 授業 / おでかけ / 开局二选一的选项按钮框（整帧坐标，从上到下；同 KAA CommuEventButtonUI.all 的按钮判定，
// 有选中项时最上面那块是描述、不算按钮）。
func OptionRegions(b *img.BGR) []ocr.Box {
	area := ui.Box("InProduce.BoxCommuEventButtonsArea")
	x1, y1 := area.X1, area.Y1
	crop := b.Slice(area.X1, area.Y1, area.X2, area.Y2)
	edges := cv.Canny(cv.ThresholdBGR(crop, 190, 255), 200, 250)
	k := cv.RectKernel{W: 7, H: 7}
	edges = cv.Dilate(cv.Morphology(edges, cv.MorphClose, k), k, 1)
	type region struct {
		y                  int
		button, unselected bool
		box                ocr.Box
	}
	var regions []region
	for _, r := range cv.ExternalBoxes(edges) {
		x, y, w, h := r.X, r.Y, r.W, r.H
		if w <= 350 || w >= 660 || h <= 60 {
			continue
		}
		isButton := 60 < h && h < 100
		wf := grayFrac(cv.Threshold(cv.Gray(crop.Slice(x, y, x+w, y+h)), 190, 255), func(v byte) bool { return v == 255 })
		regions = append(regions, region{y, isButton, isButton && wf < 0.5, ocr.Box{X1: x1 + x, Y1: y1 + y, X2: x1 + x + w, Y2: y1 + y + h}})
	}
	sort.SliceStable(regions, func(i, j int) bool { return regions[i].y < regions[j].y })
	anySel := false
	for _, r := range regions {
		anySel = anySel || r.unselected
	}
	if len(regions) > 0 && anySel {
		regions[0].button = false // 有选中项时，最上面那块是描述（同 KAA）
	}
	out := []ocr.Box{}
	for _, r := range regions {
		if r.button {
			out = append(out, r.box)
		}
	}
	return out
}

// outingOptions 外出页的选项数：亮按钮 + 灰色选不了的（P 不够）。
func outingOptions(b *img.BGR) int { return OptionButtons(b) + len(GreyRows(b)) }

// ---------------------------------------------------------------- KAA check_scene 的同一套模板判据（只判不点）

func anyTpl(b *img.BGR, names ...string) bool {
	for _, n := range names {
		if ui.Tpl(n).Exists(b, ui.Opt{}) {
			return true
		}
	}
	return false
}

// selectTitleBox 三选一弹窗标题「スキルカード / Pアイテム / Pドリンク」的位置。
var selectTitleBox = ocr.Box{X1: 230, Y1: 590, X2: 370, Y2: 670}

// title 三选一弹窗标题模板只认原位：支援能力横幅「Pアイテム獲得時、…」（y≈460）也会命中同一行字。
func title(b *img.BGR, name string) bool {
	m := ui.Tpl(name).Find(b, ui.Opt{})
	if m == nil {
		return false
	}
	c := m.Center()
	r := selectTitleBox
	return r.X1 <= c.X && c.X <= r.X2 && r.Y1 <= c.Y && c.Y <= r.Y2
}

// TemplateScene KAA ProducePage.check_scene 的顺序和模板；返回 SceneType 名，认不出返回空串。
func TemplateScene(b *img.BGR) string {
	switch {
	case Loading(b):
		return "LOADING"
	case anyTpl(b, "InProduce.TextOutingStaminaMax"):
		return "OUTING_STAMINA_MAX"
	case anyTpl(b, "InProduce.TextPDrinkMax"):
		return "PDRINK_MAX"
	case anyTpl(b, "InProduce.TextPDrinkMaxConfirmTitle"):
		return "PDRINK_MAX_CONFIRM"
	case anyTpl(b, "InProduce.TextSkillCardSelectGuideDialogTitle"):
		return "IDLE" // 引导框：KAA 判页时点 ✓，这里只判
	case anyTpl(b, "InProduce.ProduceScore.TitleText", "InProduce.End.ButtonGenerate"):
		return "PRODUCE_END"
	case title(b, "InProduce.TextSkillCard"):
		return "SELECT_CARD"
	case title(b, "InProduce.TextPItem"):
		return "SELECT_PITEM"
	case title(b, "InProduce.TextPDrink"):
		return "SELECT_DRINK"
	case anyTpl(b, "InProduce.IconTitleSkillCardEnhance"):
		return "SKILL_CARD_ENHANCE"
	case anyTpl(b, "InProduce.IconTitleSkillCardRemoval"):
		return "SKILL_CARD_REMOVAL"
	case anyTpl(b, "InProduce.DialogCardChange1.TipText"):
		return "SKILL_CARD_CHANGE_1"
	case anyTpl(b, "InProduce.DialogCardChange2.Title"):
		return "SKILL_CARD_CHANGE_2"
	}
	if anyTpl(b, "InProduce.TextReviewCriteria") {
		switch {
		case anyTpl(b, "InProduce.TextPDiary", "InProduce.ButtonFinalPracticeVisual"):
			return "ACTION_SELECT"
		case anyTpl(b, "InProduce.IconTitleStudy"):
			if OptionButtons(b) > 1 {
				return "STUDY"
			}
			return "IDLE"
		case anyTpl(b, "InProduce.TitleIconOuting"):
			if outingOptions(b) > 1 {
				return "OUTING"
			}
			return "IDLE"
		case anyTpl(b, "InProduce.IconTitleConsult"):
			return "CONSULT"
		case anyTpl(b, "InProduce.IconTitleAllowance"):
			return "ALLOWANCE"
		case anyTpl(b, "InProduce.HifInterval.Title"):
			return "HIF_ROUND_INTERVAL"
		case OptionButtons(b) > 1:
			return "INITIAL_CHOICE" // 开局饮料 / 卡二选一
		}
	} else if anyTpl(b, "InProduce.IconTitleConsult") {
		return "CONSULT" // N.I.A 右上不是「審査基準」：相談标题图标只在培育的相談页出现，单独认
	} else if anyTpl(b, "InProduce.TitleIconOuting") {
		if outingOptions(b) > 1 {
			return "OUTING"
		}
		return "IDLE"
	}
	if anyTpl(b, "InProduce.TextClearUntil", "InProduce.TextPerfectUntil") {
		return "PRACTICE"
	}
	if anyTpl(b, "InProduce.TextExamRankSmallFirst", "InProduce.TextExamRankLargeFirst") {
		return "EXAM"
	}
	return ""
}
