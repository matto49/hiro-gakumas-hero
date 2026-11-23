//go:build windows

package main

import (
	"os/exec"
	"syscall"
)

// hideWindow 不让每次调用 hiro 都闪一个控制台窗口。
func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}
