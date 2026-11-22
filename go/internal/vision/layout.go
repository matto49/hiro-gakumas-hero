package vision

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// 界面元素坐标（同 Python hiro.vision.layout）：素材包里的静态布局 + 截图校准（data/layout_calib.json），720x1280 逻辑坐标。

const examView = "ExamTypeAuditionView"

type layoutNode struct {
	Active bool       `json:"active"`
	Rect   [4]float64 `json:"rect"`
}

type calib struct {
	Delta [4]float64 `json:"delta"`
}

var (
	layoutOnce  sync.Once
	layoutViews map[string]map[string]layoutNode
	layoutCalib map[string]map[string]calib
)

func loadLayout() {
	layoutOnce.Do(func() {
		var raw []byte
		if v, err := assets.Default().Layout(); err == nil && len(v) > 0 {
			raw, _ = json.Marshal(v)
		} else {
			raw, _ = data.ReadFile("data/layout_default.json")
		}
		layoutViews = decodeViews(raw)
		cr, _ := data.ReadFile("data/layout_calib.json")
		layoutCalib = map[string]map[string]calib{}
		var top map[string]json.RawMessage
		_ = json.Unmarshal(cr, &top)
		for k, v := range top {
			var m map[string]calib
			if json.Unmarshal(v, &m) == nil {
				layoutCalib[k] = m
			}
		}
	})
}

func decodeViews(raw []byte) map[string]map[string]layoutNode {
	var top map[string]json.RawMessage
	_ = json.Unmarshal(raw, &top)
	out := map[string]map[string]layoutNode{}
	for k, v := range top {
		var m map[string]layoutNode
		if json.Unmarshal(v, &m) == nil {
			out[k] = m
		}
	}
	return out
}

// layoutStatic 按路径后缀找唯一的激活节点的静态矩形 (x1, y1, x2, y2)；找不到或不唯一时 panic（同 Python 的 KeyError，是代码写错了）。
func layoutStatic(view, suffix string) [4]float64 {
	loadLayout()
	var hits []layoutNode
	for p, n := range layoutViews[view] {
		if n.Active && (p == suffix || strings.HasSuffix(p, "/"+suffix)) {
			hits = append(hits, n)
		}
	}
	if len(hits) != 1 {
		panic(fmt.Sprintf("%s: 「%s」匹配到 %d 个激活节点", view, suffix, len(hits)))
	}
	r := hits[0].Rect
	return [4]float64{r[0], r[1], r[0] + r[2], r[1] + r[3]}
}

// LayoutBox 静态矩形加上校准偏移，取整为 (x1, y1, x2, y2)。
func LayoutBox(view, suffix string) ocr.Box {
	s := layoutStatic(view, suffix)
	d := layoutCalib[view][suffix].Delta
	r := func(v float64) int { return int(math.RoundToEven(v)) }
	return ocr.Box{X1: r(s[0] + d[0]), Y1: r(s[1] + d[1]), X2: r(s[2] + d[2]), Y2: r(s[3] + d[3])}
}
