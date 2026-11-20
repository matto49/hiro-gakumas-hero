// Package ui 是界面模板匹配（同 Python 版 hiro.vision.ui_templates）。
//
// 模板图和位置在 assets/pack/ui/templates.json（从 KAA 的界面截图切片搬来，名字沿用 R 的路径，如 "InProduce.ButtonUse"）。
// 匹配做法和 KAA 一致：画面统一到 720x1280，灰度 TM_CCOEFF_NORMED，阈值缺省 0.8；fixed 的模板只在 slice_rect 里找，
// 有 region 的只在 region 里找，其余全屏找；同一处只留分数最高的一个（中心点落在已命中区域里的跳过）。
package ui

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// W、H 是模板和坐标的基准分辨率（竖屏）。
const (
	W = 720
	H = 1280
)

// Match 是一处命中。
type Match struct {
	Rect  ocr.Box `json:"rect"`
	Score float64 `json:"score"`
}

// Center 命中框的中心。
func (m Match) Center() ocr.Point { return m.Rect.Center() }

// Normalize 截图统一到 720x1280；已经是就原样返回。
func Normalize(b *img.BGR) *img.BGR {
	if b.W == W && b.H == H {
		return b
	}
	return cv.Resize(b, W, H, cv.Area)
}

// Rect 是 (x, y, w, h)。
type Rect = [4]int

// Template 是一张界面模板。
type Template struct {
	Name      string
	File      string
	SliceRect *Rect // 模板在 720x1280 画面里的位置 (x, y, w, h)
	Threshold float64
	Fixed     bool
	Region    *Rect // 限定搜索区域 (x, y, w, h)
	Source    string
}

type entry struct {
	File      string   `json:"file"`
	SliceRect *Rect    `json:"slice_rect"`
	Threshold *float64 `json:"threshold"`
	Fixed     bool     `json:"fixed"`
	Region    *Rect    `json:"region"`
	Source    string   `json:"source"`
}

type catalogFile struct {
	Templates map[string]entry  `json:"templates"`
	Boxes     map[string][4]int `json:"boxes"`
	Points    map[string][2]int `json:"points"`
}

var (
	catOnce sync.Once
	cat     catalogFile
	catDir  string
	catErr  error

	pixMu sync.Mutex
	pix   = map[string]*img.Gray{}
)

func catalog() (*catalogFile, string) {
	catOnce.Do(func() {
		catDir, catErr = assets.UIPack()
		if catErr != nil {
			return
		}
		b, err := os.ReadFile(filepath.Join(catDir, "templates.json"))
		if err != nil {
			catErr = err
			return
		}
		catErr = json.Unmarshal(b, &cat)
	})
	if catErr != nil {
		panic(fmt.Errorf("界面模板读不了：%w", catErr)) // 素材包是仓库的一部分：缺了是安装坏了
	}
	return &cat, catDir
}

// Tpl 按名字取模板；名字不存在 panic（代码写错了，同 Python 的 KeyError）。
func Tpl(name string) Template {
	c, _ := catalog()
	e, ok := c.Templates[name]
	if !ok {
		panic("没有这个界面模板：" + name)
	}
	th := 0.8
	if e.Threshold != nil {
		th = *e.Threshold
	}
	return Template{Name: name, File: e.File, SliceRect: e.SliceRect, Threshold: th, Fixed: e.Fixed, Region: e.Region, Source: e.Source}
}

// Path 模板图文件的路径。
func (t Template) Path() string {
	_, dir := catalog()
	return filepath.Join(dir, t.File)
}

// Pixels 模板的灰度像素（彩色读再转灰度，同 KAA；直接按灰度读分数差在千分位，并列时选点会不同）。
func (t Template) Pixels() *img.Gray {
	pixMu.Lock()
	defer pixMu.Unlock()
	if g, ok := pix[t.File]; ok {
		return g
	}
	b, err := cv.Read(t.Path())
	if err != nil {
		panic(fmt.Errorf("界面模板 %s 读不了：%w", t.File, err))
	}
	g := cv.Gray(b)
	pix[t.File] = g
	return g
}

func (t Template) searchRect(region *Rect) *Rect {
	switch {
	case region != nil:
		return region
	case t.Region != nil:
		return t.Region
	case t.Fixed:
		return t.SliceRect
	}
	return nil
}

// Opt 是 FindAll 的可选参数：Threshold 覆盖模板阈值，Region 覆盖搜索区域（nil = 用模板自己的）。
type Opt struct {
	Threshold *float64
	Region    *Rect
}

// Th 方便写 Opt{Threshold: ui.Th(0.6)}。
func Th(v float64) *float64 { return &v }

// R 方便写 Opt{Region: ui.R(x, y, w, h)}。
func R(x, y, w, h int) *Rect { return &Rect{x, y, w, h} }

// FindAll 画面里所有命中（分数从高到低）。
func (t Template) FindAll(b *img.BGR, opt Opt) []Match {
	return t.FindAllGray(ScreenGray(b), opt)
}

// 判页时同一帧要找几十个模板：720x1280 灰度图按帧缓存几张（识别层不改截图的像素，按像素数组的地址认帧；
// 缓存里留着原像素数组的引用，帧没被回收，地址就不会被别的帧复用）。
type grayEntry struct {
	pix []byte
	g   *img.Gray
}

var (
	grayMu    sync.Mutex
	grayCache [4]grayEntry
	grayNext  int
)

// ScreenGray 截图统一到 720x1280 再转灰度（同 Python 的 _gray(normalize(img))），同一帧只算一次。
func ScreenGray(b *img.BGR) *img.Gray {
	if len(b.Pix) == 0 {
		return cv.Gray(Normalize(b))
	}
	grayMu.Lock()
	for _, e := range grayCache {
		if len(e.pix) == len(b.Pix) && &e.pix[0] == &b.Pix[0] {
			grayMu.Unlock()
			return e.g
		}
	}
	grayMu.Unlock()
	g := cv.Gray(Normalize(b))
	grayMu.Lock()
	grayCache[grayNext] = grayEntry{b.Pix, g}
	grayNext = (grayNext + 1) % len(grayCache)
	grayMu.Unlock()
	return g
}

// FindAllGray 在已经是 720x1280 灰度的画面里找（Python 收单通道图时不做缩放 / 转灰度）。
func (t Template) FindAllGray(screen *img.Gray, opt Opt) []Match {
	ox, oy := 0, 0
	if r := t.searchRect(opt.Region); r != nil {
		x, y, w, h := r[0], r[1], r[2], r[3]
		ox, oy = max(0, x), max(0, y)
		screen = screen.Slice(ox, oy, x+w, y+h)
	}
	tpl := t.Pixels()
	th, tw := tpl.H, tpl.W
	if screen.H < th || screen.W < tw {
		return nil
	}
	res := cv.MatchTemplate(screen, tpl, cv.CcoeffNormed)
	thr := t.Threshold
	if opt.Threshold != nil {
		thr = *opt.Threshold
	}
	return pick(res, tw, th, ox, oy, screen.W, screen.H, float32(thr))
}

// pick 同 Python：np.where(res >= thr) 的点按分数从高到低，中心点落在已命中区域里的跳过。
func pick(res *cv.Scores, tw, th, ox, oy, sw, sh int, thr float32) []Match {
	type hit struct {
		x, y int
		v    float32
	}
	var hits []hit
	for y := range res.H {
		for x := range res.W {
			if v := res.Val[y*res.W+x]; v >= thr {
				hits = append(hits, hit{x, y, v})
			}
		}
	}
	if len(hits) == 0 {
		return nil
	}
	sort.SliceStable(hits, func(i, j int) bool { return hits[i].v > hits[j].v })
	used := make([]bool, sw*sh)
	var out []Match
	for _, h := range hits {
		if used[(h.y+th/2)*sw+h.x+tw/2] {
			continue
		}
		for yy := h.y; yy < min(h.y+th, sh); yy++ {
			for xx := h.x; xx < min(h.x+tw, sw); xx++ {
				used[yy*sw+xx] = true
			}
		}
		out = append(out, Match{Rect: ocr.Box{X1: h.x + ox, Y1: h.y + oy, X2: h.x + ox + tw, Y2: h.y + oy + th}, Score: float64(h.v)})
	}
	return out
}

// Find 最高分的命中；没有返回 nil。
func (t Template) Find(b *img.BGR, opt Opt) *Match {
	hits := t.FindAll(b, opt)
	if len(hits) == 0 {
		return nil
	}
	return &hits[0]
}

// Exists 有没有命中。
func (t Template) Exists(b *img.BGR, opt Opt) bool { return t.Find(b, opt) != nil }

// Tap 在 b 上找模板，找到就 click 点中心（同 KAA 的 try_click）；返回是否点了。
func Tap(name string, b *img.BGR, click func(x, y int)) bool {
	m := Tpl(name).Find(b, Opt{})
	if m == nil {
		return false
	}
	c := m.Center()
	click(c.X, c.Y)
	return true
}

// FindEnabled 找主按钮，且按钮是亮的（同 KAA 的 GakumasPrimaryButtonPrefab.q(enabled=True)）。
func FindEnabled(name string, b *img.BGR) *Match {
	m := Tpl(name).Find(b, Opt{})
	if m == nil {
		return nil
	}
	if on := PrimaryButtonEnabled(b, *m); on == nil || !*on {
		return nil
	}
	return m
}

// Box 固定区域（KAA 的 HintBox），(x1, y1, x2, y2)，720x1280。
func Box(name string) ocr.Box {
	c, _ := catalog()
	v, ok := c.Boxes[name]
	if !ok {
		panic("没有这个固定区域：" + name)
	}
	return ocr.Box{X1: v[0], Y1: v[1], X2: v[2], Y2: v[3]}
}

// Point 固定点（KAA 的 HintPoint），720x1280。
func Point(name string) ocr.Point {
	c, _ := catalog()
	v, ok := c.Points[name]
	if !ok {
		panic("没有这个固定点：" + name)
	}
	return ocr.Point{X: v[0], Y: v[1]}
}

func crop(b *img.BGR, m Match) *img.BGR {
	r := m.Rect
	return Normalize(b).Slice(r.X1, r.Y1, r.X2, r.Y2)
}

func ptr[T any](v T) *T { return &v }

// PrimaryButtonEnabled 主按钮（橙底）亮没亮：红通道五箱直方图的峰在第 5 箱 = 亮，第 4 箱 = 灰，其余不确定（nil）。
func PrimaryButtonEnabled(b *img.BGR, m Match) *bool {
	c := crop(b, m)
	if c.Empty() {
		return nil
	}
	hist := cv.Hist(cv.Channel(c, 2), 5, 0, 256)
	peak := 0
	for i, v := range hist {
		if v > hist[peak] {
			peak = i
		}
	}
	switch peak {
	case 4:
		return ptr(true)
	case 3:
		return ptr(false)
	}
	return nil
}

// CheckboxChecked 复选框选中（橙色占比 > 30% 且平均饱和度 > 100，同 KAA checkbox_checked_state）。
func CheckboxChecked(b *img.BGR, m Match) *bool {
	c := crop(b, m)
	if c.Empty() {
		return nil
	}
	hsv := cv.HSV(c)
	orange := cv.InRange(hsv, [3]float64{10, 50, 50}, [3]float64{25, 255, 255})
	return ptr(float64(orange.CountNonZero())/float64(len(orange.Pix)) > 0.3 && cv.Channel(hsv, 1).Mean() > 100)
}
