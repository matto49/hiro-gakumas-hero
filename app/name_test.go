package main

import (
	"os"
	"regexp"
	"testing"
)

// 程序名只在一处定（AppName），打包配置、窗口标题、网页标题要和它一致。
func TestAppNameIsConsistent(t *testing.T) {
	if AppName != "初星助手" {
		t.Fatalf("AppName = %q", AppName)
	}
	cfg, _ := os.ReadFile("mygo.config.ts")
	if m := regexp.MustCompile(`name: "([^"]+)"`).FindSubmatch(cfg); m == nil || string(m[1]) != AppName {
		t.Fatalf("mygo.config.ts name = %q", m)
	}
	html, _ := os.ReadFile("index.html")
	if m := regexp.MustCompile(`<title>([^<]+)</title>`).FindSubmatch(html); m == nil || string(m[1]) != AppName {
		t.Fatalf("index.html title = %q", m)
	}
}
