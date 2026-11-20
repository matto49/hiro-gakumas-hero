//go:build !windows

package proc

import (
	"os"
	"os/exec"
	"strconv"
	"strings"
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

// Terminate 请子进程退出（SIGTERM，同 Python Popen.terminate）。
func Terminate(p *os.Process) { _ = p.Signal(syscall.SIGTERM) }

// CommandLine 返回一个进程的命令行；进程不在了返回 ""。
func CommandLine(pid int) string {
	out, err := exec.Command("ps", "-o", "command=", "-p", strconv.Itoa(pid)).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
