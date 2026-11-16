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
	if s := a.Settings(); s.Provider != "" || s.HasKey {
		t.Fatalf("fresh settings %+v", s)
	}
	if err := a.Save(Settings{Provider: "deepseek", Model: "deepseek-chat", WizardDone: true}, "sk-secret-123"); err != nil {
		t.Fatal(err)
	}
	s := a.Settings()
	if s.Provider != "deepseek" || !s.HasKey || s.Model != "deepseek-chat" || !s.WizardDone {
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
	_ = a.Save(Settings{Provider: "deepseek", Model: "deepseek-chat"}, "")
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

func TestCLIAgentTurnsNewPendingsIntoCards(t *testing.T) {
	dir := t.TempDir()
	state := filepath.Join(dir, "state")
	// 假 hiro：pending list 第一次空，第二次有一条（agent 跑的时候开了待确认）
	hiro := filepath.Join(dir, "hiro")
	_ = os.WriteFile(hiro, []byte(`#!/bin/sh
if [ "$1 $2" = "pending list" ]; then
  if [ -f "`+state+`" ]; then echo '{"ok":true,"data":{"pending":[{"id":"p7","command":"run start","summary":"领日常奖励","expires_at":1}]}}'
  else echo '{"ok":true,"data":{"pending":[]}}'; fi
fi
`), 0o755)
	// 假的 claude：把状态文件建出来，再说一句话
	agentBin := filepath.Join(dir, "claude")
	_ = os.WriteFile(agentBin, []byte("#!/bin/sh\ntouch "+state+"\necho '好的，已经发起，等你确认。'\n"), 0o755)
	c := CLIAgent{Kind: "claude", Bin: agentBin, Hiro: &Hiro{Command: []string{hiro}}, Home: dir}
	r, err := c.Ask(context.Background(), nil, "帮我领日常")
	if err != nil {
		t.Fatal(err)
	}
	if r.Text != "好的，已经发起，等你确认。" || len(r.Cards) != 1 || r.Cards[0]["type"] != "confirm" {
		t.Fatalf("reply %+v", r)
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
