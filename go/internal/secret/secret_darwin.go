//go:build darwin

package secret

import (
	"errors"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"github.com/zalando/go-keyring"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
)

// keychain 是 login 钥匙串的通用密码项（服务 hiro-console，账户 = 名字）。
// go-keyring 经 /usr/bin/security 的标准输入传值，不出现在命令行参数里。
type keychain struct{}

func systemBackend() Backend { return keychain{} }

func (keychain) Set(name, value string) error {
	if err := keyring.Set(Service, name, value); err != nil {
		return clierr.New("keychain", "写入钥匙串失败", "在「钥匙串访问」里允许本程序访问后重试")
	}
	return nil
}

func (keychain) Get(name string) (string, error) {
	v, err := keyring.Get(Service, name)
	if errors.Is(err, keyring.ErrNotFound) {
		return "", nil
	}
	if err != nil {
		return "", clierr.New("keychain", "钥匙串读取失败", "在「钥匙串访问」里允许本程序访问后重试")
	}
	return v, nil
}

func (keychain) Delete(name string) error {
	if err := keyring.Delete(Service, name); err != nil && !errors.Is(err, keyring.ErrNotFound) {
		return clierr.New("keychain", "从钥匙串删除失败", "重试；一直失败请反馈")
	}
	return nil
}

// 剪贴板：JXA 写 NSPasteboard，带 org.nspasteboard.ConcealedType / TransientType 标记（剪贴板管理工具不记录）。
// 值经标准输入传给 osascript，不出现在命令行参数里。

const macWrite = `
ObjC.import('AppKit');
const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
const s = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
const pb = $.NSPasteboard.generalPasteboard;
pb.clearContents;
pb.setStringForType(s, $.NSPasteboardTypeString);
pb.setStringForType($(''), $('org.nspasteboard.ConcealedType'));
pb.setStringForType($(''), $('org.nspasteboard.TransientType'));
String(pb.changeCount);
`

// macClear：等 argv[1] 秒，剪贴板还是我们写的那次（changeCount 没变）才清空。
const macClear = `
function run(argv) {
  ObjC.import('AppKit');
  delay(Number(argv[1]));
  const pb = $.NSPasteboard.generalPasteboard;
  if (String(pb.changeCount) === argv[0]) pb.clearContents;
}
`

func systemClipboard(value string, clearAfter int) error {
	cmd := exec.Command("osascript", "-l", "JavaScript", "-e", macWrite)
	cmd.Stdin = strings.NewReader(value)
	cmd.WaitDelay = 15 * time.Second
	out, err := cmd.Output()
	if err != nil {
		return clierr.New("clipboard", "写剪贴板失败", "重试；一直失败请反馈")
	}
	count := strings.TrimSpace(string(out))
	clear := exec.Command("osascript", "-l", "JavaScript", "-e", macClear, count, strconv.Itoa(clearAfter))
	proc.Detach(clear)
	if err := clear.Start(); err != nil {
		return clierr.New("clipboard", "设置自动清空剪贴板失败", "粘贴完请自己清空剪贴板")
	}
	return clear.Process.Release()
}

// ClearLater 只有 Windows 用（Mac 由 osascript 延时清）。
func ClearLater(uint32, int) {}
