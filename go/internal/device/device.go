// Package device 是设备统一接口（同 Python 版 hiro.devices）：截图、点击、滑动，坐标一律是逻辑坐标
// （竖屏 720x1280，横屏 LIVE 1280x720）。三个实现：
//
//   - maatools：Mac + PlayCover（hguandl 分支的 MaaTools 服务，不要求前台、不动鼠标）
//   - adb：MuMu / 雷电 / 真机（直接调 adb 命令）
//   - dmm：Windows DMM 版（MaaFramework 的 Win32 控制器）
//
// 失败时返回 Gone（窗口没了 / 连接断了）或 Unavailable（连不上、截图全黑、设备离线）；驱动内部先重连一次。
package device

import (
	"errors"
	"fmt"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// Device 是设备统一接口。
type Device interface {
	Name() string
	Connect() error
	Close() error
	// Screenshot 截图并缩放到逻辑分辨率（竖屏 720x1280，横屏 1280x720）。
	Screenshot() (*img.BGR, error)
	// Tap 点逻辑坐标（和当前截图同一坐标系）。
	Tap(x, y int) error
	// Swipe 从 (x1,y1) 滑到 (x2,y2)；hold > 0 时到终点停这么久再松手（列表不带惯性）。
	Swipe(x1, y1, x2, y2 int, duration, hold time.Duration) error
	// Alive 游戏窗口 / 进程还在。
	Alive() bool
	// Orientation 看最近一张截图：Portrait 或 Landscape。
	Orientation() string
}

// Kind 是设备错误的类别。
type Kind int

const (
	// Gone 窗口没了 / 连接断了（游戏关了、闪退、模拟器断开）。
	Gone Kind = iota + 1
	// Unavailable 连不上（端口不通、找不到窗口、设备离线、截图全黑）。
	Unavailable
)

// Error 是设备层的错误。
type Error struct {
	Kind Kind
	Msg  string
	Err  error
}

func (e *Error) Error() string { return e.Msg }
func (e *Error) Unwrap() error { return e.Err }

func gone(format string, a ...any) error { return &Error{Kind: Gone, Msg: fmt.Sprintf(format, a...)} }

func unavailable(format string, a ...any) error {
	return &Error{Kind: Unavailable, Msg: fmt.Sprintf(format, a...)}
}

func is(err error, k Kind) bool {
	var e *Error
	return errors.As(err, &e) && e.Kind == k
}

// IsGone 是不是「窗口没了 / 连接断了」。
func IsGone(err error) bool { return is(err, Gone) }

// IsUnavailable 是不是「连不上」。
func IsUnavailable(err error) bool { return is(err, Unavailable) }

// names：HIRO_DEVICE → 实现（同 Python NAMES）。
var names = map[string]string{"maatools": "maatools", "dmm": "maafw-win32", "adb": "adb", "mumu": "adb", "leidian": "adb"}

// Open 按名字（缺省 HIRO_DEVICE；auto 在 macOS 上是 maatools）建设备，不连接。
func Open(name string) (Device, error) {
	key := strings.ToLower(strings.TrimSpace(name))
	if key == "" {
		key = strings.ToLower(strings.TrimSpace(os.Getenv("HIRO_DEVICE")))
	}
	if key == "" || key == "auto" {
		if runtime.GOOS != "darwin" {
			return nil, errors.New("这台机器不是 macOS，请设 HIRO_DEVICE：dmm、adb、mumu、leidian")
		}
		key = "maatools"
	}
	switch names[key] {
	case "maatools":
		return NewMaaTools(MaaToolsPort), nil
	case "maafw-win32":
		return NewDMM(), nil
	case "adb":
		return NewAdb("", ""), nil
	}
	return nil, fmt.Errorf("HIRO_DEVICE=%q 不支持；可选：maatools、dmm、adb、mumu、leidian（原版 PlayCover 的 quartz 已不支持）", key)
}
