package device

import (
	"math"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// 屏幕方向。
const (
	Portrait  = "portrait"
	Landscape = "landscape"
)

const (
	aspectTolerance = 0.1 // 和 KAA PortraitGameScaler 的缺省容差一样
	// 横屏宽高比落在这里（Mac 全屏 16:10：PlayCover 横屏 LIVE 全屏后截到 2940x1838）：居中裁成 16:9
	landscapeCropMin, landscapeCropMax = 1.5, 1.7
	// ResizeSettle：截到的画面宽高比不对（横竖屏切换时窗口正在改尺寸）最多重截这么久
	ResizeSettle = 3 * time.Second
	resizeRetry  = 200 * time.Millisecond
)

// roundHalfEven 同 Python 的 round()（四舍六入五取偶）。
func roundHalfEven(v float64) int { return int(math.RoundToEven(v)) }

// LogicalSize 原生尺寸 → 逻辑尺寸：竖屏 720x1280，横屏 1280x720。
func LogicalSize(w, h int) (int, int) {
	if w > h {
		return 1280, 720
	}
	return 720, 1280
}

// CropBox 原生截图里要用的那一块 (x0, y0, w, h)：宽高比接近 9:16 用整张；横屏 16:10 左右（全屏 LIVE）居中裁出 16:9；
// 都不是返回 ok=false。
func CropBox(w, h int) (x0, y0, cw, ch int, ok bool) {
	if w <= 0 || h <= 0 {
		return 0, 0, 0, 0, false
	}
	lw, lh := LogicalSize(w, h)
	long, short := float64(max(w, h)), float64(min(w, h))
	if math.Abs(long/short-float64(max(lw, lh))/float64(min(lw, lh))) <= aspectTolerance {
		return 0, 0, w, h, true
	}
	if r := float64(w) / float64(h); w > h && r >= landscapeCropMin && r <= landscapeCropMax {
		ch = roundHalfEven(float64(w) * 9 / 16)
		return 0, (h - ch) / 2, w, ch, true
	}
	return 0, 0, 0, 0, false
}

// CheckAspect 宽高比和 9:16 差太多（又不是能裁的横屏 16:10）时没法拉伸（模板会变形），返回 Unavailable。
func CheckAspect(w, h int) error {
	if _, _, _, _, ok := CropBox(w, h); !ok {
		lw, lh := LogicalSize(w, h)
		return unavailable("画面尺寸 %dx%d 和 9:16 差太多，不能缩放到 %dx%d", w, h, lw, lh)
	}
	return nil
}

// Screen 是原生截图 ↔ 逻辑坐标。每张截图都更新一次原生尺寸（窗口被拉大拉小、横竖屏切换时跟着变）。
type Screen struct {
	native  [2]int // 最近一张原生截图的 (宽, 高)；0 = 还没截过
	crop    [4]int // 原生截图里用的那一块
	hasCrop bool
}

// Orientation 看最近一张截图。
func (s *Screen) Orientation() string {
	if s.native[0] > s.native[1] {
		return Landscape
	}
	return Portrait
}

// Native 最近一张原生截图的尺寸；还没截过返回 0, 0。
func (s *Screen) Native() (int, int) { return s.native[0], s.native[1] }

// Frame 原生 BGR 截图 → 逻辑分辨率（OpenCV 通用 INTER_LINEAR 缩放，见 img.Resize）。
func (s *Screen) Frame(raw *img.BGR) (*img.BGR, error) {
	if !raw.Valid() {
		return nil, unavailable("截图不是 BGR 三通道图像")
	}
	x0, y0, cw, ch, ok := CropBox(raw.W, raw.H)
	if !ok {
		return nil, CheckAspect(raw.W, raw.H)
	}
	s.native, s.crop, s.hasCrop = [2]int{raw.W, raw.H}, [4]int{x0, y0, cw, ch}, true
	src := raw
	if cw != raw.W || ch != raw.H {
		src = raw.Crop(x0, y0, cw, ch)
	}
	lw, lh := LogicalSize(cw, ch)
	if cw == lw && ch == lh {
		return src, nil
	}
	return img.Resize(src, lw, lh), nil
}

// ToNative 逻辑坐标 → 原生像素（x、y 各按各的比例，夹在画面内）。要先截过一张图。
func (s *Screen) ToNative(x, y float64) (int, int, error) {
	if s.native[0] == 0 {
		return 0, 0, unavailable("还没截过图，不知道原生分辨率")
	}
	w, h := s.native[0], s.native[1]
	x0, y0, cw, ch := 0, 0, w, h
	if s.hasCrop {
		x0, y0, cw, ch = s.crop[0], s.crop[1], s.crop[2], s.crop[3]
	}
	lw, lh := LogicalSize(cw, ch)
	nx := min(w-1, max(0, x0+roundHalfEven(x*float64(cw)/float64(lw))))
	ny := min(h-1, max(0, y0+roundHalfEven(y*float64(ch)/float64(lh))))
	return nx, ny, nil
}

// Clock 是可以换掉的时钟（测试里用假的）。
type Clock struct {
	Now   func() time.Duration
	Sleep func(time.Duration)
}

var start = time.Now()

// RealClock 是真时钟。
var RealClock = Clock{Now: func() time.Duration { return time.Since(start) }, Sleep: time.Sleep}

// SettledFrame 截图 → 逻辑分辨率，三个设备共用。宽高比和 9:16 差太多时多半是窗口正在横竖屏切换（DMM 11-14：选拔③结束进 LIVE
// 时截到 540x572），每 0.2 秒重截，settle 内恢复就用新的那张；一直不对再返回 Unavailable。比例对的截图第一张就返回。
func SettledFrame(grab func() (*img.BGR, error), s *Screen, settle time.Duration, c Clock) (*img.BGR, error) {
	deadline := c.Now() + settle
	for {
		raw, err := grab()
		if err != nil {
			return nil, err
		}
		if raw.Valid() {
			if err := CheckAspect(raw.W, raw.H); err != nil {
				if c.Now() >= deadline {
					return nil, err
				}
				c.Sleep(resizeRetry)
				continue
			}
		}
		return s.Frame(raw)
	}
}
