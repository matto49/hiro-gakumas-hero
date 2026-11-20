//go:build !darwin && !windows

package secret

import "github.com/matto49/hiro-gakumas-hero/go/internal/clierr"

type unsupported struct{}

func systemBackend() Backend { return unsupported{} }

func errUnsupported() error {
	return clierr.New("unsupported", "这个系统还不支持保存账号密码", "目前只支持 Mac 和 Windows")
}

func (unsupported) Set(string, string) error   { return errUnsupported() }
func (unsupported) Get(string) (string, error) { return "", errUnsupported() }
func (unsupported) Delete(string) error        { return errUnsupported() }
func systemClipboard(string, int) error {
	return clierr.New("unsupported", "这个系统还不支持复制密码", "目前只支持 Mac 和 Windows")
}

// ClearLater 只有 Windows 用（Mac 由 osascript 延时清）。
func ClearLater(uint32, int) {}
