package vision

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
)

// P 饮料图标识别（同 Python hiro.vision.drinks）：多尺度模板匹配，对比 KAA 饮料图库（68x68）。

var drinkScales = []float64{1.05, 1.15, 1.25, 1.35}

type drinkTpl struct {
	asset  string
	scaled []*img.BGR
}

var (
	drinkOnce sync.Once
	drinkTpls []drinkTpl
)

func drinkTemplates() []drinkTpl {
	drinkOnce.Do(func() {
		folder := assets.Default().GameData("drinks")
		if folder == "" {
			return
		}
		es, err := os.ReadDir(folder)
		if err != nil {
			return
		}
		names := make([]string, 0, len(es))
		for _, e := range es {
			names = append(names, e.Name())
		}
		sort.Strings(names)
		for _, n := range names {
			b, err := cv.Read(filepath.Join(folder, n))
			if err != nil {
				continue
			}
			core := b.Slice(6, 6, -6, -6) // 去掉外圈 6px 边框
			t := drinkTpl{asset: n}
			if i := strings.LastIndex(n, "."); i >= 0 {
				t.asset = n[:i]
			}
			for _, s := range drinkScales {
				t.scaled = append(t.scaled, cv.ResizeScale(core, s, cv.Linear))
			}
			drinkTpls = append(drinkTpls, t)
		}
	})
	return drinkTpls
}

// MatchDrink 返回 (asset_id, 最佳分数, 与次佳的分差)。slot 为单个格子截图（约 100x100）。没有图库时 asset 为空。
func MatchDrink(slot *img.BGR) (string, float64, float64) {
	type sc struct {
		v     float64
		asset string
	}
	var scores []sc
	for _, t := range drinkTemplates() {
		best := 0.0
		for _, tpl := range t.scaled {
			if tpl.H > slot.H || tpl.W > slot.W {
				continue
			}
			best = max(best, matchBGR(slot, tpl))
		}
		scores = append(scores, sc{best, t.asset})
	}
	sort.SliceStable(scores, func(i, j int) bool { // Python sort(reverse=True)：按 (分数, 名称) 从大到小
		if scores[i].v != scores[j].v {
			return scores[i].v > scores[j].v
		}
		return scores[i].asset > scores[j].asset
	})
	if len(scores) == 0 {
		return "", 0, 0
	}
	s2 := 0.0
	if len(scores) > 1 {
		s2 = scores[1].v
	}
	return scores[0].asset, scores[0].v, scores[0].v - s2
}

func drinkInfo(asset string, out map[string]any) {
	if asset == "" {
		return
	}
	out["asset_id"] = asset
	if d := master.DrinkByAsset(asset); d != nil {
		out["name"], out["effect"] = d.Name, nil
	}
}

var dialogSlotsX = []int{222, 360, 498}

const dialogSlotY = 880

// ReadDrinkDialog 选饮料弹窗（三选一）的三个候选。
func ReadDrinkDialog(b *img.BGR) []map[string]any {
	out := make([]map[string]any, 0, 3)
	for i, x := range dialogSlotsX {
		slot := b.Slice(x-50, dialogSlotY-50, x+50, dialogSlotY+50)
		asset, score, margin := MatchDrink(slot)
		d := map[string]any{"slot": i, "score": pyRound(score, 3), "margin": pyRound(margin, 3)}
		drinkInfo(asset, d)
		out = append(out, d)
	}
	return out
}
