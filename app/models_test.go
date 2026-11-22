package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLegacyDeepSeekNamesBecomeFlash(t *testing.T) {
	for _, old := range []string{"", "deepseek-chat", "deepseek-reasoner"} {
		a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
		_ = a.Save(Settings{Provider: "deepseek", Model: old}, "sk-x")
		if s := a.Settings(); s.Model != "deepseek-flash" {
			t.Fatalf("%q → %q", old, s.Model)
		}
	}
	a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
	_ = a.Save(Settings{Provider: "deepseek", Model: "deepseek-v4-pro"}, "sk-x")
	if s := a.Settings(); s.Model != "deepseek-v4-pro" {
		t.Fatalf("kept %q", s.Model)
	}
}

func TestModelsListsWhatTheKeyCanUse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/models" || r.Header.Get("Authorization") != "Bearer sk-x" {
			w.WriteHeader(401)
			return
		}
		_, _ = io.WriteString(w, `{"object":"list","data":[{"id":"deepseek-flash"},{"id":"deepseek-v4-pro"}]}`)
	}))
	defer srv.Close()
	a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
	if _, err := a.Models(context.Background(), "custom", srv.URL, ""); err == nil || !strings.Contains(err.Error(), "Key") {
		t.Fatalf("no key: %v", err)
	}
	// 还没保存：用界面上刚填的 Key 列模型
	got, err := a.Models(context.Background(), "custom", srv.URL, "sk-x")
	if err != nil || strings.Join(got, ",") != "deepseek-flash,deepseek-v4-pro" {
		t.Fatalf("models %v err %v", got, err)
	}
	// 保存过：用钥匙串里这家的 Key
	_ = a.Save(Settings{Provider: "custom", BaseURL: srv.URL}, "sk-x")
	if got, err = a.Models(context.Background(), "custom", srv.URL, ""); err != nil || len(got) != 2 {
		t.Fatalf("saved key: %v %v", got, err)
	}
	if _, err := a.Models(context.Background(), "custom", srv.URL, "sk-wrong"); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("wrong key: %v", err)
	}
}

func TestKeysArePerProviderAndOldDeepSeekKeyStays(t *testing.T) {
	home := t.TempDir()
	keys := memKeys{"deepseek-api-key": "sk-old"} // 升级前只有这一个 Key
	if err := os.WriteFile(filepath.Join(home, "settings.json"), []byte(`{"provider":"deepseek","model":"deepseek-chat","has_key":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	a := &Assistant{Home: home, Keys: keys, Hiro: &Hiro{}}
	s := a.Settings()
	if s.Provider != "deepseek" || !s.HasKey || s.Model != DefaultDeepSeekModel || !s.HasKeys["deepseek"] {
		t.Fatalf("migrated %+v", s)
	}
	if env := strings.Join(a.jobEnv(), " "); !strings.Contains(env, "HIRO_LLM=openai:deepseek-flash") || !strings.Contains(env, "OPENAI_BASE_URL=https://api.deepseek.com") {
		t.Fatalf("env %s", env)
	}
	if err := a.Save(Settings{Provider: "kimi", Model: "kimi-k2"}, "sk-kimi"); err != nil {
		t.Fatal(err)
	}
	if keys["kimi-api-key"] != "sk-kimi" || keys["deepseek-api-key"] != "sk-old" {
		t.Fatalf("keys %v", keys)
	}
	s = a.Settings()
	if !s.HasKeys["kimi"] || !s.HasKeys["deepseek"] || s.HasKeys["glm"] {
		t.Fatalf("has_keys %v", s.HasKeys)
	}
	env := strings.Join(a.jobEnv(), " ")
	if !strings.Contains(env, "HIRO_LLM=openai:kimi-k2") || !strings.Contains(env, "OPENAI_BASE_URL=https://api.moonshot.cn/v1") || !strings.Contains(env, "OPENAI_API_KEY=sk-kimi") {
		t.Fatalf("env %s", env)
	}
	raw, _ := os.ReadFile(filepath.Join(home, "settings.json"))
	if strings.Contains(string(raw), "sk-") || strings.Contains(string(raw), "has_keys") || strings.Contains(string(raw), "moonshot") {
		t.Fatalf("settings.json %s", raw)
	}
	// 没选模型的预设服务商：对局不用模型，右侧对话说明要选模型
	_ = a.Save(Settings{Provider: "glm"}, "sk-glm")
	if env := a.jobEnv(); env[0] != "HIRO_LLM=off" {
		t.Fatalf("no model env %v", env)
	}
	if _, err := a.Ask(context.Background(), nil, "hi"); err == nil || !strings.Contains(err.Error(), "选模型") {
		t.Fatalf("no model: %v", err)
	}
	if err := a.Save(Settings{Provider: "codex"}, "sk-x"); err == nil {
		t.Fatal("command-line provider must not take a key")
	}
}
