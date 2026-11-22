package vision

import (
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 牌组：培育中「所持スキルカード」一览页（DECK_LIST）与相談削除 / 強化、换卡等同款卡格网格（同 Python hiro.vision.deck）。

var (
	deckButtonStrip = ocr.Box{X1: 380, Y1: 1130, X2: 720, Y2: 1270} // 底栏右侧礼盒图标
	deckTitleBox    = ocr.Box{X1: 40, Y1: 25, X2: 300, Y2: 90}      // 「所持スキルカード」白色标题条
	deckTabBox      = ocr.Box{X1: 60, Y1: 1015, X2: 340, Y2: 1075}  // 「スキルカード(N)」标签
)

const (
	deckButtonMin    = 0.8
	deckTitleMin     = 0.75
	deckTabActivePx  = 200 // 选中的标签文字是橙色（实测 738 个高饱和像素，未选中 0）
	acquireCenterX   = 370
	acquireCenterTol = 25
	acquireRetry     = 1.5
	hudWhiteMin      = 0.2
)

func crop(b *img.BGR, r ocr.Box) *img.BGR { return b.Slice(r.X1, r.Y1, r.X2, r.Y2) }

// FindDeckButton 培育画面底栏打开「所持スキルカード」一览的礼盒图标中心；没有时 nil。
func FindDeckButton(b *img.BGR) *ocr.Point {
	tpl := memo("white60:owned_cards_button", func() *img.Gray { return whiteMask(localTpl("owned_cards_button"), deckWhiteSat) })
	res := cv.MatchTemplate(whiteMask(crop(b, deckButtonStrip), deckWhiteSat), tpl, cv.CcoeffNormed)
	if res == nil {
		panic(&cv.Error{Err: errSmall})
	}
	score, x, y := res.Max()
	if float64(score) < deckButtonMin {
		return nil
	}
	return &ocr.Point{X: deckButtonStrip.X1 + x + tpl.W/2, Y: deckButtonStrip.Y1 + y + tpl.H/2}
}

// IsDeckList 「所持スキルカード」一览页，且当前是「スキルカード」标签（不是 P アイテム）。
func IsDeckList(b *img.BGR) bool {
	if matchBGR(crop(b, deckTitleBox), localTpl("deck_list_title")) < deckTitleMin {
		return false
	}
	return tabActive(b)
}

func tabActive(b *img.BGR) bool {
	hsv := cv.HSV(crop(b, deckTabBox))
	n := countWhere(hsv, func(p []byte) bool { return p[0] >= 5 && p[0] <= 22 && p[1] >= 120 && p[2] >= 180 })
	return n >= deckTabActivePx
}

var (
	deckSizeRe  = pyRe(`\((\d+)\)`)
	deckSizeRe2 = pyRe(`カード\D*(\d+)`)
)

// ReadDeckSize 底部标签「スキルカード(16)」里的张数；读不到 ok=false。
func ReadDeckSize(b *img.BGR) (int, bool, error) {
	items, err := ocr.OCR(crop(b, deckTabBox), 3, ocr.JaEn)
	if err != nil {
		return 0, false, err
	}
	text := strings.NewReplacer("（", "(", "）", ")").Replace(ocr.Joined(items, ""))
	m := deckSizeRe.FindStringSubmatch(text)
	if m == nil {
		m = deckSizeRe2.FindStringSubmatch(text)
	}
	if m == nil {
		return 0, false, nil
	}
	return mustAtoi(m[1]), true, nil
}

// ---------------------------------------------------------------- 获得弹窗：卡 / P 饮料 / P 道具的获得演出

var (
	acquireBar  = ocr.Box{X1: 40, Y1: 840, X2: 680, Y2: 910}  // 画面中下部的名称栏
	memoryLabel = ocr.Box{X1: 120, Y1: 400, X2: 620, Y2: 500} // 卡上方的橙色标签「メモリー効果」
	hudPanel    = ocr.Box{X1: 520, Y1: 25, X2: 705, Y2: 160}  // 培育 HUD 右上「審査基準」白框
	acquireArt  = ocr.Box{X1: 262, Y1: 556, X2: 455, Y2: 750} // 获得卡时中间缩略图的卡面部分
)

// MemoryEffectPopup 回忆效果的卡展示：卡上方有「メモリー効果」标签，或者画面上没有培育 HUD。
func MemoryEffectPopup(b *img.BGR) (bool, error) {
	hsv := cv.HSV(crop(b, hudPanel))
	if fracWhere(hsv, func(p []byte) bool { return p[1] < 25 && p[2] > 235 }) < hudWhiteMin {
		return true, nil
	}
	items, err := ocr.Boxes(crop(b, memoryLabel), 3, ocr.JaEn)
	if err != nil {
		return false, err
	}
	for _, it := range items {
		if strings.Contains(it.Text, "メモリー") && strings.Contains(it.Text, "効果") {
			return true, nil
		}
	}
	return false, nil
}

type barText struct {
	text string
	cx   float64
}

func acquireBarBoxes(c *img.BGR, x1 int, scale float64) ([]barText, error) {
	items, err := ocr.Boxes(c, scale, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	var out []barText
	for _, it := range items {
		if len([]rune(pyStrip(it.Text))) >= 2 && !strayFragment(it.Text) {
			out = append(out, barText{it.Text, float64(it.Box.X1+it.Box.X2)/2 + float64(x1)})
		}
	}
	return out, nil
}

// ReadAcquirePopup 获得演出的名称栏：{kind: card|drink|pitem, card_id/drink_id/pitem_id, name, upgrade?, memory_effect?, art_card_id?, agree?, text}。
// 名称栏文字居中、且精确命中主数据才算（也可当页面判定用：命中不了返回 nil）。art=false 只判页，不用卡面核对。
func ReadAcquirePopup(b *img.BGR, art bool) (map[string]any, error) {
	c := crop(b, acquireBar)
	boxes, err := acquireBarBoxes(c, acquireBar.X1, 3)
	if err != nil {
		return nil, err
	}
	if len(boxes) == 0 {
		if boxes, err = acquireBarBoxes(c, acquireBar.X1, acquireRetry); err != nil {
			return nil, err
		}
	}
	if len(boxes) == 0 {
		return nil, nil
	}
	parts := make([]string, len(boxes))
	for i, bt := range boxes {
		if bt.cx-acquireCenterX > acquireCenterTol || acquireCenterX-bt.cx > acquireCenterTol {
			return nil, nil
		}
		parts[i] = bt.text
	}
	text := strings.Join(parts, "")
	item := itemFromName(text)
	if item == nil {
		return nil, nil
	}
	out := item.Fields()
	out["text"] = text
	if item.Kind == "card" {
		mem, err := MemoryEffectPopup(b)
		if err != nil {
			return nil, err
		}
		out["memory_effect"] = mem
		if art { // 用中间缩略图认卡核对；agree=false 时 card_id 仍以名称栏为准
			out["art_card_id"], out["agree"] = nil, nil
			if a := CardMatch(crop(b, acquireArt), cardMinVotes); a != "" {
				if c := master.CardByAsset(a); c != nil {
					out["art_card_id"], out["agree"] = c.ID, c.ID == item.Card.CardID
				}
			}
		}
	}
	return out, nil
}
