// 数字小广控制台：桌面程序（设计稿 docs/ryugi/specs/2026-11-17-hiro-console-design.md）。
// 界面是 React 网页（src/），所有能力都经 Hiro 服务调工具层的 hiro 命令行。
package main

import (
	"log"

	"github.com/egoist/mygo"
)

func main() {
	mygo.Bind(&Hiro{})

	mygo.App.WhenReady(func() {
		mygo.NewWindow(mygo.WindowOptions{
			Title:           "数字小广",
			Width:           1280,
			Height:          800,
			MinWidth:        960,
			MinHeight:       640,
			BackgroundColor: "#f7f3ec", // src/style.css 的 --bg
			StateKey:        "main",
			URL:             "/",
		})
	})
	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
}
