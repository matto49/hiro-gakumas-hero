package main

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/shell"
)

func TestBuildAppShowsBuiltinPlugins(t *testing.T) {
	home := t.TempDir()
	ready := func(context.Context) bool { return true }
	call := func(_ context.Context, args []string) hiroapi.Result {
		return hiroapi.Result{OK: true, Data: json.RawMessage(`{}`)}
	}
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "model-keys.json")}}
	_ = a.Save(Settings{Provider: "deepseek", WizardDone: true}, "")
	run := &shell.SyncRunner{}
	app := buildApp(appDeps{Home: home, Call: call, Assistant: a, Bootstrap: &Bootstrap{Home: home, Hiro: &Hiro{}}, Ready: ready, Run: run})
	tt := ui.NewTester(app.View, 1280, 800)
	run.Drain() // 环境检查在后台
	tt.Frame()
	// 11-26：缺省进页面模式（首页），「问 AI」进原来的对话界面
	for _, s := range []string{"初星助手", "首页", "开局", "日常", "编成方案", "资料库", "游戏运行现状", "管理插件", "设置", "问 AI"} {
		if !tt.HasText(s) {
			t.Errorf("没有 %q：%q", s, tt.Texts())
		}
	}
	app.SetMode(shell.ModeChat)
	tt.Frame()
	for _, s := range []string{"新对话", "回到页面", "开局", "资料库"} {
		if !tt.HasText(s) {
			t.Errorf("对话界面没有 %q：%q", s, tt.Texts())
		}
	}
}

// C1：真 Agent 回来的卡片里 pending 是 *Pending 结构体，交给界面前要变成和存盘一样的 JSON 形状。
func TestAgentCardsBecomeJSONShape(t *testing.T) {
	cards := jsonCards([]map[string]any{
		{"type": "confirm", "pending": &Pending{ID: "p1", Command: "hiro run start hajime", Summary: "初", ExpiresAt: 4102444800}, "status": "waiting"},
		{"type": "error", "error": &CliError{Code: "x", Message: "坏了"}},
	})
	p, ok := cards[0]["pending"].(map[string]any)
	if !ok || p["id"] != "p1" || p["expires_at"].(float64) != 4102444800 {
		t.Fatalf("pending 形状 %#v", cards[0]["pending"])
	}
	if e, ok := cards[1]["error"].(map[string]any); !ok || e["message"] != "坏了" {
		t.Errorf("error 形状 %#v", cards[1]["error"])
	}
}

// I7：设置每帧都要问，读盘要有缓存，保存后立刻失效。
func TestSettingsCacheInvalidatesOnSave(t *testing.T) {
	home := t.TempDir()
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "model-keys.json")}}
	c := &settingsCache{a: a}
	_ = c.Save(Settings{Provider: "deepseek", Model: "m1"}, "")
	if c.Get().Model != "m1" {
		t.Fatal("读不到")
	}
	_ = a.Save(Settings{Provider: "deepseek", Model: "外面改的"}, "") // 绕过缓存改盘：缓存期内还是旧的
	if c.Get().Model != "m1" {
		t.Error("缓存期内不该每次读盘")
	}
	_ = c.Save(Settings{Provider: "deepseek", Model: "m2"}, "")
	if c.Get().Model != "m2" {
		t.Error("保存后要立刻生效")
	}
}
