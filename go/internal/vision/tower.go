package vision

import (
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// 偶像之路（同 Python hiro.vision.tower）。

const (
	towerRight, towerNpcW, towerMeW, towerGap, towerDivider = 710, 69, 127, 2, 13
	towerMeCyanMin                                          = 0.15
	towerRows                                               = 6
	towerTurnsMin                                           = 0.45
)

type towerCol struct{ rank, left, right int }

// towerLayout 自己排第 me 名时 6 列的 [(名次, 左, 右)]，从 1st 到 6th。
func towerLayout(me int) []towerCol {
	cols := make([]towerCol, towerRows)
	right := towerRight
	for rank := towerRows; rank >= 1; rank-- {
		w := towerNpcW
		if rank == me {
			w = towerMeW
		}
		cols[rank-1] = towerCol{rank, right - w, right}
		right -= w + towerGap
		if rank == 4 {
			right -= towerDivider
		}
	}
	return cols
}

func towerCyan(hsv *img.BGR, left, right int) float64 {
	return fracWhere(hsv.Slice(left+4, 50, right-40, 120), func(p []byte) bool { return p[0] >= 85 && p[0] <= 105 && p[1] >= 120 && p[2] >= 150 })
}

// TowerMyRank 自己的名次（1–6）和那一列的青色底比例；哪一列都不够青返回 (0, 最大比例)。
func TowerMyRank(b *img.BGR) (int, float64) {
	hsv := cv.HSV(b.Rows(0, 170))
	best, br := -1.0, 0
	for r := 1; r <= towerRows; r++ {
		c := towerLayout(r)[r-1]
		if v := towerCyan(hsv, c.left, c.right); v >= best { // Python max((比例, 名次))：并列取名次大的
			best, br = v, r
		}
	}
	if best >= towerMeCyanMin {
		return br, best
	}
	return 0, best
}

// IsTowerExam 偶像之路考试页：左上「残りターン」+ 没有突破线、不是本战 3 人名次条 + 6 人名次条里认得出自己那一列。
func IsTowerExam(b *img.BGR) bool {
	if s, _ := matchAt(b, "hif_exam_remaining_turns", hifTurnsLabel); s < towerTurnsMin {
		return false
	}
	if IsFinalsStrip(b) {
		return false
	}
	if s, _ := matchWhite(b, "hif_exam_breakthrough", hifTopStrip); s >= breakthroughMin {
		return false
	}
	r, _ := TowerMyRank(b)
	return r != 0
}
