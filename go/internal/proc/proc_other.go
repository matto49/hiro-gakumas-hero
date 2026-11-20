//go:build !windows

package proc

import (
	"os/exec"
	"syscall"
)

// Hide 在 Mac / Linux 上不用做什么。
func Hide(*exec.Cmd) {}

// Detach 让子进程脱离当前终端会话（后台服务、延时清剪贴板）：终端关了它继续跑。
func Detach(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.Setsid = true
}
