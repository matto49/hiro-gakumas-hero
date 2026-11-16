package main

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// 实测：HIRO_LIVE=claude|codex 时用本机已登录的命令行问一个只读问题（要 HIRO_HOME 里有支援卡数据、HIRO_CLI 指向工具层）。
func TestLiveCLIAgent(t *testing.T) {
	kind := os.Getenv("HIRO_LIVE")
	if kind == "" {
		t.Skip("设 HIRO_LIVE=claude 或 codex 才跑")
	}
	bin, err := exec.LookPath(kind)
	if err != nil {
		t.Skip(kind + " 不在 PATH")
	}
	c := CLIAgent{Kind: kind, Bin: bin, Hiro: &Hiro{}, Home: HiroHome()}
	r, err := c.Ask(context.Background(), nil, "我有多少张 SSR 支援卡？只回答数字和一句话。")
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("reply: %s", r.Text)
	if !strings.Contains(r.Text, "45") {
		t.Fatalf("want 45 in reply: %q", r.Text)
	}
}
