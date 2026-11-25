package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// TestLLMEnvSharedCases：开局模型环境变量的共享用例（Python hiro.cli.llm_env 读同一份 testdata/llm_env_cases.json）。
func TestLLMEnvSharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "llm_env_cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Cases []struct {
			Name     string            `json:"name"`
			Settings map[string]any    `json:"settings"`
			Keys     map[string]string `json:"keys"`
			Want     map[string]string `json:"want"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	for _, c := range doc.Cases {
		home := t.TempDir()
		b, _ := json.Marshal(c.Settings)
		if err := os.WriteFile(filepath.Join(home, "settings.json"), b, 0o600); err != nil {
			t.Fatal(err)
		}
		kb, _ := json.Marshal(c.Keys)
		if err := os.WriteFile(filepath.Join(home, "model-keys.json"), kb, 0o600); err != nil {
			t.Fatal(err)
		}
		a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "model-keys.json")}, Hiro: &Hiro{}}
		got := envMap(a.jobEnv())
		if len(got) != len(c.Want) {
			t.Errorf("%s：得到 %v，要 %v", c.Name, got, c.Want)
			continue
		}
		for k, v := range c.Want {
			if got[k] != v {
				t.Errorf("%s：%s=%q，要 %q", c.Name, k, got[k], v)
			}
		}
	}
}
