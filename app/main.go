// 初星助手（原名数字小广控制台）：桌面程序（设计稿 docs/ryugi/specs/2026-11-17-hiro-console-design.md）。
// 界面是 React 网页（src/），所有能力都经 Hiro 服务调工具层的 hiro 命令行。
package main

import (
	"context"
	"log"
	"path/filepath"

	"github.com/egoist/mygo"

	"github.com/matto49/hiro-gakumas-hero/app/locales"
)

// AppName 是程序的显示名（窗口标题；mygo.config.ts 的 name、index.html 的标题要和它一致）。
// 内部标识 top.matto.hiro-console、数据目录 hiro-console、命令行 hiro 都不随改名变（设置和已存的 Key 不丢）。
var AppName = locales.AppTitle()

func main() {
	fixPath()
	hiro := &Hiro{}
	mygo.Bind(hiro)
	assistant := &Assistant{Home: HiroHome(), Keys: fileKeys{Path: filepath.Join(HiroHome(), "model-keys.json")}, Hiro: hiro}
	hiro.Env = assistant.jobEnv
	mygo.Bind(assistant)
	mygo.Bind(&Threads{Home: HiroHome()})
	mygo.Bind(&Bootstrap{Home: HiroHome(), Hiro: hiro})
	// 网页里的小图和对局截图：hiro://localhost/sprites/…、/traces/…（只读 HIRO_HOME 下这两处）
	mygo.Protocol.Handle("hiro", homeFiles(HiroHome(), func(ctx context.Context, asset string) error {
		r := hiro.Call(ctx, []string{"catalog", "sprite", asset})
		if !r.OK {
			return context.Canceled
		}
		return nil
	}))

	mygo.App.WhenReady(func() {
		mygo.NewWindow(mygo.WindowOptions{
			Title:           AppName,
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
