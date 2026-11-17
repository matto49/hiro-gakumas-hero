package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestDefaultProviderIsDeepSeek(t *testing.T) {
	a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
	if s := a.Settings(); s.Provider != "deepseek" || s.HasKey {
		t.Fatalf("fresh settings %+v", s)
	}
	if err := a.Save(Settings{Provider: "none"}, ""); err != nil {
		t.Fatal(err)
	}
	if s := a.Settings(); s.Provider != "none" {
		t.Fatalf("none not kept: %+v", s)
	}
}

func TestSaveRecordsHasKeyForDoctorWithoutTheKey(t *testing.T) {
	home := t.TempDir()
	a := &Assistant{Home: home, Keys: memKeys{}, Hiro: &Hiro{}}
	_ = a.Save(Settings{Provider: "deepseek"}, "sk-zzz-123")
	raw, _ := os.ReadFile(filepath.Join(home, "settings.json"))
	var f map[string]any
	_ = json.Unmarshal(raw, &f)
	if f["has_key"] != true || strings.Contains(string(raw), "sk-zzz") {
		t.Fatalf("settings.json %s", raw)
	}
}

func envMap(env []string) map[string]string {
	m := map[string]string{}
	for _, e := range env {
		k, v, _ := strings.Cut(e, "=")
		m[k] = v
	}
	return m
}

func TestJobEnvFollowsTheModelSetting(t *testing.T) {
	keys := memKeys{}
	a := &Assistant{Home: t.TempDir(), Keys: keys, Hiro: &Hiro{}}
	if m := envMap(a.jobEnv()); m["HIRO_LLM"] != "off" || m["OPENAI_API_KEY"] != "" {
		t.Fatalf("deepseek without key: %v", m)
	}
	_ = a.Save(Settings{Provider: "deepseek"}, "sk-abc")
	m := envMap(a.jobEnv())
	if m["HIRO_LLM"] != "openai:deepseek-flash" || m["OPENAI_BASE_URL"] != "https://api.deepseek.com" || m["OPENAI_API_KEY"] != "sk-abc" {
		t.Fatalf("deepseek: %v", m)
	}
	_ = a.Save(Settings{Provider: "claude"}, "")
	if m := envMap(a.jobEnv()); m["HIRO_LLM"] != "claude:sonnet" || m["OPENAI_API_KEY"] != "" {
		t.Fatalf("claude: %v", m)
	}
	_ = a.Save(Settings{Provider: "codex"}, "")
	if m := envMap(a.jobEnv()); m["HIRO_LLM"] != "codex" {
		t.Fatalf("codex: %v", m)
	}
	_ = a.Save(Settings{Provider: "none"}, "")
	if m := envMap(a.jobEnv()); m["HIRO_LLM"] != "off" {
		t.Fatalf("none: %v", m)
	}
}

func TestOnlyConfirmCallsCarryTheJobEnv(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "hiro")
	_ = os.WriteFile(p, []byte(`#!/bin/sh
echo "{\"ok\":true,\"data\":{\"llm\":\"$HIRO_LLM\",\"key\":\"${OPENAI_API_KEY:+set}\"}}"
`), 0o755)
	h := &Hiro{Command: []string{p}, Env: func() []string { return []string{"HIRO_LLM=openai:deepseek-chat", "OPENAI_API_KEY=sk-abc"} }}
	r := h.Call(context.Background(), []string{"confirm", "abc"})
	if !strings.Contains(string(r.Data), `"llm":"openai:deepseek-chat"`) || !strings.Contains(string(r.Data), `"key":"set"`) {
		t.Fatalf("confirm: %s", r.Data)
	}
	r = h.Call(context.Background(), []string{"info", "summary"})
	if strings.Contains(string(r.Data), "deepseek") || strings.Contains(string(r.Data), `"key":"set"`) {
		t.Fatalf("info: %s", r.Data)
	}
}

func TestPageCannotReachTheKey(t *testing.T) {
	// mygo 把导出方法绑定给网页：能返回 Key 的方法不能导出
	typ := reflect.TypeOf(&Assistant{})
	for i := 0; i < typ.NumMethod(); i++ {
		if name := typ.Method(i).Name; name == "JobEnv" {
			t.Fatalf("Assistant.%s is exported and would be callable from the page", name)
		}
	}
}
