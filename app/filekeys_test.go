package main

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// 模型 Key 存进数据目录的 model-keys.json（0600），不走系统钥匙串：开发版每次重新构建签名都变，钥匙串每次都要用户授权。
func TestFileKeysRoundTripAndPermissions(t *testing.T) {
	home := t.TempDir()
	k := fileKeys{Path: filepath.Join(home, "model-keys.json")}
	if _, err := k.Get("deepseek-api-key"); !errors.Is(err, errNoKey) {
		t.Fatalf("还没存时应是 errNoKey，得到 %v", err)
	}
	if err := k.Set("deepseek-api-key", "sk-test-1"); err != nil {
		t.Fatal(err)
	}
	if err := k.Set("kimi-api-key", "sk-test-2"); err != nil {
		t.Fatal(err)
	}
	if v, err := k.Get("deepseek-api-key"); err != nil || v != "sk-test-1" {
		t.Fatalf("got %q %v", v, err)
	}
	if runtime.GOOS != "windows" {
		st, err := os.Stat(k.Path)
		if err != nil || st.Mode().Perm() != 0o600 {
			t.Fatalf("文件权限应是 0600：%v %v", st.Mode().Perm(), err)
		}
	}
	if err := k.Delete("deepseek-api-key"); err != nil {
		t.Fatal(err)
	}
	if _, err := k.Get("deepseek-api-key"); !errors.Is(err, errNoKey) {
		t.Fatalf("删了以后应是 errNoKey，得到 %v", err)
	}
	if v, _ := k.Get("kimi-api-key"); v != "sk-test-2" {
		t.Fatalf("删一个不该影响别的：%q", v)
	}
}

func TestAssistantSavesKeyToFileNotSettings(t *testing.T) {
	home := t.TempDir()
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "model-keys.json")}, Hiro: &Hiro{}}
	if err := a.Save(Settings{Provider: "deepseek"}, "sk-file-key"); err != nil {
		t.Fatal(err)
	}
	if !a.Settings().HasKey {
		t.Fatal("存了 Key 以后 has_key 应为 true")
	}
	b, _ := os.ReadFile(filepath.Join(home, "settings.json"))
	if len(b) == 0 || strings.Contains(string(b), "sk-file-key") {
		t.Fatalf("settings.json 不该有 Key：%s", b)
	}
}
