//go:build !windows

package main

// relaunchAdmin 只在 Windows 上需要（DMM 版游戏是管理员运行）；其他系统没有这个按钮。
var relaunchAdmin func() error
