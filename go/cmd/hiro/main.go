// hiro 是初星助手的命令行：查信息、开局、看进度、暂停或接手。每条命令打印一行 JSON 信封（说明见 skills/hiro-cli/SKILL.md）。
package main

import (
	"fmt"
	"os"
	"strconv"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
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
