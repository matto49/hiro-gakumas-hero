//go:build windows

package proc

import (
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
)

const (
	createNewProcessGroup = 0x00000200
	detachedProcess       = 0x00000008
	createNoWindow        = 0x08000000
)

// Hide 让子进程不弹控制台窗口（服务没有控制台；不加的话每个任务弹一个黑窗口，用户一关这局就没了）。
func Hide(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.HideWindow = true
	cmd.SysProcAttr.CreationFlags |= createNoWindow | createNewProcessGroup
}

// Detach 让子进程脱离当前进程（后台服务、延时清剪贴板）：父进程退出后它继续跑。
func Detach(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.HideWindow = true
	cmd.SysProcAttr.CreationFlags |= detachedProcess | createNewProcessGroup
}

// Terminate 结束子进程（Windows 上同 Python Popen.terminate：TerminateProcess）。
func Terminate(p *os.Process) { _ = p.Kill() }

// CommandLine 返回一个进程的映像名（tasklist）；进程不在了返回 ""。
func CommandLine(pid int) string {
	cmd := exec.Command("tasklist", "/FI", "PID eq "+strconv.Itoa(pid), "/FO", "CSV", "/NH")
	Hide(cmd)
	out, err := cmd.Output()
	if err != nil || !strings.Contains(string(out), strconv.Itoa(pid)) {
		return ""
	}
	return strings.TrimSpace(string(out))
}
