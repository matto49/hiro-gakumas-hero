// 初星助手（原名数字小广控制台）：桌面程序。界面是 mygo 原生 ui（设计稿 docs/ryugi/specs/2026-11-24-hiro-console-native-chat-first-design.md），
// 对话为主、页面都是插件；所有能力都经 Hiro 调工具层的 hiro 命令行。
package main

import (
	"context"
	"log"
	"path/filepath"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/locales"
	"github.com/matto49/hiro-gakumas-hero/app/shell"
)

// AppName 是程序的显示名（窗口标题；mygo 配置的 name 要和它一致）。
// 内部标识 top.matto.hiro-console、数据目录 hiro-console、命令行 hiro 都不随改名变（设置和已存的 Key 不丢）。
var AppName = locales.AppTitle()

func main() {
	fixPath()
	home := HiroHome()
	hiro := &Hiro{}
	assistant := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "model-keys.json")}, Hiro: hiro}
	hiro.Env = assistant.jobEnv
	// 侧边面板里的网页视图（复盘页）读 HIRO_HOME 下的图：hiro://localhost/…
	mygo.Protocol.Handle("hiro", homeFiles(home, func(ctx context.Context, asset string) error {
		r := hiro.Call(ctx, []string{"catalog", "sprite", asset})
		if !r.OK {
			return context.Canceled
		}
		return nil
	}))

	// 外部插件的网页面板：hiroplug://localhost/<插件 id>/<文件>（只给已启用插件目录里的文件，带 CSP）
	web := &webPanels{}
	mygo.Protocol.Handle("hiroplug", pluginFilesFor(func(id string) string {
		if plugins == nil {
			return ""
		}
		return plugins.Dir(id)
	}, web.ownerOf))

	mygo.App.WhenReady(func() {
		runner := &lateRunner{}
		app := buildApp(appDeps{Home: home, Call: hiro.Call, Hiro: hiro, Assistant: assistant, Bootstrap: &Bootstrap{Home: home, Hiro: hiro}, Run: runner, Web: web})
		win := mygo.NewWindow(mygo.WindowOptions{
			Title:     AppName,
			Width:     1280,
			Height:    800,
			MinWidth:  960,
			MinHeight: 640,
			StateKey:  "main",
			Content:   ui.View(app.View),
		})
		web.setWindow(win)
		runner.set(shell.WindowRunner{Update: win.Update})
		app.StartPolling(make(chan struct{}))
	})
	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
}
