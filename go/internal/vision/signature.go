package vision

import (
	"embed"
	"encoding/json"
	"math"
	"path/filepath"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// 页面签名（同 Python hiro.vision.signature）：用客户端 Sprite 在固定小窗口里做模板匹配，快速判断当前页面。
// 签名（signatures.json）每类页面若干个（Sprite, 屏幕位置, 阈值）；只在每个位置附近 ±sigMargin 像素内匹配，
// 一类页面的签名至少 sigMinVote 比例通过才算命中；有多个页面命中时取平均超出阈值最多的。

//go:embed data
var data embed.FS

const (
	sigUIScale     = 720.0 / 1080
	sigMargin      = 10
	sigMinVote     = 0.66
	sigCoarseArea  = 6000 // 模板面积超过此值走「半分辨率定位 + 全分辨率精算」
	sigRefine      = 3
	sigFlatVar     = 1.0 // 窗口内灰度方差（每像素）低于此值视为纯色，不参与匹配
	signaturesFile = "data/signatures.json"
)

// prepared 是遮罩 NCC 的模板侧常量：去均值后的模板 tc、遮罩 m、像素数 n、tc 的平方和。
type prepared struct {
	tc, m *cv.F32
	n, tt float32
}

type signature struct {
	Sprite    string  `json:"sprite"`
	Box       [4]int  `json:"box"`
	Threshold float64 `json:"threshold"`
	gray      *img.Gray
	full      prepared
	half      *prepared
}

type sigScene struct {
	name string
	sigs []*signature
}

var (
	sigOnce   sync.Once
	sigScenes []sigScene
)

func loadSignatures() []sigScene {
	sigOnce.Do(func() {
		raw, err := data.ReadFile(signaturesFile)
		if err != nil {
			return
		}
		root := assets.Default().SpritesDir()
		if root == "" {
			return // 没有界面 Sprite：签名判页关闭
		}
		obj, err := jsonx.DecodeObject(raw)
		if err != nil {
			panic(err)
		}
		for _, scene := range obj.Keys() {
			v, _ := obj.Get(scene)
			b, _ := json.Marshal(v)
			var sigs []*signature
			if err := json.Unmarshal(b, &sigs); err != nil {
				panic(err)
			}
			var loaded []*signature
			for _, s := range sigs {
				im, err := cv.ReadBGRA(filepath.Join(root, s.Sprite))
				if err != nil {
					panic(err) // 同 Python：签名引用的 Sprite 缺了，cv2.imread 返回 None 后出错
				}
				w := max(6, int(math.RoundToEven(float64(im.W)*sigUIScale)))
				h := max(6, int(math.RoundToEven(float64(im.H)*sigUIScale)))
				small := cv.ResizeBGRA(im, w, h, cv.Area)
				bgr, alpha := small.Split()
				s.gray = cv.Gray(bgr)
				mask := img.NewGray(alpha.W, alpha.H)
				for i, a := range alpha.Pix {
					if a > 128 {
						mask.Pix[i] = 255
					}
				}
				s.full = prepare(s.gray, mask)
				if len(s.gray.Pix) >= sigCoarseArea {
					hw, hh := s.gray.W/2, s.gray.H/2
					hm := cv.ResizeGray(mask, hw, hh, cv.Area)
					for i, v := range hm.Pix {
						hm.Pix[i] = 0
						if v > 128 {
							hm.Pix[i] = 1
						}
					}
					p := prepare(cv.ResizeGray(s.gray, hw, hh, cv.Area), hm)
					s.half = &p
				}
				loaded = append(loaded, s)
			}
			if len(loaded) > 0 {
				sigScenes = append(sigScenes, sigScene{scene, loaded})
			}
		}
	})
	return sigScenes
}

func prepare(gray, mask *img.Gray) prepared {
	m := cv.NewF32(gray.W, gray.H)
	var n, mt float64
	for i, v := range mask.Pix {
		if v > 0 {
			m.Val[i] = 1
			n++
			mt += float64(gray.Pix[i])
		}
	}
	mean := float32(mt / n)
	tc := cv.NewF32(gray.W, gray.H)
	var tt float64
	for i, g := range gray.Pix {
		v := m.Val[i] * (float32(g) - mean)
		tc.Val[i] = v
		tt += float64(v * v)
	}
	return prepared{tc: tc, m: m, n: float32(n), tt: float32(tt)}
}

// maskedMax 与 cv2.matchTemplate(TM_CCOEFF_NORMED, mask=...) 数值一致，但只用三次不带遮罩的相关：
// 分子 = Σ tc·P，分母 = sqrt(Σtc² · (ΣmP² − (ΣmP)²/n))。窗口内近乎纯色的位置记 0。返回最高分和位置。
func maskedMax(crop *img.Gray, t prepared) (float32, int, int) {
	p := cv.F32Of(crop)
	pp := cv.NewF32(p.W, p.H)
	for i, v := range p.Val {
		pp.Val[i] = v * v
	}
	num := cv.MatchTemplateF32(p, t.tc, cv.Ccorr)
	if num == nil {
		panic(&cv.Error{Err: errSmall})
	}
	s1 := cv.MatchTemplateF32(p, t.m, cv.Ccorr)
	s2 := cv.MatchTemplateF32(pp, t.m, cv.Ccorr)
	best, bx, by := float32(math.Inf(-1)), 0, 0
	for i := range num.Val {
		v := s2.Val[i] - s1.Val[i]*s1.Val[i]/t.n
		r := float32(0)
		if v > t.n*sigFlatVar {
			r = num.Val[i] / float32(math.Sqrt(float64(max(v, float32(1e-6))*t.tt)))
		}
		if r > best {
			best, bx, by = r, i%num.W, i/num.W
		}
	}
	return best, bx, by
}

func (s *signature) score(gray *img.Gray) float32 {
	x, y, w, h := s.Box[0], s.Box[1], s.Box[2], s.Box[3]
	crop := gray.Slice(max(0, x-sigMargin), max(0, y-sigMargin), x+w+sigMargin, y+h+sigMargin)
	th, tw := s.gray.H, s.gray.W
	if crop.H < th || crop.W < tw {
		return 0
	}
	if s.half == nil {
		v, _, _ := maskedMax(crop, s.full)
		return v
	}
	small := cv.ResizeGray(crop, crop.W/2, crop.H/2, cv.Area)
	if small.H < s.half.tc.H || small.W < s.half.tc.W {
		v, _, _ := maskedMax(crop, s.full)
		return v
	}
	_, lx, ly := maskedMax(small, *s.half)
	x0, y0 := max(0, 2*lx-sigRefine), max(0, 2*ly-sigRefine)
	window := crop.Slice(x0, y0, min(crop.W, 2*lx+sigRefine+tw), min(crop.H, 2*ly+sigRefine+th))
	v, _, _ := maskedMax(window, s.full)
	return v
}

// SignatureMatch 返回 (命中的页面或空串, 页面 → 各签名得分（三位小数）)。b 为 720x1280 BGR。
func SignatureMatch(b *img.BGR) (string, *jsonx.Object) {
	gray := cv.Gray(b)
	detail := jsonx.NewObject()
	best, bestEx := "", 0.0
	for _, sc := range loadSignatures() {
		scores := make([]float64, len(sc.sigs))
		rounded := make([]float64, len(sc.sigs))
		var passed []float64
		for i, s := range sc.sigs {
			scores[i] = float64(s.score(gray))
			rounded[i] = pyRound(scores[i], 3)
			if scores[i] >= s.Threshold {
				passed = append(passed, scores[i]-s.Threshold)
			}
		}
		detail.Set(sc.name, rounded)
		if len(passed) >= max(1, int(math.RoundToEven(float64(len(sc.sigs))*sigMinVote))) {
			sum := 0.0
			for _, p := range passed {
				sum += p
			}
			if ex := sum / float64(len(passed)); ex > bestEx {
				best, bestEx = sc.name, ex
			}
		}
	}
	return best, detail
}
