package main

import (
	"encoding/json"
	"os"
	"testing"
)

// 程序名只在一处定（AppName），打包配置（mygo.json）、窗口标题要和它一致；标识不随改名变。
func TestAppNameIsConsistent(t *testing.T) {
	if AppName != "初星助手" {
		t.Fatalf("AppName = %q", AppName)
	}
	b, err := os.ReadFile("mygo.json")
	if err != nil {
		t.Fatal(err)
	}
	var cfg struct {
		Name       string `json:"name"`
		Identifier string `json:"identifier"`
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Name != AppName || cfg.Identifier != "top.matto.hiro-console" {
		t.Fatalf("mygo.json name = %q identifier = %q", cfg.Name, cfg.Identifier)
	}
}
