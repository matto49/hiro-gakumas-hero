package vision

import (
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// 识别层自己的界面文字小模板（data/templates，同 Python 的 hiro/vision/templates/）：截图裁的标签 / 按钮字样，编进程序。

var (
	tplMu    sync.Mutex
	tplCache = map[string]any{}
)

func memo[T any](key string, build func() T) T {
	tplMu.Lock()
	if v, ok := tplCache[key]; ok {
		tplMu.Unlock()
		out, _ := v.(T)
		return out
	}
	tplMu.Unlock()
	v := build()
	tplMu.Lock()
	tplCache[key] = v
	tplMu.Unlock()
	return v
}

// localTpl 彩色读模板（cv2.imread(TEMPLATES / f"{name}.png")）。
func localTpl(name string) *img.BGR {
	return memo("bgr:"+name, func() *img.BGR {
		raw, err := data.ReadFile("data/templates/" + name + ".png")
		if err != nil {
			panic("没有这个模板：" + name)
		}
		b, err := cv.Decode(raw)
		if err != nil {
			panic(err)
		}
		return b
	})
}

// localTplGray 模板转灰度（彩色读再转，同 Python）。
func localTplGray(name string) *img.Gray {
	return memo("gray:"+name, func() *img.Gray { return cv.Gray(localTpl(name)) })
}

// whiteMask HSV 白色掩码：(0, 0, 200)–(180, smax, 255)。各模块的饱和度上限不一样（classify 30、quick 50、deck 60）。
func whiteMask(b *img.BGR, smax float64) *img.Gray {
	return cv.InRange(cv.HSV(b), [3]float64{0, 0, 200}, [3]float64{180, smax, 255})
}

// maxScore matchTemplate 结果的最大值（Python 的 .max()）；画面比模板小时 cv2 报错，这里同样按 OpenCV 报错处理。
func maxScore(s *cv.Scores) float64 {
	if s == nil {
		panic(&cv.Error{Err: errSmall})
	}
	v, _, _ := s.Max()
	return float64(v)
}

func matchGray(screen, tpl *img.Gray) float64 {
	return maxScore(cv.MatchTemplate(screen, tpl, cv.CcoeffNormed))
}

func matchBGR(screen, tpl *img.BGR) float64 {
	return maxScore(cv.MatchTemplateBGR(screen, tpl, cv.CcoeffNormed))
}
