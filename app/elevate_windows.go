//go:build windows

package main

import (
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

var shellExecuteW = syscall.NewLazyDLL("shell32.dll").NewProc("ShellExecuteW")

// relaunchAdmin 以管理员身份重新打开初星助手（G3 11-26 Windows 小白验收：DMM 版游戏是管理员运行的，
// 普通权限的初星助手截不到游戏画面，原来要玩家自己关掉再右键「以管理员身份运行」）。
// 用 ShellExecute「runas」提权启动自己，带 --restart-service：新窗口一起来先关掉旧的、普通权限的后台服务（不然会连上它），
// 再按需拉起自己的（管理员）服务；启动成功后退出当前进程。玩家在「是否允许」里点「否」时什么都不变（服务和对局都还在），
// 返回原因，当前窗口留着（#140 review：原来先关服务，被拒后服务和对局都停了）。
func relaunchAdmin() error {
	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("找不到初星助手程序的位置：%w", err)
	}
	verb, _ := syscall.UTF16PtrFromString("runas")
	args, _ := syscall.UTF16PtrFromString(restartServiceFlag)
	file, err := syscall.UTF16PtrFromString(exe)
	if err != nil {
		return err
	}
	const swShowNormal = 1
	r, _, _ := shellExecuteW.Call(0, uintptr(unsafe.Pointer(verb)), uintptr(unsafe.Pointer(file)), uintptr(unsafe.Pointer(args)), 0, swShowNormal)
	if r <= 32 { // ShellExecute 成功时返回值大于 32；在「是否允许」里点「否」是 5（SE_ERR_ACCESSDENIED）
		if r == 5 {
			return fmt.Errorf("没有以管理员身份重新打开：「是否允许」里点了「否」。要连 DMM 版游戏，需要点「是」")
		}
		return fmt.Errorf("没能以管理员身份重新打开（错误 %d）", r)
	}
	os.Exit(0)
	return nil
}
