package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type memKeys map[string]string

func (m memKeys) Get(name string) (string, error) {
	v, ok := m[name]
	if !ok {
		return "", errNoKey
	}
	return v, nil
}
func (m memKeys) Set(name, v string) error { m[name] = v; return nil }
func (m memKeys) Delete(name string) error { delete(m, name); return nil }

func TestSettingsRoundTripKeepsKeyOutOfTheFile(t *testing.T) {
	home := t.TempDir()
	keys := memKeys{}
	a := &Assistant{Home: home, Keys: keys, Hiro: &Hiro{}}
	if s := a.Settings(); s.Provider != "deepseek" || s.HasKey {
		t.Fatalf("fresh settings %+v", s)
	}
	if err := a.Save(Settings{Provider: "deepseek", Model: "deepseek-v4-pro", WizardDone: true}, "sk-secret-123"); err != nil {
		t.Fatal(err)
	}
	s := a.Settings()
	if s.Provider != "deepseek" || !s.HasKey || s.Model != "deepseek-v4-pro" || !s.WizardDone {
		t.Fatalf("settings %+v", s)
	}
	raw, _ := os.ReadFile(filepath.Join(home, "settings.json"))
	if strings.Contains(string(raw), "sk-secret") {
		t.Fatal("key leaked into settings.json")
	}
	b, _ := json.Marshal(s)
	if strings.Contains(string(b), "sk-secret") {
		t.Fatal("key leaked to the page")
	}
	// 空 key：不改已存的
	_ = a.Save(Settings{Provider: "deepseek", Model: "deepseek-v4-pro"}, "")
	if keys["deepseek-api-key"] != "sk-secret-123" {
		t.Fatal("empty key must not erase")
	}
}

func TestAskWithoutProviderExplains(t *testing.T) {
	a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
	_, err := a.Ask(context.Background(), nil, "hi")
	if err == nil || !strings.Contains(err.Error(), "设置") {
		t.Fatalf("err %v", err)
	}
}

func TestCLIAgentIsReadOnlyAndActionsBecomeConfirmCards(t *testing.T) {
	dir := t.TempDir()
	calls := filepath.Join(dir, "calls")
	hiro := filepath.Join(dir, "hiro")
	_ = os.WriteFile(hiro, []byte(`#!/bin/sh
echo "$*" >> "`+calls+`"
case "$1 $2" in
  "run start") echo '{"ok":true,"data":{"pending":{"id":"p7","command":"run start","summary":"领日常奖励","expires_at":1}}}'; exit 3 ;;
  "run status") echo '{"ok":true,"data":{"current":null,"recent":[]}}' ;;
esac
`), 0o755)
	// 假的 claude：记下参数和沙箱环境变量，回答里请求一个开局、一个不允许的确认
	argsFile := filepath.Join(dir, "args")
	agentBin := filepath.Join(dir, "claude")
	script := strings.ReplaceAll(`#!/bin/sh
for a in "$@"; do echo "$a"; done > ARGS
echo "sandbox=$HIRO_AGENT_SANDBOX" >> ARGS
echo '好的，给你出确认卡。'
echo 'HIRO_ACTION: run start daily'
echo 'HIRO_ACTION: confirm p7'
`, "ARGS", argsFile)
	_ = os.WriteFile(agentBin, []byte(script), 0o755)
	c := CLIAgent{Kind: "claude", Bin: agentBin, Hiro: &Hiro{Command: []string{hiro}}, Home: dir}
	r, err := c.Ask(context.Background(), nil, "帮我领日常")
	if err != nil {
		t.Fatal(err)
	}
	if r.Text != "好的，给你出确认卡。" {
		t.Fatalf("text %q", r.Text)
	}
	if len(r.Cards) != 1 || r.Cards[0]["type"] != "confirm" {
		t.Fatalf("cards %+v", r.Cards)
	}
	b, _ := os.ReadFile(calls)
	if strings.Contains(string(b), "confirm") || !strings.Contains(string(b), "run start daily") {
		t.Fatalf("hiro calls %q", b)
	}
	a, _ := os.ReadFile(argsFile)
	args := string(a)
	if strings.Contains(args, "Bash(hiro:*)") || !strings.Contains(args, "Bash(hiro info:*)") || !strings.Contains(args, "sandbox=1") {
		t.Fatalf("claude args %q", args)
	}
	for _, deny := range []string{"Bash(hiro confirm:*)", "Bash(hiro secret:*)", "Bash(hiro run start:*)"} {
		if !strings.Contains(args, deny) {
			t.Fatalf("missing deny %s in %q", deny, args)
		}
	}
}

func TestCodexRunsReadOnly(t *testing.T) {
	argv := CLIAgent{Kind: "codex", Bin: "codex", Home: "/h"}.argv("p", "/h/last.txt")
	j := strings.Join(argv, " ")
	if !strings.Contains(j, "-s read-only") || strings.Contains(j, "workspace-write") || strings.Contains(j, "network_access") {
		t.Fatalf("codex argv %v", argv)
	}
}

func TestThreadsStoreRejectsBadIDs(t *testing.T) {
	ts := &Threads{Home: t.TempDir()}
	if err := ts.Save("t-1", json.RawMessage(`{"id":"t-1","messages":[]}`)); err != nil {
		t.Fatal(err)
	}
	if err := ts.Save("../x", json.RawMessage(`{}`)); err == nil {
		t.Fatal("bad id accepted")
	}
	list := ts.List()
	if len(list) != 1 {
		t.Fatalf("list %v", list)
	}
	_ = ts.Delete("t-1")
	if len(ts.List()) != 0 {
		t.Fatal("not deleted")
	}
}
