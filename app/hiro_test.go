package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// fakeHiro 写一个假的 hiro 命令：按第一个参数打印不同的信封。
func fakeHiro(t *testing.T) []string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("假命令用 sh 脚本")
	}
	p := filepath.Join(t.TempDir(), "hiro")
	script := `#!/bin/sh
case "$1" in
  ok) echo '{"ok":true,"data":{"args":"'"$*"'"}}' ;;
  fail) echo '{"ok":false,"error":{"code":"busy","message":"已经有任务在跑","hint":"等它结束"}}'; exit 1 ;;
  pending) echo '{"ok":true,"data":{"pending":{"id":"abc","command":"run start","summary":"开一局","expires_at":1}}}'; exit 3 ;;
  garbage) echo 'Traceback: boom' ; exit 1 ;;
esac
`
	if err := os.WriteFile(p, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return []string{p}
}

func TestCallParsesOkEnvelope(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"ok", "x"})
	if !r.OK || r.Exit != 0 {
		t.Fatalf("want ok, got %+v", r)
	}
	var d map[string]string
	if err := json.Unmarshal(r.Data, &d); err != nil || d["args"] != "ok x" {
		t.Fatalf("data = %s (%v)", r.Data, err)
	}
}

func TestCallKeepsErrorCodeAndHint(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"fail"})
	if r.OK || r.Error == nil || r.Error.Code != "busy" || r.Error.Hint != "等它结束" || r.Exit != 1 {
		t.Fatalf("got %+v", r)
	}
}

func TestCallReportsPending(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"pending"})
	if !r.OK || r.Exit != 3 || r.Pending == nil || r.Pending.ID != "abc" {
		t.Fatalf("got %+v", r)
	}
}

func TestCallTurnsGarbageIntoAnError(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"garbage"})
	if r.OK || r.Error == nil || r.Error.Code != "bad_output" {
		t.Fatalf("got %+v", r)
	}
}

func TestCallWithoutHiroExplains(t *testing.T) {
	h := &Hiro{Command: []string{filepath.Join(t.TempDir(), "missing")}}
	r := h.Call(context.Background(), []string{"ok"})
	if r.OK || r.Error.Code != "hiro_missing" || r.Error.Hint == "" {
		t.Fatalf("got %+v", r)
	}
}

func TestResolvePrefersBundledGoHiro(t *testing.T) {
	res := t.TempDir()
	name := "hiro"
	if runtime.GOOS == "windows" {
		name = "hiro.exe"
	}
	if err := os.MkdirAll(filepath.Join(res, "bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	bundled := filepath.Join(res, "bin", name)
	if err := os.WriteFile(bundled, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	old := resourcesDir
	resourcesDir = func() string { return res }
	defer func() { resourcesDir = old }()
	t.Setenv("HIRO_HOME", t.TempDir())
	t.Setenv("HIRO_CLI", "")
	if got := ResolveHiroCommand(); len(got) != 1 || got[0] != bundled {
		t.Fatalf("got %v want [%s]", got, bundled)
	}
	t.Setenv("HIRO_CLI", `["/dev/hiro"]`) // 开发时 HIRO_CLI 优先
	if got := ResolveHiroCommand(); len(got) != 1 || got[0] != "/dev/hiro" {
		t.Fatalf("HIRO_CLI should win: %v", got)
	}
	t.Setenv("HIRO_CLI", "")
	if err := os.Remove(bundled); err != nil {
		t.Fatal(err)
	}
	if got := ResolveHiroCommand(); len(got) != 1 || got[0] != "hiro" { // 没带 Go 版、没装 Python：PATH 上的 hiro
		t.Fatalf("fallback %v", got)
	}
}
