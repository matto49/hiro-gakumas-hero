package vision

import (
	"math"
	"sort"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/vision/ui"
)

// 三选一选卡弹窗的候选（同 Python hiro.vision.card_select）：位置、卡、「おすすめ」。

const (
	letterThreshold = 0.65
	sameCardDX      = 30
)

// CardOption 是一张候选：类别字母框（点这里选卡）和认出的卡（认不出 nil）。
type CardOption struct {
	Rect ocr.Box
	Card *master.Card
}

// CardLetters 卡区里的类别字母框，按左上角排序、去掉同一张卡的重复命中。
func CardLetters(b *img.BGR) []ocr.Box {
	b = ui.Normalize(b)
	region := ui.Box("InProduce.SkillCardSelect.CardsBox").XYWH()
	var hits []ocr.Box
	for _, n := range []string{"InProduce.A", "InProduce.M"} {
		for _, m := range ui.Tpl(n).FindAll(b, ui.Opt{Threshold: ui.Th(letterThreshold), Region: &region}) {
			hits = append(hits, m.Rect)
		}
	}
	sort.SliceStable(hits, func(i, j int) bool {
		if hits[i].X1 != hits[j].X1 {
			return hits[i].X1 < hits[j].X1
		}
		return hits[i].Y1 < hits[j].Y1
	})
	out := []ocr.Box{}
	for _, r := range hits {
		ok := true
		for _, o := range out {
			if d := r.Center().X - o.Center().X; d > -sameCardDX && d < sameCardDX {
				ok = false
				break
			}
		}
		if ok {
			out = append(out, r)
		}
	}
	return out
}

func dialogCard(c *img.BGR) *master.Card {
	if a := CardMatch(c, cardMinVotes); a != "" {
		return master.CardByAsset(a)
	}
	return nil
}

// cardCrop 字母框往上取卡面（字母中心 ±60、上 105）；出了画面返回 nil。
func cardCrop(b *img.BGR, r ocr.Box) *img.BGR {
	x1, y1, x2, y2 := r.Center().X-60, r.Y1-105, r.Center().X+60, r.Y2
	if x1 < 0 || y1 < 0 {
		return nil
	}
	return b.Slice(x1, y1, x2, y2)
}

// SelectCardOptions 每个字母框一张候选。
func SelectCardOptions(b *img.BGR) []CardOption {
	b = ui.Normalize(b)
	var out []CardOption
	for _, r := range CardLetters(b) {
		o := CardOption{Rect: r}
		if c := cardCrop(b, r); c != nil {
			o.Card = dialogCard(c)
		}
		out = append(out, o)
	}
	return out
}

// BadgeMatch 每个对象正下方最近的徽章，一个徽章只配一次（同 KAA badge.match 的 mb）。
func BadgeMatch(objects, badges []ocr.Box) []*ocr.Box {
	left := append([]ocr.Box(nil), badges...)
	out := make([]*ocr.Box, 0, len(objects))
	for _, o := range objects {
		cx, cy := o.X1+o.W()/2, o.Y1+o.H()/2
		best, bestD := -1, math.Inf(1)
		for i, b := range left {
			bx, by := b.X1+b.W()/2, b.Y1+b.H()/2
			if bx < o.X1 || bx > o.X2 || by <= cy {
				continue
			}
			if d := math.Sqrt(float64((bx-cx)*(bx-cx) + (by-cy)*(by-cy))); d < bestD {
				best, bestD = i, d
			}
		}
		if best < 0 {
			out = append(out, nil)
			continue
		}
		b := left[best]
		left = append(left[:best], left[best+1:]...)
		out = append(out, &b)
	}
	return out
}

// RecommendedFlags 每张候选下面有没有「おすすめ」。
func RecommendedFlags(b *img.BGR, options []CardOption) []bool {
	out := make([]bool, len(options))
	if len(options) == 0 {
		return out
	}
	var badges []ocr.Box
	for _, m := range ui.Tpl("InProduce.TextRecommend").FindAll(ui.Normalize(b), ui.Opt{}) {
		badges = append(badges, m.Rect)
	}
	if len(badges) == 0 {
		return out
	}
	objs := make([]ocr.Box, len(options))
	for i, o := range options {
		objs[i] = o.Rect
	}
	for i, m := range BadgeMatch(objs, badges) {
		out[i] = m != nil
	}
	return out
}

// RecommendedIndex 第一张带「おすすめ」的候选；没有返回 -1。
func RecommendedIndex(b *img.BGR, options []CardOption) int {
	for i, f := range RecommendedFlags(b, options) {
		if f {
			return i
		}
	}
	return -1
}
