package device

import (
	"fmt"
	"os"
	"sort"
	"strings"
)

// DMM 版（Windows）：MaaFramework（LGPL-3.0）的 Win32 控制器截图 / 点击，同 Python 版 hiro.devices.maafw_win32。
//
//	HIRO_MAAFW_SCREENCAP  缺省 FramePool；可写 GDI、ScreenDC，多个用「|」连起来让框架挑最快的
//	HIRO_MAAFW_INPUT      缺省 SendMessageWithWindowPos（游戏认真实光标位置：先挪窗口让目标点落在光标下再发消息）
//	HIRO_MAAFW_LOG        框架日志目录（缺省系统临时目录下 hiro-maafw）
//	HIRO_MAAFW_LIB        MaaFramework 动态库目录（缺省 hiro 程序旁边的 maafw/）
//
// 框架给窗口原生尺寸的截图，点击坐标也按原生像素；这里一次拉伸到 720x1280，点击按比例换回原生像素（Screen）。
// 不让框架先缩到短边 720：它的插值和我们再拉一次叠起来，画面和 Python 版截的不一样（2026-11-14 DMM 实机：OCR 读偏）。

// MaaFrameworkVersion 是要随安装包带的 MaaFramework 版本：和 Python 版用的 maafw 5.14.3 一样。
// 枚举的数值在不同版本里含义不同（v5.14 的 128 是 SendMessageWithWindowPos，maa-framework-go v3.5.1 的同名常量表里
// 128 叫 SendMessageWithCursorPosAndBlockInput），所以下面的对照表照 maafw 5.14.3 抄，不用绑定库的常量名。
const MaaFrameworkVersion = "5.14.3"

const (
	dmmWindowTitle    = "gakumas"
	dmmWindowClass    = "UnityWndClass"
	dmmDefaultCapture = "FramePool"
	dmmDefaultInput   = "SendMessageWithWindowPos"
)

// maafw 5.14.3 的 MaaWin32ScreencapMethodEnum / MaaWin32InputMethodEnum（Null、All 不让选）。
var (
	maaScreencap = map[string]uint64{
		"GDI": 1, "FramePool": 2, "DXGI_DesktopDup": 4, "DXGI_DesktopDup_Window": 8,
		"PrintWindow": 16, "ScreenDC": 32, "Background": 18, "Foreground": 40,
	}
	maaInput = map[string]uint64{
		"Seize": 1, "SendMessage": 2, "PostMessage": 4, "LegacyEvent": 8, "PostThreadMessage": 16,
		"SendMessageWithCursorPos": 32, "PostMessageWithCursorPos": 64, "SendMessageWithWindowPos": 128,
		"PostMessageWithWindowPos": 256, "Interception": 512, "AnchoredTouch": 1024,
	}
)

func keys(m map[string]uint64) string {
	var ks []string
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return strings.Join(ks, ", ")
}

// screencapFlags「FramePool|GDI」→ 位或；名字写错直接报错，列出可选值。
func screencapFlags(spec string) (uint64, error) {
	var flags uint64
	for _, part := range strings.Split(spec, "|") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		v, ok := maaScreencap[part]
		if !ok {
			return 0, fmt.Errorf("截图方式 %q 不认识；可选：%s", part, keys(maaScreencap))
		}
		flags |= v
	}
	if flags == 0 {
		return 0, fmt.Errorf("HIRO_MAAFW_SCREENCAP 是空的")
	}
	return flags, nil
}

// inputMethod 输入方式名 → 数值。
func inputMethod(name string) (uint64, error) {
	v, ok := maaInput[strings.TrimSpace(name)]
	if !ok {
		return 0, fmt.Errorf("输入方式 %q 不认识；可选：%s", name, keys(maaInput))
	}
	return v, nil
}

// dmmOptions 是 DMM 驱动的选项（环境变量，缺省值同 Python）。
type dmmOptions struct {
	screencap, input, title string
}

func dmmOptionsFromEnv() dmmOptions {
	o := dmmOptions{screencap: os.Getenv("HIRO_MAAFW_SCREENCAP"), input: os.Getenv("HIRO_MAAFW_INPUT"), title: dmmWindowTitle}
	if o.screencap == "" {
		o.screencap = dmmDefaultCapture
	}
	if o.input == "" {
		o.input = dmmDefaultInput
	}
	return o
}

// isGameWindow：标题是 gakumas 的 Unity 窗口（DMM 版只有一个）。
func isGameWindow(title, class, want string) bool {
	return strings.ToLower(strings.TrimSpace(title)) == want && class == dmmWindowClass
}

// ---------------------------------------------------------------- 游戏窗口尺寸（同 Python hiro.devices.win_window）

// DMM 窗口客户区的上下限（用户 11-14：按显示器自适应，9:16）。
var (
	DMMMinSize = [2]int{540, 960}
	DMMMaxSize = [2]int{1080, 1920}
)

// FitSize 按规则算目标客户区：高 = 工作区高 − 外框高，宽按 9:16，夹到上限；比下限小返回 ok=false。
func FitSize(workHeight, frameHeight int) (w, h int, ok bool) {
	h = min(workHeight-frameHeight, DMMMaxSize[1])
	w = roundHalfEven(float64(h) * 9 / 16)
	if w < DMMMinSize[0] || h < DMMMinSize[1] {
		return 0, 0, false
	}
	return w, h, true
}
