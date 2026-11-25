package extplugin

import (
	"os/exec"
	"syscall"
)

// hide 让插件进程不弹控制台窗口。
func hide(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}
