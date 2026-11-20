//go:build windows

package secret

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"time"
	"unsafe"

	"github.com/danieljoos/wincred"
	"golang.org/x/sys/windows"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
)

// credentials 是 Windows 凭据管理器的通用凭据（目标名 hiro-console:<名字>，值 UTF-16，同 Python 版）。
type credentials struct{}

func systemBackend() Backend { return credentials{} }

func target(name string) string { return Service + ":" + name }

func (credentials) Set(name, value string) error {
	c := wincred.NewGenericCredential(target(name))
	c.UserName = name
	c.CredentialBlob = toUTF16(value)
	c.Persist = wincred.PersistLocalMachine
	if err := c.Write(); err != nil {
		return clierr.New("credentials", fmt.Sprintf("写入凭据管理器失败（%v）", err), "重试；一直失败请反馈")
	}
	return nil
}

func (credentials) Get(name string) (string, error) {
	c, err := wincred.GetGenericCredential(target(name))
	if errors.Is(err, windows.ERROR_NOT_FOUND) {
		return "", nil
	}
	if err != nil {
		return "", clierr.New("credentials", fmt.Sprintf("读取凭据管理器失败（%v）", err), "重试；一直失败请反馈")
	}
	return fromUTF16(c.CredentialBlob), nil
}

func (credentials) Delete(name string) error {
	c, err := wincred.GetGenericCredential(target(name))
	if err != nil {
		return nil //nolint:nilerr // 没有这一项就当删掉了
	}
	_ = c.Delete()
	return nil
}

// ---------------------------------------------------------------- 剪贴板（同 Python _win_write）

var (
	user32   = windows.NewLazySystemDLL("user32.dll")
	kernel32 = windows.NewLazySystemDLL("kernel32.dll")

	pCreateWindowExW            = user32.NewProc("CreateWindowExW")
	pDestroyWindow              = user32.NewProc("DestroyWindow")
	pOpenClipboard              = user32.NewProc("OpenClipboard")
	pEmptyClipboard             = user32.NewProc("EmptyClipboard")
	pCloseClipboard             = user32.NewProc("CloseClipboard")
	pSetClipboardData           = user32.NewProc("SetClipboardData")
	pRegisterClipboardFormatW   = user32.NewProc("RegisterClipboardFormatW")
	pGetClipboardSequenceNumber = user32.NewProc("GetClipboardSequenceNumber")
	pGlobalAlloc                = kernel32.NewProc("GlobalAlloc")
	pGlobalLock                 = kernel32.NewProc("GlobalLock")
	pGlobalUnlock               = kernel32.NewProc("GlobalUnlock")
	pGlobalFree                 = kernel32.NewProc("GlobalFree")
	pRtlMoveMemory              = kernel32.NewProc("RtlMoveMemory")
)

const (
	cfUnicodeText = 13
	gmemMoveable  = 0x0002
)

func clipFail(step string) error {
	return clierr.New("clipboard", "写剪贴板失败（"+step+"）", "稍后重试；一直失败请反馈")
}

func put(format uintptr, raw []byte) error {
	h, _, _ := pGlobalAlloc.Call(gmemMoveable, uintptr(len(raw)))
	if h == 0 {
		return clipFail("GlobalAlloc")
	}
	p, _, _ := pGlobalLock.Call(h)
	if p == 0 {
		_, _, _ = pGlobalFree.Call(h)
		return clipFail("GlobalLock")
	}
	if len(raw) > 0 {
		_, _, _ = pRtlMoveMemory.Call(p, uintptr(unsafe.Pointer(&raw[0])), uintptr(len(raw))) //nolint:gosec // 系统调用要传指针
	}
	_, _, _ = pGlobalUnlock.Call(h)
	if r, _, _ := pSetClipboardData.Call(format, h); r == 0 {
		_, _, _ = pGlobalFree.Call(h) // 没交给系统的内存要自己放
		return clipFail("SetClipboardData")
	}
	return nil
}

// writeLocked：用一个隐藏窗口当剪贴板所有者（OpenClipboard(NULL) 时 SetClipboardData 会失败），每一步都查返回值。
func writeLocked(value string) (uint32, error) {
	static, _ := syscall.UTF16PtrFromString("STATIC")
	hwnd, _, _ := pCreateWindowExW.Call(0, uintptr(unsafe.Pointer(static)), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) //nolint:gosec // 系统调用要传指针
	if hwnd == 0 {
		return 0, clipFail("CreateWindowEx")
	}
	defer func() { _, _, _ = pDestroyWindow.Call(hwnd) }()
	opened := false
	for range 20 {
		if r, _, _ := pOpenClipboard.Call(hwnd); r != 0 {
			opened = true
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if !opened {
		return 0, clierr.New("clipboard", "剪贴板被别的程序占着", "稍后重试")
	}
	err := func() error {
		defer func() { _, _, _ = pCloseClipboard.Call() }()
		if r, _, _ := pEmptyClipboard.Call(); r == 0 {
			return clipFail("EmptyClipboard")
		}
		if err := put(cfUnicodeText, toUTF16(value+"\x00")); err != nil {
			return err
		}
		// 不进剪贴板历史、不同步到云剪贴板、剪贴板监控工具不记录
		for _, name := range []string{"ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"} {
			n, _ := syscall.UTF16PtrFromString(name)
			if f, _, _ := pRegisterClipboardFormatW.Call(uintptr(unsafe.Pointer(n))); f != 0 { //nolint:gosec // 系统调用要传指针
				if err := put(f, []byte{0, 0, 0, 0}); err != nil {
					return err
				}
			}
		}
		return nil
	}()
	if err != nil {
		return 0, err
	}
	seq, _, _ := pGetClipboardSequenceNumber.Call()
	return uint32(seq), nil //nolint:gosec // 剪贴板序号本来就是 32 位 DWORD
}

func systemClipboard(value string, clearAfter int) error {
	seq, err := writeLocked(value)
	if err != nil {
		return err
	}
	exe, err := os.Executable()
	if err != nil {
		return clierr.New("clipboard", "设置自动清空剪贴板失败", "粘贴完请自己清空剪贴板")
	}
	clear := exec.Command(exe, ClearCommand, strconv.FormatUint(uint64(seq), 10), strconv.Itoa(clearAfter))
	proc.Detach(clear)
	if err := clear.Start(); err != nil {
		return clierr.New("clipboard", "设置自动清空剪贴板失败", "粘贴完请自己清空剪贴板")
	}
	return clear.Process.Release()
}

// ClearLater 等 seconds 秒，剪贴板还是我们写的那次（序号没变）才清空。由 hiro 的隐藏子命令调用。
func ClearLater(seq uint32, seconds int) {
	time.Sleep(time.Duration(seconds) * time.Second)
	if cur, _, _ := pGetClipboardSequenceNumber.Call(); uint32(cur) != seq { //nolint:gosec // 剪贴板序号本来就是 32 位 DWORD
		return
	}
	static, _ := syscall.UTF16PtrFromString("STATIC")
	hwnd, _, _ := pCreateWindowExW.Call(0, uintptr(unsafe.Pointer(static)), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) //nolint:gosec // 系统调用要传指针
	if hwnd == 0 {
		return
	}
	defer func() { _, _, _ = pDestroyWindow.Call(hwnd) }()
	if r, _, _ := pOpenClipboard.Call(hwnd); r != 0 {
		_, _, _ = pEmptyClipboard.Call()
		_, _, _ = pCloseClipboard.Call()
	}
}
