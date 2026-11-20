//go:build windows

package device

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
	"unsafe"

	maa "github.com/MaaXYZ/maa-framework-go/v3"
	"github.com/MaaXYZ/maa-framework-go/v3/controller/win32"
	"golang.org/x/sys/windows"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

var (
	maaOnce sync.Once
	maaErr  error
)

// maaLibDir：HIRO_MAAFW_LIB → hiro 程序旁边的 maafw/。
func maaLibDir() string {
	if v := os.Getenv("HIRO_MAAFW_LIB"); v != "" {
		return v
	}
	exe, err := os.Executable()
	if err != nil {
		return "maafw"
	}
	return filepath.Join(filepath.Dir(exe), "maafw")
}

func maaLogDir() string {
	if v := os.Getenv("HIRO_MAAFW_LOG"); v != "" {
		return v
	}
	return filepath.Join(os.TempDir(), "hiro-maafw")
}

func initMaa() error {
	maaOnce.Do(func() {
		log := maaLogDir()
		_ = os.MkdirAll(log, 0o750)
		if err := maa.Init(maa.WithLibDir(maaLibDir()), maa.WithLogDir(log)); err != nil {
			maaErr = fmt.Errorf("要随程序带 MaaFramework %s（%s）：%w", MaaFrameworkVersion, maaLibDir(), err)
		}
	})
	return maaErr
}

// DMM 是设备统一接口：DMM 版学マス（Windows，MaaFramework Win32 控制器）。
type DMM struct {
	opts   dmmOptions
	ctrl   *maa.Controller
	hwnd   unsafe.Pointer
	screen Screen
	clock  Clock
}

// NewDMM 建 DMM 设备（不连接）。
func NewDMM() *DMM { return &DMM{opts: dmmOptionsFromEnv(), clock: RealClock} }

func (d *DMM) Name() string        { return "maafw-win32" }
func (d *DMM) Orientation() string { return d.screen.Orientation() }

func (d *DMM) find() (unsafe.Pointer, bool) {
	for _, w := range maa.FindDesktopWindows() {
		if isGameWindow(w.WindowName, w.ClassName, d.opts.title) {
			return w.Handle, true
		}
	}
	return nil, false
}

func (d *DMM) open() error {
	if err := initMaa(); err != nil {
		return unavailable("%v", err)
	}
	scap, err := screencapFlags(d.opts.screencap)
	if err != nil {
		return unavailable("%v", err)
	}
	inp, err := inputMethod(d.opts.input)
	if err != nil {
		return unavailable("%v", err)
	}
	hwnd, ok := d.find()
	if !ok {
		return gone("找不到游戏窗口（标题 %q、类 %s）", d.opts.title, dmmWindowClass)
	}
	ctrl := maa.NewWin32Controller(hwnd, win32.ScreencapMethod(scap), win32.InputMethod(inp), win32.InputMethod(inp))
	if ctrl == nil {
		return unavailable("MaaFramework 建不了控制器")
	}
	ctrl.SetScreenshotUseRawSize(true)
	if !ctrl.PostConnect().Wait().Success() {
		ctrl.Destroy()
		return unavailable("MaaFramework 连不上游戏窗口（截图 %s、输入 %s），日志见 %s", d.opts.screencap, d.opts.input,
			filepath.Join(maaLogDir(), "debug", "maa.log"))
	}
	d.ctrl, d.hwnd = ctrl, hwnd
	return nil
}

func (d *DMM) drop() {
	if d.ctrl != nil {
		d.ctrl.Destroy()
	}
	d.ctrl = nil
}

// job 发一个动作并等它做完；失败时重连一次（游戏重开后窗口句柄会变）再试。窗口没了返回 Gone。
func (d *DMM) job(what string, do func(*maa.Controller) *maa.Job) error {
	for range 2 {
		if d.ctrl == nil {
			if err := d.open(); err != nil {
				return err
			}
		}
		if do(d.ctrl).Wait().Success() {
			return nil
		}
		d.drop()
	}
	if _, ok := d.find(); !ok {
		return gone("MaaFramework %s失败：游戏窗口没了", what)
	}
	return unavailable("MaaFramework %s失败（截图 %s、输入 %s）", what, d.opts.screencap, d.opts.input)
}

func (d *DMM) grab() (*img.BGR, error) {
	if err := d.job("截图", func(c *maa.Controller) *maa.Job { return c.PostScreencap() }); err != nil {
		return nil, err
	}
	m := d.ctrl.CacheImage()
	if m == nil || m.Bounds().Empty() {
		return nil, unavailable("MaaFramework 截图是空的")
	}
	return img.FromImage(m), nil
}

func (d *DMM) Connect() error {
	if err := d.open(); err != nil {
		if IsGone(err) { // 连接时找不到窗口算「连不上」
			return unavailable("%v", err)
		}
		return err
	}
	first, err := d.grab()
	if err != nil {
		return err
	}
	if !first.AnyNonZero() { // DXGI 窗口方式截出来全黑：换截图方式
		d.drop()
		return unavailable("截图方式 %s 截出来全黑，换 HIRO_MAAFW_SCREENCAP（GDI / FramePool）", d.opts.screencap)
	}
	used := false
	_, err = SettledFrame(func() (*img.BGR, error) { // 连上那一刻正在横竖屏切换也等一下
		if !used {
			used = true
			return first, nil
		}
		return d.grab()
	}, &d.screen, ResizeSettle, d.clock)
	return err
}

func (d *DMM) Close() error {
	d.drop()
	d.hwnd = nil
	return nil
}

func (d *DMM) Alive() bool {
	if initMaa() != nil {
		return false
	}
	_, ok := d.find()
	return ok
}

// Screenshot 截图 → 逻辑分辨率；窗口正在横竖屏切换时重截。
func (d *DMM) Screenshot() (*img.BGR, error) {
	return SettledFrame(d.grab, &d.screen, ResizeSettle, d.clock)
}

func (d *DMM) native(x, y int) (int32, int32, error) {
	if w, _ := d.screen.Native(); w == 0 {
		if _, err := d.Screenshot(); err != nil {
			return 0, 0, err
		}
	}
	nx, ny, err := d.screen.ToNative(float64(x), float64(y))
	return int32(nx), int32(ny), err //nolint:gosec // 屏幕坐标
}

func (d *DMM) Tap(x, y int) error {
	nx, ny, err := d.native(x, y)
	if err != nil {
		return err
	}
	return d.job("点击", func(c *maa.Controller) *maa.Job { return c.PostClick(nx, ny) })
}

func (d *DMM) Swipe(x1, y1, x2, y2 int, duration, hold time.Duration) error {
	a1, b1, err := d.native(x1, y1)
	if err != nil {
		return err
	}
	ax2, by2, err := d.screen.ToNative(float64(x2), float64(y2))
	if err != nil {
		return err
	}
	a2, b2 := int32(ax2), int32(by2) //nolint:gosec // 屏幕坐标
	if duration <= 0 {
		duration = 300 * time.Millisecond
	}
	if hold <= 0 {
		return d.job("滑动", func(c *maa.Controller) *maa.Job { return c.PostSwipe(a1, b1, a2, b2, duration) })
	}
	// 按下、匀速移到终点、原地停 hold（继续发同一位置）、松手：列表不按松手速度惯性多滑
	if err := d.job("按下", func(c *maa.Controller) *maa.Job { return c.PostTouchDown(0, a1, b1, 1) }); err != nil {
		return err
	}
	steps := max(2, int(duration.Seconds()*swipeFPS))
	for i := 1; i <= steps; i++ {
		t := float64(i) / float64(steps)
		time.Sleep(time.Second / swipeFPS)
		px := int32(roundHalfEven(float64(a1) + float64(a2-a1)*t)) //nolint:gosec // 屏幕坐标
		py := int32(roundHalfEven(float64(b1) + float64(b2-b1)*t)) //nolint:gosec // 同上
		if err := d.job("拖动", func(c *maa.Controller) *maa.Job { return c.PostTouchMove(0, px, py, 1) }); err != nil {
			return err
		}
	}
	end := time.Now().Add(hold)
	for time.Now().Before(end) {
		time.Sleep(time.Second / swipeFPS)
		if err := d.job("拖动", func(c *maa.Controller) *maa.Job { return c.PostTouchMove(0, a2, b2, 1) }); err != nil {
			return err
		}
	}
	return d.job("松手", func(c *maa.Controller) *maa.Job { return c.PostTouchUp(0) })
}

// ---------------------------------------------------------------- 游戏窗口尺寸（同 Python hiro.devices.win_window）

var (
	user32                         = windows.NewLazySystemDLL("user32.dll")
	pFindWindowW                   = user32.NewProc("FindWindowW")
	pGetClientRect                 = user32.NewProc("GetClientRect")
	pGetWindowRect                 = user32.NewProc("GetWindowRect")
	pSystemParametersInfoW         = user32.NewProc("SystemParametersInfoW")
	pSetWindowPos                  = user32.NewProc("SetWindowPos")
	pSetProcessDpiAwarenessContext = user32.NewProc("SetProcessDpiAwarenessContext")
	dpiOnce                        sync.Once
)

type rect struct{ Left, Top, Right, Bottom int32 }

// dpiAware：进程设成按显示器 DPI 感知（PER_MONITOR_AWARE_V2），尺寸按物理像素算，和 DMM 原生截图同一个口径。
func dpiAware() {
	dpiOnce.Do(func() {
		if pSetProcessDpiAwarenessContext.Find() == nil {
			_, _, _ = pSetProcessDpiAwarenessContext.Call(^uintptr(3)) // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
		}
	})
}

func findGameWindow() (uintptr, bool) {
	dpiAware()
	cls, _ := windows.UTF16PtrFromString(dmmWindowClass)
	title, _ := windows.UTF16PtrFromString(dmmWindowTitle)
	h, _, _ := pFindWindowW.Call(uintptr(unsafe.Pointer(cls)), uintptr(unsafe.Pointer(title))) //nolint:gosec // 系统调用传指针
	return h, h != 0
}

func clientSize(hwnd uintptr) [2]int {
	var r rect
	_, _, _ = pGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&r))) //nolint:gosec // 系统调用传指针
	return [2]int{int(r.Right - r.Left), int(r.Bottom - r.Top)}
}

func frameSize(hwnd uintptr) [2]int {
	var w, c rect
	_, _, _ = pGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&w))) //nolint:gosec // 同上
	_, _, _ = pGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&c))) //nolint:gosec // 同上
	return [2]int{int((w.Right - w.Left) - (c.Right - c.Left)), int((w.Bottom - w.Top) - (c.Bottom - c.Top))}
}

func workArea() rect {
	var r rect
	_, _, _ = pSystemParametersInfoW.Call(0x0030, 0, uintptr(unsafe.Pointer(&r)), 0) //nolint:gosec // SPI_GETWORKAREA
	return r
}

func targetSize(hwnd uintptr) ([2]int, bool) {
	wa := workArea()
	w, h, ok := FitSize(int(wa.Bottom-wa.Top), frameSize(hwnd)[1])
	return [2]int{w, h}, ok
}

// FitDMMWindow 客户区不是 size（nil 就按显示器算的目标）就改；窗口挪到工作区左上角。返回（改前, 改后）。要管理员权限。
func FitDMMWindow(size *[2]int) (before, after [2]int, err error) {
	hwnd, ok := findGameWindow()
	if !ok {
		return before, after, fmt.Errorf("找不到游戏窗口（类 %s、标题 %q）", dmmWindowClass, dmmWindowTitle)
	}
	want := size
	if want == nil {
		t, ok := targetSize(hwnd)
		if !ok {
			wa := workArea()
			return before, after, fmt.Errorf("显示器工作区 %v 放不下 %dx%d 的游戏画面", wa, DMMMinSize[0], DMMMinSize[1])
		}
		want = &t
	}
	before = clientSize(hwnd)
	if before == *want {
		return before, before, nil
	}
	f, wa := frameSize(hwnd), workArea()
	const swpNoZOrder, swpNoActivate = 0x0004, 0x0010
	_, _, _ = pSetWindowPos.Call(hwnd, 0, uintptr(wa.Left), uintptr(wa.Top), uintptr(want[0]+f[0]), uintptr(want[1]+f[1]), swpNoZOrder|swpNoActivate) //nolint:gosec // 窗口坐标
	return before, clientSize(hwnd), nil
}

// DMMWindowInfo 现在的客户区尺寸、工作区、外框、目标尺寸（hiro __win-window 不带参数时打印）。
func DMMWindowInfo() (string, error) {
	hwnd, ok := findGameWindow()
	if !ok {
		return "", fmt.Errorf("找不到游戏窗口")
	}
	c, t := clientSize(hwnd), [2]int{}
	if tt, ok := targetSize(hwnd); ok {
		t = tt
	}
	return fmt.Sprintf("client %dx%d，work area %v，frame %v，target %v", c[0], c[1], workArea(), frameSize(hwnd), t), nil
}
