// hiro 是初星助手的命令行：查信息、开局、看进度、暂停或接手。每条命令打印一行 JSON 信封（说明见 skills/hiro-cli/SKILL.md）。
package main

import (
	"fmt"
	"os"
	"strconv"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/device"
	"github.com/matto49/hiro-gakumas-hero/go/internal/hiro"
	"github.com/matto49/hiro-gakumas-hero/go/internal/secret"
	"github.com/matto49/hiro-gakumas-hero/go/internal/service"
)

func main() {
	args := os.Args[1:]
	if len(args) > 0 {
		switch args[0] { // 隐藏子命令：程序自己用，不在 hiro tools 里
		case service.ServiceCommand:
			os.Exit(service.Serve())
		case service.SelftestCommand:
			os.Exit(service.Selftest(args[1:]))
		case service.WinWindowCommand:
			os.Exit(winWindow(args[1:]))
		case secret.ClearCommand:
			if len(args) < 3 {
				fmt.Fprintln(os.Stderr, "用法：hiro __clear-clipboard <序号> <秒>")
				os.Exit(2)
			}
			seq, err1 := strconv.ParseUint(args[1], 10, 32)
			secs, err2 := strconv.Atoi(args[2])
			if err1 != nil || err2 != nil {
				os.Exit(2)
			}
			secret.ClearLater(uint32(seq), secs)
			os.Exit(0)
		}
	}
	os.Exit(hiro.Registry().Main(args, &cli.Ctx{Stdin: os.Stdin, Stdout: os.Stdout}))
}

// winWindow 看 / 调 DMM 游戏窗口尺寸（同 python -m hiro.devices.win_window）：
//
//	hiro __win-window            看现在的客户区尺寸和按显示器算的目标
//	hiro __win-window fit        调成目标尺寸（要管理员）
//	hiro __win-window 600 1067   调成指定尺寸（要管理员）
func winWindow(args []string) int {
	if len(args) == 0 {
		info, err := device.DMMWindowInfo()
		if err != nil {
			fmt.Println(err)
			return 2
		}
		fmt.Println(info)
		return 0
	}
	var want *[2]int
	if args[0] != "fit" {
		if len(args) < 2 {
			fmt.Println("用法：hiro __win-window [fit | 宽 高]")
			return 2
		}
		w, err1 := strconv.Atoi(args[0])
		h, err2 := strconv.Atoi(args[1])
		if err1 != nil || err2 != nil {
			fmt.Println("宽高要是整数")
			return 2
		}
		want = &[2]int{w, h}
	}
	before, after, err := device.FitDMMWindow(want)
	if err != nil {
		fmt.Println(err)
		return 2
	}
	fmt.Printf("client %dx%d -> %dx%d\n", before[0], before[1], after[0], after[1])
	if want != nil && after != *want {
		return 1
	}
	return 0
}
