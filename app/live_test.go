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

// 实测安装：HIRO_LIVE=bootstrap，HIRO_HOME 指向一个空目录；HIRO_REPO_URL / HIRO_REPO_REF 指向要装的源（Ref 不设就按安装包版本下标签、没有就退回 main）。
func TestLiveBootstrap(t *testing.T) {
	if os.Getenv("HIRO_LIVE") != "bootstrap" {
		t.Skip("设 HIRO_LIVE=bootstrap 才跑")
	}
	b := &Bootstrap{Home: HiroHome(), Hiro: &Hiro{}}
	e := b.env(context.Background(), true)
	steps, perr := PlanBootstrap(e)
	if perr != nil {
		t.Fatal(perr.Message, perr.Hint)
	}
	for _, st := range steps {
		t.Logf("==> %s %v", st.Title, st.Argv)
		if st.Run != nil {
			if err := st.Run(context.Background(), func(line string) { t.Log(line) }); err != nil {
				t.Fatalf("%s: %v", st.Title, err)
			}
			continue
		}
		cmd := exec.Command(st.Argv[0], st.Argv[1:]...)
		cmd.Dir, cmd.Env = st.Dir, append(os.Environ(), st.Env...)
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		if err := cmd.Run(); err != nil {
			t.Fatalf("%s: %v", st.Title, err)
		}
	}
	if steps, _ := PlanBootstrap(e); len(steps) != 0 {
		t.Fatalf("still steps after install: %v", steps)
	}
	h := &Hiro{}
	r := h.Call(context.Background(), []string{"doctor"})
	if !r.OK {
		t.Fatalf("doctor after install: %+v", r.Error)
	}
	t.Logf("doctor: %s", r.Data)
}

// 实测：命令行 agent 要开局时只能产出确认卡（HIRO_LIVE=claude|codex）。
func TestLiveCLIAgentAction(t *testing.T) {
	kind := os.Getenv("HIRO_LIVE")
	if kind != "claude" && kind != "codex" {
		t.Skip("设 HIRO_LIVE=claude 或 codex 才跑")
	}
	bin, err := exec.LookPath(kind)
	if err != nil {
		t.Skip(kind + " 不在 PATH")
	}
	c := CLIAgent{Kind: kind, Bin: bin, Hiro: &Hiro{}, Home: HiroHome()}
	r, err := c.Ask(context.Background(), nil, "帮我领日常奖励")
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("reply: %s cards: %v", r.Text, r.Cards)
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "confirm" {
		t.Fatalf("want one confirm card, got %v", r.Cards)
	}
}

// 实测：模拟 Finder 启动的最小 PATH，fixPath 之后能找到 claude / codex（HIRO_LIVE=path）。
func TestLiveFixPath(t *testing.T) {
	if os.Getenv("HIRO_LIVE") != "path" {
		t.Skip("设 HIRO_LIVE=path 才跑")
	}
	fixPath()
	for _, name := range []string{"claude", "codex"} {
		p, err := exec.LookPath(name)
		t.Logf("%s → %s %v", name, p, err)
		if err != nil {
			t.Fail()
		}
	}
}
